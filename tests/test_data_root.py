"""Source and storage separation must not change ownership or recovery routing."""
import json,os,pathlib,tempfile,unittest
from unittest.mock import Mock,patch
from lvm.manager import Manager,ManagerError,ROOT,data_root

class DataRootTests(unittest.TestCase):
    def test_installation_selects_existing_storage_without_moving_owner(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as raw:
            base=pathlib.Path(raw).resolve();code=base/'source';code.mkdir();storage=base/'storage';storage.mkdir()
            original=Manager(storage).init().owner
            (code/'installation.json').write_text(json.dumps({'schema_version':1,'data_root':str(storage)}))
            with patch.dict(os.environ,{},clear=True):
                self.assertEqual(data_root(code),storage)
            with patch('lvm.manager.data_root',return_value=storage):
                installed=Manager().init()
                self.assertEqual(installed.owner,original);self.assertEqual(installed.root,storage)
                self.assertEqual(installed.code_root,ROOT)

    def test_relative_and_symlinked_installation_are_rejected(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as raw:
            code=pathlib.Path(raw).resolve();file=code/'installation.json'
            with patch.dict(os.environ,{},clear=True):
                file.write_text(json.dumps({'schema_version':1,'data_root':'relative'}))
                with self.assertRaisesRegex(ManagerError,'absolute'):data_root(code)
                target=code/'target';file.rename(target);file.symlink_to(target)
                with self.assertRaisesRegex(ManagerError,'symlinked'):data_root(code)

    def test_supervisor_uses_canonical_source_and_same_owned_storage(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as raw:
            storage=pathlib.Path(raw).resolve();manager=Manager(storage).init();job='1234abcd'
            (manager.evidence/job).mkdir()
            with patch('lvm.manager.subprocess.Popen') as spawn:manager.spawn(job)
            self.assertEqual(spawn.call_args.kwargs['cwd'],ROOT)
            self.assertEqual(spawn.call_args.kwargs['env']['LVM_DATA_ROOT'],str(storage))

if __name__=='__main__':unittest.main()
