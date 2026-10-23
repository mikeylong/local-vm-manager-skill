#!/usr/bin/python3
"""Guest-only Playwright runner. Exports reports, never source trees or caches."""
from __future__ import annotations
import hashlib,json,os,pathlib,shutil,signal,subprocess,sys,tarfile,time,traceback
WORK=pathlib.Path('/tmp/lvm-playwright')
OUT=pathlib.Path('/tmp/lvm-playwright-evidence')
LIMIT=250*1024**2
ALLOWED={'report','results','run.log','environment.json','result.json','source-manifest.json','discovery.json','progress.jsonl','suite-logs','suite-artifacts','suite-results.json','browser-launches.jsonl'}
current=None

def digest(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def write(p,x):
    tmp=p.with_suffix(p.suffix+'.part');tmp.write_text(json.dumps(x,indent=2)+'\n');tmp.replace(p)
def interrupt(signum,frame):raise InterruptedError('Guest runner interrupted by signal '+str(signum))
def execute(cmd,env,timeout=600,stdout=None):
    global current
    with (OUT/'run.log').open('ab',buffering=0) as log:
        log.write(('\n$ '+json.dumps(cmd)+'\n').encode())
        with (stdout.open('wb',buffering=0) if stdout else open(os.devnull,'wb')) as alternate:
            current=subprocess.Popen(cmd,cwd=WORK/'source',env=env,stdout=alternate if stdout else log,stderr=log,start_new_session=True)
            started=time.monotonic();offset=0
            def forward_progress():
                nonlocal offset
                progress=OUT/'progress.jsonl'
                if not progress.exists():return
                if progress.stat().st_size>1024**2:raise RuntimeError('Progress log exceeds bound')
                with progress.open('rb') as f:
                    f.seek(offset)
                    for line in f:
                        if not line.endswith(b'\n'):break
                        json.loads(line) # forward complete valid records only
                        print('LVM_PLAYWRIGHT_EVENT '+line.decode().strip(),flush=True)
                        offset+=len(line)
            try:
                while current.poll() is None:
                    forward_progress()
                    if time.monotonic()-started>timeout:raise subprocess.TimeoutExpired(cmd,timeout)
                    time.sleep(.025)
                forward_progress()
                return current.returncode
            finally:
                if current.poll() is None:
                    os.killpg(current.pid,signal.SIGTERM)
                    try:current.wait(timeout=5)
                    except subprocess.TimeoutExpired:os.killpg(current.pid,signal.SIGKILL);current.wait()
                current=None

def unpack(payload):
    WORK.mkdir(exist_ok=True)
    with tarfile.open(payload) as t:
        total=0;seen=set()
        for member in t:
            p=pathlib.PurePosixPath(member.name)
            if p.is_absolute() or '..' in p.parts or not p.parts or member.name in seen:raise RuntimeError('Unsafe payload path')
            if not member.isfile() or p.parts[0] not in {'source','node.tar.xz','workload.json','git-fixture.pack'}:raise RuntimeError('Unexpected payload entry')
            seen.add(member.name);total+=member.size
            if total>200*1024**2:raise RuntimeError('Payload exceeds bound')
            target=WORK/member.name;target.parent.mkdir(parents=True,exist_ok=True)
            with t.extractfile(member) as src,target.open('xb') as dst:shutil.copyfileobj(src,dst)
    spec=json.loads((WORK/'workload.json').read_text())
    assert digest(WORK/'node.tar.xz')==spec['node']['sha256'],'Node archive mismatch'
    for name,meta in spec['source_files'].items():assert digest(WORK/'source'/name)==meta['sha256'],'Source mismatch: '+name
    return spec

def test_count(report):
    return sum(len(s.get('tests',[])) for s in report.get('specs',[]))+sum(test_count(s) for s in report.get('suites',[]))

def seal():
    WORK.mkdir(exist_ok=True)
    OUT.mkdir(exist_ok=True)
    if not (OUT/'result.json').exists():
        write(OUT/'result.json',{'passed':False,'error':'Runner interrupted before final report; available evidence retained','finished_at':time.time()})
    entries={}
    for p in sorted(OUT.rglob('*')):
        rel=p.relative_to(OUT)
        if rel.parts[0] not in ALLOWED:continue
        if p.is_symlink():raise RuntimeError('Symlink in evidence')
        if p.is_file():entries[str(rel)]={'bytes':p.stat().st_size,'sha256':digest(p)}
    if sum(v['bytes'] for v in entries.values())>LIMIT:raise RuntimeError('Evidence allowance exceeded; guest retained for resolution')
    bundle=WORK/'evidence.tar.gz';part=WORK/'evidence.tar.gz.part'
    manifest=json.dumps({'schema_version':1,'files':entries},indent=2).encode()
    import io
    with tarfile.open(part,'w:gz') as t:
        info=tarfile.TarInfo('manifest.json');info.size=len(manifest);t.addfile(info,io.BytesIO(manifest))
        for rel in entries:t.add(OUT/rel,arcname=rel,recursive=False)
    part.replace(bundle)
    return {'path':str(bundle),'bytes':bundle.stat().st_size,'sha256':digest(bundle),'files':len(entries)}

def run(payload,expected_digest):
    OUT.mkdir(exist_ok=True)
    result={'passed':False,'started_at':time.time(),'engine':'chromium','sandbox_enabled':True}
    env={**os.environ,'CI':'1','PLAYWRIGHT_HTML_OPEN':'never','PLAYWRIGHT_BROWSERS_PATH':str(WORK/'browsers'),
         'npm_config_cache':str(WORK/'npm-cache'),'DEBUG':'pw:browser'}
    try:
        assert digest(pathlib.Path(payload))==expected_digest,'Payload hash mismatch'
        spec=unpack(payload);result['expected_tests']=spec['expected_tests']
        write(OUT/'source-manifest.json',spec)
        node_dir=WORK/'node';node_dir.mkdir()
        subprocess.run(['tar','-xJf',str(WORK/'node.tar.xz'),'-C',str(node_dir),'--strip-components=1'],check=True,timeout=60)
        env['PATH']=str(node_dir/'bin')+':/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'
        node=str(node_dir/'bin/node');npm=[node,str(node_dir/'lib/node_modules/npm/bin/npm-cli.js')]
        assert execute(npm+['ci','--no-audit','--no-fund'],env)==0,'npm ci failed'
        if spec.get('runner')=='node-suites':
            import importlib.util
            module=importlib.util.spec_from_file_location('guest_suites','/opt/lvm-suites.py')
            suites=importlib.util.module_from_spec(module);module.loader.exec_module(suites)
            result.pop('expected_tests',None);result['expected_suites']=len(spec['tests'])
            result.update(suites.run(spec,node,npm,env,execute,WORK,OUT))
            return 0 if result['passed'] else 1
        assert execute(npm+['run','build'],env)==0,'Project web build failed'
        pw=[node,str(WORK/'source/node_modules/playwright/cli.js')]
        assert execute(pw+['install','--with-deps','chromium'],env,timeout=600)==0,'Matching Playwright browser installation failed'
        (WORK/'reporter.cjs').write_text('''const fs=require('fs');
const emit=(event,test,result)=>{const p='/tmp/lvm-playwright-evidence/progress.jsonl';
 const f=fs.openSync(p,'a');fs.writeSync(f,JSON.stringify({event,at:Date.now(),id:test.id,title:test.titlePath(),
 ...(result?{status:result.status,expectedStatus:test.expectedStatus,retry:result.retry}:{})})+'\\n');fs.fsyncSync(f);fs.closeSync(f);};
module.exports=class {
 onTestBegin(test) {emit('test_begin',test);}
 onTestEnd(test,result) {emit('test_end',test,result);}
};''')
        config=WORK/'source/lvm.playwright.config.ts'
        config.write_text('''import {defineConfig} from '@playwright/test';
import original from './playwright.config';
export default defineConfig({...original, workers:1,retries:0,forbidOnly:true,globalTimeout:600000,
 outputDir:'/tmp/lvm-playwright-evidence/results',
 reporter:[['list'],['json',{outputFile:'/tmp/lvm-playwright-evidence/test-results.json'}],
 ['html',{outputFolder:'/tmp/lvm-playwright-evidence/report',open:'never'}],['/tmp/lvm-playwright/reporter.cjs']],
 use:{...original.use,browserName:'chromium',headless:true,trace:'on',
 launchOptions:{...original.use?.launchOptions,chromiumSandbox:true}}});''')
        args=['test','--config',str(config),*spec['tests']]
        discovery_env={k:v for k,v in env.items() if not k.startswith('PLAYWRIGHT_JSON_')}
        assert execute(pw+args+['--list','--reporter=json'],discovery_env,stdout=OUT/'discovery.json')==0,'Test discovery failed'
        discovered=json.loads((OUT/'discovery.json').read_text())
        count=test_count(discovered);result['discovered_tests']=count
        assert count==spec['expected_tests'],f'Expected {spec["expected_tests"]} tests; discovered {count}'
        js="const p=require('playwright'); console.log(JSON.stringify({playwright:require('@playwright/test/package.json').version,chromium:p.chromium.executablePath()}))"
        versions=json.loads(subprocess.check_output([node,'-e',js],cwd=WORK/'source',env=env,text=True))
        versions['browser_version']=subprocess.check_output([versions['chromium'],'--version'],env=env,text=True).strip()
        versions.update(node=subprocess.check_output([node,'--version'],text=True).strip(),architecture=os.uname().machine,
                        os_release=pathlib.Path('/etc/os-release').read_text(),sandbox_enabled=True)
        write(OUT/'environment.json',versions)
        rc=execute(pw+args,env,timeout=660);result['test_exit_code']=rc
        raw=json.loads((OUT/'test-results.json').read_text());stats=raw['stats'];result['stats']=stats
        result['source_tests_unchanged']=all(digest(WORK/'source'/name)==spec['source_files'][name]['sha256'] for name in spec['tests'])
        result['passed']=rc==0 and stats['expected']==count and stats['unexpected']==0 and stats['skipped']==0 and stats['flaky']==0 and result['source_tests_unchanged']
    except BaseException as e:
        result['error']=str(e)
        (OUT/'run.log').open('a').write('\n'+traceback.format_exc())
    finally:
        result['finished_at']=time.time();write(OUT/'result.json',result)
        # JSON test reporter output is allowed explicitly, never the source workspace.
        ALLOWED.add('test-results.json')
        print(json.dumps(seal()),flush=True)
    return 0 if result['passed'] else 1

if __name__=='__main__':
    for sig in (signal.SIGTERM,signal.SIGHUP,signal.SIGINT):signal.signal(sig,interrupt)
    if sys.argv[1]=='seal':
        ALLOWED.add('test-results.json');print(json.dumps(seal()))
    else:sys.exit(run(sys.argv[2],sys.argv[3]))
