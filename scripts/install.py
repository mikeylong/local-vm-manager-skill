"""Register canonical source, optionally retaining a reviewed legacy data root."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import subprocess
import sys
import tempfile
import time
import tomllib

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lvm.manager import Manager, data_root as selected_data_root, lock


def require_host():
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise RuntimeError('This release requires an Apple Silicon Mac (Darwin arm64).')


def replace_routing(text, command, cwd):
    """Change just two keys; preserve every unrelated TOML byte and table."""
    table = re.search(r'^\[mcp_servers\.local-vm-manager\][^\n]*\n(?P<body>.*?)(?=^\s*\[|\Z)', text, re.M | re.S)
    if table is None:
        raise RuntimeError('MCP routing uses unsupported TOML syntax; original preserved')
    body = table.group('body')
    for key, value in (('command', command), ('cwd', cwd)):
        body, count = re.subn(r'^' + key + r'\s*=.*$', lambda _: key + ' = ' + json.dumps(value, ensure_ascii=False), body, flags=re.M)
        if count != 1:
            raise RuntimeError('MCP routing key is ambiguous; original preserved')
    candidate = text[:table.start('body')] + body + text[table.end('body'):]
    before, after = tomllib.loads(text), tomllib.loads(candidate)
    expected = before['mcp_servers']['local-vm-manager']
    expected.update(command=command, cwd=cwd)
    if before != after:
        raise RuntimeError('Migration would change unrelated configuration')
    return candidate.encode()


def plan(root, codex_home, agents_dir, *, migrate_from=None):
    root, codex_home, agents_dir = map(Path, (root, codex_home, agents_dir))
    legacy = Path(migrate_from) if migrate_from is not None else None
    storage = legacy if legacy is not None else selected_data_root(root)
    Manager(storage)
    registration = root / 'installation.json'
    value = {'schema_version': 1, 'data_root': str(storage)}
    if registration.is_symlink() or (registration.exists() and json.loads(registration.read_text()) != value):
        raise RuntimeError('Existing installation data root conflicts; refusing overwrite')
    if legacy is not None:
        for relative in ('bin/lvm','bin/lvm-mcp','lvm/__init__.py'):
            candidate=legacy/relative
            if not candidate.is_file() or candidate.is_symlink():raise RuntimeError('Legacy compatibility target is unsafe: '+str(candidate))
        if legacy == root or not (legacy / 'state/owner.json').is_file():
            raise RuntimeError('Migration needs a distinct existing owned data root')
        owner = json.loads((legacy / 'state/owner.json').read_text())
        if owner.get('root') != str(legacy) or not owner.get('manager_id'):
            raise RuntimeError('Legacy ownership identity differs')
    target = root / 'skills/local-vm-manager'
    for path in (root / 'bin/lvm', root / 'bin/lvm-mcp', root / '.venv/bin/python', target / 'SKILL.md'):
        if not path.is_file():
            raise RuntimeError(f'Missing installation prerequisite: {path}')
    skill = codex_home / 'skills/local-vm-manager'
    old_skill = legacy / 'skill/local-vm-manager' if legacy else None
    if skill.is_symlink():
        if skill.resolve() not in (target, old_skill):
            raise RuntimeError('Existing skill points elsewhere; refusing overwrite')
    elif skill.exists():
        raise RuntimeError('Existing skill exists; refusing overwrite')
    config = codex_home / 'config.toml'
    if config.is_symlink():
        raise RuntimeError('Refusing symlinked Codex configuration')
    original = config.read_bytes() if config.exists() else None
    text = original.decode() if original is not None else ''
    existing = tomllib.loads(text).get('mcp_servers', {}).get('local-vm-manager')
    command, cwd = str(root / 'bin/lvm-mcp'), str(root)
    if existing is None:
        candidate = (text + ('\n' if text and not text.endswith('\n') else '') + '\n[mcp_servers.local-vm-manager]\n'
                     f'command = {json.dumps(command, ensure_ascii=False)}\n'
                     f'cwd = {json.dumps(cwd, ensure_ascii=False)}\n'
                     'startup_timeout_sec = 30\ntool_timeout_sec = 60\n').encode()
        tomllib.loads(candidate.decode())
    elif existing.get('command') == command and existing.get('cwd') == cwd:
        candidate = original
    elif legacy is not None and existing.get('command') == str(legacy / 'bin/lvm-mcp') and existing.get('cwd') == str(legacy):
        candidate = replace_routing(text, command, cwd)
    else:
        raise RuntimeError('Existing MCP entry points elsewhere; refusing overwrite')
    label = 'com.local-vm-manager.' + hashlib.sha256(str(root).encode()).hexdigest()[:16]
    launch = agents_dir / (label + '.plist')
    plist = {'Label': label, 'ProgramArguments': [str(root / 'bin/lvm'), 'sweep'],
             'RunAtLoad': True, 'StartInterval': 30, 'WorkingDirectory': str(root),
             'StandardOutPath': str(storage / 'state/sweeper.log'),
             'StandardErrorPath': str(storage / 'state/sweeper-error.log'), 'ProcessType': 'Background'}
    if launch.is_symlink() or (launch.exists() and plistlib.loads(launch.read_bytes()) != plist):
        raise RuntimeError('Existing launch-agent file conflicts; refusing overwrite')
    # This is the only legacy service this migration knows how to retire.
    old_launch = agents_dir / 'com.mikeylong.local-vm-manager.plist' if legacy else None
    if old_launch is not None and old_launch.exists():
        if old_launch.is_symlink():raise RuntimeError('Refusing symlinked legacy launch agent')
        old = plistlib.loads(old_launch.read_bytes())
        if old.get('Label') != 'com.mikeylong.local-vm-manager' or old.get('ProgramArguments') != [str(legacy / 'bin/lvm'), 'sweep'] or old.get('WorkingDirectory') != str(legacy):
            raise RuntimeError('Legacy recovery identity differs; original preserved')
    return dict(root=root, storage=storage, registration=registration, installation=value,
                legacy=legacy, old_launch=old_launch, skill=skill, old_skill=old_skill, target=target,
                config=config, original=original, candidate=candidate, launch=launch, plist=plist,
                label=label, service=f'gui/{os.getuid()}/{label}')


def replace_file(path, content):
    fd, name = tempfile.mkstemp(prefix='.lvm-install-', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def assert_idle(manager):
    if any(manager.runtime.iterdir()):raise RuntimeError('Manager runtime is present; wait for owned cleanup before migration')
    if any(manager.status(p.stem)['state'] not in ('complete', 'failed') for p in manager.jobs.glob('*.json')):
        raise RuntimeError('Active or cleanup-blocked job; wait before migration')


def install(root, codex_home, agents_dir, *, load=True, dry_run=False, migrate_from=None, wait_idle_seconds=0):
    require_host()
    p = plan(root, codex_home, agents_dir, migrate_from=migrate_from)
    summary = {k: str(p[k]) for k in ('root', 'storage', 'skill', 'config', 'launch', 'service')}
    summary.update(dry_run=dry_run, recovery_loaded=False)
    if dry_run:
        if p['legacy'] is not None:
            manager=Manager(p['storage']);manager.owner=json.loads((manager.state/'owner.json').read_text());assert_idle(manager)
        return summary
    manager = Manager(p['storage']).init()
    with lock(manager.state / 'admission.lock'):
        if p['legacy'] is not None:
            until=time.monotonic()+wait_idle_seconds
            while True:
                try:assert_idle(manager);break
                except RuntimeError:
                    if time.monotonic()>=until:raise
                    time.sleep(1)
        p['config'].parent.mkdir(parents=True, exist_ok=True)
        p['skill'].parent.mkdir(parents=True, exist_ok=True)
        p['launch'].parent.mkdir(parents=True, exist_ok=True)
        # Recheck before any registration mutation.
        current = p['config'].read_bytes() if p['config'].exists() else None
        if current != p['original']:raise RuntimeError('Codex config changed concurrently; retry against fresh content')
        if p['skill'].is_symlink() and p['skill'].resolve() not in (p['target'],p['old_skill']):
            raise RuntimeError('Skill changed concurrently; original preserved')
        backup = None
        if p['legacy'] is not None:
            backup = p['root'] / 'verification' / ('migration-' + __import__('uuid').uuid4().hex)
            backup.mkdir(parents=True)
            if current is not None:(backup / 'config.toml').write_bytes(current)
            (backup / 'skill-link.json').write_text(json.dumps({'target':os.readlink(p['skill']) if p['skill'].is_symlink() else None}))
            if p['old_launch'].exists():(backup / p['old_launch'].name).write_bytes(p['old_launch'].read_bytes())
            summary['backup'] = str(backup)
            for relative in ('bin/lvm','bin/lvm-mcp','lvm/__init__.py'):
                (backup/relative.replace('/','-')).write_bytes((p['legacy']/relative).read_bytes())
        if not p['registration'].exists():
            replace_file(p['registration'], (json.dumps(p['installation'], indent=2)+'\n').encode())
        if current != p['candidate']:replace_file(p['config'], p['candidate'])
        if not p['skill'].is_symlink() or p['skill'].resolve()!=p['target']:
            temporary=p['skill'].with_name('.lvm-skill-'+__import__('uuid').uuid4().hex)
            temporary.symlink_to(p['target'],target_is_directory=True)
            try:os.replace(temporary,p['skill'])
            finally:temporary.unlink(missing_ok=True)
        if not p['launch'].exists():
            with p['launch'].open('xb') as stream:stream.write(plistlib.dumps(p['plist']))
        if p['legacy'] is not None:
            import shlex
            for name in ('lvm','lvm-mcp'):
                launcher=p['legacy']/'bin'/name
                content=('#!/bin/sh\nset -eu\n# Compatibility entrypoint; edit the canonical repository.\nexec '+shlex.quote(str(p['root']/'bin'/name))+' "$@"\n').encode()
                replace_file(launcher,content);launcher.chmod(0o755)
            shim=('\"\"\"Compatibility import; canonical code is maintained in the repository.\"\"\"\n'
                  'from pathlib import Path\n'
                  '__path__ = [str(Path('+repr(str(p['root']))+') / "lvm")]\n')
            replace_file(p['legacy']/'lvm/__init__.py',shim.encode())
            notice=('Canonical source: '+str(p['root'])+'\n'
                    'This directory retains local owned storage, evidence, and historical source.\n'
                    'CLI launchers and Python imports delegate to the canonical repository.\n'
                    'Do not edit the historical source copy. Do not delete retained evidence.\n')
            replace_file(p['legacy']/'CANONICAL_SOURCE.md',notice.encode())
        if load:
            probe = subprocess.run(['/bin/launchctl', 'print', p['service']], capture_output=True)
            if probe.returncode:
                subprocess.run(['/bin/launchctl', 'bootstrap', f'gui/{os.getuid()}', str(p['launch'])], check=True)
            summary['recovery_loaded'] = True
            if p['old_launch'] is not None and p['old_launch'].exists():
                service=f'gui/{os.getuid()}/com.mikeylong.local-vm-manager'
                if subprocess.run(['/bin/launchctl','print',service],capture_output=True).returncode==0:
                    subprocess.run(['/bin/launchctl','bootout',service],check=True)
                p['old_launch'].rename(backup / (p['old_launch'].name+'.retired'))
                summary['legacy_recovery_retired']=True
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex-home', type=Path, default=Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))))
    parser.add_argument('--launch-agent-dir', type=Path, default=Path.home() / 'Library/LaunchAgents')
    parser.add_argument('--migrate-from',type=Path,help='Explicitly replace this legacy registration while keeping its owned storage/evidence in place')
    parser.add_argument('--wait-idle',type=int,default=0,metavar='SECONDS',help='Hold admission while an existing job finishes naturally, up to this timeout')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--no-load', action='store_true', help='Write registrations without loading recovery; not ready for unattended runs')
    args = parser.parse_args()
    if not 0<=args.wait_idle<=3600:parser.error('--wait-idle must be 0..3600 seconds')
    try:
        result = install(ROOT,args.codex_home.expanduser().resolve(),args.launch_agent_dir.expanduser().resolve(),
                         load=not args.no_load,dry_run=args.dry_run,wait_idle_seconds=args.wait_idle,migrate_from=args.migrate_from.expanduser().absolute() if args.migrate_from else None)
    except (RuntimeError, OSError, ValueError) as error:
        parser.exit(1, f'{error}\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':main()
