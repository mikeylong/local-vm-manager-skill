import hashlib,json,pathlib,tarfile,tempfile,unittest
from types import SimpleNamespace
from lvm.suites import policy
from lvm.playwright import snapshot
from lvm.manager import ManagerError
class SuiteBoundaries(unittest.TestCase):
 def test_native_and_arbitrary_commands_rejected(self):
  for profile,files in [('surfaces',['ios/Native.test.ts']),('surfaces',['scripts/agent.ts']),('judgmentkit',['tests/next-native.test.mjs'])]:
   with self.subTest(profile=profile,files=files),self.assertRaises(ManagerError):policy(profile,files)
  with self.assertRaises(ManagerError):policy('arbitrary',None)
 def test_profile_snapshot_freezes_originals_without_host_dependencies_or_native_trees(self):
  with tempfile.TemporaryDirectory(dir='/private/tmp') as d:
   root=pathlib.Path(d).resolve();source=root/'source';source.mkdir();cache=root/'cache';cache.mkdir()
   files={'package.json':json.dumps({'name':'surfaces'}),'package-lock.json':json.dumps({'packages':{'node_modules/playwright':{'version':'1.63.0'},'node_modules/playwright-core':{'version':'1.63.0'}}}),
    'scripts/check-workspace.mjs':'original assertion','desktop/resources.mjs':'shared node helper','server/index.ts':'server','vite.config.ts':'config','native/keep.swift':'native','node_modules/secret':'secret','.env':'secret','output/dag/live-state.json':'{}','.surfaces/credentials.json':'secret','.surfaces/dag-implementation/artifacts/abc/index.html':'fixture'}
   for n,c in files.items():p=source/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(c)
   (cache/'node.tar.xz').write_bytes(b'fixture');(cache/'node-linux.json').write_text(json.dumps({'filename':'node.tar.xz','sha256':hashlib.sha256(b'fixture').hexdigest()}))
   payload,spec=snapshot(SimpleNamespace(root=root),str(source),['scripts/check-workspace.mjs'],1,suite_profile='surfaces')
   self.assertEqual(spec['runner'],'node-suites');self.assertIn('output/dag/live-state.json',spec['source_files']);self.assertIn('vite.config.ts',spec['source_files']);self.assertIn('desktop/resources.mjs',spec['source_files'])
   self.assertEqual(spec['source_files']['scripts/check-workspace.mjs']['sha256'],hashlib.sha256(b'original assertion').hexdigest())
   with tarfile.open(payload) as t:
    names=t.getnames();self.assertNotIn('source/.env',names);self.assertNotIn('source/.surfaces/credentials.json',names);self.assertNotIn('source/native/keep.swift',names)
   payload.unlink();(source/'output/dag/live-state.json').unlink();(source/'output/dag/live-state.json').symlink_to(source/'.env')
   with self.assertRaises(ManagerError):snapshot(SimpleNamespace(root=root),str(source),['scripts/check-workspace.mjs'],1,suite_profile='surfaces')
 def test_package_profile_mismatch_fails_before_allocation(self):
  with tempfile.TemporaryDirectory(dir='/private/tmp') as d:
   source=pathlib.Path(d).resolve();(source/'package.json').write_text('{"name":"other"}')
   with self.assertRaisesRegex(ManagerError,'identity mismatch'):snapshot(None,str(source),['scripts/check-workspace.mjs'],1,suite_profile='surfaces')

class HistoricalFixtureTests(unittest.TestCase):
 def test_minimal_pack_preserves_exact_historical_blob_without_repository_config(self):
  import subprocess
  from lvm.suites import retained_git
  with tempfile.TemporaryDirectory(dir='/private/tmp') as d:
   root=pathlib.Path(d).resolve();source=root/'source';source.mkdir();guest=root/'guest';guest.mkdir()
   def git(*args,**kwargs):return subprocess.check_output(['git',*args],cwd=source,**kwargs)
   git('init','-q');p=source/'examples/local-product/app.tsx';p.parent.mkdir(parents=True);p.write_bytes(b'exact historical bytes\n')
   git('add','examples/local-product/app.tsx');git('-c','user.name=Fixture','-c','user.email=fixture@invalid','commit','-qm','fixture')
   packed,meta=retained_git(source,'HEAD');self.assertLess(meta['bytes'],2*1024**2)
   subprocess.run(['git','init','-q'],cwd=guest,check=True)
   subprocess.run(['git','unpack-objects','-r'],cwd=guest,input=packed,check=True)
   blob=subprocess.check_output(['git','show',meta['historical_commit']+':'+meta['path']],cwd=guest)
   self.assertEqual(blob,p.read_bytes());self.assertEqual(hashlib.sha256(blob).hexdigest(),meta['historical_blob_sha256'])
   self.assertNotIn('fixture@invalid',(guest/'.git/config').read_text())
