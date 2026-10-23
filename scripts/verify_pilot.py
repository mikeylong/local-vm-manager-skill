"""Actual Linux/MCP verification. Kills only its own newly spawned client."""
import hashlib,json,os,pathlib,signal,subprocess,sys,time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent.parent))
from lvm.manager import Manager,identity
ROOT=pathlib.Path(__file__).resolve().parent.parent
m=Manager().init();out=ROOT/'verification'/('lifecycle-'+str(time.time_ns()));out.mkdir(parents=True)
def verify(job):
    x=m.status(job)
    assert x['state']=='complete',x
    assert x['vm_deleted'] and x['runtime_deleted'] and x['evidence_verified'] and x['evidence_complete'],x
    assert not pathlib.Path(x['runtime_path']).exists()
    assert len(x['exports'])==4,x
    for name,meta in x['exports'].items():
        p=pathlib.Path(x['evidence_path'])/name
        assert p.stat().st_size==meta['bytes']
        assert hashlib.sha256(p.read_bytes()).hexdigest()==meta['sha256']
    result=next(pathlib.Path(x['evidence_path']).glob('*-result.json'))
    r=json.loads(result.read_text());assert r['passed'] and r['pilot_saved'] and not r['sandbox_disabled'],r
    screenshot=next(pathlib.Path(x['evidence_path']).glob('*-page.png'))
    assert screenshot.read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
    return {'job_id':job,'reason':x['finish_reason'],'browser':r['browser'],'architecture':r['architecture'],
            'evidence_bytes':x['retained_evidence_bytes'],'peak_manager_allocated_bytes':x['peak_allocated_bytes'],
            'evidence_directory':x['evidence_path'],'vm_deleted':True,'runtime_deleted':True,'hash_readback_passed':True}
if sys.argv[1]=='normal':
    report=verify(sys.argv[2]);(out/'normal-run.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report));sys.exit()
assert sys.argv[1]=='crash'
before={p.stem for p in m.jobs.glob('*.json')};label='mcp-agent-crash-'+str(time.time_ns())
with (out/'crash-agent.jsonl').open('w') as stdout,(out/'crash-agent-stderr.log').open('w') as stderr:
    agent=subprocess.Popen([sys.executable,str(ROOT/'scripts/mcp_pilot_agent.py'),label],cwd=ROOT,stdout=stdout,stderr=stderr,start_new_session=True)
    end=time.monotonic()+900;job=None;last=None;killed=False
    try:
        while time.monotonic()<end:
            new={p.stem for p in m.jobs.glob('*.json') if p.stem not in before and m.status(p.stem).get('label')==label}
            if new:
                assert len(new)==1;job=next(iter(new));x=m.status(job)
                if x['state']!=last:print(json.dumps({'job_id':job,'state':x['state']}),flush=True);last=x['state']
                if x['state']=='exporting' and len(x['exports'])<4:
                    proof={'client_pid':agent.pid,'client_start':identity(agent.pid),'mcp_server_pid':x['owner_pid'],
                           'supervisor_pid':x['supervisor_pid'],'exports_before_crash':list(x['exports']),
                           'crashed_at':time.time(),'phase_at_crash':x['state']}
                    assert proof['client_start'] and identity(x['supervisor_pid'])
                    agent.kill();agent.wait(timeout=10);assert agent.returncode==-signal.SIGKILL
                    killed=True;break
                if x['state'] in ('failed','cleanup_blocked','complete'):raise RuntimeError('Unexpected early terminal state: '+str(x))
            if agent.poll() is not None:raise RuntimeError('MCP agent exited before crash injection')
            time.sleep(.025)
        assert killed,'Did not reach export boundary'
        print(json.dumps({'event':'client_sigkill','job_id':job,**proof}),flush=True)
        end=time.monotonic()+180
        while time.monotonic()<end:
            x=m.status(job)
            if x['state'] in ('complete','failed','cleanup_blocked'):break
            time.sleep(.5)
        report=verify(job);assert report['reason']=='agent_crash',x
        assert identity(proof['mcp_server_pid']) is None,'MCP server did not exit on client EOF'
        report.update(proof);report['cleanup_after_crash_seconds']=x['finished_at']-proof['crashed_at']
        (out/'agent-crash-run.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report),flush=True)
    finally:
        if agent.poll() is None:agent.terminate();agent.wait(timeout=10)
        if job and not killed:m.finish(job)
