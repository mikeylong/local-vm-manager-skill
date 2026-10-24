"""Admission accounting for independent owned jobs. No external VM discovery."""
from __future__ import annotations
import os,pathlib,re,shutil,subprocess
from .manager import ManagerError,allocated,read,sha
GIB=1024**3
MIB=1024**2
VERSION=2
DEFAULTS={'resource_policy_version':2,'cpus':2,'disk_gib':12,'memory_gib':2,
          'max_concurrent':3,'max_guest_count':4,'max_vcpus':12,'max_memory_gib':16,
          'storage_budget_gib':80,'free_floor_gib':10,'host_memory_floor_gib':16,
          'host_cpu_reserve':4,'runtime_overhead_gib':4,'staging_mib':256,'evidence_mib':1024}


def host_capacity(root):
    """Use fresh host metrics. An unavailable probe is not spare capacity."""
    def sysctl(name):
        p=subprocess.run(['/usr/sbin/sysctl','-n',name],capture_output=True,text=True)
        if p.returncode:raise ManagerError('Cannot verify host capacity: '+name)
        return int(p.stdout.strip())
    total=sysctl('hw.memsize');cpus=sysctl('hw.logicalcpu')
    p=subprocess.run(['/usr/bin/vm_stat'],capture_output=True,text=True)
    match=re.search(r'page size of (\d+) bytes',p.stdout)
    if p.returncode or not match:raise ManagerError('Cannot verify host memory availability')
    values={key:int(value) for key,value in re.findall(r'^(Pages [^:]+):\s+(\d+)\.',p.stdout,re.M)}
    if not {'Pages free','Pages inactive','Pages speculative'}<=values.keys():raise ManagerError('Incomplete host memory metrics')
    available=sum(values[k] for k in ('Pages free','Pages inactive','Pages speculative'))*int(match.group(1))
    return {'logical_cpus':cpus,'memory_bytes':total,'available_memory_bytes':available,
            'disk_free_bytes':shutil.disk_usage(root).free}


def validate(settings):
    if settings.get('resource_policy_version')!=VERSION:raise ManagerError('Resource policy upgrade required; run scripts/configure_resources.py')
    for key in DEFAULTS:
        if type(settings.get(key)) is not int or settings[key]<1:raise ManagerError('Invalid positive resource setting: '+key)
    if settings['max_concurrent']>16 or settings['max_guest_count']>32:raise ManagerError('Configured concurrency exceeds supported bounded limit')
    return settings


