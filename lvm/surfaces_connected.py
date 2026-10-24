"""Fixed manager adapter for Surfaces connected-observer technical fixtures.

This profile admits one reviewed task root and two declared fixtures.  It does
not expose a general command, source path, VM name, or guest RPC surface.
"""
from __future__ import annotations
import hashlib,json,os,pathlib,shutil,signal,subprocess,tarfile,tempfile,time
from .manager import ManagerError,Cancelled,atomic,allocated,identity,read,sha,data_root

MANAGER_EVIDENCE_LIMIT=2*1024**2
TASK_EVIDENCE_LIMIT=64*1024**2
RUNTIME_BACKING_LIMIT=20*1024**3
HOST_FREE_FLOOR=10*1024**3
MANAGER_SOURCE_NAMES=('lvm/surfaces_connected.py','lvm/manager.py','lvm/cli.py','lvm/mcp_server.py','lvm/resources.py')


CONFIG_PATH=data_root()/'adapters/surfaces-connected.json'
PATH_KEYS={'TASK_ROOT','PROJECT','STATE_ROOT','LAUNCH','EXPECTATION','BINDING','PREPARE','RUNNER','EVIDENCE_BUDGET','NODE','TSX_ROOT'}

def load_binding(path):
 if path.is_symlink() or path.resolve()!=path or not path.is_file():
  raise ManagerError('Surfaces connected-observer is not configured with a canonical local binding')
 value=read(path)
 if value.get('schema_version')!=1 or set(value.get('paths',{}))!=PATH_KEYS or set(value.get('fixed',{}))!={'launch','expectation','node'}:
  raise ManagerError('Invalid Surfaces adapter binding schema')
 paths={key:pathlib.Path(raw) for key,raw in value['paths'].items()}
 for p in paths.values():
  if not p.is_absolute() or p.resolve()!=p or any(s in str(p) for s in ('/Documents/','/Desktop/','Mobile Documents')):
   raise ManagerError('Surfaces binding paths must be canonical and outside iCloud')
 fixed={key:(pathlib.Path(row['path']),row['bytes'],row['sha256']) for key,row in value['fixed'].items()}
 for key,name in (('launch','LAUNCH'),('expectation','EXPECTATION'),('node','NODE')):
  if fixed[key][0]!=paths[name]:raise ManagerError('Surfaces binding pin path differs')
 return {**paths,'FIXED':fixed,'TSX_TREE_DIGEST':value['tsx_tree_digest']}

# Importing or listing tools works on another Mac; dispatch requires its local reviewed binding.
if CONFIG_PATH.exists() or CONFIG_PATH.is_symlink():
 globals().update(load_binding(CONFIG_PATH))
else:
 globals().update({key:pathlib.Path('/__unconfigured_surfaces_adapter__')/key for key in PATH_KEYS})
 FIXED={};TSX_TREE_DIGEST=''

def binding_identity():
 configured=load_binding(CONFIG_PATH)
 if any(globals().get(key)!=value for key,value in configured.items()):
  raise ManagerError('Surfaces adapter binding changed since process start; reload before dispatch')
 return {'path':str(CONFIG_PATH),'bytes':CONFIG_PATH.stat().st_size,'sha256':sha(CONFIG_PATH)}

def verify_binding(x):
 if x['workload'].get('adapter_binding')!=binding_identity():
  raise ManagerError('Reviewed Surfaces adapter binding changed after admission')

def canonical_rows(root:pathlib.Path):
 rows=[]
 for p in sorted(root.rglob('*')):
  if p.is_symlink():raise ManagerError('TSX runtime contains a symlink')
  if p.is_file():rows.append({'path':str(p.relative_to(root)),'bytes':p.stat().st_size,'sha256':sha(p)})
 return rows

def tree_digest(root:pathlib.Path):
 raw=json.dumps(canonical_rows(root),sort_keys=True,separators=(',',':')).encode()
 return hashlib.sha256(raw).hexdigest()

