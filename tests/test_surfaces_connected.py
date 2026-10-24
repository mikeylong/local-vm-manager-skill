import hashlib,inspect,json,pathlib,tempfile,time,unittest
import contextlib
from types import SimpleNamespace
from unittest.mock import Mock,patch

from lvm.manager import Manager,ManagerError
from lvm.resources import DEFAULTS,GIB
from lvm import surfaces_connected as connected


class SurfacesConnectedObserverTests(unittest.TestCase):
    @contextlib.contextmanager
    def bound_fixture(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp',prefix='lvm-bound-') as raw, contextlib.ExitStack() as stack:
            root=pathlib.Path(raw).resolve();task=root/'task';task.mkdir();project=root/'project';project.mkdir()
            paths={name:task/name for name in connected.PATH_KEYS}
            paths.update(TASK_ROOT=task,PROJECT=project,STATE_ROOT=project/'fresh-runtime',TSX_ROOT=project/'tsx')
            paths['TSX_ROOT'].mkdir();(paths['TSX_ROOT']/'loader.mjs').write_text('loader fixture')
            for name in ('NODE','PREPARE','RUNNER','EVIDENCE_BUDGET','BINDING'):paths[name].write_text(name)
            rows=[{'path':str(paths[name]),'bytes':paths[name].stat().st_size,'sha256':hashlib.sha256(paths[name].read_bytes()).hexdigest()} for name in ('PREPARE','RUNNER','EVIDENCE_BUDGET')]
            now=int(time.time()*1000)
            launch={'preparationDeadline':now+60000,'workDeadline':now+120000,'absoluteDeadline':now+180000,
                    'taskRoot':str(task),'stateRoot':str(paths['STATE_ROOT']),'reviewRequiredBeforeDispatch':True,
                    'readyForProduction':False,'digest':'fixture-digest','sourcePins':rows,
                    'limits':{'evidenceBytes':64*1024**2,'perVmAggregateAllocatedBytes':20*1024**3,'baseCount':1,'executionCount':1}}
            paths['LAUNCH'].write_text(json.dumps(launch));raw_launch=paths['LAUNCH'].read_bytes()
            paths['EXPECTATION'].write_text(json.dumps({'rawBytes':len(raw_launch),'rawSha256':hashlib.sha256(raw_launch).hexdigest(),'canonicalDigest':launch['digest'],'launch':launch}))
            fixed={key:{'path':str(paths[name]),'bytes':paths[name].stat().st_size,'sha256':hashlib.sha256(paths[name].read_bytes()).hexdigest()} for key,name in (('launch','LAUNCH'),('expectation','EXPECTATION'),('node','NODE'))}
            config=root/'binding.json';config.write_text(json.dumps({'schema_version':1,'paths':{k:str(v) for k,v in paths.items()},'fixed':fixed,'tsx_tree_digest':connected.tree_digest(paths['TSX_ROOT'])}))
            stack.enter_context(patch.object(connected,'CONFIG_PATH',config))
            for key,value in connected.load_binding(config).items():stack.enter_context(patch.object(connected,key,value))
            yield root,launch,config

    def test_reviewed_contract_checks_bound_inputs_and_deadline(self):
        with self.bound_fixture() as (_,launch,_):
            self.assertEqual(connected.validate_contract()['digest'],launch['digest'])
            with self.assertRaisesRegex(ManagerError,'deadline expired'):
                connected.validate_contract(now_ms=launch['absoluteDeadline']+1)
            connected.PREPARE.write_text('changed source')
            with self.assertRaisesRegex(ManagerError,'input changed'):connected.validate_contract()

    def test_snapshot_has_no_arbitrary_source_or_command_input(self):
        self.assertEqual(list(inspect.signature(connected.start).parameters),['m','label','owner_pid'])
        with self.bound_fixture() as (root,_,config):
            fake=SimpleNamespace(root=root,code_root=pathlib.Path(__file__).resolve().parents[1])
            payload,spec=connected.snapshot(fake)
            try:
                self.assertEqual(spec['runner'],'surfaces-connected-observer')
                self.assertEqual(spec['fixtures'],['npm-vite-dist','npm-vite-nested-output'])
                self.assertNotIn('command',spec);self.assertEqual(len(spec['manager_sources']),5)
                connected.verify_binding({'workload':spec})
                config.write_text(config.read_text()+'\n')
                with self.assertRaisesRegex(ManagerError,'binding changed'):connected.verify_binding({'workload':spec})
            finally:payload.unlink(missing_ok=True)

    def test_unconfigured_binding_cannot_dispatch(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as raw:
            with patch.object(connected,'CONFIG_PATH',pathlib.Path(raw)/'missing'):
                with self.assertRaisesRegex(ManagerError,'not configured'):connected.validate_contract()

    def test_state_without_reviewed_runtime_pointer_blocks_cleanup_commands(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp',prefix='lvm-surfaces-state-') as raw:
            state=pathlib.Path(raw).resolve()
            with patch.object(connected,'STATE_ROOT',state):
                with self.assertRaisesRegex(ManagerError,'without its reviewed runtime pointer'):
                    connected.lima(SimpleNamespace(),{},['list','--json'])

    def test_special_admission_reserves_both_twenty_gib_backings(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp',prefix='lvm-surfaces-manager-') as raw:
            root=pathlib.Path(raw).resolve();root.joinpath('settings.json').write_text(json.dumps({**DEFAULTS}))
            manager=Manager(root).init()
            payload=root/'payload.tar';payload.write_bytes(b'x')
            workload=(payload,{'runner':'surfaces-connected-observer','absolute_deadline':time.time()+3600,'state_root':str(connected.STATE_ROOT)})
            usage=SimpleNamespace(total=100*1024**3,used=51*1024**3,free=49*1024**3)
            with patch('lvm.resources.host_capacity',return_value={'logical_cpus':16,'memory_bytes':128*GIB,'available_memory_bytes':96*GIB,'disk_free_bytes':49*GIB}):
                with self.assertRaisesRegex(ManagerError,'free-space floor'):
                    manager.start(workload=workload)

    def test_manager_evidence_is_immutable_across_recovery_export(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp',prefix='lvm-surfaces-evidence-') as raw:
            path=pathlib.Path(raw)/'result.json';value={'passed':False,'error':'fixed'}
            connected.write_manager_json(path,value)
            connected.write_manager_json(path,value)
            with self.assertRaisesRegex(ManagerError,'changed across export recovery'):
                connected.write_manager_json(path,{'passed':True})

    def test_manager_routes_fixed_workload_through_special_supervision(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp',prefix='lvm-surfaces-route-') as raw:
            root=pathlib.Path(raw).resolve();root.joinpath('settings.json').write_text(json.dumps({'max_concurrent':1}))
            manager=Manager(root).init();job='1234abcd';runtime=manager.runtime/job;runtime.mkdir();home=runtime/'l';home.mkdir();(manager.evidence/job).mkdir()
            record={'manager_id':manager.owner['manager_id'],'job_id':job,'device':home.stat().st_dev,'inode':home.stat().st_ino,
                    'state':'queued','owner_pid':1,'owner_start':'fixed','deadline':time.time()+60,'hard_deadline':time.time()+120,
                    'captures':[],'exports':{},'runtime_path':str(runtime),'evidence_path':str(manager.evidence/job),
                    'workload':{'runner':'surfaces-connected-observer'},'schema_version':1}
            from lvm.manager import atomic
            atomic(runtime/'owner.json',{k:record[k] for k in ('manager_id','job_id','device','inode')});atomic(manager.path(job),record)
            run=Mock(side_effect=lambda m,x:x.update(test_result={'passed':True}))
            export=Mock();cleanup=Mock(side_effect=lambda m,x:x.update(vm_deleted=True,runtime_deleted=True,remaining_runtime_bytes=0))
            manager.abort_reason=Mock(return_value=None)
            with patch('lvm.manager.identity',return_value='supervisor'),patch.object(connected,'run',run),patch.object(connected,'export',export),patch.object(connected,'cleanup',cleanup):
                manager._supervise(job)
            terminal=manager.status(job)
            self.assertEqual(terminal['state'],'complete');run.assert_called_once();export.assert_called_once();cleanup.assert_called_once()

    @contextlib.contextmanager
    def cleanup_fixture(self):
        with self.bound_fixture() as (root,_,config):
            store=root/'manager';store.mkdir();m=Manager(store).init()
            (store/'settings.json').write_text(json.dumps(DEFAULTS));job='8765abcd'
            state=connected.STATE_ROOT;state.mkdir(mode=0o700)
            runtime=m.runtime/job;runtime.mkdir();home=runtime/'l';home.mkdir();evidence=m.evidence/job;evidence.mkdir()
            proof=connected.TASK_ROOT/'proof.json';proof.write_text('original evidence')
            manifest={'schema_version':1,'kind':'surfaces-connected-observer-external-evidence/1','verified':True,
                      'files':[{'path':str(proof),'bytes':proof.stat().st_size,'sha256':hashlib.sha256(proof.read_bytes()).hexdigest()}]}
            mp=evidence/'external-evidence-manifest.json';mp.write_text(json.dumps(manifest))
            rp=evidence/'result.json';rp.write_text(json.dumps({'passed':False,'error':'original preparation failure'}))
            x={'manager_id':m.owner['manager_id'],'job_id':job,'device':home.stat().st_dev,'inode':home.stat().st_ino,
               'state':'cleanup_blocked','owner_pid':1,'owner_start':'original','deadline':0,'hard_deadline':0,'recovery_attempts':3,
               'profile':'surfaces-connected-observer','captures':[],'exports':{},'runtime_path':str(runtime),'evidence_path':str(evidence),
               'error':'original preparation failure','cleanup_error':'original cleanup blocker',
               'workload':{'runner':'surfaces-connected-observer','state_root':str(state),'adapter_binding':connected.binding_identity()},
               'surfaces_state_intent':{'path':str(state),'expected_absent_at_admission':True},
               'surfaces_state_identity':{'device':state.stat().st_dev,'inode':state.stat().st_ino}}
            for f in (mp,rp):x['exports'][f.name]={'bytes':f.stat().st_size,'sha256':hashlib.sha256(f.read_bytes()).hexdigest()}
            from lvm.manager import atomic
            atomic(runtime/'owner.json',{k:x[k] for k in ('manager_id','job_id','device','inode')});atomic(m.path(job),x)
            m.holders=Mock(return_value=[])
            yield m,x,mp,rp,proof

    def test_original_manifest_is_reused_after_later_evidence_is_added(self):
        with self.cleanup_fixture() as (m,x,manifest,result,proof):
            original=manifest.read_bytes();(connected.TASK_ROOT/'later.json').write_text('later')
            connected.export(m,x)
            self.assertEqual(manifest.read_bytes(),original);self.assertFalse(json.loads(result.read_text())['passed'])
            proof.write_text('tampered')
            with self.assertRaisesRegex(ManagerError,'External Surfaces evidence changed'):connected.export(m,x)

    def test_tampered_manifest_is_rejected(self):
        with self.cleanup_fixture() as (m,x,manifest,_,_):
            manifest.write_text(manifest.read_text()+' ')
            with self.assertRaisesRegex(ManagerError,'cannot be authenticated'):connected.export(m,x)

    def test_empty_pre_pointer_cleanup_is_scoped_and_never_reruns_fixture(self):
        with self.cleanup_fixture() as (m,x,manifest,result,_):
            original=(manifest.read_bytes(),result.read_bytes())
            with patch.object(connected,'run') as run,patch.object(connected,'lima') as lima:
                m._supervise(x['job_id'])
                run.assert_not_called();lima.assert_not_called()
            end=m.status(x['job_id'])
            self.assertEqual(end['state'],'failed');self.assertFalse(end['evidence_complete'])
            for key in ('vm_deleted','runtime_deleted','evidence_verified'):self.assertTrue(end[key])
            self.assertIn('original cleanup blocker',str(end['cleanup_failures']))
            self.assertEqual(end['error'],'original preparation failure')
            self.assertEqual((manifest.read_bytes(),result.read_bytes()),original)
            self.assertFalse(connected.STATE_ROOT.exists());self.assertFalse((m.runtime/x['job_id']).exists())

    def test_recovery_after_both_directories_deleted_is_idempotent(self):
        with self.cleanup_fixture() as (m,x,_,_,_):
            connected.cleanup(m,x)
            connected.cleanup(m,x)
            self.assertTrue(x['vm_deleted']);self.assertTrue(x['runtime_deleted'])

    def test_running_guest_is_stopped_before_rejecting_open_handles(self):
        for lingering in (False,True):
            with self.subTest(lingering=lingering),self.cleanup_fixture() as (m,x,manifest,result,proof):
                pointer=connected.STATE_ROOT/'private/runtime-preparation/current.json'
                pointer.parent.mkdir(parents=True);pointer.write_text('{}')
                live={'execution':'Running'};events=[]
                def holders(path):
                    if path!=connected.STATE_ROOT:return []
                    events.append('handles')
                    return ['p123'] if live or lingering else []
                def command(manager,record,args,allow_fail=False):
                    events.append(tuple(args))
                    if args==['list','--json']:
                        return 0,'\n'.join(json.dumps({'name':name,'status':status}) for name,status in live.items())
                    if args==['stop','--force','execution']:live['execution']='Stopped';return 0,''
                    if args==['delete','execution']:live.pop('execution');return 0,''
                    self.fail('Unexpected lifecycle command: '+str(args))
                m.holders=Mock(side_effect=holders);original=(manifest.read_bytes(),result.read_bytes(),proof.read_bytes())
                with patch.object(connected,'lima',side_effect=command):
                    if lingering:
                        with self.assertRaisesRegex(ManagerError,'open handles'):connected.cleanup(m,x)
                        self.assertTrue(connected.STATE_ROOT.exists());self.assertTrue((m.runtime/x['job_id']).exists())
                    else:
                        connected.cleanup(m,x)
                        self.assertTrue(x['vm_deleted']);self.assertTrue(x['runtime_deleted'])
                self.assertLess(events.index(('stop','--force','execution')),events.index('handles'))
                self.assertEqual((manifest.read_bytes(),result.read_bytes(),proof.read_bytes()),original)

    def test_prepared_state_identity_mismatch_blocks_shutdown(self):
        with self.cleanup_fixture() as (m,x,_,_,_):
            pointer=connected.STATE_ROOT/'private/runtime-preparation/current.json'
            pointer.parent.mkdir(parents=True);pointer.write_text('{}')
            x['surfaces_state_identity']['inode']+=1
            with patch.object(connected,'lima') as lima,self.assertRaisesRegex(ManagerError,'identity changed'):
                connected.cleanup(m,x)
            lima.assert_not_called();self.assertTrue(connected.STATE_ROOT.exists())

    def test_unsafe_pre_pointer_state_is_never_deleted(self):
        for defect in ('nonempty','replaced','intent','holders','symlink','mode'):
            with self.subTest(defect=defect),self.cleanup_fixture() as (m,x,_,_,_):
                state=connected.STATE_ROOT
                if defect=='nonempty':(state/'keep').write_text('keep')
                if defect=='replaced':x['surfaces_state_identity']['inode']+=1
                if defect=='intent':x['surfaces_state_intent']={}
                if defect=='holders':m.holders=Mock(return_value=['p123'])
                if defect=='symlink':state.rename(state.with_name('original'));state.symlink_to(state.with_name('original'))
                if defect=='mode':state.chmod(0o755)
                with patch.object(connected,'lima') as lima,self.assertRaises(ManagerError):connected.cleanup(m,x)
                lima.assert_not_called();self.assertTrue(state.exists())


if __name__=='__main__':unittest.main()
