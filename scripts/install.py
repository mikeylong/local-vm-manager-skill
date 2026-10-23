"""Register this checkout without replacing another installation or starting a VM."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import subprocess
import sys
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lvm.manager import Manager


def require_host():
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise RuntimeError('This release requires an Apple Silicon Mac (Darwin arm64).')


def plan(root, codex_home, agents_dir):
    root, codex_home, agents_dir = map(Path, (root, codex_home, agents_dir))
    Manager(root)
    target = root / 'skills/local-vm-manager'
    for path in (root / 'bin/lvm', root / 'bin/lvm-mcp', root / '.venv/bin/python', target / 'SKILL.md'):
        if not path.is_file():
            raise RuntimeError(f'Missing installation prerequisite: {path}')
    skill = codex_home / 'skills/local-vm-manager'
    if skill.is_symlink():
        if skill.resolve() != target:
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
    else:
        if existing.get('command') != command or existing.get('cwd') != cwd:
            raise RuntimeError('Existing MCP entry points elsewhere; refusing overwrite')
        candidate = original
    label = 'com.local-vm-manager.' + hashlib.sha256(str(root).encode()).hexdigest()[:16]
    launch = agents_dir / (label + '.plist')
    plist = {'Label': label, 'ProgramArguments': [str(root / 'bin/lvm'), 'sweep'],
             'RunAtLoad': True, 'StartInterval': 30, 'WorkingDirectory': str(root),
             'StandardOutPath': str(root / 'state/sweeper.log'),
             'StandardErrorPath': str(root / 'state/sweeper-error.log'), 'ProcessType': 'Background'}
    if launch.is_symlink() or (launch.exists() and plistlib.loads(launch.read_bytes()) != plist):
        raise RuntimeError('Existing launch-agent file conflicts; refusing overwrite')
    return dict(root=root, skill=skill, target=target, config=config, original=original,
                candidate=candidate, launch=launch, plist=plist, label=label,
                service=f'gui/{os.getuid()}/{label}')


def install(root, codex_home, agents_dir, *, load=True, dry_run=False):
    require_host()
    p = plan(root, codex_home, agents_dir)
    summary = {k: str(p[k]) for k in ('root', 'skill', 'config', 'launch', 'service')}
    summary.update(dry_run=dry_run, recovery_loaded=False)
    if dry_run:
        return summary
    Manager(p['root']).init()
    p['config'].parent.mkdir(parents=True, exist_ok=True)
    p['skill'].parent.mkdir(parents=True, exist_ok=True)
    p['launch'].parent.mkdir(parents=True, exist_ok=True)
    current = p['config'].read_bytes() if p['config'].exists() else None
    if current != p['original']:
        raise RuntimeError('Codex config changed concurrently; retry against fresh content')
    if current != p['candidate']:
        fd, name = tempfile.mkstemp(prefix='.lvm-config-', dir=p['config'].parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(p['candidate']); stream.flush(); os.fsync(stream.fileno())
            current = p['config'].read_bytes() if p['config'].exists() else None
            if current != p['original']:
                raise RuntimeError('Codex config changed concurrently; original preserved')
            os.replace(temporary, p['config'])
        finally:
            temporary.unlink(missing_ok=True)
    if not p['skill'].is_symlink():
        p['skill'].symlink_to(p['target'], target_is_directory=True)
    if not p['launch'].exists():
        with p['launch'].open('xb') as stream:
            stream.write(plistlib.dumps(p['plist']))
    if load:
        probe = subprocess.run(['/bin/launchctl', 'print', p['service']], capture_output=True)
        if probe.returncode:
            subprocess.run(['/bin/launchctl', 'bootstrap', f'gui/{os.getuid()}', str(p['launch'])], check=True)
        summary['recovery_loaded'] = True
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex-home', type=Path,
                        default=Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))))
    parser.add_argument('--launch-agent-dir', type=Path, default=Path.home() / 'Library/LaunchAgents')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--no-load', action='store_true', help='Write registration files without loading recovery; not ready for unattended runs')
    args = parser.parse_args()
    try:
        result = install(ROOT, args.codex_home.expanduser().resolve(), args.launch_agent_dir.expanduser().resolve(),
                         load=not args.no_load, dry_run=args.dry_run)
    except (RuntimeError, OSError, ValueError) as error:
        parser.exit(1, f'{error}\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
