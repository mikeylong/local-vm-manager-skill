"""Guest-only adapters for unchanged Node browser suites in fixed profiles."""
import hashlib,json,os,pathlib,shutil,signal,subprocess,time,urllib.request

def run(spec,node,npm,env,execute,work,out):
 source=work/'source';profile=spec['suite_profile'];selected=spec['tests'];rows=[]
 artifacts=out/'suite-artifacts';artifacts.mkdir();logs=out/'suite-logs';logs.mkdir()
 def save():
  p=out/'suite-results.json';tmp=p.with_suffix('.part');tmp.write_text(json.dumps({'profile':profile,'suites':rows},indent=2)+'\n');tmp.replace(p)
 def event(kind,**fields):
  with (out/'progress.jsonl').open('a') as f:
   f.write(json.dumps({'event':kind,'at':time.time(),**fields})+'\n');f.flush();os.fsync(f.fileno())
 env={**env,'SURFACES_SERVICES':'0','SURFACES_BACKGROUND':'0','SURFACES_READ_ONLY':'0','SURFACES_EXAMPLE':'0'}
 if profile=='surfaces':
  if spec.get('git_fixture'):
   fixture=spec['git_fixture'];packed=(work/'git-fixture.pack').read_bytes()
   assert hashlib.sha256(packed).hexdigest()==fixture['sha256'],'Historical Git pack mismatch'
   subprocess.run(['git','init','-q'],cwd=source,check=True,capture_output=True)
   subprocess.run(['git','unpack-objects','-r'],cwd=source,input=packed,check=True,capture_output=True)
   subprocess.run(['git','update-ref','refs/heads/lvm-input',fixture['source_base_commit']],cwd=source,check=True,capture_output=True)
   subprocess.run(['git','symbolic-ref','HEAD','refs/heads/lvm-input'],cwd=source,check=True,capture_output=True)
   blob=subprocess.check_output(['git','show',fixture['historical_commit']+':'+fixture['path']],cwd=source)
   assert hashlib.sha256(blob).hexdigest()==fixture['historical_blob_sha256'],'Historical source bytes changed'
  assert execute(npm+['run','build'],env)==0,'Surfaces web build failed'
  pw=[node,str(source/'node_modules/playwright/cli.js')]
  assert execute(pw+['install','--with-deps','chromium'],env)==0,'Locked Chromium install failed'
  js="const p=require('playwright');console.log(JSON.stringify({playwright:require('playwright/package.json').version,chromium:p.chromium.executablePath()}))"
  versions=json.loads(subprocess.check_output([node,'-e',js],cwd=source,env=env,text=True))
  # Runtime instrumentation changes launch/evidence options, never the source assertions.
  preload=work/'suite-preload.mjs'
  preload.write_text('''import {chromium} from '/tmp/lvm-playwright/source/node_modules/playwright/index.mjs';
import fs from 'node:fs';import crypto from 'node:crypto';
const dir='/tmp/lvm-playwright-evidence/suite-artifacts';
const original=chromium.launch.bind(chromium);
chromium.launch=async(options={})=>{
 if((options.executablePath && options.executablePath!==chromium.executablePath()) || (options.args||[]).some(x=>x==='--no-sandbox'||x==='--disable-setuid-sandbox'))throw Error('Browser sandbox override rejected');
 const browser=await original({...options,headless:true,chromiumSandbox:true});
 fs.appendFileSync('/tmp/lvm-playwright-evidence/browser-launches.jsonl',JSON.stringify({suite:process.env.LVM_SUITE,version:browser.version(),sandboxEnabled:true,at:Date.now()})+'\\n');
 const make=browser.newContext.bind(browser);const done=new WeakSet();
 const finish=async context=>{if(done.has(context))return;done.add(context);
 const id=crypto.randomUUID();for(const [i,page] of context.pages().entries())try{await page.screenshot({path:dir+'/'+id+'-'+i+'.png',timeout:3000});}catch{}
 try{await context.tracing.stop({path:dir+'/'+id+'-trace.zip'});}catch{}};
 browser.newContext=async options=>{const context=await make(options);await context.tracing.start({screenshots:true,snapshots:true,sources:false});
 const close=context.close.bind(context);context.close=async(...args)=>{await finish(context);return close(...args)};return context;};
 const close=browser.close.bind(browser);browser.close=async(...args)=>{for(const c of browser.contexts())await finish(c);return close(...args);};return browser;};
''')
  env['NODE_OPTIONS']='--import='+str(preload)
 else:
  versions={'chromium':'/usr/bin/chromium','locked_sparticuz':spec['source_files'].get('package-lock.json',{}),'browser_selection':'existing configured Chrome environment override; Debian ARM64 binary'}
  env.update(CHROME_BIN='/usr/bin/chromium',JUDGMENTKIT_COMPONENT_CHROME_PATH='/usr/bin/chromium',JUDGMENTKIT_VISUAL_COMPOSITION_CHROME_PATH='/usr/bin/chromium')
 versions.update(browser_version=subprocess.check_output([versions['chromium'],'--version'],env=env,text=True).strip(),node=subprocess.check_output([node,'--version'],text=True).strip(),architecture=os.uname().machine,os_release=pathlib.Path('/etc/os-release').read_text(),sandbox_enabled=True)
 (out/'environment.json').write_text(json.dumps(versions,indent=2)+'\n')
 static=work/'static-server.mjs'
 static.write_text('''import http from 'node:http';import fs from 'node:fs';import path from 'node:path';
const root=process.cwd(),mime={'.html':'text/html','.js':'application/javascript','.css':'text/css','.json':'application/json','.svg':'image/svg+xml','.png':'image/png','.woff2':'font/woff2'};
http.createServer((req,res)=>{let p=new URL(req.url,'http://localhost').pathname;
if(req.method!=='GET'){res.writeHead(405).end();return;}
if(p==='/favicon.ico'){res.writeHead(204).end();return;}
if(p==='/workspace-config.json'){res.setHeader('Content-Type','application/json');res.end(JSON.stringify(process.env.LVM_WORKFLOW==='1'?{mode:'workflow',workspaceId:'lvm-retained'}:{mode:'standalone'}));return;}
let base=path.join(root,'dist');if(p.startsWith('/artifacts/')){base=path.join(root,'.surfaces/dag-implementation');}
let file=path.resolve(base,'.'+decodeURIComponent(p));if(!file.startsWith(base+'/')&&file!==base){res.writeHead(403).end();return;}
try{if(fs.statSync(file).isDirectory())file=path.join(file,'index.html');res.setHeader('Content-Type',mime[path.extname(file)]||'application/octet-stream');res.end(fs.readFileSync(file));}catch{res.writeHead(404).end();}
}).listen(4319,'127.0.0.1');''')
 def start_server(cmd,server_env,name,url):
  handle=(logs/(name+'-server.log')).open('ab',buffering=0)
  p=subprocess.Popen(cmd,cwd=source,env=server_env,stdout=handle,stderr=handle,start_new_session=True)
  try:
   end=time.monotonic()+30
   while time.monotonic()<end:
    if p.poll() is not None:raise RuntimeError('Guest-local server exited; see '+name+'-server.log')
    try:
     with urllib.request.urlopen(url,timeout=1) as response:
      if response.status==200:return p,handle
    except Exception:time.sleep(.1)
   raise RuntimeError('Guest-local server readiness timeout')
  except BaseException:
   stop_server(p,handle);raise
 def stop_server(p,handle):
  try:os.killpg(p.pid,signal.SIGTERM)
  except ProcessLookupError:pass
  try:p.wait(timeout=5)
  except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
  handle.close()
 if profile=='surfaces' and any(t in selected for t in ['scripts/check-history.mjs','scripts/check-workspace-refinement.mjs']) and not (source/'output/dag/live-state.json').exists():
  helper=source/'scripts/browser-review-fixture.ts'
  if helper.is_file():
   assert execute([node,'--import','tsx',str(helper)],env)==0,'Disposable browser-review fixture generation failed'
   for filename in ['live-state.json','browser-fixture-provenance.json']:
    shutil.copy2(source/'output/dag'/filename,artifacts/('review-fixture-'+filename))
 for index,name in enumerate(selected):
  row={'entrypoint':name,'started_at':time.time(),'status':'running'};rows.append(row);save();event('suite_begin',entrypoint=name)
  suite_env={**env,'LVM_SUITE':name};server=None
  try:
   if name in ('scripts/check-history.mjs','scripts/check-workspace-refinement.mjs') and not (source/'output/dag/live-state.json').is_file():
    row.update(status='blocked',error='Required retained fixture output/dag/live-state.json and matching artifact tree are absent; no replacement fabricated')
    continue
   command=[node,name]
   if profile=='surfaces':
    command=[node,'--import','tsx',*(['--test'] if name.endswith('.test.ts') else []),name]
    if name in ('scripts/check-workspace.mjs','scripts/check-history.mjs','scripts/check-workspace-refinement.mjs'):
     suite_env.update(WORKSPACE_URL='http://127.0.0.1:4319',LVM_WORKFLOW='0' if name.endswith('/check-workspace.mjs') else '1')
     server=start_server([node,str(static)],suite_env,'static-'+str(index),'http://127.0.0.1:4319/')
    elif name=='scripts/check-theme.mjs':
     suite_env.update(PORT='4320',THEME_URL='http://127.0.0.1:4320',SURFACES_DATA_DIR=str(work/'theme-data'),SURFACES_EXAMPLE='1')
     server=start_server([node,'--import','tsx','server/index.ts','--production'],suite_env,'theme','http://127.0.0.1:4320/api/state')
     request=urllib.request.Request('http://127.0.0.1:4320/api/submit',data=b'{}',headers={'Content-Type':'application/json','X-Surfaces-Local':'1'},method='POST')
     with urllib.request.urlopen(request,timeout=90) as response:assert response.status==200
   else:
    suite_env['JUDGMENTKIT_SYSTEM_MAP_EVIDENCE_OUT']=str(artifacts/'first-use')
    if name=='tests/components/component-browser.test.mjs':command+=['--evidence-out',str(artifacts/'components')]
   row['command']=command;save()
   rc=execute(command,suite_env,timeout=600,stdout=logs/(str(index)+'-'+pathlib.Path(name).stem+'.stdout.log'))
   row.update(exit_code=rc,status='passed' if rc==0 else 'failed')
  except Exception as error:row.update(status='failed',error=str(error))
  finally:
   if server:stop_server(*server)
   row['source_unchanged']=hashlib.sha256((source/name).read_bytes()).hexdigest()==spec['source_files'][name]['sha256']
   if not row['source_unchanged']:row['status']='failed'
   row['finished_at']=time.time();save();event('suite_end',entrypoint=name,status=row['status'])
 # Export only browser diagnostics, not disposable source/backend stores or artifacts.
 if profile=='surfaces':
  for folder in ['output/playwright','output/onboarding-execution/browser','output/guidance-reuse-browser']:
   for p in (source/folder).rglob('*'):
    if not p.is_file() or p.is_symlink():continue
    if p.suffix.lower() not in {'.png','.webm','.zip','.log'} and not (p.suffix=='.json' and folder!='output/guidance-reuse-browser') and p.name not in {'checks.json','failed-checks.json'}:continue
    rel=p.relative_to(source);dest=artifacts/'project-output'/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
 return {'passed':len(rows)==len(selected) and all(r['status']=='passed' and r['source_unchanged'] for r in rows),
         'suite_profile':profile,'suite_count':len(rows),'suite_passed':sum(r['status']=='passed' for r in rows),'suite_failed':sum(r['status']=='failed' for r in rows),
         'suite_blocked':sum(r['status']=='blocked' for r in rows),'source_tests_unchanged':all(r['source_unchanged'] for r in rows)}
