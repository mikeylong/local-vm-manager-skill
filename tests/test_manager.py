import json,os,pathlib,tempfile,unittest
from lvm.manager import Manager,ManagerError,atomic,identity
from lvm.resources import DEFAULTS

class OwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir='/private/tmp',prefix='lvm-test-')
        self.root=pathlib.Path(self.temp.name).resolve();self.m=Manager(self.root).init();atomic(self.root/'settings.json',DEFAULTS)
    def tearDown(self):self.temp.cleanup()
    def owned(self):
        j='1234abcd';d=self.m.runtime/j;d.mkdir();h=d/'l';h.mkdir()
        x={'manager_id':self.m.owner['manager_id'],'job_id':j,'device':h.stat().st_dev,'inode':h.stat().st_ino}
        atomic(d/'owner.json',x);return x,h
    def test_unknown_and_traversal_cannot_target_external_vms(self):
        for key in ('minerva','../minerva','/Users/fixture/.tart','ffffffff'):
            with self.subTest(key=key),self.assertRaises(ManagerError):self.m.status(key)
    def test_replaced_home_is_rejected_before_any_lima_command(self):
        x,h=self.owned();h.rename(h.parent/'retained');h.mkdir()
        with self.assertRaises(ManagerError):self.m.home(x)
    def test_symlinked_runtime_is_rejected_before_any_lima_command(self):
        x,h=self.owned();h.rmdir();outside=self.root/'untouched';outside.mkdir();(outside/'keep').write_text('preserve')
        h.symlink_to(outside,target_is_directory=True)
        with self.assertRaises(ManagerError):self.m.cleanup(x)
        self.assertEqual((outside/'keep').read_text(),'preserve')
    def test_foreign_marker_is_rejected(self):
        x,h=self.owned();atomic(h.parent/'owner.json',{**x,'manager_id':'foreign'})
        with self.assertRaises(ManagerError):self.m.cleanup(x)
    def test_foreign_instance_in_own_home_blocks_deletion(self):
        x,h=self.owned();(h/'unrecognized').mkdir()
        with self.assertRaises(ManagerError):self.m.cleanup(x)
        self.assertTrue((h/'unrecognized').exists())
    def test_graceful_stop_timeout_uses_owned_force_stop_before_deletion(self):
        from unittest.mock import Mock
        x,h=self.owned();(h/'guest').mkdir()
        def command(record,args,**kwargs):
            self.assertEqual(self.m.home(record),h)
            if args==['stop','guest']:raise ManagerError('Lima command timed out: stop')
            if args==['delete','guest']:(h/'guest').rmdir()
            return 0
        self.m.command=Mock(side_effect=command);self.m.holders=Mock(return_value=[])
        self.m.cleanup(x)
        self.assertEqual([c.args[1] for c in self.m.command.call_args_list],[['stop','guest'],['stop','--force','guest'],['delete','guest']])
        self.assertTrue(x['graceful_stop_timed_out']);self.assertTrue(x['runtime_deleted']);self.assertFalse(h.parent.exists())
    def test_timeout_fallback_still_refuses_open_runtime_files(self):
        from unittest.mock import Mock
        x,h=self.owned();(h/'guest').mkdir()
        self.m.command=Mock(side_effect=[ManagerError('Lima command timed out: stop'),0])
        self.m.holders=Mock(side_effect=[['before'],['still open'],['still open']])
        with self.assertRaisesRegex(ManagerError,'open handles'):self.m.cleanup(x)
        self.assertTrue((h/'guest').exists());self.assertEqual(self.m.command.call_count,2)
    def test_orphan_cleanup_rejects_other_hostagent_namespaces(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        x,h=self.owned()
        with patch('lvm.manager.identity',return_value='same start'),patch('lvm.manager.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=str(self.m.lima)+' hostagent --pidfile /foreign/ha.pid --socket /foreign/ha.sock guest')),patch('lvm.manager.os.kill') as kill:
            self.m.stop_orphaned_hostagents(x,h,['p12345'])
            kill.assert_not_called()
    def test_orphan_cleanup_refuses_reused_pid(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        x,h=self.owned()
        cmd=f'{self.m.lima} hostagent --pidfile {h}/guest/ha.pid --socket {h}/guest/ha.sock guest'
        with patch('lvm.manager.identity',side_effect=['original','different']),patch('lvm.manager.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=cmd)),patch('lvm.manager.os.kill') as kill:
            self.m.stop_orphaned_hostagents(x,h,['p12345'])
            kill.assert_not_called()
    def test_orphan_cleanup_signals_only_exact_owned_hostagent(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        import signal
        x,h=self.owned()
        cmd=f'{self.m.lima} hostagent --pidfile {h}/guest/ha.pid --socket {h}/guest/ha.sock guest'
        with patch('lvm.manager.identity',side_effect=['same start','same start',None,None]),patch('lvm.manager.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=cmd)),patch('lvm.manager.os.kill') as kill:
            self.m.stop_orphaned_hostagents(x,h,['p12345'])
            kill.assert_called_once_with(12345,signal.SIGTERM)
        self.assertEqual(x['orphaned_hostagents_stopped'][0]['pid'],12345)
    def test_owner_process_death_and_lease_expiry_are_distinct(self):
        x={'job_id':'1234abcd','owner_pid':99999999,'owner_start':'never','deadline':0,'hard_deadline':0}
        self.assertEqual(self.m.abort_reason(x),'agent_crash')
        x.update(owner_pid=os.getpid(),owner_start=identity(os.getpid()))
        self.assertEqual(self.m.abort_reason(x),'lease_expired')
    def test_renewal_cannot_exceed_hard_deadline(self):
        x={'job_id':'1234abcd','owner_pid':os.getpid(),'owner_start':identity(os.getpid()),'deadline':1,'hard_deadline':2}
        atomic(self.m.state/'1234abcd.lease',{'deadline':99999999999})
        self.assertEqual(self.m.abort_reason(x),'lease_expired');self.assertEqual(x['deadline'],2)
    def test_unknown_job_record_blocks_admission(self):
        atomic(self.m.path('1234abcd'),{'state':'cleanup_blocked'})
        with self.assertRaises((ManagerError,KeyError)):self.m.start()
        self.assertEqual(list(self.m.runtime.iterdir()),[])
    def test_no_icloud_root(self):
        with self.assertRaises(ManagerError):Manager('/Users/fixture/Documents/Codex/forbidden')
    def test_recovery_after_recorded_deletion_is_idempotent(self):
        x={'job_id':'1234abcd','deletion_intent':True};self.m.cleanup(x)
        self.assertTrue(x['vm_deleted']);self.assertTrue(x['runtime_deleted'])

if __name__=='__main__':unittest.main()
