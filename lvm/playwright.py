"""Bounded web-source snapshots and verified Playwright evidence export."""
import io,json,os,pathlib,shutil,tarfile,tempfile
from .manager import ManagerError,atomic,read,sha
ROOTS={'src','api','scripts','tests','public','brand','website-assets'}
ROOT_FILES={'package.json','package-lock.json','playwright.config.ts','index.html','robots.txt'}
EXCLUDED={'node_modules','.git','ios','macos','native','data','.env','output','dist'}
ALLOWED_EVIDENCE={'report','results','run.log','environment.json','result.json','source-manifest.json','discovery.json','progress.jsonl','test-results.json','suite-logs','suite-artifacts','suite-results.json','browser-launches.jsonl'}

def safe_path(name):
    p=pathlib.PurePosixPath(name)
    if not name or not p.parts or '\x00' in name or p.is_absolute() or '..' in p.parts or str(p)!=name:raise ManagerError('Unsafe relative path: '+name)
    return p

def snapshot(m,source_dir,tests,expected_tests,suite_profile=None):
    source=pathlib.Path(source_dir).absolute()
    if source.resolve()!=source or not source.is_dir():raise ManagerError('Source must be a canonical directory')
    if not tests or len(tests)>20 or len(set(tests))!=len(tests):raise ManagerError('Select 1–20 distinct web spec files')
    if not isinstance(expected_tests,int) or not 1<=expected_tests<=500:raise ManagerError('Expected test count must be 1–500')
    roots=ROOTS;root_files=ROOT_FILES
    if suite_profile:
        from .suites import policy
        profile,tests=policy(suite_profile,tests);roots=profile['roots'];root_files=ROOT_FILES|profile['root_files']
        if read(source/'package.json').get('name')!=profile['package']:raise ManagerError('Suite profile package identity mismatch')
    for name in tests:
        p=safe_path(name)
        if not suite_profile and (p.parts[:2]!=('tests','browser') or not name.endswith('.spec.ts')):raise ManagerError('Only tests/browser/*.spec.ts supported')
    selected=[]
    for p in sorted(source.iterdir()):
        if p.name in root_files or p.name.startswith('tsconfig') and p.suffix=='.json' or p.suffix=='.css':selected.append(p)
        elif p.name in roots:
            if p.is_symlink():raise ManagerError('Source symlinks are forbidden')
            for directory,dirs,files in os.walk(p,followlinks=False):
                for d in dirs:
                    if (pathlib.Path(directory)/d).is_symlink():raise ManagerError('Source symlinks are forbidden')
                dirs[:]=[d for d in dirs if d not in EXCLUDED and not d.startswith('.') and (suite_profile or p.name!='tests' or pathlib.Path(directory)!=p or d=='browser')]
                selected.extend(pathlib.Path(directory)/f for f in sorted(files) if not f.startswith('.env') and not f.startswith('.'))
    manifest={};total=0
    for p in selected:
        rel=str(p.relative_to(source));safe_path(rel)
        if p.is_symlink() or not p.is_file():raise ManagerError('Only regular source files supported')
        total+=p.stat().st_size
        if total>150*1024**2:raise ManagerError('Web source exceeds 150 MiB bound')
        manifest[rel]={'bytes':p.stat().st_size,'sha256':sha(p)}
    if any(t not in manifest for t in tests):raise ManagerError('Selected test missing from snapshot')
    if suite_profile=='surfaces':
        # Optional retained browser fixtures only; never import whole hidden stores.
        extras=[source/'output/dag/live-state.json',source/'desktop/resources.mjs']
        tree=source/'.surfaces/dag-implementation/artifacts'
        if tree.is_dir():extras.extend(tree.glob('*/index.html'))
        for p in extras:
            if not p.exists():continue
            if p.is_symlink() or p.resolve()!=p or not p.is_file():raise ManagerError('Fixture must be a canonical regular file')
            total+=p.stat().st_size
            if total>150*1024**2:raise ManagerError('Web source exceeds 150 MiB bound')
            manifest[str(p.relative_to(source))]={'bytes':p.stat().st_size,'sha256':sha(p)}
    lock=read(source/'package-lock.json');version=None
    if not suite_profile or profile['runtime']=='playwright':
        version=lock['packages']['node_modules/playwright']['version']
        names=('playwright-core',) if suite_profile else ('@playwright/test','playwright-core')
        if any(lock['packages'][f'node_modules/{name}']['version']!=version for name in names):raise ManagerError('Inconsistent locked Playwright version')
    node=read(m.root/'cache/node-linux.json');archive=m.root/'cache'/node['filename']
    if sha(archive)!=node['sha256']:raise ManagerError('Pinned Linux Node archive changed')
    spec={'schema_version':1,'source_dir':str(source),'tests':tests,'expected_tests':expected_tests,'playwright_version':version,'node':node,'source_files':manifest}
    if suite_profile:spec.update(runner='node-suites',suite_profile=suite_profile,suite_policy={k:v for k,v in profile.items() if k not in {'roots','root_files'}})
    git_fixture=None
    if suite_profile=='surfaces' and 'scripts/check-retained-session.mjs' in tests:
        from .suites import retained_git
        git_fixture,spec['git_fixture']=retained_git(source)
    staging=m.root/'work';staging.mkdir(exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='playwright-',suffix='.tar',dir=staging);os.close(fd);tmp=pathlib.Path(tmp)
    try:
        with tarfile.open(tmp,'w') as t:
            for rel in manifest:
                p=source/rel
                if sha(p)!=manifest[rel]['sha256']:raise ManagerError('Source changed during snapshot')
                t.add(p,arcname='source/'+rel,recursive=False)
            if git_fixture is not None:
                entry=tarfile.TarInfo('git-fixture.pack');entry.size=len(git_fixture);t.addfile(entry,io.BytesIO(git_fixture))
            t.add(archive,arcname='node.tar.xz',recursive=False)
            data=json.dumps(spec).encode();entry=tarfile.TarInfo('workload.json');entry.size=len(data);t.addfile(entry,io.BytesIO(data))
        if tmp.stat().st_size>200*1024**2:raise ManagerError('Payload exceeds 200 MiB')
        return tmp,spec
    except BaseException:tmp.unlink(missing_ok=True);raise

