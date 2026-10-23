#!/usr/bin/env python3
"""Download pinned dependencies into this checkout; never boot a VM."""
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lvm.manager import Manager


def digest(path, algorithm='sha256'):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, algorithm).hexdigest()


def download(url, destination, expected, algorithm='sha256'):
    fd, name = tempfile.mkstemp(prefix='.download-', dir=destination.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as out, urllib.request.urlopen(url, timeout=120) as src:
            while chunk := src.read(1024 ** 2):
                out.write(chunk)
        if digest(temporary, algorithm) != expected:
            raise RuntimeError('Downloaded dependency checksum mismatch: ' + destination.name)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def bootstrap(root=ROOT):
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise RuntimeError('This release requires an Apple Silicon Mac (Darwin arm64).')
    if sys.version_info < (3, 12):
        raise RuntimeError('Python 3.12 or newer is required.')
    root = Path(root)
    Manager(root)
    settings = root / 'settings.json'
    defaults = json.loads((root / 'settings.example.json').read_text())
    selected = json.loads(settings.read_text()) if settings.exists() else defaults
    for field in ('image_url', 'image_archive_sha512', 'image_sha256', 'lima_version', 'lima_archive_sha256', 'limactl_sha256'):
        if selected.get(field) != defaults[field]:
            raise RuntimeError('Local settings differ from the pinned dependency manifest: ' + field)
    tools, cache = root / 'tools', root / 'cache'
    for directory in (tools, cache):
        if directory.is_symlink():
            raise RuntimeError('Refusing symlinked dependency directory')
        directory.mkdir(exist_ok=True)
    lima = tools / 'lima'
    if lima.is_symlink():
        raise RuntimeError('Refusing symlinked Lima installation')
    if not (lima / 'bin/limactl').exists():
        archive = tools / 'lima.tar.gz'
        version = defaults['lima_version']
        download(f'https://github.com/lima-vm/lima/releases/download/v{version}/lima-{version}-Darwin-arm64.tar.gz', archive, defaults['lima_archive_sha256'])
        lima.mkdir(exist_ok=True)
        with tarfile.open(archive) as package:
            package.extractall(lima, filter='data')
        archive.unlink()
    if digest(lima / 'bin/limactl') != defaults['limactl_sha256']:
        raise RuntimeError('Installed Lima checksum mismatch')
    image = cache / 'debian.raw'
    if image.is_symlink():
        raise RuntimeError('Refusing symlinked base image')
    if not image.exists():
        archive = cache / 'debian.tar.xz'
        download(defaults['image_url'], archive, defaults['image_archive_sha512'], 'sha512')
        with tarfile.open(archive) as package:
            members = [member for member in package.getmembers() if member.isfile() and member.name.endswith('.raw')]
            if len(members) != 1:
                raise RuntimeError('Expected exactly one Debian raw image')
            member = members[0]; member.name = 'debian.raw'
            package.extract(member, cache, filter='data')
        archive.unlink()
    if digest(image) != defaults['image_sha256']:
        raise RuntimeError('Base image checksum mismatch')
    if image.stat().st_blocks * 512 > image.stat().st_size // 2:
        if any((root / 'runtime').glob('*')):
            raise RuntimeError('Do not rewrite the shared base while a guest exists')
        fd, name = tempfile.mkstemp(prefix='.debian-sparse-', dir=cache)
        sparse = Path(name)
        try:
            with image.open('rb') as src, os.fdopen(fd, 'wb') as dst:
                zero = bytes(1024 ** 2)
                while block := src.read(1024 ** 2):
                    if block == zero:
                        dst.seek(len(block), 1)
                    else:
                        dst.write(block)
                dst.truncate(src.tell())
            if digest(sparse) != defaults['image_sha256']:
                raise RuntimeError('Sparse base image readback mismatch')
            sparse.chmod(0o444); sparse.replace(image)
        finally:
            sparse.unlink(missing_ok=True)
    image.chmod(0o444)
    if not settings.exists():
        with settings.open('x') as out:
            out.write(json.dumps(selected, indent=2) + '\n')
    return {'root': str(root), 'image': str(image), 'logical_bytes': image.stat().st_size,
            'allocated_bytes': image.stat().st_blocks * 512, 'settings': str(settings)}


if __name__ == '__main__':
    try:
        print(json.dumps(bootstrap()))
    except (RuntimeError, OSError, ValueError) as error:
        raise SystemExit(str(error))
