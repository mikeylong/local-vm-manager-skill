import contextlib,importlib.util,io,json,pathlib,shutil,tempfile,unittest
from unittest.mock import Mock,patch
from test_resources import fixture,HOST
from lvm.manager import ManagerError,atomic,sha
from lvm.resources import GIB,configure
from lvm.playwright import export,evidence_probe_command

class SoftEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir='/private/tmp',prefix='lvm-soft-')
        self.root=pathlib.Path(self.temp.name).resolve();self.m=fixture(self.root)
        self.host=patch('lvm.resources.host_capacity',return_value=HOST);self.host.start()
        with patch.object(self.m,'spawn'):self.x=self.m.start('soft-evidence')
    def tearDown(self):self.host.stop();self.temp.cleanup()
    def blocked(self):
        self.x.update(state='cleanup_blocked',profile='linux-playwright',workload_started=True)
        atomic(self.m.path(self.x['job_id']),self.x)
    def test_default_reserves_one_gib_without_changing_guest_dimensions(self):
        self.assertEqual(self.m.settings()['evidence_mib'],1024)
        self.assertEqual(self.x['reservation']['evidence_bytes'],3*GIB+16*1024**2)
        self.assertEqual(self.x['reservation']['guest_disk_gib'],12)
        self.assertEqual(self.x['reservation']['memory_bytes'],2*GIB)
    def test_soft_threshold_warns_and_grows_only_one_job(self):
        with patch.object(self.m,'spawn'):other=self.m.start('other')
        original=self.m.path(other['job_id']).read_bytes();settings=(self.root/'settings.json').read_bytes()
        self.m.reserve_evidence(self.x,GIB+42,measured_bytes=GIB+42)
        self.assertEqual(self.x['approved_export_limit_bytes'],GIB+42)
        self.assertEqual(self.x['evidence_warning']['code'],'soft_evidence_allowance_reached')
        self.assertEqual(self.x['reservation']['evidence_bytes'],3*(GIB+42)+16*1024**2)
        self.assertEqual(self.m.path(other['job_id']).read_bytes(),original)
        self.assertEqual((self.root/'settings.json').read_bytes(),settings)
    def test_reservation_never_shrinks(self):
        self.m.reserve_evidence(self.x,2*GIB)
        reserved=self.x['reservation']['evidence_bytes']
        self.m.reserve_evidence(self.x,1)
        self.assertEqual(self.x['reservation']['evidence_bytes'],reserved)
    def test_capacity_failure_keeps_receipt_and_guest(self):
        before=self.m.path(self.x['job_id']).read_bytes();home=self.m.home(self.x)
        for capacity,message in [({'unknown_state':[],'committed_storage_bytes':80*GIB,'physical_reservation_headroom_bytes':0,'host':HOST},'shared storage'),({'unknown_state':[],'committed_storage_bytes':0,'physical_reservation_headroom_bytes':0,'host':dict(HOST,disk_free_bytes=1)},'free-space floor')]:
            with patch('lvm.resources.snapshot',return_value=capacity),self.assertRaisesRegex(ManagerError,message):
                self.m.reserve_evidence(self.x,2*GIB,measured_bytes=2*GIB)
            self.assertEqual(self.m.path(self.x['job_id']).read_bytes(),before);self.assertTrue(home.exists())
    def test_recovery_without_override_uses_existing_job(self):
        self.blocked()
        with patch.object(self.m,'_supervise') as supervise,patch.object(self.m,'start') as start:
            self.m.recover_export(self.x['job_id'])
        supervise.assert_called_once_with(self.x['job_id']);start.assert_not_called()
    def test_recovery_allows_more_than_old_512_mib_ceiling(self):
        self.blocked()
        with patch.object(self.m,'_supervise'):
            result=self.m.recover_export(self.x['job_id'],1536)
        self.assertEqual(result['approved_export_limit_bytes'],1536*1024**2)
    def test_policy_upgrade_preserves_runtime_and_shared_limits(self):
        policy=self.m.settings();policy.update(evidence_mib=250,max_concurrent=2,storage_budget_gib=70,max_vcpus=6)
        atomic(self.root/'settings.json',policy);atomic(self.m.state/'resource-policy.json',{})
        self.x.update(state='complete',vm_deleted=True,runtime_deleted=True,evidence_verified=True)
        shutil.rmtree(self.m.runtime/self.x['job_id']);atomic(self.m.path(self.x['job_id']),self.x)
        configure(self.m);after=self.m.settings()
        self.assertEqual(after['evidence_mib'],1024);self.assertEqual(after['evidence_policy_version'],1)
        for key in set(policy)-{'evidence_mib'}:self.assertEqual(after[key],policy[key])
    def test_export_grows_before_copy_and_preserves_duplicate_diagnostics(self):
        self.blocked();policy=self.m.settings();policy['evidence_mib']=1;atomic(self.root/'settings.json',policy)
        guest_path=pathlib.Path(__file__).resolve().parents[1]/'lvm/guest_playwright.py'
        spec=importlib.util.spec_from_file_location('soft_guest',guest_path);guest=importlib.util.module_from_spec(spec);spec.loader.exec_module(guest)
        guest.OUT=self.root/'guest-out';guest.WORK=self.root/'guest-work';guest.OUT.mkdir();guest.WORK.mkdir()
        for folder in ('results','report'):(guest.OUT/folder).mkdir()
        body=b'evidence'*100000
        for folder in ('results','report'):(guest.OUT/folder/'trace.zip').write_bytes(body)
        result=b'{"passed":false,"error":"original test failure"}'
        (guest.OUT/'result.json').write_bytes(result)
        self.x['guest_helper_sha256']=sha(guest_path);atomic(self.m.path(self.x['job_id']),self.x)
        copied=[]
        def command(x,args,output=None,**kwargs):
            if output is None:return 0
            if output.name=='playwright-measure.meta-part':
                output.write_text(json.dumps({'bytes':2*len(body)+len(result),'manifest_bytes':1000,'files':3,'helper_sha256':sha(guest_path)}))
            elif output.name=='playwright-export.meta-part':
                guest.LIMIT=self.m.evidence_limit(x);receipt=guest.seal();receipt['path']='/tmp/lvm-playwright/evidence.tar.gz';output.write_text(json.dumps(receipt))
            else:
                self.assertGreater(x['approved_export_limit_bytes'],1024**2)
                copied.append(x['reservation']['evidence_bytes']);shutil.copyfile(guest.WORK/'evidence.tar.gz',output)
            return 0
        with patch.object(self.m,'command',side_effect=command):export(self.m,self.x)
        self.assertTrue(copied);self.assertIn('evidence_warning',self.x)
        host=self.m.evidence/self.x['job_id']/'playwright'
        self.assertEqual((host/'result.json').read_bytes(),result)
        for folder in ('results','report'):self.assertEqual((host/folder/'trace.zip').read_bytes(),body)
        self.assertFalse(self.x['test_result']['passed'])
    def test_probe_only_reads_allowlisted_paths(self):
        out=self.root/'probe';out.mkdir();(out/'result.json').write_bytes(b'{}')
        (out/'node_modules').mkdir();(out/'node_modules'/'secret').write_bytes(b'private')
        helper=self.root/'helper.py';helper.write_bytes(b'fixture')
        code=evidence_probe_command()[-1].replace('/tmp/lvm-playwright-evidence',str(out)).replace('/opt/lvm-playwright.py',str(helper))
        output=io.StringIO()
        with contextlib.redirect_stdout(output):exec(code,{})
        measured=json.loads(output.getvalue());self.assertEqual(measured['files'],1);self.assertEqual(measured['bytes'],2)

    def test_capacity_failure_prevents_sealing_and_host_copy(self):
        self.blocked();before=self.m.path(self.x['job_id']).read_bytes();outputs=[]
        self.x['guest_helper_sha256']='a'*64
        def command(x,args,output=None,**kwargs):
            if output is None:return 0
            outputs.append(output.name)
            self.assertEqual(output.name,'playwright-measure.meta-part')
            output.write_text(json.dumps({'bytes':2*GIB,'manifest_bytes':500,'files':1,'helper_sha256':'a'*64}))
            return 0
        capacity={'unknown_state':[],'committed_storage_bytes':80*GIB,'physical_reservation_headroom_bytes':0,'host':HOST}
        with patch.object(self.m,'command',side_effect=command),patch('lvm.resources.snapshot',return_value=capacity),self.assertRaisesRegex(ManagerError,'shared storage'):
            export(self.m,self.x)
        self.assertEqual(outputs,['playwright-measure.meta-part'])
        self.assertEqual(self.m.path(self.x['job_id']).read_bytes(),before)
        self.assertFalse((self.m.evidence/self.x['job_id']/'playwright').exists())

    def test_cli_displays_soft_warning_before_terminal_receipt(self):
        from lvm.cli import wait
        warning={'code':'soft_evidence_allowance_reached','soft_limit_bytes':GIB,'measured_bytes':GIB}
        states=[{'state':'cleaning','evidence_warning':warning},{'state':'complete','evidence_warning':warning}]
        m=Mock();m.status.side_effect=states;output=io.StringIO()
        with patch('lvm.cli.time.sleep'),contextlib.redirect_stdout(output):
            wait(m,'1234abcd',lambda x:x['state']=='complete')
        events=[json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len([x for x in events if 'warning' in x]),1)

if __name__=='__main__':unittest.main()