def start(m,source_dir,tests,expected_tests,label='playwright',owner_pid=None):
    payload,spec=snapshot(m,source_dir,tests,expected_tests)
    try:return m.start(label,owner_pid=owner_pid,workload=(payload,spec))
    finally:payload.unlink(missing_ok=True)

def run(m,x):
    payload=m.runtime/x['job_id']/'input.tar';remote='/tmp/lvm-playwright-input.tar'
    code="import sys,shutil; f=open('/tmp/lvm-playwright-input.tar','xb'); shutil.copyfileobj(sys.stdin.buffer,f); f.close()"
    m.command(x,['shell','guest','--','python3','-c',code],input_path=payload,timeout=120)
    x['workload_started']=True;atomic(m.path(x['job_id']),x)
    m.command(x,['shell','guest','--','python3','/opt/lvm-playwright.py','run',remote,x['payload_sha256']],timeout=2100,allow_fail=True)

def extract(bundle,target,limit):
    if target.resolve()!=target or target.is_symlink():raise ManagerError('Evidence target must be canonical')
    target.mkdir(exist_ok=True)
    with tarfile.open(bundle,'r:gz') as t:
        members=t.getmembers();seen=set();total=0
        for e in members:
            p=safe_path(e.name)
            if not e.isfile() or e.name in seen or (e.name!='manifest.json' and p.parts[0] not in ALLOWED_EVIDENCE):raise ManagerError('Unsafe evidence archive entry')
            seen.add(e.name);total+=e.size
            if total>limit:raise ManagerError('Evidence exceeds allowance')
        if 'manifest.json' not in seen:raise ManagerError('Missing evidence manifest')
        manifest=json.load(t.extractfile('manifest.json'))['files']
        if set(manifest)!=seen-{'manifest.json'}:raise ManagerError('Evidence manifest entries mismatch')
        for e in members:
            dest=target/e.name;dest.parent.mkdir(parents=True,exist_ok=True)
            if dest.is_symlink() or any(p.is_symlink() for p in dest.parents if p!=target.parent):raise ManagerError('Symlink in evidence destination')
            part=dest.with_name(dest.name+'.part')
            with t.extractfile(e) as src,part.open('wb') as dst:shutil.copyfileobj(src,dst);dst.flush();os.fsync(dst.fileno())
            if e.name!='manifest.json' and (part.stat().st_size!=manifest[e.name]['bytes'] or sha(part)!=manifest[e.name]['sha256']):raise ManagerError('Evidence file hash mismatch')
            os.replace(part,dest)
    return {**manifest,'manifest.json':{'bytes':(target/'manifest.json').stat().st_size,'sha256':sha(target/'manifest.json')}}

def export(m,x):
    root=m.evidence/x['job_id'];meta=root/'playwright-export.meta-part'
    if not x.get('workload_started'):return
    if x.get('bundle_sha256') and x.get('test_result') and 'playwright/manifest.json' in x['exports']:
        # Sealed bytes already reside on the host. Recovery after stop must not
        # require restarting a guest merely to retrieve the same evidence.
        if not all((root/name).is_file() and (root/name).stat().st_size==meta['bytes'] and sha(root/name)==meta['sha256'] for name,meta in x['exports'].items()):
            raise ManagerError('Previously sealed host evidence changed; refusing cleanup')
        return
    # Stop only the fixed workload runner in this owned guest before sealing.
    stop="""import pathlib,os,signal,time
matched=[]
for p in pathlib.Path('/proc').iterdir():
    try:
        if p.name.isdigit() and b'/opt/lvm-playwright.py'+bytes([0])+b'run'+bytes([0]) in (p/'cmdline').read_bytes():
            os.kill(int(p.name),signal.SIGTERM);matched.append(p)
    except (FileNotFoundError,ProcessLookupError,PermissionError):pass
if matched:time.sleep(6)
if any(p.exists() for p in matched):raise RuntimeError('Workload runner did not stop')
"""
    m.command(x,['shell','guest','--','python3','-c',stop],watch=False,timeout=20)
    # Fixed script in this owned guest; sealing is bounded and exports no workspace/cache.
    m.command(x,['shell','guest','--','python3','/opt/lvm-playwright.py','seal'],watch=False,output=meta,timeout=60)
    receipt=read(meta);meta.unlink()
    if receipt['path']!='/tmp/lvm-playwright/evidence.tar.gz' or receipt['bytes']>m.settings()['evidence_mib']*1024**2:raise ManagerError('Invalid evidence bundle receipt')
    bundle=root/'playwright-evidence.tar.gz.part'
    m.command(x,['shell','guest','--','cat',receipt['path']],watch=False,output=bundle,timeout=90)
    if bundle.stat().st_size!=receipt['bytes'] or sha(bundle)!=receipt['sha256']:raise ManagerError('Evidence bundle readback mismatch')
    files=extract(bundle,root/'playwright',m.settings()['evidence_mib']*1024**2)
    x['exports'].update({'playwright/'+k:v for k,v in files.items()})
    x['bundle_sha256']=receipt['sha256'];bundle.unlink()
    result=read(root/'playwright/result.json');x['test_result']=result
    if not result.get('passed'):x['error']='Playwright run failed; see retained result.json and run.log'
    atomic(m.path(x['job_id']),x)
