"""Explicit trusted project browser entrypoints; no arbitrary command RPC."""
from .manager import ManagerError,lock
PROFILES={
 'surfaces':{
  'package':'surfaces',
  'roots':{'src','server','scripts','tests','public','examples'},
  'tests':[
   'scripts/check-workspace.mjs','tests/guidance-reuse-browser.test.ts',
   'scripts/check-owner.mjs','scripts/check-onboarding.mjs','scripts/check-project-onboarding.mjs',
   'scripts/check-retained-session.mjs','scripts/check-theme.mjs',
   'scripts/check-history.mjs','scripts/check-workspace-refinement.mjs'],
  'root_files':{'vite.config.ts'},'runtime':'playwright',
 },
 'judgmentkit':{
  'package':'judgmentkit',
  'roots':{'src','site','scripts','tests','contracts','docs','specs','examples','evals','brand','experiments'},
  'tests':['tests/site-header-browser.test.mjs','tests/site-first-use-browser.test.mjs','tests/patterns-browser.test.mjs',
           'tests/components/component-browser.test.mjs','tests/visual-composition-browser-runtime.test.mjs','tests/chart-review-browser.test.mjs'],
  'root_files':{'README.md','DESIGN.md','VISION.md','ONTOLOGY.md'},'runtime':'configured-chromium',
 }
}
def policy(profile,tests=None):
 if profile not in PROFILES:raise ManagerError('Unknown browser suite profile')
 p=PROFILES[profile];tests=p['tests'] if tests is None else tests
 if not tests or len(tests)!=len(set(tests)) or any(t not in p['tests'] for t in tests):raise ManagerError('Only explicit profile browser entrypoints are permitted')
 return p,list(tests)
def start(m,source_dir,profile,tests=None,label='browser-suites',owner_pid=None):
 from .playwright import snapshot
 _,tests=policy(profile,tests)
 with lock(m.state/'snapshot.lock'):
  payload,spec=snapshot(m,source_dir,tests,len(tests),suite_profile=profile)
  try:return m.start(label,owner_pid=owner_pid,workload=(payload,spec))
  finally:payload.unlink(missing_ok=True)

def retained_git(source,commit='9c195f7',path='examples/local-product/app.tsx'):
 """Pack only the exact historical lookup's objects plus the source base commit."""
 import subprocess,hashlib
 parts=path.split('/')
 revisions=[commit+'^{commit}',commit+'^{tree}']+[commit+':'+('/'.join(parts[:i])) for i in range(1,len(parts)+1)]+['HEAD']
 result=subprocess.run(['git','rev-parse',*revisions],cwd=source,capture_output=True,text=True)
 if result.returncode:raise ManagerError('Required retained Git fixture unavailable: '+result.stderr.strip())
 ids=list(dict.fromkeys(result.stdout.splitlines()))
 if any(len(x)!=40 or any(c not in '0123456789abcdef' for c in x) for x in ids):raise ManagerError('Invalid Git object identity')
 packed=subprocess.run(['git','pack-objects','--stdout'],cwd=source,input=('\n'.join(ids)+'\n').encode(),capture_output=True)
 if packed.returncode or len(packed.stdout)>2*1024**2:raise ManagerError('Cannot pack bounded retained Git fixture')
 values=result.stdout.splitlines();blob=subprocess.check_output(['git','cat-file','blob',values[-2]],cwd=source)
 return packed.stdout,{'historical_commit':values[0],'source_base_commit':values[-1],'path':path,'blob_object':values[-2],
  'historical_blob_sha256':hashlib.sha256(blob).hexdigest(),'sha256':hashlib.sha256(packed.stdout).hexdigest(),'bytes':len(packed.stdout),'object_ids':ids}