def exact_file(path:pathlib.Path,size:int,digest:str):
 if path.is_symlink() or not path.is_file() or path.resolve()!=path or path.stat().st_size!=size or sha(path)!=digest:
  raise ManagerError('Reviewed Surfaces input changed: '+str(path))

def validate_contract(now_ms=None):
 binding_identity()
 for path,size,digest in FIXED.values():exact_file(path,size,digest)
 if tree_digest(TSX_ROOT)!=TSX_TREE_DIGEST:raise ManagerError('Pinned TSX host loader changed')
 raw=LAUNCH.read_bytes(); launch=json.loads(raw); expected=json.loads(EXPECTATION.read_text())
 if expected.get('rawBytes')!=len(raw) or expected.get('rawSha256')!=hashlib.sha256(raw).hexdigest() or expected.get('canonicalDigest')!=launch.get('digest') or expected.get('launch')!=launch:
  raise ManagerError('Reviewed launch and independent expectation differ')
 now_ms=int(time.time()*1000) if now_ms is None else now_ms
 if not (int(launch['preparationDeadline'])>now_ms and int(launch['workDeadline'])>now_ms and int(launch['absoluteDeadline'])>now_ms):
  raise ManagerError('Reviewed Surfaces launch deadline expired')
 if launch.get('taskRoot')!=str(TASK_ROOT) or launch.get('stateRoot')!=str(STATE_ROOT) or launch.get('reviewRequiredBeforeDispatch') is not True or launch.get('readyForProduction') is not False:
  raise ManagerError('Reviewed Surfaces task or readiness boundary changed')
 limits=launch.get('limits',{})
 if limits.get('evidenceBytes')!=TASK_EVIDENCE_LIMIT or limits.get('perVmAggregateAllocatedBytes')!=RUNTIME_BACKING_LIMIT or limits.get('baseCount')!=1 or limits.get('executionCount')!=1:
  raise ManagerError('Reviewed Surfaces budget or VM population changed')
 source={row['path']:row for row in launch.get('sourcePins',[])}
 for path in (PREPARE,RUNNER,EVIDENCE_BUDGET):
  pin=source.get(str(path))
  if not pin:raise ManagerError('Required Surfaces source pin missing: '+str(path))
  exact_file(path,pin['bytes'],pin['sha256'])
 if STATE_ROOT.exists() or STATE_ROOT.is_symlink():raise ManagerError('Reviewed Surfaces state root is not fresh')
 return launch

def manager_source_rows(root:pathlib.Path):
 rows=[]
 for name in MANAGER_SOURCE_NAMES:
  p=root/name
  if p.is_symlink() or not p.is_file() or p.resolve()!=p:
   raise ManagerError('Manager source identity is unsafe: '+str(p))
  rows.append({'path':name,'bytes':p.stat().st_size,'sha256':sha(p)})
 return rows

def validate_manager_sources(m,x):
 if x['workload'].get('manager_sources')!=manager_source_rows(m.code_root):
  raise ManagerError('Manager source changed after workload admission')

def snapshot(m):
 launch=validate_contract()
 if shutil.disk_usage(PROJECT).free < RUNTIME_BACKING_LIMIT+HOST_FREE_FLOOR:
  raise ManagerError('Surfaces fixture runtime would breach host free-space floor')
 spec={'schema_version':1,'runner':'surfaces-connected-observer','task_root':str(TASK_ROOT),'state_root':str(STATE_ROOT),
       'fixtures':['npm-vite-dist','npm-vite-nested-output'],'launch':{'path':str(LAUNCH),'bytes':LAUNCH.stat().st_size,'sha256':sha(LAUNCH),'digest':launch['digest']},
       'expectation':{'path':str(EXPECTATION),'bytes':EXPECTATION.stat().st_size,'sha256':sha(EXPECTATION)},
       'binding':{'path':str(BINDING),'bytes':BINDING.stat().st_size,'sha256':sha(BINDING)},
       'node':{'path':str(NODE),'bytes':NODE.stat().st_size,'sha256':sha(NODE)},'tsx_tree_digest':TSX_TREE_DIGEST,
       'manager_sources':manager_source_rows(m.code_root),'adapter_binding':binding_identity(),
       'evidence_limit_bytes':TASK_EVIDENCE_LIMIT,'manager_evidence_limit_bytes':MANAGER_EVIDENCE_LIMIT,
       'runtime_backing_limit_bytes':RUNTIME_BACKING_LIMIT,'work_deadline':launch['workDeadline']/1000,'absolute_deadline':launch['absoluteDeadline']/1000}
 staging=m.root/'work';staging.mkdir(exist_ok=True)
 fd,tmp=tempfile.mkstemp(prefix='surfaces-connected-',suffix='.tar',dir=staging);os.close(fd);tmp=pathlib.Path(tmp)
 data=json.dumps(spec,sort_keys=True,separators=(',',':')).encode()
 with tarfile.open(tmp,'w') as t:
  entry=tarfile.TarInfo('workload.json');entry.size=len(data);entry.mode=0o400;t.addfile(entry,io_bytes(data))
 return tmp,spec

