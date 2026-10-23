"""Guest runner process and durable progress behavior; no browsers are launched."""
import contextlib,io,json,os,pathlib,subprocess,sys,tempfile,unittest
from unittest.mock import patch
from lvm import guest_playwright as guest
class GuestProgressTests(unittest.TestCase):
    def test_only_complete_durable_progress_records_are_forwarded(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as d:
            root=pathlib.Path(d);work=root/'work';(work/'source').mkdir(parents=True);out=root/'out';out.mkdir()
            events=[{'event':'test_begin','id':'a'},{'event':'test_end','id':'a','status':'passed'}]
            (out/'progress.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events)+'{"incomplete":')
            stream=io.StringIO()
            with patch.object(guest,'WORK',work),patch.object(guest,'OUT',out),contextlib.redirect_stdout(stream):
                rc=guest.execute([sys.executable,'-c','pass'],os.environ.copy(),timeout=10)
            self.assertEqual(rc,0)
            records=[json.loads(line.removeprefix('LVM_PLAYWRIGHT_EVENT ')) for line in stream.getvalue().splitlines()]
            self.assertEqual(records,events)
    def test_timeout_terminates_and_reaps_own_child(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as d:
            root=pathlib.Path(d);work=root/'work';(work/'source').mkdir(parents=True);out=root/'out';out.mkdir();pidfile=root/'pid'
            code='import os,pathlib,time; pathlib.Path('+repr(str(pidfile))+').write_text(str(os.getpid())); time.sleep(30)'
            with patch.object(guest,'WORK',work),patch.object(guest,'OUT',out):
                with self.assertRaises(subprocess.TimeoutExpired):guest.execute([sys.executable,'-c',code],os.environ.copy(),timeout=.2)
            pid=int(pidfile.read_text())
            with self.assertRaises(ProcessLookupError):os.kill(pid,0)
            self.assertIsNone(guest.current)
if __name__=='__main__':unittest.main()
