"""Owned VM lifecycle. No discovery, adoption, or deletion of external VMs."""
from __future__ import annotations
import base64, contextlib, fcntl, hashlib, json, os, pathlib, re, shutil, signal
import subprocess, sys, time, uuid
from urllib.parse import urlsplit

ROOT=pathlib.Path(__file__).resolve().parent.parent
GIB=1024**3
TERMINAL={'complete','failed','cleanup_blocked'}
EXPORTS=('page.png','page.html','browser.log','result.json')
class ManagerError(RuntimeError): pass
class Cancelled(ManagerError): pass

def atomic(path, obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.tmp-'+uuid.uuid4().hex)
    with tmp.open('x') as f:
        json.dump(obj,f,indent=2); f.write('\n');f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)
    fd=os.open(path.parent,os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)

def read(path): return json.loads(path.read_text())
def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1024**2),b''):h.update(b)
    return h.hexdigest()

def identity(pid):
    p=subprocess.run(['/bin/ps','-p',str(pid),'-o','lstart=','-o','stat='],capture_output=True,text=True)
    fields=p.stdout.split()
    return ' '.join(fields[:5]) if p.returncode==0 and len(fields)>=6 and 'Z' not in fields[5] else None

def allocated(path):
    total=0
    if not path.exists():return 0
    for directory,dirs,files in os.walk(path,followlinks=False):
        dirs[:]=[d for d in dirs if not (pathlib.Path(directory)/d).is_symlink()]
        for name in files:
            try:s=(pathlib.Path(directory)/name).lstat()
            except FileNotFoundError:continue # cleanup may remove a file during this read-only sample
            total+=s.st_blocks*512
    return total