def io_bytes(data):
 import io
 return io.BytesIO(data)

def start(m,label='surfaces-connected-observer',owner_pid=None):
 payload,spec=snapshot(m)
 try:return m.start(label,owner_pid=owner_pid,workload=(payload,spec))
 finally:payload.unlink(missing_ok=True)

def evidence_size(root:pathlib.Path):
 total=0
 if not root.exists():return 0
 for directory,dirs,files in os.walk(root,followlinks=False):
  dirs[:]=[d for d in dirs if not (pathlib.Path(directory)/d).is_symlink()]
  for name in files:
   p=pathlib.Path(directory)/name
   if p.is_symlink():raise ManagerError('Manager evidence contains a symlink')
   total+=p.stat().st_size
 return total

def note_state_identity(m,x):
 if not STATE_ROOT.exists():return
 if STATE_ROOT.is_symlink() or STATE_ROOT.resolve()!=STATE_ROOT or not STATE_ROOT.is_dir():raise ManagerError('Surfaces state root identity is unsafe')
 s=STATE_ROOT.stat()
 current=(s.st_dev,s.st_ino)
 saved=x.get('surfaces_state_identity')
 if saved and current!=(saved['device'],saved['inode']):raise ManagerError('Surfaces state root was replaced')
 if not saved:
  x['surfaces_state_identity']={'device':s.st_dev,'inode':s.st_ino};atomic(m.path(x['job_id']),x)

def host_command(m,x,name,script,timeout):
 if script not in (PREPARE,RUNNER):raise ManagerError('Unrecognized Surfaces workload entrypoint')
 log=m.evidence/x['job_id']/(name+'.log')
 env={'PATH':'/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin','HOME':str(PROJECT),'TMPDIR':str(m.runtime/x['job_id']/'tmp'),
      'SURFACES_CONNECTED_MANAGER_JOB':x['job_id']}
 pathlib.Path(env['TMPDIR']).mkdir(exist_ok=True)
 started=time.monotonic()
 with log.open('xb',buffering=0) as out:
  p=subprocess.Popen([str(NODE),'--import','tsx',str(script)],cwd=PROJECT,env=env,stdin=subprocess.DEVNULL,stdout=out,stderr=out,start_new_session=True)
  x['active_workload']={'pid':p.pid,'start':identity(p.pid),'name':name};atomic(m.path(x['job_id']),x)
  try:
   while p.poll() is None:
    reason=m.abort_reason(x)
    if reason:raise Cancelled(reason)
    if time.monotonic()-started>timeout:raise ManagerError('Surfaces '+name+' deadline reached')
    note_state_identity(m,x)
    if evidence_size(m.evidence/x['job_id'])>MANAGER_EVIDENCE_LIMIT:raise ManagerError('Manager evidence allowance exceeded')
    m.check_job_budget(x)
    if shutil.disk_usage(PROJECT).free<HOST_FREE_FLOOR:raise ManagerError('Host free-space floor breached')
    time.sleep(.25)
  finally:
   if p.poll() is None:
    try:os.killpg(p.pid,signal.SIGTERM);p.wait(timeout=8)
    except (ProcessLookupError,subprocess.TimeoutExpired):
     if p.poll() is None:os.killpg(p.pid,signal.SIGKILL);p.wait()
   x.pop('active_workload',None);atomic(m.path(x['job_id']),x)
 if p.returncode:raise ManagerError('Surfaces '+name+' failed ('+str(p.returncode)+'); see '+log.name)

