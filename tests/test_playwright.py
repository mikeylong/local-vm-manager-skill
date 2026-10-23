import hashlib,io,json,pathlib,tarfile,tempfile,unittest
from lvm.playwright import extract,safe_path,snapshot
from lvm.manager import ManagerError
class PlaywrightBoundaries(unittest.TestCase):
    def test_unsafe_paths_rejected(self):
        for name in ('.','../disk.img','/Users/fixture/.tart/disk.img','a/../b','./result.json'):
            with self.assertRaises(ManagerError):safe_path(name)
    def bundle(self,root,name,content=b'x',sha=None,link=False):
        p=root/'bundle.tar.gz'
        with tarfile.open(p,'w:gz') as t:
            data=json.dumps({'files':{name:{'bytes':len(content),'sha256':sha or hashlib.sha256(content).hexdigest()}}}).encode()
            info=tarfile.TarInfo('manifest.json');info.size=len(data);t.addfile(info,io.BytesIO(data))
            info=tarfile.TarInfo(name);info.size=len(content)
            if link:info.type=tarfile.SYMTYPE;info.linkname='/etc/passwd';t.addfile(info)
            else:t.addfile(info,io.BytesIO(content))
        return p
    def test_evidence_survives_verified_extraction(self):
        with tempfile.TemporaryDirectory() as d:
            root=pathlib.Path(d).resolve();bundle=self.bundle(root,'results/a/trace.zip');files=extract(bundle,root/'evidence',10000);bundle.unlink()
            self.assertEqual((root/'evidence/results/a/trace.zip').read_bytes(),b'x');self.assertIn('manifest.json',files)
    def test_archive_escape_native_cache_and_links_rejected(self):
        for name,link in (('../disk.img',False),('source/key',False),('node_modules/a',False),('results/link',True)):
            with tempfile.TemporaryDirectory() as d:
                root=pathlib.Path(d).resolve();bundle=self.bundle(root,name,link=link)
                with self.assertRaises(ManagerError):extract(bundle,root/'evidence',10000)
    def test_corruption_and_overflow_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=pathlib.Path(d).resolve();bundle=self.bundle(root,'result.json',sha='0'*64)
            with self.assertRaises(ManagerError):extract(bundle,root/'evidence',10000)
            with self.assertRaises(ManagerError):extract(bundle,root/'bounded',1)
    def test_native_specs_rejected_before_reading_source(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ManagerError,'Only tests/browser'):
                snapshot(None,str(pathlib.Path(d).resolve()),['ios/a.spec.ts'],9)
    def test_snapshot_excludes_native_credentials_and_dependencies(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory(dir='/private/tmp') as d:
            root=pathlib.Path(d).resolve();source=root/'source';source.mkdir();cache=root/'cache';cache.mkdir()
            files={'tests/browser/a.spec.ts':'test', 'tests/native/keep.spec.ts':'native', 'ios/Test.swift':'native', 'macos/Test.swift':'native', '.env':'secret', 'node_modules/key':'secret', 'src/server.ts':'web', 'package.json':'{}', 'playwright.config.ts':'export default {}'}
            for name,content in files.items():
                p=source/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(content)
            packages={f'node_modules/{name}':{'version':'1.62.1'} for name in ('@playwright/test','playwright','playwright-core')}
            (source/'package-lock.json').write_text(json.dumps({'packages':packages}))
            (cache/'node.tar.xz').write_bytes(b'node-fixture');(cache/'node-linux.json').write_text(json.dumps({'filename':'node.tar.xz','sha256':hashlib.sha256(b'node-fixture').hexdigest()}))
            payload,spec=snapshot(SimpleNamespace(root=root),str(source),['tests/browser/a.spec.ts'],1)
            self.assertEqual(set(spec['source_files']),{'tests/browser/a.spec.ts','src/server.ts','package.json','package-lock.json','playwright.config.ts'})
            with tarfile.open(payload) as t:self.assertNotIn('source/.env',t.getnames())
            payload.unlink();(source/'src/symlink').symlink_to(source/'.env')
            with self.assertRaises(ManagerError):snapshot(SimpleNamespace(root=root),str(source),['tests/browser/a.spec.ts'],1)
    def test_interrupted_guest_seals_diagnostic_without_source_or_cache(self):
        from unittest.mock import patch
        from lvm import guest_playwright as guest
        with tempfile.TemporaryDirectory(dir='/private/tmp') as d:
            root=pathlib.Path(d);out=root/'out';out.mkdir();(out/'run.log').write_text('interrupted')
            (out/'node_modules').mkdir();(out/'node_modules/key').write_text('must not export')
            with patch.object(guest,'WORK',root/'work'),patch.object(guest,'OUT',out):receipt=guest.seal()
            target=root/'evidence';extract(pathlib.Path(receipt['path']),target,10000)
            self.assertFalse(json.loads((target/'result.json').read_text())['passed']);self.assertFalse((target/'node_modules').exists())
if __name__=='__main__':unittest.main()

class ExportRecoveryTests(unittest.TestCase):
    def test_sealed_evidence_recovery_needs_no_stopped_guest(self):
        from unittest.mock import Mock
        from lvm.playwright import export
        from types import SimpleNamespace
        from lvm.manager import sha,ManagerError
        with tempfile.TemporaryDirectory(dir='/private/tmp') as d:
            root=pathlib.Path(d);e=root/'abcd1234';(e/'playwright').mkdir(parents=True)
            file=e/'playwright/manifest.json';file.write_text('{}')
            m=SimpleNamespace(evidence=root,command=Mock())
            x={'job_id':'abcd1234','workload_started':True,'bundle_sha256':'sealed','test_result':{'passed':False},'exports':{'playwright/manifest.json':{'bytes':2,'sha256':sha(file)}}}
            export(m,x);m.command.assert_not_called()
            file.write_text('changed')
            with self.assertRaisesRegex(ManagerError,'sealed host evidence changed'):export(m,x)
            m.command.assert_not_called()
