"""Kill only a new job's supervisor; verify the installed launchd sweeper recovers it."""
import json,os,pathlib,signal,subprocess,sys,time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent.parent))
from lvm.manager import Manager,identity,sha
root=pathlib.Path(__file__).resolve().parent.parent;m=Manager().init();out=root/'verification'/('lifecycle-'+str(time.time_ns()));out.mkdir(parents=True)
before={p.stem for p in m.jobs.glob('*.json')};label='launchd-recovery-'+str(time.time_ns())
with (out/'reaper-agent.jsonl').open('w') as f:
    agent=subprocess.Popen([str(root/'bin/lvm'),'run','--label',label,'--hold-after-capture'],cwd=root,stdout=f,stderr=subprocess.STDOUT,start_new_session=True)
    job=None;injected=False
    try:
        end=time.monotonic()+900
        while time.monotonic()<end:
            new={p.stem for p in m.jobs.glob('*.json') if p.stem not in before and m.status(p.stem).get('label')==label}
            if new:
                assert len(new)==1;job=next(iter(new));x=m.status(job)
                if x['state']=='ready' and len(x['exports'])==4:break
                if x['state'] in ('failed','cleanup_blocked'):raise RuntimeError(str(x))
            if agent.poll() is not None:raise RuntimeError('Agent exited before injection')
            time.sleep(.25)
        assert job and len(x['exports'])==4
        assert identity(x['supervisor_pid'])==x['supervisor_start']
        old_supervisor=x['supervisor_pid'];killed_at=time.time()
        os.kill(old_supervisor,signal.SIGKILL);injected=True
        print(json.dumps({'job_id':job,'event':'owned_supervisor_sigkill','supervisor_pid':old_supervisor,'agent_pid':agent.pid}),flush=True)
        end=time.monotonic()+150
        while time.monotonic()<end:
            x=m.status(job)
            if x['state'] in ('complete','failed','cleanup_blocked'):break
            time.sleep(.5)
        assert x['state']=='complete' and x['finish_reason']=='supervisor_recovery',x
        assert x['supervisor_pid']!=old_supervisor and x['recovery_attempts']==1
        assert x['runtime_deleted'] and x['vm_deleted'] and x['evidence_complete'] and x['evidence_verified']
        assert not pathlib.Path(x['runtime_path']).exists()
        for name,meta in x['exports'].items():assert sha(pathlib.Path(x['evidence_path'])/name)==meta['sha256']
        agent.wait(timeout=20);assert agent.returncode==0
        report={'job_id':job,'passed':True,'finish_reason':x['finish_reason'],'old_supervisor_pid':old_supervisor,
                'recovery_supervisor_pid':x['supervisor_pid'],'cleanup_after_supervisor_crash_seconds':x['finished_at']-killed_at,
                'evidence_bytes':x['retained_evidence_bytes'],'runtime_deleted':True,'hash_readback_passed':True}
        (out/'supervisor-crash-run.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report),flush=True)
    finally:
        if agent.poll() is None:agent.terminate();agent.wait(timeout=10)
        if job and not injected:m.finish(job)
