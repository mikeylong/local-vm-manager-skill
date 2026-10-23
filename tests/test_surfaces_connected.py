import hashlib,inspect,json,pathlib,tempfile,time,unittest
import contextlib
from types import SimpleNamespace
from unittest.mock import Mock,patch

from lvm.manager import Manager,ManagerError
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
                self.assertNotIn('command',spec);self.assertEqual(len(spec['manager_sources']),4)
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
            root=pathlib.Path(raw).resolve();root.joinpath('settings.json').write_text(json.dumps({'max_concurrent':1,'disk_gib':12,'free_floor_gib':10,'storage_budget_gib':20}))
            manager=Manager(root).init()
            payload=root/'payload.tar';payload.write_bytes(b'x')
            workload=(payload,{'runner':'surfaces-connected-observer','absolute_deadline':time.time()+3600})
            usage=SimpleNamespace(total=100*1024**3,used=51*1024**3,free=49*1024**3)
            with patch('lvm.manager.shutil.disk_usage',return_value=usage):
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


if __name__=='__main__':unittest.main()
