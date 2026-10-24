"""Real Linux acceptance: overlap, stranded cleanup, admission, and crash isolation.

Creates only new manager-owned jobs. --reconcile-job explicitly authorizes the
named pre-existing blocked Surfaces job; it never selects another job for repair.
"""
import argparse,json,os,pathlib,subprocess,sys,time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent.parent))
from lvm.manager import Manager,sha,identity
ROOT=pathlib.Path(__file__).resolve().parent.parent
parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--reconcile-job');args=parser.parse_args()
m=Manager().init();out=ROOT/'verification'/('concurrency-runtime-'+str(time.time_ns()));out.mkdir()
started=time.time();before={p.stem for p in m.jobs.glob('*.json')};children=[];owned=[];holder=None
report={'started_at':started,'report_directory':str(out),'events':[],'jobs':[]}

def event(name,**values):
    row={'at':time.time(),'event':name,**values};report['events'].append(row)
    (out/'progress.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(row),flush=True)

def cli(arguments,timeout=180,check=True):
    result=subprocess.run([str(ROOT/'bin/lvm'),*arguments],cwd=ROOT,capture_output=True,text=True,timeout=timeout)
    if check and result.returncode:raise RuntimeError(arguments[0]+': '+result.stderr)
    return result

def wait(job,predicate,timeout=900):
    until=time.monotonic()+timeout;last=None
    while time.monotonic()<until:
        x=m.status(job)
        if predicate(x):return x
        if x['state']!=last:event('phase',job_id=job,state=x['state']);last=x['state']
        if x['state']=='failed':raise RuntimeError(str(x))
        time.sleep(.3)
    raise RuntimeError('Timed out: '+job)

def start_hold(label):
    label=label+'-'+str(time.time_ns());log=out/(label+'.log');stream=log.open('w')
    process=subprocess.Popen([str(ROOT/'bin/lvm'),'run','--url','pilot','--label',label,'--hold-after-capture'],cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
    children.append((process,stream));until=time.monotonic()+60
    while time.monotonic()<until:
        for p in m.jobs.glob('*.json'):
            if p.stem not in before and m.status(p.stem).get('label')==label:
                owned.append(p.stem);return process,p.stem
        if process.poll() is not None:raise RuntimeError(log.read_text())
        time.sleep(.1)
    raise RuntimeError('No job admitted')

def verify(job):
    x=m.status(job)
    assert x['state']=='complete',x
    for key in ('vm_deleted','runtime_deleted','evidence_complete','evidence_verified'):assert x.get(key),x
    assert not pathlib.Path(x['runtime_path']).exists()
    for name,meta in x['exports'].items():
        path=pathlib.Path(x['evidence_path'])/name;assert path.stat().st_size==meta['bytes'] and sha(path)==meta['sha256']
    results=[json.loads(p.read_text()) for p in pathlib.Path(x['evidence_path']).glob('*-result.json')]
    assert results and all(x['passed'] and not x['sandbox_disabled'] for x in results)
    return {k:x.get(k) for k in ('job_id','label','finish_reason','vm_deleted','runtime_deleted','evidence_complete','evidence_verified','evidence_path','reservation_released_at')}

try:
    a,ja=start_hold('concurrency-a');b,jb=start_hold('concurrency-b')
    xa=wait(ja,lambda x:x['state']=='ready' and len(x['exports'])==4)
    xb=wait(jb,lambda x:x['state']=='ready' and len(x['exports'])==4)
    assert a.poll() is None and b.poll() is None and xa['runtime_path']!=xb['runtime_path']
    event('independent_guests_overlap',jobs=[ja,jb],runtime_paths=[xa['runtime_path'],xb['runtime_path']],supervisors=[xa['supervisor_pid'],xb['supervisor_pid']])
    capacity=m.inventory()['capacity'];report['overlap_capacity']=capacity
    if capacity['held_job_count']==m.settings()['max_concurrent']:
        ids={p.stem for p in m.jobs.glob('*.json')};denied=cli(['start','--label','capacity-denial'],check=False)
        assert denied.returncode and 'capacity exhausted' in denied.stderr.lower()
        assert {p.stem for p in m.jobs.glob('*.json')}==ids
        event('capacity_exhaustion_denied_without_allocation',reason=denied.stderr.strip())
    hold_path=m.home(xa)/'acceptance-open-handle.txt';holder=hold_path.open('w');holder.write('Owned acceptance handle intentionally blocks cleanup.\n');holder.flush()
    cli(['finish',ja]);blocked=wait(ja,lambda x:x['state']=='cleanup_blocked',180)
    assert blocked.get('evidence_verified') and not blocked.get('runtime_deleted')
    assert b.poll() is None and m.status(jb)['state']=='ready'
    event('cleanup_failure_isolated',job_id=ja,healthy_job=jb,cleanup_error=blocked.get('cleanup_error'))
    r=json.loads(cli(['capture',jb,'--url','pilot']).stdout);request=r['request_id']
    wait(jb,lambda x:x['state']=='ready' and request+'-result.json' in x['exports'])
    event('healthy_browser_still_executes',job_id=jb,request_id=request)
    if args.reconcile_job:
        from lvm import surfaces_connected as connected
        old=m.status(args.reconcile_job)
        assert old['state']=='cleanup_blocked' and old.get('workload',{}).get('runner')=='surfaces-connected-observer'
        assert identity(old.get('supervisor_pid',0))!=old.get('supervisor_start')
        m.home(old);connected.verify_binding(old);connected.verify_external(m,old);connected.checked_state(m,old,empty=True)
        original={name:sha(m.evidence/old['job_id']/name) for name in old['exports']}
        (out/'reconciled-job-before.json').write_text(json.dumps(old,indent=2)+'\n')
        cli(['supervise',old['job_id']],timeout=180)
        result=m.status(old['job_id']);assert result['state']=='failed' and result['evidence_complete'] is False
        assert result['vm_deleted'] and result['runtime_deleted'] and result['evidence_verified']
        assert all(sha(m.evidence/old['job_id']/name)==digest for name,digest in original.items())
        (out/'reconciled-job-after.json').write_text(json.dumps(result,indent=2)+'\n')
        event('old_failed_job_reconciled_without_fixture_rerun',job_id=old['job_id'],evidence_complete=False,vm_deleted=True,runtime_deleted=True)
    for name,command in [('agent-crash',['scripts/verify_pilot.py','crash']),('supervisor-crash',['scripts/verify_reaper.py'])]:
        phase_before={p.stem for p in m.jobs.glob('*.json')}
        with (out/(name+'.log')).open('w') as log:
            subprocess.run([sys.executable,*command],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=1100)
        added={p.stem for p in m.jobs.glob('*.json')}-phase_before
        assert len(added)==1;job=added.pop();owned.append(job);result=verify(job);report['jobs'].append(result)
        assert b.poll() is None and m.status(jb)['state']=='ready'
        event('new_admission_and_crash_isolation',kind=name,job_id=job,healthy_job=jb,blocked_job=ja)
    holder.close();holder=None
    cli(['supervise',ja]);wait(ja,lambda x:x['state']=='complete',180)
    cli(['finish',jb]);wait(jb,lambda x:x['state']=='complete',180)
    report['jobs'] += [verify(ja),verify(jb)]
    report['final_capacity']=m.inventory()['capacity']
    assert not [x for x in report['final_capacity']['held_jobs'] if x['job_id'] in owned]
    report['passed']=True;report['finished_at']=time.time()
    (out/'result.json').write_text(json.dumps(report,indent=2)+'\n')
    event('all_owned_acceptance_jobs_deleted',jobs=owned)
    print(json.dumps({'passed':True,'report':str(out/'result.json'),'jobs':report['jobs']}),flush=True)
finally:
    if holder is not None:holder.close()
    for job in owned:
        x=m.status(job)
        if x['state'] not in ('complete','failed'):
            m.finish(job)
            if x['state']=='cleanup_blocked':m.supervise(job)
    for process,stream in children:
        try:process.wait(timeout=30)
        except subprocess.TimeoutExpired:pass
        stream.close()