def register(m,x,path,key=None):
 key=key or path.name
 if not path.is_file() or path.is_symlink():raise ManagerError('Missing manager evidence: '+str(path))
 x['exports'][key]={'bytes':path.stat().st_size,'sha256':sha(path)}
 if evidence_size(m.evidence/x['job_id'])>MANAGER_EVIDENCE_LIMIT:
  raise ManagerError('Manager evidence allowance exceeded')
 atomic(m.path(x['job_id']),x)

def external_manifest():
 selected=[LAUNCH,EXPECTATION,BINDING,
           TASK_ROOT/'manager-resume-authorization-v1.json',
           TASK_ROOT/'resume-manager-compatibility-blocker-v1.json',
           TASK_ROOT/'binding-supersession-v1.json',
           TASK_ROOT/'manager-adapter-installation-v1.json',
           TASK_ROOT/'checks/manager-dispatch-preflight-v1.json',
           TASK_ROOT/'checks/manager-resume-focused-tests-v1.log',
           TASK_ROOT/'checks/manager-resume-typecheck-v1.log',
           TASK_ROOT/'checks/manager-adapter-units-v2.log',
           TASK_ROOT/'checks/manager-mcp-protocol-v1.json']
 execution=TASK_ROOT/'execution'
 if execution.exists():selected.extend(p for p in execution.rglob('*') if p.is_file() or p.is_symlink())
 rows=[];total=0
 for p in sorted(set(selected)):
  if not p.exists():continue
  if p.is_symlink() or not p.is_file() or p.resolve()!=p:raise ManagerError('External evidence path is unsafe: '+str(p))
  size=p.stat().st_size;total+=size
  if len(rows)>=30000 or total>TASK_EVIDENCE_LIMIT:raise ManagerError('External evidence inventory exceeds task bound')
  rows.append({'path':str(p),'bytes':size,'sha256':sha(p)})
 return rows

def write_manager_json(path,value):
 encoded=json.dumps(value,indent=2)+'\n'
 if path.exists():
  if path.is_symlink() or path.read_text()!=encoded:raise ManagerError('Manager evidence changed across export recovery: '+str(path))
  return
 tmp=path.with_name(path.name+'.part');tmp.write_text(encoded);os.chmod(tmp,0o600);os.replace(tmp,path)

def run(m,x):
 verify_binding(x)
 validate_manager_sources(m,x);validate_contract();x['surfaces_state_intent']={'path':str(STATE_ROOT),'expected_absent_at_admission':True};atomic(m.path(x['job_id']),x)
 launch=read(LAUNCH);now=time.time()
 host_command(m,x,'prepare',PREPARE,min(1800,max(1,launch['preparationDeadline']/1000-now)))
 note_state_identity(m,x)
 host_command(m,x,'fixtures',RUNNER,min(19800,max(1,launch['workDeadline']/1000-time.time())))
 result=read(TASK_ROOT/'execution/fixture-connected-result.json')
 passed=result.get('state')=='BOTH_DECLARED_CONNECTED_FIXTURES_VERIFIED' and len(result.get('receipts',[]))==2 and result.get('drawDBShareRun') is False and result.get('productHolds')=='OPEN'
 if not passed:raise ManagerError('Surfaces connected fixture result is incomplete')
 x['test_result']={'passed':True,'profile':'surfaces-connected-observer','fixtures':['npm-vite-dist','npm-vite-nested-output'],
                   'fixture_count':2,'product_holds':'OPEN','ready_for_production':False,'finished_at':time.time()}
 export(m,x)

