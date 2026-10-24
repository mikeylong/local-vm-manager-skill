import hashlib,importlib.util,json,pathlib,tarfile,tempfile,unittest
from unittest.mock import Mock,patch
from test_resources import fixture,HOST
from lvm.manager import ManagerError,atomic
from lvm.resources import GIB
from lvm.playwright import recovery_seal_command

class ExportBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir='/private/tmp',prefix='lvm-export-budget-');self.root=pathlib.Path(self.temp.name).resolve();self.m=fixture(self.root)
        self.host=patch('lvm.resources.host_capacity',return_value=HOST);self.host.start()
        with patch.object(self.m,'spawn'):self.x=self.m.start('failed-export')
        self.x.update(state='cleanup_blocked',profile='linux-playwright',workload_started=True,supervisor_pid=99999999,supervisor_start='absent',error='original failure')
        atomic(self.m.path(self.x['job_id']),self.x)
    def tearDown(self):self.host.stop();self.temp.cleanup()
    def test_job_only_limit_and_reservation_change(self):
        original_settings=(self.root/'settings.json').read_bytes()
        with patch.object(self.m,'_supervise') as supervise:
            result=self.m.recover_export(self.x['job_id'],352)
        self.assertEqual(result['approved_export_limit_bytes'],352*1024**2)
        self.assertEqual(result['reservation']['evidence_bytes'],(3*352+16)*1024**2)
        self.assertEqual(result['error'],'original failure');self.assertEqual((self.root/'settings.json').read_bytes(),original_settings)
        supervise.assert_called_once_with(self.x['job_id'])
    def test_insufficient_capacity_preserves_original_receipt(self):
        original=self.m.path(self.x['job_id']).read_bytes()
        capacity={'unknown_state':[],'committed_storage_bytes':80*GIB-1,'physical_reservation_headroom_bytes':0,'host':HOST}
        with patch('lvm.resources.snapshot',return_value=capacity),patch.object(self.m,'_supervise') as supervise:
            with self.assertRaisesRegex(ManagerError,'shared storage budget'):self.m.recover_export(self.x['job_id'],352)
        self.assertEqual(self.m.path(self.x['job_id']).read_bytes(),original);supervise.assert_not_called()
    def test_active_or_invalid_cap_rejected(self):
        for value in (0,250,513):
            with self.assertRaises(ManagerError):self.m.recover_export(self.x['job_id'],value)
        self.x['state']='testing';atomic(self.m.path(self.x['job_id']),self.x)
        with self.assertRaisesRegex(ManagerError,'blocked'):self.m.recover_export(self.x['job_id'],352)
    def test_invalid_job_id_creates_no_lock_file(self):
        before=set(self.root.rglob('*'))
        with self.assertRaisesRegex(ManagerError,'Invalid job ID'):self.m.recover_export('../outside',352)
        self.assertEqual(set(self.root.rglob('*')),before)
    def test_recovery_never_reduces_an_existing_larger_reservation(self):
        self.x['reservation']['evidence_bytes']=2*GIB;atomic(self.m.path(self.x['job_id']),self.x)
        with patch.object(self.m,'_supervise'):
            result=self.m.recover_export(self.x['job_id'],352)
        self.assertEqual(result['reservation']['evidence_bytes'],2*GIB)
    def test_symlinked_job_evidence_is_rejected(self):
        import shutil
        evidence=self.m.evidence/self.x['job_id'];shutil.rmtree(evidence)
        outside=self.root/'outside';outside.mkdir();evidence.symlink_to(outside)
        with self.assertRaisesRegex(ManagerError,'evidence directory'):self.m.recover_export(self.x['job_id'],352)

    def test_seal_command_cannot_run_tests_and_pins_original_helper(self):
        command=recovery_seal_command(352*1024**2,'a'*64)
        self.assertIn('m.seal()',command[-1]);self.assertNotIn('m.run(',command[-1]);self.assertIn('a'*64,command[-1])
    def test_explicit_sealing_retains_all_duplicate_paths_and_bytes(self):
        spec=importlib.util.spec_from_file_location('guest_fixture',pathlib.Path(__file__).resolve().parents[1]/'lvm/guest_playwright.py')
        guest=importlib.util.module_from_spec(spec);spec.loader.exec_module(guest)
        out=self.root/'evidence-fixture';out.mkdir();work=self.root/'work-fixture';work.mkdir()
        (out/'report').mkdir();(out/'results').mkdir();(out/'result.json').write_text('{"passed": false}')
        body=b'unchanged trace'*200
        (out/'report/trace.zip').write_bytes(body);(out/'results/trace.zip').write_bytes(body)
        with patch.object(guest,'OUT',out),patch.object(guest,'WORK',work),patch.object(guest,'LIMIT',1):
            with self.assertRaisesRegex(RuntimeError,'allowance'):guest.seal()
            guest.LIMIT=10000;receipt=guest.seal()
        with tarfile.open(receipt['path']) as archive:
            names=archive.getnames();self.assertIn('report/trace.zip',names);self.assertIn('results/trace.zip',names)
            self.assertEqual(archive.extractfile('report/trace.zip').read(),body);self.assertEqual(archive.extractfile('results/trace.zip').read(),body)
        self.assertEqual((out/'report/trace.zip').read_bytes(),body)
        self.assertFalse(json.loads((out/'result.json').read_text())['passed'])

if __name__=='__main__':unittest.main()
