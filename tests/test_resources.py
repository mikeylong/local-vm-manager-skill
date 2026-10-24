import json,multiprocessing,os,pathlib,tempfile,unittest
from unittest.mock import patch,Mock
from lvm.manager import Manager,ManagerError,atomic,sha
from lvm.resources import DEFAULTS,GIB,host_capacity,snapshot,configure,configured_policy
HOST={'logical_cpus':16,'memory_bytes':128*GIB,'available_memory_bytes':96*GIB,'disk_free_bytes':160*GIB}

def fixture(root):
    m=Manager(root).init();(m.root/'tools/lima/bin').mkdir(parents=True);m.lima.write_bytes(b'fake lima')
    (m.root/'cache').mkdir();image=m.root/'cache/debian.raw';image.write_bytes(b'fake base')
    settings={**DEFAULTS,'limactl_sha256':sha(m.lima),'image_sha256':sha(image)}
    atomic(m.root/'settings.json',settings);return m

def race_worker(root,gate,queue):
    m=Manager(root).init();gate.wait()
    try:
        with patch('lvm.resources.host_capacity',return_value=HOST),patch.object(m,'spawn'):
            x=m.start('race');queue.put(('ok',x['job_id']))
    except ManagerError as e:queue.put(('denied',str(e)))

class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir='/private/tmp',prefix='lvm-resources-');self.m=fixture(pathlib.Path(self.temp.name).resolve())
        self.host=patch('lvm.resources.host_capacity',return_value=HOST);self.host.start()
        self.spawn=patch.object(self.m,'spawn');self.spawn.start()
    def tearDown(self):self.spawn.stop();self.host.stop();self.temp.cleanup()
    def settings(self,**values):
        s=self.m.settings();s.update(values);atomic(self.m.root/'settings.json',s)
    def start(self,label='test'):return self.m.start(label)
    def test_independent_jobs_and_conservative_capacity(self):
        jobs=[self.start(str(i)) for i in range(3)]
        self.assertEqual(len({str(self.m.home(x)) for x in jobs}),3)
        self.assertEqual(len({x['evidence_path'] for x in jobs}),3)
        with self.assertRaisesRegex(ManagerError,'Concurrent job capacity'):self.start()
        self.assertEqual(len(list(self.m.jobs.glob('*.json'))),3)
    def test_blocked_cleanup_holds_resources_without_blanket_denial(self):
        first=self.start();first['state']='cleanup_blocked';atomic(self.m.path(first['job_id']),first)
        self.start('unrelated-a');self.start('unrelated-b')
        state=snapshot(self.m,self.m.settings());self.assertEqual(state['reserved_vcpus'],6)
        self.assertEqual(state['held_job_count'],3)
        self.assertEqual(self.m.status(first['job_id'])['state'],'cleanup_blocked')
    def test_limits_deny_before_runtime_allocation(self):
        for limits,message in [({'storage_budget_gib':30},'Storage reservation'),({'max_vcpus':2},'CPU reservation'),({'max_memory_gib':2},'Memory reservation')]:
            with self.subTest(limits=limits):
                self.settings(**{**DEFAULTS,**limits});first=self.start()
                with self.assertRaisesRegex(ManagerError,message):self.start()
                # Unit fixtures contain no real guest; remove only these exact fixtures.
                import shutil
                shutil.rmtree(self.m.runtime/first['job_id']);self.m.path(first['job_id']).unlink()
    def test_host_floors_fail_closed_without_creating_job(self):
        for value,message in [({**HOST,'disk_free_bytes':1*GIB},'free-space floor'),({**HOST,'available_memory_bytes':17*GIB},'memory floor')]:
            with patch('lvm.resources.host_capacity',return_value=value),self.assertRaisesRegex(ManagerError,message):self.start()
        self.assertEqual(list(self.m.jobs.glob('*.json')),[]);self.assertEqual(list(self.m.runtime.iterdir()),[])
    def test_concurrent_process_admission_race_is_atomic(self):
        ctx=multiprocessing.get_context('spawn');gate=ctx.Event();queue=ctx.Queue()
        workers=[ctx.Process(target=race_worker,args=(str(self.m.root),gate,queue)) for _ in range(6)]
        for p in workers:p.start()
        gate.set();results=[queue.get(timeout=25) for _ in workers]
        for p in workers:p.join(15);self.assertEqual(p.exitcode,0)
        self.assertEqual(sum(r[0]=='ok' for r in results),3)
        self.assertEqual(len(list(self.m.jobs.glob('*.json'))),3)
    def test_unknown_runtime_and_foreign_owner_block_admission(self):
        unknown=self.m.runtime/'unknown';unknown.mkdir()
        with self.assertRaisesRegex(ManagerError,'unaccounted'):self.start()
        unknown.rmdir();first=self.start();marker=self.m.runtime/first['job_id']/'owner.json';value=json.loads(marker.read_text());value['manager_id']='foreign';atomic(marker,value)
        with self.assertRaisesRegex(ManagerError,'identity mismatch'):self.start()
    def test_failed_partial_allocation_stays_reserved_until_scoped_recovery(self):
        original=pathlib.Path.mkdir
        def fail_home(path,*args,**kwargs):
            if path.name=='l' and path.parent.parent==self.m.runtime:raise OSError('injected mkdir failure')
            return original(path,*args,**kwargs)
        with patch('pathlib.Path.mkdir',new=fail_home),self.assertRaisesRegex(OSError,'injected'):self.start()
        first=self.m.status(next(self.m.jobs.glob('*.json')).stem)
        self.assertEqual(first['state'],'cleanup_blocked');self.assertIn('reservation',first)
        self.start('healthy')
        self.m._supervise(first['job_id']);result=self.m.status(first['job_id'])
        self.assertEqual(result['state'],'failed');self.assertTrue(result['vm_deleted']);self.assertTrue(result['runtime_deleted'])
        self.assertFalse(result['evidence_complete']);self.assertTrue(result['evidence_verified']);self.assertIn('reservation_released_at',result)
        self.assertEqual(snapshot(self.m,self.m.settings())['held_job_count'],1)
    def test_over_budget_job_does_not_cancel_healthy_job(self):
        broken=self.start('broken');healthy=self.start('healthy')
        def usage(m,x,reservation):return (reservation['runtime_bytes']+1 if x['job_id']==broken['job_id'] else 0,0)
        with patch('lvm.resources.usage',side_effect=usage):
            with self.assertRaisesRegex(ManagerError,'This job exceeded'):self.m.check_job_budget(broken)
            self.m.check_job_budget(healthy)
        self.assertEqual(self.m.status(healthy['job_id'])['state'],'queued')
    def test_deletion_flags_cannot_release_a_still_existing_runtime(self):
        first=self.start();first.update(state='complete',vm_deleted=True,runtime_deleted=True,evidence_verified=True);atomic(self.m.path(first['job_id']),first)
        with self.assertRaisesRegex(ManagerError,'unaccounted'):self.start()
    def test_sweeper_cannot_observe_in_progress_admission(self):
        import threading,time
        published=threading.Event();release=threading.Event();swept=threading.Event()
        def spawn(job):
            if threading.current_thread().name=='creator':
                published.set();release.wait(4)
            else:swept.set()
        with patch.object(self.m,'spawn',side_effect=spawn):
            creator=threading.Thread(target=self.start,name='creator');creator.start()
            self.assertTrue(published.wait(3))
            sweeper=threading.Thread(target=self.m.sweep,name='sweeper');sweeper.start()
            try:self.assertFalse(swept.wait(.2))
            finally:release.set();creator.join(5);sweeper.join(5)
            self.assertTrue(swept.is_set())

    def test_identity_collision_never_overwrites_historical_receipt(self):
        from types import SimpleNamespace
        path=self.m.path('aaaaaaaa');record={'manager_id':self.m.owner['manager_id'],'job_id':'aaaaaaaa','state':'complete',
             'vm_deleted':True,'runtime_deleted':True,'evidence_verified':True,'proof':'preserve'}
        atomic(path,record);original=path.read_bytes()
        with patch('lvm.manager.uuid.uuid4',return_value=SimpleNamespace(hex='a'*32)),self.assertRaisesRegex(ManagerError,'unused job identity'):
            self.start()
        self.assertEqual(path.read_bytes(),original)

    def test_fixed_adapter_runtime_path_cannot_be_shared_by_two_jobs(self):
        from lvm import surfaces_connected as connected
        import time
        self.settings(max_vcpus=16,max_memory_gib=24,storage_budget_gib=128)
        with patch('lvm.resources.host_capacity',return_value=dict(HOST,logical_cpus=32)),patch.object(connected,'STATE_ROOT',self.m.root/'external-fixture'):
            def workload():
                payload=self.m.root/'payload.tar';payload.write_bytes(b'fixture')
                return payload,{'runner':'surfaces-connected-observer','state_root':str(connected.STATE_ROOT),'absolute_deadline':time.time()+3600}
            self.m.start('fixed-one',workload=workload())
            with self.assertRaisesRegex(ManagerError,'already reserved'):self.m.start('fixed-two',workload=workload())

    def test_reservation_cannot_be_shrunk_below_guest_dimensions(self):
        first=self.start();first['reservation']['runtime_bytes']=1;atomic(self.m.path(first['job_id']),first)
        with self.assertRaisesRegex(ManagerError,'dimensions changed'):self.start()
    def test_committed_budget_keeps_full_claim_when_runtime_shrinks(self):
        first=self.start();path=self.m.runtime/first['job_id']/'transient';path.write_bytes(b'x'*1024*1024)
        before=snapshot(self.m,self.m.settings());path.unlink();after=snapshot(self.m,self.m.settings())
        self.assertEqual(before['committed_storage_bytes'],after['committed_storage_bytes'])

    def test_policy_upgrade_preserves_identity_pins_and_failed_job(self):
        previous={k:v for k,v in self.m.settings().items() if k not in DEFAULTS or k in ('disk_gib','memory_gib','evidence_mib','free_floor_gib')}
        previous['max_concurrent']=1;previous['storage_budget_gib']=20;atomic(self.m.root/'settings.json',previous)
        owner=(self.m.state/'owner.json').read_bytes()
        result=configure(self.m,dry_run=True);self.assertTrue(result['changed'])
        self.assertEqual(self.m.root.joinpath('settings.json').read_text(),json.dumps(previous,indent=2)+'\n')
        result=configure(self.m);self.assertEqual(self.m.settings()['max_concurrent'],3)
        self.assertEqual(self.m.settings()['limactl_sha256'],previous['limactl_sha256'])
        self.assertEqual((self.m.state/'owner.json').read_bytes(),owner)
        self.assertTrue(pathlib.Path(result['backup']).is_file())
        self.assertFalse(configure(self.m)['changed'])
    def test_smaller_mac_policy_is_bounded_to_actual_cpu_and_memory(self):
        policy=configured_policy({},dict(HOST,logical_cpus=8,memory_bytes=16*GIB))
        self.assertLessEqual(policy['max_vcpus'],6);self.assertLessEqual(policy['max_memory_gib'],12)
        self.assertEqual(policy['host_memory_floor_gib'],4)
    def test_policy_upgrade_refuses_live_jobs(self):
        self.start()
        with self.assertRaisesRegex(ManagerError,'Stop active jobs'):configure(self.m)

    def test_guest_configuration_uses_its_frozen_reservation(self):
        first=self.start();self.settings(cpus=4,memory_gib=4,disk_gib=20)
        config=self.m.config(first);self.assertEqual(config['cpus'],2);self.assertEqual(config['memory'],'2GiB');self.assertEqual(config['disk'],'12GiB')

if __name__=='__main__':unittest.main()