def export(m,x):
 verify_binding(x)
 root=m.evidence/x['job_id']
 for name in ('prepare.log','fixtures.log'):
  p=root/name
  if p.exists():register(m,x,p,name)
 manifest=root/'external-evidence-manifest.json'
 if manifest.exists():
  # Recovery authenticates the original manifest; later task files cannot replace it.
  verify_external(m,x)
 else:
  rows=external_manifest()
  write_manager_json(manifest,{'schema_version':1,'kind':'surfaces-connected-observer-external-evidence/1','files':rows,'verified':True})
  register(m,x,manifest)
 result=root/'result.json'
 if not result.exists():
  terminal=x.get('test_result')
  if terminal is None:
   terminal={'passed':False,'profile':'surfaces-connected-observer','error':x.get('error','Workload did not produce a terminal result'),'finished_at':x.get('finished_at',time.time())}
  write_manager_json(result,terminal)
 register(m,x,result)
 if evidence_size(root)>MANAGER_EVIDENCE_LIMIT:raise ManagerError('Manager evidence allowance exceeded')
 x['external_evidence_verified']=True;atomic(m.path(x['job_id']),x)

def verify_external(m,x):
 root=m.evidence/x['job_id'];path=root/'external-evidence-manifest.json'
 pin=x.get('exports',{}).get(path.name)
 if not pin or path.is_symlink() or not path.is_file() or path.stat().st_size!=pin['bytes'] or sha(path)!=pin['sha256']:
  raise ManagerError('Original external evidence manifest cannot be authenticated')
 manifest=read(path)
 if manifest.get('kind')!='surfaces-connected-observer-external-evidence/1' or manifest.get('verified') is not True or not isinstance(manifest.get('files'),list):
  raise ManagerError('Invalid original external evidence manifest')
 rows=manifest['files']
 if len(rows)>30000 or sum(row['bytes'] for row in rows)>TASK_EVIDENCE_LIMIT:raise ManagerError('External evidence manifest exceeds bound')
 for row in rows:
  p=pathlib.Path(row['path'])
  if not p.is_absolute() or not p.is_relative_to(TASK_ROOT) or p.resolve()!=p or not p.is_file() or p.is_symlink() or p.stat().st_size!=row['bytes'] or sha(p)!=row['sha256']:
   raise ManagerError('External Surfaces evidence changed: '+str(p))


def checked_state(m,x,empty=False,require_no_holders=True):
 intent=x.get('surfaces_state_intent',{});saved=x.get('surfaces_state_identity')
 if intent.get('path')!=str(STATE_ROOT) or intent.get('expected_absent_at_admission') is not True or x.get('workload',{}).get('state_root')!=str(STATE_ROOT):
  raise ManagerError('Missing reviewed fresh-state cleanup intent')
 if STATE_ROOT.is_symlink() or STATE_ROOT.resolve()!=STATE_ROOT or not STATE_ROOT.is_dir():raise ManagerError('Surfaces state identity is unsafe')
 st=STATE_ROOT.stat()
 if not saved or (st.st_dev,st.st_ino)!=(saved['device'],saved['inode']):raise ManagerError('Surfaces state identity changed before deletion')
 if empty and ((st.st_mode & 0o777)!=0o700 or any(STATE_ROOT.iterdir())):raise ManagerError('Pre-pointer state must be an empty private directory')
 if require_no_holders and m.holders(STATE_ROOT):raise ManagerError('Surfaces owned runtime still has open handles')
 return st

