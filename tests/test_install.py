"""Portable installation checks; no real Codex profile, launchd job, or VM is changed."""
import importlib.util
import json
from pathlib import Path
import plistlib
import tempfile
import tomllib
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('installer', Path(__file__).resolve().parents[1] / 'scripts/install.py')
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir='/private/tmp', prefix='lvm-install-')
        self.base = Path(self.tmp.name).resolve()
        self.root = self.base / 'another mac checkout with spaces'
        for name in ('bin/lvm', 'bin/lvm-mcp', '.venv/bin/python', 'skills/local-vm-manager/SKILL.md'):
            p = self.root / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text('fixture')
        self.codex = self.base / 'new-profile'
        self.agents = self.base / 'agents'
        self.host = patch.object(installer, 'require_host')
        self.host.start()

    def tearDown(self):
        self.host.stop(); self.tmp.cleanup()

    def run_install(self, **kwargs):
        return installer.install(self.root, self.codex, self.agents, load=False, **kwargs)

    def test_new_profile_is_created_with_relocatable_paths(self):
        result = self.run_install()
        config = tomllib.loads((self.codex / 'config.toml').read_text())
        self.assertEqual(config['mcp_servers']['local-vm-manager']['cwd'], str(self.root))
        self.assertEqual((self.codex / 'skills/local-vm-manager').resolve(), self.root / 'skills/local-vm-manager')
        plist = plistlib.loads(Path(result['launch']).read_bytes())
        self.assertEqual(plist['ProgramArguments'], [str(self.root / 'bin/lvm'), 'sweep'])
        self.assertEqual(plist['StartInterval'], 30)
        self.assertTrue((self.root / 'state/owner.json').is_file())

    def test_unrelated_configuration_is_preserved_and_reinstall_is_idempotent(self):
        self.codex.mkdir(); original = 'model = "existing"\n[mcp_servers.other]\ncommand = "preserve"\n\n\n'
        (self.codex / 'config.toml').write_text(original)
        self.run_install(); first = (self.codex / 'config.toml').read_bytes()
        self.assertTrue(first.decode().startswith(original))
        self.run_install()
        self.assertEqual((self.codex / 'config.toml').read_bytes(), first)

    def test_foreign_skill_conflict_writes_nothing(self):
        skill = self.codex / 'skills/local-vm-manager'; skill.parent.mkdir(parents=True)
        foreign = self.base / 'existing-manager'; foreign.mkdir(); skill.symlink_to(foreign)
        with self.assertRaisesRegex(RuntimeError, 'refusing overwrite'):
            self.run_install()
        self.assertEqual(skill.resolve(), foreign)
        self.assertFalse((self.codex / 'config.toml').exists())
        self.assertFalse((self.root / 'state').exists())
        self.assertFalse(self.agents.exists())

    def test_foreign_mcp_conflict_writes_nothing(self):
        self.codex.mkdir(); path = self.codex / 'config.toml'
        original = b'[mcp_servers.local-vm-manager]\ncommand="/other/bin/server"\ncwd="/other"\n'
        path.write_bytes(original)
        with self.assertRaisesRegex(RuntimeError, 'MCP entry'):
            self.run_install()
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse((self.codex / 'skills').exists())
        self.assertFalse((self.root / 'state').exists())

    def test_foreign_launch_agent_conflict_writes_nothing(self):
        plan = installer.plan(self.root, self.codex, self.agents)
        self.agents.mkdir(); plan['launch'].write_bytes(plistlib.dumps({'Label': 'foreign'}))
        with self.assertRaisesRegex(RuntimeError, 'launch-agent'):
            self.run_install()
        self.assertFalse(self.codex.exists())
        self.assertFalse((self.root / 'state').exists())

    def test_dry_run_has_no_registration_or_state_side_effects(self):
        result = self.run_install(dry_run=True)
        self.assertTrue(result['dry_run'])
        self.assertFalse(self.codex.exists()); self.assertFalse(self.agents.exists())
        self.assertFalse((self.root / 'state').exists())

    def test_different_roots_have_distinct_recovery_labels(self):
        second = self.base / 'another-checkout'
        for name in ('bin/lvm', 'bin/lvm-mcp', '.venv/bin/python', 'skills/local-vm-manager/SKILL.md'):
            p = second / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text('fixture')
        first = installer.plan(self.root, self.codex, self.agents)
        other = installer.plan(second, self.codex, self.agents)
        self.assertNotEqual(first['label'], other['label'])

    def legacy_profile(self):
        from lvm.manager import Manager
        legacy=self.base/'legacy';legacy.mkdir();Manager(legacy).init()
        for name in ('bin/lvm','bin/lvm-mcp','lvm/__init__.py'):
            file=legacy/name;file.parent.mkdir(exist_ok=True);file.write_text('fixture original')
        target=legacy/'skill/local-vm-manager';target.mkdir(parents=True)
        skill=self.codex/'skills/local-vm-manager';skill.parent.mkdir(parents=True);skill.symlink_to(target)
        original=('model = "keep"\n[mcp_servers.local-vm-manager]\ncommand = '+json.dumps(str(legacy/'bin/lvm-mcp'))+'\ncwd = '+json.dumps(str(legacy))+'\ntool_timeout_sec = 60\n[mcp_servers.other]\ncommand="keep"\n')
        (self.codex/'config.toml').write_text(original)
        return legacy,original

    def test_explicit_migration_preserves_owner_evidence_and_other_config(self):
        legacy,original=self.legacy_profile()
        owner=(legacy/'state/owner.json').read_bytes();evidence=legacy/'evidence/keep.txt';evidence.write_text('keep evidence')
        report=self.run_install(migrate_from=legacy)
        self.assertEqual((legacy/'state/owner.json').read_bytes(),owner);self.assertEqual(evidence.read_text(),'keep evidence')
        self.assertEqual(json.loads((self.root/'installation.json').read_text())['data_root'],str(legacy))
        current=tomllib.loads((self.codex/'config.toml').read_text())
        self.assertEqual(current['mcp_servers']['other'],{'command':'keep'})
        self.assertEqual((self.codex/'skills/local-vm-manager').resolve(),self.root/'skills/local-vm-manager')
        self.assertEqual((Path(report['backup'])/'config.toml').read_text(),original)
        self.assertFalse((self.root/'state').exists())
        self.assertIn(str(self.root/'bin/lvm'),(legacy/'bin/lvm').read_text())
        self.assertIn(str(self.root),(legacy/'lvm/__init__.py').read_text())
        again=self.run_install(migrate_from=legacy);self.assertEqual(again['storage'],str(legacy))

    def test_active_legacy_job_blocks_all_registration_changes(self):
        legacy,original=self.legacy_profile();(legacy/'runtime/active').mkdir()
        with self.assertRaisesRegex(RuntimeError,'runtime is present'):self.run_install(migrate_from=legacy)
        self.assertEqual((self.codex/'config.toml').read_text(),original)
        self.assertEqual((self.codex/'skills/local-vm-manager').resolve(),legacy/'skill/local-vm-manager')
        self.assertFalse((self.root/'installation.json').exists());self.assertFalse(self.agents.exists())

    def test_legacy_dry_run_creates_no_lock_registration_or_backup(self):
        legacy,original=self.legacy_profile();(legacy/'state/init.lock').unlink()
        report=self.run_install(migrate_from=legacy,dry_run=True)
        self.assertTrue(report['dry_run']);self.assertFalse((legacy/'state/init.lock').exists())
        self.assertEqual((self.codex/'config.toml').read_text(),original)
        self.assertFalse((self.root/'installation.json').exists());self.assertFalse((self.root/'verification').exists())

    def test_foreign_legacy_recovery_service_is_preserved(self):
        legacy,original=self.legacy_profile();self.agents.mkdir()
        old=self.agents/'com.mikeylong.local-vm-manager.plist';content=plistlib.dumps({'Label':'foreign'})
        old.write_bytes(content)
        with self.assertRaisesRegex(RuntimeError,'recovery identity'):self.run_install(migrate_from=legacy)
        self.assertEqual(old.read_bytes(),content);self.assertEqual((self.codex/'config.toml').read_text(),original)
        self.assertFalse((self.root/'installation.json').exists())

    def test_host_gate_rejects_intel_and_non_macos(self):
        self.host.stop()
        with patch.object(installer.platform, 'system', return_value='Darwin'), patch.object(installer.platform, 'machine', return_value='x86_64'):
            with self.assertRaisesRegex(RuntimeError, 'Apple Silicon'):
                installer.require_host()
        with patch.object(installer.platform, 'system', return_value='Linux'), patch.object(installer.platform, 'machine', return_value='arm64'):
            with self.assertRaisesRegex(RuntimeError, 'Apple Silicon'):
                installer.require_host()
        self.host.start()


if __name__ == '__main__':
    unittest.main()