def configured_policy(previous,host):
    """Keep dependency pins and evidence policy; choose bounded machine capacity."""
    x={**previous,**DEFAULTS}
    for key in ('disk_gib','memory_gib','cpus','evidence_mib','free_floor_gib'):
        if key in previous:x[key]=previous[key]
    x.update(evidence_mib=1024,evidence_policy_version=1)
    x['host_cpu_reserve']=max(2,min(4,host['logical_cpus']//4))
    x['host_memory_floor_gib']=max(4,min(16,host['memory_bytes']//GIB//4))
    x['max_vcpus']=min(12,max(0,host['logical_cpus']-x['host_cpu_reserve']))
    x['max_memory_gib']=min(16,max(0,host['memory_bytes']//GIB-x['host_memory_floor_gib']))
    x['max_concurrent']=min(3,x['max_vcpus']//x['cpus'],x['max_memory_gib']//x['memory_gib'])
    x['max_guest_count']=max(1,x['max_concurrent']+1)
    if x['max_concurrent']<1:raise ManagerError('Host lacks safe capacity for an ordinary browser guest')
    return validate(x)


def claim(settings,workload=None,legacy=False):
    special=bool(workload and workload.get('runner')=='surfaces-connected-observer')
    if special:
        from . import surfaces_connected as connected
        state=str(connected.STATE_ROOT)
        if workload.get('state_root')!=state:raise ManagerError('Unrecognized external runtime binding')
        return {'version':VERSION,'profile':'surfaces-connected-observer','guests':2,'vcpus':8,'memory_bytes':10*GIB,
                'runtime_bytes':42*GIB+64*MIB,'evidence_bytes':22*MIB,'external_evidence_bytes':64*MIB,
                'external_runtime_paths':[state],'exclusive_paths':[state]}
    return {'version':VERSION,'profile':'linux-browser','guests':1,'vcpus':2 if legacy else settings['cpus'],
            'memory_bytes':(2 if legacy else settings['memory_gib'])*GIB,
            'runtime_bytes':(16 if legacy else settings['disk_gib']+settings['runtime_overhead_gib'])*GIB,
            'evidence_bytes':3*settings['evidence_mib']*MIB+16*MIB,'external_evidence_bytes':0,
            'external_runtime_paths':[],'exclusive_paths':[],
            'guest_disk_gib':12 if legacy else settings['disk_gib'],'guest_memory_gib':2 if legacy else settings['memory_gib']}


def job_claim(settings,x):
    value=x.get('reservation')
    if value is None:
        if x.get('profile') not in ('linux-browser','linux-playwright','surfaces-connected-observer'):
            raise ManagerError('Unknown legacy resource profile: '+x['job_id'])
        value=claim(settings,x.get('workload'),legacy=True)
    if value.get('version')!=VERSION:raise ManagerError('Unknown resource reservation version')
    for key in ('guests','vcpus','memory_bytes','runtime_bytes','evidence_bytes'):
        if type(value.get(key)) is not int or value[key]<1:raise ManagerError('Malformed resource reservation: '+key)
    if value.get('profile')=='linux-browser':
        if value['guests']!=1 or value.get('external_runtime_paths') or value.get('exclusive_paths'):
            raise ManagerError('Ordinary job reservation profile changed')
        if value.get('guest_disk_gib',0)<1 or value.get('guest_memory_gib',0)<1 or value['memory_bytes']!=value['guest_memory_gib']*GIB or value['runtime_bytes']<(value['guest_disk_gib']+4)*GIB:
            raise ManagerError('Ordinary job reservation dimensions changed')
    elif value.get('profile')!='surfaces-connected-observer':raise ManagerError('Unknown reservation profile')
    if value.get('external_runtime_paths'):
        expected=claim(settings,x.get('workload'),legacy=True)
        if any(value.get(k)!=expected[k] for k in ('guests','vcpus','memory_bytes','runtime_bytes','evidence_bytes','external_evidence_bytes','external_runtime_paths','exclusive_paths')):
            raise ManagerError('External reservation path differs from reviewed binding')
    return value


def usage(m,x,reservation):
    runtime=allocated(m.runtime/x['job_id'])
    for raw in reservation.get('external_runtime_paths',[]):
        p=pathlib.Path(raw)
        if p.is_symlink() or p.resolve()!=p:raise ManagerError('External runtime identity is unsafe')
        if p.exists():
            expected=x.get('surfaces_state_identity')
            s=p.stat()
            if expected and (s.st_dev,s.st_ino)!=(expected['device'],expected['inode']):raise ManagerError('External runtime identity changed')
        runtime+=allocated(p)
    return runtime,allocated(m.evidence/x['job_id'])


def base_allocation(root,excluded):
    """Measure shared/retained data without subtracting changing active files."""
    total=0
    for directory,dirs,files in os.walk(root,followlinks=False):
        parent=pathlib.Path(directory)
        dirs[:]=[d for d in dirs if parent/d not in excluded and not (parent/d).is_symlink()]
        for name in files:
            try:total+=(parent/name).lstat().st_blocks*512
            except FileNotFoundError:continue
    return total


def snapshot(m,settings,host=None):
    validate(settings)
    jobs={p.stem:m.status(p.stem) for p in m.jobs.glob('*.json')}
    unknown=[]
    for p in m.runtime.iterdir():
        if p.name not in jobs or p.is_symlink():unknown.append(str(p))
    held=[];used_held=0;committed_held=0;remaining=0;physical=0;exclusive=set();excluded=set()
    for job,x in jobs.items():
        d=m.runtime/job
        released=x.get('vm_deleted') is True and x.get('runtime_deleted') is True and x.get('evidence_verified') is True and x['state'] in ('complete','failed')
        if released:
            if d.exists() or d.is_symlink():unknown.append(str(d)+' exists after recorded deletion')
            continue
        reservation=job_claim(settings,x)
        if d.exists():
            if (d/'l').exists() and (d/'owner.json').exists():m.home(x)
            else:
                expected=x.get('runtime_identity');s=d.stat()
                if not expected or (s.st_dev,s.st_ino)!=(expected['device'],expected['inode']):unknown.append(str(d)+' has no verified allocation identity')
        runtime,evidence=usage(m,x,reservation)
        excluded.update((m.runtime/job,m.evidence/job))
        used_held+=runtime+evidence
        committed_held+=max(reservation['runtime_bytes'],runtime)+max(reservation['evidence_bytes'],evidence)+reservation.get('external_evidence_bytes',0)
        physical+=reservation['runtime_bytes']+reservation['evidence_bytes']+reservation.get('external_evidence_bytes',0)
        remaining+=max(0,reservation['runtime_bytes']-runtime)+max(0,reservation['evidence_bytes']-evidence)+reservation.get('external_evidence_bytes',0)
        if exclusive.intersection(reservation.get('exclusive_paths',[])):unknown.append('Conflicting exclusive runtime claims')
        exclusive.update(reservation.get('exclusive_paths',[]))
        held.append({'job_id':job,'state':x['state'],'reservation':reservation,'runtime_allocated_bytes':runtime,'evidence_allocated_bytes':evidence})
    base=base_allocation(m.root,excluded);used=base+used_held
    return {'policy_version':VERSION,'host':host if host is not None else host_capacity(m.root),'held_jobs':held,
            'held_job_count':len(held),'reserved_guests':sum(j['reservation']['guests'] for j in held),
            'reserved_vcpus':sum(j['reservation']['vcpus'] for j in held),
            'reserved_memory_bytes':sum(j['reservation']['memory_bytes'] for j in held),
            'allocated_bytes_including_external':used,'remaining_reserved_bytes':remaining,
            'committed_storage_bytes':base+committed_held+settings['staging_mib']*MIB,
            'physical_reservation_headroom_bytes':physical,
            'exclusive_paths':sorted(exclusive),'unknown_state':unknown}


def admit(m,settings,reservation):
    state=snapshot(m,settings)
    if state['unknown_state']:raise ManagerError('Unsafe unaccounted runtime state: '+str(state['unknown_state']))
    checks=[(state['held_job_count']+1<=settings['max_concurrent'],'Concurrent job capacity exhausted'),
            (state['reserved_guests']+reservation['guests']<=settings['max_guest_count'],'Guest capacity exhausted'),
            (state['reserved_vcpus']+reservation['vcpus']<=min(settings['max_vcpus'],state['host']['logical_cpus']-settings['host_cpu_reserve']),'CPU reservation exhausted'),
            (state['reserved_memory_bytes']+reservation['memory_bytes']<=settings['max_memory_gib']*GIB,'Memory reservation exhausted')]
    for okay,message in checks:
        if not okay:raise ManagerError(message)
    if set(state['exclusive_paths']).intersection(reservation.get('exclusive_paths',[])):raise ManagerError('Reviewed runtime path is already reserved by its owning job')
    memory=state['reserved_memory_bytes']+reservation['memory_bytes']
    if memory+settings['host_memory_floor_gib']*GIB>state['host']['available_memory_bytes']:raise ManagerError('Host memory floor would be breached')
    extra=reservation['runtime_bytes']+reservation['evidence_bytes']+reservation.get('external_evidence_bytes',0)
    if state['committed_storage_bytes']+extra>settings['storage_budget_gib']*GIB:raise ManagerError('Storage reservation budget exhausted')
    if state['physical_reservation_headroom_bytes']+extra+settings['staging_mib']*MIB+settings['free_floor_gib']*GIB>state['host']['disk_free_bytes']:
        raise ManagerError('Host free-space floor would be breached')
    return state


def configure(m,*,dry_run=False):
    """Upgrade settings under admission lock; never reset owner state or erase jobs."""
    from .manager import atomic,lock
    import uuid
    with lock(m.state/'admission.lock'):
        for p in m.jobs.glob('*.json'):
            if m.status(p.stem)['state'] not in ('complete','failed','cleanup_blocked'):
                raise ManagerError('Stop active jobs before upgrading resource policy')
        previous=read(m.root/'settings.json');host=host_capacity(m.root)
        configured=m.state/'resource-policy.json'
        selected=validate(previous.copy()) if previous.get('resource_policy_version')==VERSION and configured.exists() else configured_policy(previous,host)
        if selected.get('evidence_policy_version')!=1:selected.update(evidence_mib=1024,evidence_policy_version=1)
        state=snapshot(m,selected,host)
        if state['unknown_state']:raise ManagerError('Cannot upgrade with unaccounted runtime: '+str(state['unknown_state']))
        changed=selected!=previous
        result={'changed':changed,'policy':selected,'host':host,'existing_held_jobs':state['held_job_count'],'dry_run':dry_run}
        if changed and not dry_run:
            backup=m.state/'resource-policy-backups'/(uuid.uuid4().hex+'.json');atomic(backup,previous)
            atomic(m.root/'settings.json',selected);result['backup']=str(backup)
        if not dry_run and not configured.exists():atomic(configured,{'version':VERSION,'host':host,'policy':selected})
        return result