def lima(m,x,args,allow_fail=False):
 stage_file=STATE_ROOT/'private/runtime-preparation/current.json'
 if not stage_file.is_file() or stage_file.is_symlink():
  if STATE_ROOT.exists():raise ManagerError('Surfaces state exists without its reviewed runtime pointer')
  return 0,''
 pointer=read(stage_file);stage=pathlib.Path(pointer.get('root',''))
 if stage.parent!=STATE_ROOT/'.r' or stage.name!=pointer.get('id','').replace('-','')[:8] or stage.is_symlink() or not stage.is_dir():
  raise ManagerError('Prepared Surfaces stage identity is invalid')
 exe=stage/'lima/bin/limactl';home=stage/'l'
 if not exe.is_file() or exe.is_symlink() or not home.is_dir() or home.is_symlink():raise ManagerError('Prepared Surfaces runtime tools changed')
 env={'PATH':'/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin','HOME':str(stage),'TMPDIR':str(stage/'tmp'),'LIMA_HOME':str(home),'LIMA_SSH_OVER_VSOCK':'false','LIMA_SHELL':'/bin/bash'}
 (stage/'tmp').mkdir(exist_ok=True)
 p=subprocess.run([str(exe),'--tty=false',*args],cwd=stage,env=env,capture_output=True,text=True,timeout=90)
 log=m.evidence/x['job_id']/'cleanup.log'
 with log.open('a') as f:f.write(json.dumps({'args':args,'returncode':p.returncode,'stdout':p.stdout[-8192:],'stderr':p.stderr[-8192:]})+'\n')
 if p.returncode and not allow_fail:raise ManagerError('Surfaces cleanup command failed: '+args[0])
 return p.returncode,p.stdout

def cleanup(m,x):
 verify_binding(x)
 verify_external(m,x)
 if not (m.runtime/x['job_id']).exists() and not STATE_ROOT.exists() and x.get('deletion_intent'):
  x.update(vm_deleted=True,runtime_deleted=True,remaining_runtime_bytes=0);return
 # Verify both namespaces before deleting either; never adopt an unrecorded root.
 home=m.home(x)
 if m.holders(home):raise ManagerError('Manager workload runtime still has open handles')
 pre_pointer=False
 if STATE_ROOT.exists() or STATE_ROOT.is_symlink():
  # Running guests normally hold their disks open; validate identity before stop.
  checked_state(m,x,require_no_holders=False)
  pointer=STATE_ROOT/'private/runtime-preparation/current.json'
  if not pointer.exists():
   checked_state(m,x,empty=True);pre_pointer=True
 if (STATE_ROOT.exists() or STATE_ROOT.is_symlink()) and not pre_pointer:
  _,out=lima(m,x,['list','--json'],allow_fail=True)
  entries=[json.loads(line) for line in out.splitlines() if line.strip()]
  names={row.get('name'):row.get('status') for row in entries}
  if not set(names)<= {'clean-base','execution'}:raise ManagerError('Unexpected Surfaces VM inside owned state')
  for name in ('execution','clean-base'):
   if name not in names:continue
   if names[name]!='Stopped':lima(m,x,['stop','--force',name],allow_fail=True)
   lima(m,x,['delete',name])
  _,out=lima(m,x,['list','--json'],allow_fail=True)
  if out.strip():raise ManagerError('Surfaces VM deletion did not settle')
  holders=m.holders(STATE_ROOT);x['holders_after_stop']=holders
  if holders:raise ManagerError('Surfaces owned runtime still has open handles')
  checked_state(m,x)
 x['deletion_intent']=True;atomic(m.path(x['job_id']),x)
 if STATE_ROOT.exists():
  checked_state(m,x,empty=pre_pointer)
  if pre_pointer:STATE_ROOT.rmdir()
  else:shutil.rmtree(STATE_ROOT)
 cleanup_log=m.evidence/x['job_id']/'cleanup.log'
 if cleanup_log.exists():register(m,x,cleanup_log,'cleanup.log')
 home=m.home(x);manager_holders=m.holders(home);x['manager_holders_before_delete']=manager_holders
 if manager_holders:raise ManagerError('Manager workload runtime still has open handles')
 shutil.rmtree(home.parent)
 x['vm_deleted']=not STATE_ROOT.exists();x['runtime_deleted']=not STATE_ROOT.exists() and not home.parent.exists();x['remaining_runtime_bytes']=0
 if not x['vm_deleted'] or not x['runtime_deleted']:raise ManagerError('Surfaces managed runtime deletion incomplete')
 x['evidence_verified']=all((m.evidence/x['job_id']/k).is_file() and sha(m.evidence/x['job_id']/k)==v['sha256'] for k,v in x['exports'].items())
 if not x['evidence_verified']:raise ManagerError('Manager evidence verification failed after cleanup')
