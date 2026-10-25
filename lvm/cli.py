import argparse,json,os,sys,time
from .manager import Manager,ManagerError,TERMINAL

def emit(x): print(json.dumps(x),flush=True)
def wait(m,job,predicate,seconds=900):
    end=time.monotonic()+seconds; last=None; renewed=0; warning=None
    while time.monotonic()<end:
        x=m.status(job)
        if x['state'] not in TERMINAL and time.monotonic()-renewed>20:m.renew(job);renewed=time.monotonic()
        if x['state']!=last:emit({'job_id':job,'state':x['state']});last=x['state']
        if x.get('evidence_warning') and x['evidence_warning']!=warning:
            warning=x['evidence_warning'];emit({'job_id':job,'warning':warning})
        if predicate(x):return x
        if x['state'] in TERMINAL:raise ManagerError(json.dumps(x))
        time.sleep(1)
    raise ManagerError('Wait timed out; supervisor retains cleanup responsibility')

def main():
    p=argparse.ArgumentParser(description='Disposable manager-owned Linux browser VMs')
    sub=p.add_subparsers(dest='command',required=True)
    a=sub.add_parser('run');a.add_argument('--url',default='pilot');a.add_argument('--label',default='browser-pilot');a.add_argument('--hold-after-capture',action='store_true')
    a=sub.add_parser('playwright');a.add_argument('--source',required=True);a.add_argument('--test',action='append',required=True);a.add_argument('--expected-tests',type=int,required=True);a.add_argument('--label',default='playwright')
    a=sub.add_parser('browser-suites');a.add_argument('--source',required=True);a.add_argument('--profile',required=True,choices=['surfaces','judgmentkit']);a.add_argument('--test',action='append');a.add_argument('--label',default='browser-suites')
    a=sub.add_parser('surfaces-connected-observer');a.add_argument('--label',default='surfaces-connected-observer')
    a=sub.add_parser('start');a.add_argument('--label',default='browser');a.add_argument('--owner-pid',type=int);a.add_argument('--lease-seconds',type=int,default=120)
    for name in ('status','finish','supervise'):
        a=sub.add_parser(name);a.add_argument('job_id')
    a=sub.add_parser('capture');a.add_argument('job_id');a.add_argument('--url',default='pilot')
    a=sub.add_parser('renew');a.add_argument('job_id');a.add_argument('--lease-seconds',type=int,default=120)
    sub.add_parser('inventory');sub.add_parser('sweep')
    a=sub.add_parser('recover-export');a.add_argument('job_id');a.add_argument('--evidence-mib',type=int)
    args=p.parse_args();m=Manager().init()
    try:
        if args.command=='recover-export':
            x=m.recover_export(args.job_id,args.evidence_mib);emit(x)
            return 0 if x.get('vm_deleted') and x.get('runtime_deleted') and x.get('evidence_verified') else 1
        if args.command=='surfaces-connected-observer':
            from . import surfaces_connected
            x=surfaces_connected.start(m,args.label,os.getpid());job=x['job_id'];emit({'job_id':job,'state':x['state']})
            try:x=wait(m,job,lambda s:s['state'] in TERMINAL,seconds=max(60,int(x['hard_deadline']-time.time())+120))
            finally:m.finish(job)
            emit(x);return 0 if x['state']=='complete' and x.get('test_result',{}).get('passed') else 1
        if args.command=='browser-suites':
            from . import suites
            x=suites.start(m,args.source,args.profile,args.test,args.label);job=x['job_id'];emit({'job_id':job,'state':x['state']})
            try:x=wait(m,job,lambda s:s['state'] in TERMINAL,seconds=3300)
            finally:m.finish(job)
            emit(x);return 0 if x['state']=='complete' and x.get('test_result',{}).get('passed') else 1
        if args.command=='playwright':
            from . import playwright
            x=playwright.start(m,args.source,args.test,args.expected_tests,args.label);job=x['job_id'];emit({'job_id':job,'state':x['state']})
            try:x=wait(m,job,lambda s:s['state'] in TERMINAL,seconds=3300)
            finally:m.finish(job)
            emit(x);return 0 if x['state']=='complete' and x.get('test_result',{}).get('passed') else 1
        if args.command=='run':
            x=m.start(args.label);job=x['job_id'];emit(x)
            try:
                wait(m,job,lambda s:s['state']=='ready')
                r=m.capture(job,args.url);emit(r)
                wait(m,job,lambda s:r['request_id']+'-result.json' in s['exports'] and s['state']=='ready')
                emit({'job_id':job,'event':'capture_exported','request_id':r['request_id']})
                if args.hold_after_capture:
                    while m.status(job)['state'] not in TERMINAL:m.renew(job);time.sleep(15)
            finally:m.finish(job)
            x=wait(m,job,lambda s:s['state'] in TERMINAL,seconds=180);emit(x)
            return 0 if x['state']=='complete' else 1
        if args.command=='start':emit(m.start(args.label,args.lease_seconds,args.owner_pid or os.getppid()))
        elif args.command=='status':emit(m.status(args.job_id))
        elif args.command=='capture':emit(m.capture(args.job_id,args.url))
        elif args.command=='renew':emit(m.renew(args.job_id,args.lease_seconds))
        elif args.command=='finish':emit(m.finish(args.job_id))
        elif args.command=='inventory':emit(m.inventory())
        elif args.command=='sweep':emit(m.sweep())
        elif args.command=='supervise':m.supervise(args.job_id)
        return 0
    except (ManagerError,KeyboardInterrupt) as e:
        print(str(e),file=sys.stderr);return 1
if __name__=='__main__':sys.exit(main())