@contextlib.contextmanager
def lock(path, blocking=True):
    with path.open('a') as f:
        fcntl.flock(f,fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        try:yield
        finally:fcntl.flock(f,fcntl.LOCK_UN)

def data_root(code_root=ROOT):
    """Select local storage without moving its ownership record or evidence."""
    code_root=pathlib.Path(code_root)
    config=code_root/'installation.json'
    if config.is_symlink():raise ManagerError('Refusing symlinked installation configuration')
    value=os.environ.get('LVM_DATA_ROOT')
    if value is None and config.exists():
        selected=read(config)
        if selected.get('schema_version')!=1:raise ManagerError('Unsupported installation configuration')
        value=selected['data_root']
    chosen=pathlib.Path(value) if value is not None else code_root
    if not chosen.is_absolute():raise ManagerError('Data root must be absolute')
    return chosen

class Manager:
    def __init__(self,root=None):
        self.code_root=ROOT
        self.root=pathlib.Path(root if root is not None else data_root()).absolute()
        if self.root.is_symlink() or self.root.resolve()!=self.root:raise ManagerError('Project root must be canonical, without symlinks')
        if '/Documents/' in str(self.root) or '/Desktop/' in str(self.root) or 'Mobile Documents' in str(self.root):
            raise ManagerError('Runtime and evidence must live outside iCloud locations')
        self.state=self.root/'state'; self.jobs=self.state/'jobs'
        self.runtime=self.root/'runtime'; self.evidence=self.root/'evidence'
        self.lima=self.root/'tools/lima/bin/limactl'
    def init(self):
        os.umask(0o077)
        for p in (self.state,self.jobs,self.runtime,self.evidence):
            if p.is_symlink():raise ManagerError('Refusing symlinked storage: '+str(p))
            p.mkdir(parents=True,exist_ok=True)
        with lock(self.state/'init.lock'):
            owner=self.state/'owner.json'
            if not owner.exists():atomic(owner,{'manager_id':uuid.uuid4().hex,'root':str(self.root)})
            self.owner=read(owner)
            if self.owner['root']!=str(self.root):raise ManagerError('Manager root identity mismatch')
        return self
    def settings(self):
        x=read(self.root/'settings.json')
        from .resources import validate
        return validate(x)
    def path(self,job):
        if not re.fullmatch('[a-f0-9]{8}',job):raise ManagerError('Invalid job ID')
        return self.jobs/(job+'.json')
    def status(self,job):
        p=self.path(job)
        if not p.is_file() or p.is_symlink():raise ManagerError('Unknown job')
        x=read(p)
        if x['manager_id']!=self.owner['manager_id'] or x['job_id']!=job:raise ManagerError('Job owner mismatch')
        return x
    def inventory(self):
        from .resources import snapshot
        with lock(self.state/'admission.lock'):
            capacity=snapshot(self,self.settings())
        return {'implementation':'resource-reservations-v2','source_sha256':sha(self.code_root/'lvm/manager.py'),
                'capacity':capacity,'scope':'only this manager; external VM stores are never queried',
                'code_root':str(self.code_root),'data_root':str(self.root),
                'allocated_bytes':allocated(self.root), 'settings':self.settings(),
                'evidence_policy':{'implementation':'soft-evidence-v1','soft_limit_bytes':self.settings()['evidence_mib']*1024**2,'capacity_gated_growth':True},
                'jobs':[self.status(p.stem) for p in sorted(self.jobs.glob('*.json'))]}
    def start(self,label='browser',lease_seconds=120,owner_pid=None,workload=None):
        if not isinstance(label,str) or not 1<=len(label)<=120:raise ManagerError('Label must be 1–120 characters')
        if not isinstance(lease_seconds,int) or not 30<=lease_seconds<=3600:raise ManagerError('Lease must be 30–3600 seconds')
        with lock(self.state/'admission.lock'):
            s=self.settings()
            special=bool(workload and workload[1].get('runner')=='surfaces-connected-observer')
            from .resources import claim,admit
            reservation=claim(s,workload[1] if workload else None)
            admission=admit(self,s,reservation)
            if not special:
                if not self.lima.is_file() or not (self.root/'cache/debian.raw').is_file():raise ManagerError('Run scripts/bootstrap.py first')
                if sha(self.lima)!=s['limactl_sha256'] or sha(self.root/'cache/debian.raw')!=s['image_sha256']:raise ManagerError('Pinned runtime dependency changed')
            owner_pid=owner_pid or os.getpid();start=identity(owner_pid)
            if not start:raise ManagerError('Owner process is not alive')
            hard_deadline=workload[1]['absolute_deadline'] if special else time.time()+3600
            if hard_deadline<=time.time()+60 or hard_deadline>time.time()+21600:raise ManagerError('Invalid workload hard deadline')
            for _ in range(100):
                job=uuid.uuid4().hex[:8];d=self.runtime/job;e=self.evidence/job
                if not any(p.exists() or p.is_symlink() for p in (self.path(job),d,e)):break
            else:raise ManagerError('Cannot allocate an unused job identity')
            x={'manager_id':self.owner['manager_id'],'job_id':job,'label':label,
               'profile':'surfaces-connected-observer' if special else ('linux-playwright' if workload else 'linux-browser'),
               'created_at':time.time(),'deadline':min(time.time()+lease_seconds,hard_deadline),
               'hard_deadline':hard_deadline,'owner_pid':owner_pid,'owner_start':start,
               'state':'allocating','allocation_pending':True,'captures':[],'exports':{},'peak_allocated_bytes':allocated(self.root),
               'runtime_path':str(d),'evidence_path':str(e),'schema_version':2,
               'reservation':reservation,'admission':{'held_jobs_before':admission['held_job_count'],
                 'committed_storage_before':admission['committed_storage_bytes'],'host':admission['host']}}
            if workload:x['workload']=workload[1]
            # Durable reservation precedes runtime creation. Crashes cannot lose a claim.
            atomic(self.path(job),x)
            try:
                e.mkdir();d.mkdir()
                x['runtime_identity']={'device':d.stat().st_dev,'inode':d.stat().st_ino}
                atomic(self.path(job),x)
                home=d/'l';home.mkdir()
                x.update(device=home.stat().st_dev,inode=home.stat().st_ino)
                atomic(d/'owner.json',{k:x[k] for k in ('manager_id','job_id','device','inode')})
                if workload:
                    payload,spec=workload;shutil.move(payload,d/'input.tar')
                    x['payload_sha256']=sha(d/'input.tar')
                    atomic(e/'source-manifest.json',spec)
                    x['exports']['source-manifest.json']={'bytes':(e/'source-manifest.json').stat().st_size,'sha256':sha(e/'source-manifest.json')}
                x['allocation_pending']=False;x['state']='queued';atomic(self.path(job),x)
                self.spawn(job)
            except BaseException as error:
                x.update(state='cleanup_blocked',error='Preparation failed: '+str(error),preparation_failed=True)
                if e.is_dir():
                    atomic(e/'preparation-error.json',{'error':x['error'],'job_id':job})
                    f=e/'preparation-error.json';x['exports'][f.name]={'bytes':f.stat().st_size,'sha256':sha(f)}
                atomic(self.path(job),x)
                raise
            return self.status(job)
    def evidence_limit(self,x):
        return max(x.get('approved_export_limit_bytes',0),self.settings()['evidence_mib']*1024**2)
    def reserve_evidence(self,x,needed_bytes,measured_bytes=None):
        """Reserve export/archive/extraction headroom before copying host evidence."""
        if type(needed_bytes) is not int or needed_bytes<0:raise ManagerError('Invalid measured evidence size')
        with lock(self.state/'admission.lock'):
            current=self.status(x['job_id']);self.home(current)
            evidence=self.evidence/x['job_id']
            if evidence.is_symlink() or evidence.resolve()!=evidence or not evidence.is_dir():raise ManagerError('Recovery evidence directory is unsafe')
            from .resources import snapshot,job_claim
            policy=self.settings();capacity=snapshot(self,policy)
            if capacity['unknown_state']:raise ManagerError('Unaccounted runtime state prevents evidence reservation')
            reservation=job_claim(policy,current).copy()
            if reservation['profile']!='linux-browser':raise ManagerError('This profile has a separately bound evidence policy')
            previous_limit=self.evidence_limit(current);new_limit=max(previous_limit,needed_bytes)
            old_reserved=reservation['evidence_bytes'];new_reserved=max(old_reserved,3*new_limit+16*1024**2);delta=new_reserved-old_reserved
            if capacity['committed_storage_bytes']+delta>policy['storage_budget_gib']*GIB:
                raise ManagerError('Evidence export needs more shared storage capacity; guest and evidence retained')
            if capacity['remaining_reserved_bytes']+delta+policy['staging_mib']*1024**2+policy['free_floor_gib']*GIB>capacity['host']['disk_free_bytes']:
                raise ManagerError('Evidence export would breach host free-space floor; guest and evidence retained')
            if delta or new_limit>previous_limit:
                current.setdefault('export_recovery_history',[]).append({'at':time.time(),'previous_limit_bytes':previous_limit,'approved_limit_bytes':new_limit,'previous_reserved_bytes':old_reserved,'reserved_bytes':new_reserved})
            reservation['evidence_bytes']=new_reserved;current['reservation']=reservation;current['approved_export_limit_bytes']=new_limit
            soft=policy['evidence_mib']*1024**2
            if measured_bytes is not None and measured_bytes>=soft:
                current['evidence_warning']={'code':'soft_evidence_allowance_reached','soft_limit_bytes':soft,'measured_bytes':measured_bytes,'message':'Evidence reached the soft allowance; export continues within reserved shared capacity.'}
            atomic(self.path(x['job_id']),current);x.update(current)
        return new_limit
    def recover_export(self,job,limit_mib=None):
        """Export-only recovery; the default measures evidence and reserves capacity."""
        if limit_mib is not None and (type(limit_mib) is not int or limit_mib<1):raise ManagerError('Recovery allowance must be a positive MiB value')
        self.path(job)
        with lock(self.state/(job+'.supervisor.lock'),blocking=False):
            x=self.status(job)
            if x['state']!='cleanup_blocked' or x.get('profile')!='linux-playwright' or not x.get('workload_started'):
                raise ManagerError('Export recovery requires a blocked, already-started Playwright job')
            pid=x.get('supervisor_pid')
            if pid and identity(pid)==x.get('supervisor_start'):raise ManagerError('The existing job supervisor is still running')
            self.home(x)
            if limit_mib is not None:
                new_limit=limit_mib*1024**2
                if new_limit<=self.evidence_limit(x):raise ManagerError('Recovery hint must increase this job allowance')
                self.reserve_evidence(x,new_limit)
            self._supervise(job)
            return self.status(job)
    def check_job_budget(self,x):
        from .resources import job_claim,usage
        reservation=job_claim(self.settings(),x);runtime,evidence=usage(self,x,reservation)
        x['peak_runtime_allocated_bytes']=max(x.get('peak_runtime_allocated_bytes',0),runtime)
        if runtime>reservation['runtime_bytes']:raise ManagerError('This job exceeded its reserved runtime storage')
        if evidence>reservation['evidence_bytes']:raise ManagerError('This job exceeded its reserved evidence storage')
        if shutil.disk_usage(self.root).free<self.settings()['free_floor_gib']*GIB:
            raise ManagerError('Host emergency free-space floor breached')
    def spawn(self,job):
        path=self.evidence/job
        if path.is_symlink():raise ManagerError('Symlinked job evidence directory')
        path.mkdir(exist_ok=True)
        log=(path/'supervisor.log').open('ab',buffering=0)
        try:
            subprocess.Popen([sys.executable,'-m','lvm.cli','supervise',job],cwd=self.code_root,env={**os.environ,'LVM_DATA_ROOT':str(self.root)},
                             stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True,close_fds=True)
        finally:log.close()
    def capture(self,job,url='pilot'):
        if url!='pilot':
            u=urlsplit(url)
            if u.scheme not in ('http','https') or not u.hostname or u.username or u.password:raise ManagerError('Use an HTTP(S) URL without credentials, or pilot')
        x=self.status(job)
        if x['state']!='ready':raise ManagerError('Guest is not ready')
        q=self.state/'requests'/job;q.mkdir(parents=True,exist_ok=True)
        if len(list(q.glob('*.json')))>=20:raise ManagerError('Capture limit is 20 per job')
        request=uuid.uuid4().hex[:12]
        atomic(q/(request+'.json'),{'request_id':request,'url':url})
        return {'job_id':job,'request_id':request,'state':'queued'}
    def renew(self,job,lease_seconds=120):
        x=self.status(job)
        if x['state'] in TERMINAL:raise ManagerError('Job has ended')
        if not isinstance(lease_seconds,int) or not 30<=lease_seconds<=900:raise ManagerError('Renewal must be 30–900 seconds')
        deadline=min(time.time()+lease_seconds,x['hard_deadline'])
        atomic(self.state/(job+'.lease'),{'deadline':deadline})
        return {'job_id':job,'requested_deadline':deadline}
    def finish(self,job):
        x=self.status(job)
        if x['state'] in TERMINAL:return x
        atomic(self.state/(job+'.finish'),{'requested_at':time.time()})
        return {'job_id':job,'state':'finish_requested'}
    def home(self,x):
        d=self.runtime/x['job_id'];home=d/'l'
        if d.is_symlink() or home.is_symlink() or d.resolve()!=d or home.resolve()!=home:raise ManagerError('Owned runtime path changed')
        m=read(d/'owner.json')
        expected={k:x[k] for k in ('manager_id','job_id','device','inode')}
        if m!=expected or home.stat().st_ino!=x['inode'] or home.stat().st_dev!=x['device']:raise ManagerError('Runtime identity mismatch')
        return home
    def env(self,x):
        import getpass
        return {'PATH':'/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin','USER':getpass.getuser(),
                'HOME':str(pathlib.Path.home()),'TMPDIR':str(self.home(x).parent/'tmp'),
                'LIMA_HOME':str(self.home(x)),'LIMA_SSH_OVER_VSOCK':'false','LIMA_SHELL':'/bin/bash'}
    def abort_reason(self,x):
        renewal=self.state/(x['job_id']+'.lease')
        if renewal.exists():x['deadline']=min(max(x['deadline'],read(renewal)['deadline']),x['hard_deadline'])
        if identity(x['owner_pid'])!=x['owner_start']:return 'agent_crash'
        if time.time()>x['deadline']:return 'lease_expired'
        if (self.state/(x['job_id']+'.finish')).exists():return 'normal'
        return None
    def command(self,x,args,timeout=120,watch=True,output=None,allow_fail=False,input_path=None):
        log=self.evidence/x['job_id']/'lifecycle.log'
        out=output or log
        env=self.env(x);pathlib.Path(env['TMPDIR']).mkdir(exist_ok=True)
        started=time.monotonic();last_check=0
        with contextlib.ExitStack() as stack:
            f=stack.enter_context(out.open('ab' if output is None else 'wb',buffering=0))
            input_file=stack.enter_context(input_path.open('rb')) if input_path else subprocess.DEVNULL
            p=subprocess.Popen([str(self.lima),'--tty=false',*args],env=env,cwd=self.root,
                stdin=input_file,stdout=f,stderr=f if output is None else subprocess.DEVNULL,
                start_new_session=True)
            x['active_command']={'pid':p.pid,'start':identity(p.pid),'operation':args[0]}
            atomic(self.path(x['job_id']),x)
            try:
                while p.poll() is None:
                    if time.monotonic()-started>timeout:raise ManagerError('Lima command timed out: '+args[0])
                    if watch:
                        reason=self.abort_reason(x)
                        if reason:raise Cancelled(reason)
                    if time.monotonic()-last_check>5:
                        last_check=time.monotonic(); size=allocated(self.root)
                        x['peak_allocated_bytes']=max(x['peak_allocated_bytes'],size)
                        if watch:self.check_job_budget(x)
                    limit=self.evidence_limit(x) if output is not None else 16*1024**2
                    if out.stat().st_size>limit:raise ManagerError('Command output limit exceeded')
                    time.sleep(.25)
            except BaseException:
                try:os.killpg(p.pid,signal.SIGTERM);p.wait(timeout=5)
                except (ProcessLookupError,subprocess.TimeoutExpired):
                    if p.poll() is None:os.killpg(p.pid,signal.SIGKILL);p.wait()
                raise
            finally:
                x.pop('active_command',None);atomic(self.path(x['job_id']),x)
        if p.returncode and not allow_fail:raise ManagerError('Lima '+args[0]+' failed ('+str(p.returncode)+'); see lifecycle.log')
        return p.returncode
    def config(self,x):
        script=base64.b64encode((self.code_root/'lvm/guest_capture.py').read_bytes()).decode()
        provision='''#!/bin/sh
set -eu
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends chromium fonts-liberation ca-certificates python3
apt-get clean
printf '%s' SCRIPT | base64 -d > /opt/lvm-capture.py
chmod 755 /opt/lvm-capture.py
'''.replace('SCRIPT',script)
        if x.get('workload'):
            provision=provision.replace('chromium fonts-liberation ca-certificates python3','fonts-liberation ca-certificates python3 xz-utils')
            x['guest_helper_sha256']=sha(self.code_root/'lvm/guest_playwright.py')
            helper=base64.b64encode((self.code_root/'lvm/guest_playwright.py').read_bytes()).decode()
            provision += "printf '%s' "+helper+" | base64 -d > /opt/lvm-playwright.py\nchmod 755 /opt/lvm-playwright.py\n"
            if x['workload'].get('runner')=='node-suites':
                packages='git chromium' if x['workload']['suite_profile']=='judgmentkit' else 'git'
                provision += 'apt-get install -y --no-install-recommends '+packages+'\napt-get clean\n'
                helper=base64.b64encode((self.code_root/'lvm/guest_suites.py').read_bytes()).decode()
                provision += "printf '%s' "+helper+" | base64 -d > /opt/lvm-suites.py\n"
        s=self.settings();reservation=x.get('reservation',{})
        return {'vmType':'vz','arch':'aarch64','plain':True,'cpus':reservation.get('vcpus',s['cpus']),'memory':str(reservation.get('guest_memory_gib',s['memory_gib']))+'GiB',
                'disk':str(reservation.get('guest_disk_gib',s['disk_gib']))+'GiB','images':[{'location':str(self.root/'cache/debian.raw'),
                'arch':'aarch64','digest':'sha256:'+s['image_sha256']}],
                'mounts':[],'containerd':{'system':False,'user':False},'ssh':{'forwardAgent':False},
                'portForwards':[], 'provision':[{'mode':'system','script':provision}]}
    def export(self,x,request):
        # Never export a guest directory/archive, credentials, browser profile, or SDK.
        for name in EXPORTS:
            key=request+'-'+name
            target=self.evidence/x['job_id']/key
            remote='/tmp/lvm-evidence/'+request+'/'+name
            metadata=target.with_suffix(target.suffix+'.meta-part')
            code="import pathlib,hashlib,json; p=pathlib.Path("+repr(remote)+"); assert p.is_file() and not p.is_symlink(); f=p.open('rb'); print(json.dumps({'size':p.stat().st_size,'sha256':hashlib.file_digest(f,'sha256').hexdigest()}))"
            rc=self.command(x,['shell','guest','--','python3','-c',code],watch=False,timeout=25,output=metadata,allow_fail=True)
            if rc:
                metadata.unlink(missing_ok=True);continue
            m=read(metadata);metadata.unlink()
            total=m['size']+sum(v['bytes'] for k,v in x['exports'].items() if k!=key)
            self.reserve_evidence(x,total,measured_bytes=total)
            part=target.with_suffix(target.suffix+'.part')
            self.command(x,['shell','guest','--','cat',remote],watch=False,timeout=30,output=part)
            if part.stat().st_size!=m['size'] or sha(part)!=m['sha256']:raise ManagerError('Evidence readback mismatch')
            with part.open('rb') as f:os.fsync(f.fileno())
            os.replace(part,target)
            x['exports'][key]={'bytes':m['size'],'sha256':m['sha256']}
            atomic(self.path(x['job_id']),x)
    def holders(self,home):
        p=subprocess.run(['/usr/sbin/lsof','-nP','-Fpcn','+D',str(home)],capture_output=True,text=True)
        if p.returncode not in (0,1) or p.stderr.strip():raise ManagerError('Open-file verification unavailable: '+p.stderr[:200])
        return p.stdout.splitlines()
    def stop_orphaned_hostagents(self,x,home,holders):
        # A timed-out Lima stop can leave a re-exec'd hostagent after stale PID
        # files were removed. Only the exact owned hostagent argv is eligible.
        expected=[str(self.lima),'hostagent','--pidfile',str(home/'guest/ha.pid'),
                  '--socket',str(home/'guest/ha.sock'),'guest']
        import shlex
        for pid in sorted({int(line[1:]) for line in holders if re.fullmatch(r'p[0-9]+',line)}):
            start=identity(pid)
            command=subprocess.run(['/bin/ps','-p',str(pid),'-o','command='],capture_output=True,text=True)
            if not start or command.returncode or shlex.split(command.stdout.strip())!=expected:continue
            self.home(x)
            if identity(pid)!=start:continue
            record={'pid':pid,'start':start,'command':expected}
            x.setdefault('orphaned_hostagents_stopped',[]).append(record)
            atomic(self.path(x['job_id']),x)
            try:os.kill(pid,signal.SIGTERM)
            except ProcessLookupError:continue
            end=time.monotonic()+5
            while identity(pid)==start and time.monotonic()<end:time.sleep(.1)
            if identity(pid)==start:os.kill(pid,signal.SIGKILL)
    def cleanup(self,x):
        if not (self.runtime/x['job_id']).exists() and x.get('deletion_intent'):
            x.update(vm_deleted=True,runtime_deleted=True,remaining_runtime_bytes=0);return
        d=self.runtime/x['job_id']
        if not d.exists() and (x.get('preparation_failed') or x.get('allocation_pending')):
            x.update(vm_deleted=True,runtime_deleted=True,remaining_runtime_bytes=0);return
        if not (d/'owner.json').exists() or not (d/'l').exists():
            expected=x.get('runtime_identity');st=d.stat()
            if d.is_symlink() or d.resolve()!=d or not expected or (st.st_dev,st.st_ino)!=(expected['device'],expected['inode']):
                raise ManagerError('Partial runtime allocation identity cannot be verified')
            if any(p.name not in ('l','input.tar','tmp') for p in d.iterdir()) or self.holders(d):
                raise ManagerError('Partial runtime has unknown entries or open handles')
            if (d/'l').exists() and any((d/'l').iterdir()):raise ManagerError('Unmarked partial guest is not empty')
            st=d.stat()
            if (st.st_dev,st.st_ino)!=(expected['device'],expected['inode']):raise ManagerError('Partial runtime changed before deletion')
            x['deletion_intent']=True;atomic(self.path(x['job_id']),x);shutil.rmtree(d)
            x.update(vm_deleted=True,runtime_deleted=True,remaining_runtime_bytes=0);return
        home=self.home(x)
        unexpected=[p.name for p in home.iterdir() if p.is_dir() and p.name!='guest' and not p.name.startswith(('_','.'))]
        if unexpected:raise ManagerError('Unrecognized instance inside owned namespace; refusing deletion')
        x['holders_before_stop']=self.holders(home)
        # Commands are scoped by our validated LIMA_HOME, never the default registry.
        try:
            rc=self.command(x,['stop','guest'],timeout=40,watch=False,allow_fail=True)
        except ManagerError as error:
            if str(error)!='Lima command timed out: stop':raise
            x['graceful_stop_timed_out']=True
            rc=124
        if rc:self.command(x,['stop','--force','guest'],timeout=25,watch=False,allow_fail=True)
        holders=self.holders(home)
        if holders:
            self.stop_orphaned_hostagents(x,home,holders)
            holders=self.holders(home)
        x['holders_after_stop']=holders
        if holders:raise ManagerError('VM still has open handles; refusing deletion')
        if (home/'guest').exists():self.command(x,['delete','guest'],timeout=30,watch=False)
        if (home/'guest').exists() or self.holders(home):raise ManagerError('VM deletion did not complete')
        self.home(x) # recheck directory identity immediately before namespace deletion
        x['deletion_intent']=True;atomic(self.path(x['job_id']),x)
        shutil.rmtree(home.parent)
        x['vm_deleted']=not home.exists();x['runtime_deleted']=not home.parent.exists()
        x['remaining_runtime_bytes']=0
    def supervise(self,job):
        try:
            with lock(self.state/(job+'.supervisor.lock'),blocking=False):self._supervise(job)
        except BlockingIOError:return
    def _supervise(self,job):
        x=self.status(job)
        if x['state'] in ('complete','failed'):return
        recovering=x['state']!='queued'
        if x.get('cleanup_error'):
            entry={'at':x.get('finished_at'),'error':x['cleanup_error']}
            if entry not in x.setdefault('cleanup_failures',[]):x['cleanup_failures'].append(entry)
        x['recovery_attempts']=x.get('recovery_attempts',0)+(1 if recovering else 0)
        x['supervisor_pid']=os.getpid();x['supervisor_start']=identity(os.getpid())
        reason='supervisor_recovery' if recovering else None
        try:
            if recovering:
                command=x.get('active_command')
                if command and command.get('start') and identity(command['pid'])==command['start']:
                    os.killpg(command['pid'],signal.SIGTERM)
                    until=time.monotonic()+5
                    while identity(command['pid'])==command['start'] and time.monotonic()<until:time.sleep(.1)
                    if identity(command['pid'])==command['start']:os.killpg(command['pid'],signal.SIGKILL)
                x.pop('active_command',None)
                workload=x.get('active_workload')
                if workload and workload.get('start') and identity(workload['pid'])==workload['start']:
                    os.killpg(workload['pid'],signal.SIGTERM)
                    until=time.monotonic()+8
                    while identity(workload['pid'])==workload['start'] and time.monotonic()<until:time.sleep(.1)
                    if identity(workload['pid'])==workload['start']:os.killpg(workload['pid'],signal.SIGKILL)
                x.pop('active_workload',None)
                raise Cancelled(reason)
            reason=self.abort_reason(x)
            if reason:raise Cancelled(reason)
            if x.get('workload',{}).get('runner')=='surfaces-connected-observer':
                from . import surfaces_connected
                x['state']='testing';atomic(self.path(job),x)
                surfaces_connected.run(self,x)
                raise Cancelled('normal')
            x['state']='provisioning';atomic(self.path(job),x)
            config=self.home(x).parent/'guest.json';atomic(config,self.config(x))
            self.command(x,['start','--name=guest',str(config)],timeout=720)
            x['state']='ready';x['ready_at']=time.time();atomic(self.path(job),x)
            if x.get('workload'):
                from . import playwright
                x['state']='testing';atomic(self.path(job),x)
                playwright.run(self,x)
                raise Cancelled('normal')
            while True:
                reason=self.abort_reason(x)
                if reason:break
                requests=self.state/'requests'/job
                for p in sorted(requests.glob('*.json')) if requests.exists() else []:
                    r=read(p);request=r['request_id']
                    if request in x['captures']:continue
                    if not re.fullmatch('[a-f0-9]{12}',request):raise ManagerError('Invalid request identity')
                    x['captures'].append(request);x['state']='capturing';atomic(self.path(job),x)
                    self.command(x,['shell','guest','--','python3','/opt/lvm-capture.py',request,r['url']],timeout=70)
                    x['state']='exporting';atomic(self.path(job),x)
                    self.export(x,request)
                    required=[request+'-'+name for name in EXPORTS]
                    if any(k not in x['exports'] for k in required):raise ManagerError('Capture evidence is incomplete')
                    if not read(self.evidence/job/(request+'-result.json'))['passed']:raise ManagerError('Browser capture failed')
                    x['state']='ready';atomic(self.path(job),x)
                size=allocated(self.root);x['peak_allocated_bytes']=max(x['peak_allocated_bytes'],size)
                self.check_job_budget(x)
                time.sleep(1)
        except Cancelled as e:reason=str(e)
        except BaseException as e:x['error']=str(e);reason='run_failed'
        x['state']='cleaning';x['finish_reason']=reason;atomic(self.path(job),x)
        try:
            # Recover captures even when the owner died in the middle of a call.
            if (self.runtime/job).exists():
                for request in x['captures']:self.export(x,request)
                if x.get('workload'):
                    if x['workload'].get('runner')=='surfaces-connected-observer':
                        from . import surfaces_connected
                        surfaces_connected.export(self,x)
                    else:
                        from . import playwright
                        playwright.export(self,x)
            if x.get('preparation_failed') or x.get('allocation_pending'):
                path=self.evidence/job/'preparation-error.json'
                if not path.exists():atomic(path,{'job_id':job,'error':x.get('error','Interrupted while allocating runtime')})
                x['exports'][path.name]={'bytes':path.stat().st_size,'sha256':sha(path)}
            x['evidence_complete']=not (x.get('preparation_failed') or x.get('allocation_pending')) and all(request+'-'+name in x['exports'] for request in x['captures'] for name in EXPORTS)
            if x.get('workload'):
                x['evidence_complete']=bool(x.get('test_result'))
            if not x['evidence_complete']:x.setdefault('error','Capture interrupted before all evidence was produced; available evidence retained')
            x['evidence_verified']=all((self.evidence/job/k).is_file() and sha(self.evidence/job/k)==v['sha256'] for k,v in x['exports'].items())
            if not x['evidence_verified']:raise ManagerError('Host evidence verification failed; refusing deletion')
            if x.get('workload',{}).get('runner')=='surfaces-connected-observer':
                from . import surfaces_connected
                surfaces_connected.cleanup(self,x)
            else:self.cleanup(x)
            x['state']='failed' if x.get('error') else 'complete'
            x.pop('cleanup_error',None)
        except BaseException as e:x['state']='cleanup_blocked';x['cleanup_error']=str(e)
        x['finished_at']=time.time();x['retained_evidence_bytes']=sum(v['bytes'] for v in x['exports'].values())
        with lock(self.state/'admission.lock'):
            if x.get('vm_deleted') and x.get('runtime_deleted') and x.get('evidence_verified'):
                x['reservation_released_at']=time.time()
            atomic(self.path(job),x)
    def sweep(self):
        # Admission publishes the reservation and ownership marker as one operation.
        # Recovery must not observe the intermediate allocation record.
        with lock(self.state/'admission.lock'):
            recovered=[]
            for p in self.jobs.glob('*.json'):
                x=self.status(p.stem)
                if x['state'] in ('complete','failed') or x.get('recovery_attempts',0)>=3:continue
                pid=x.get('supervisor_pid')
                if not pid or identity(pid)!=x.get('supervisor_start'):
                    self.spawn(x['job_id']);recovered.append(x['job_id'])
            return {'recovery_started':recovered}
