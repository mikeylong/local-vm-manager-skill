"""Fetch checksum-pinned Linux ARM64 Node for guest-only Playwright workloads."""
import hashlib,json,pathlib,urllib.request
ROOT=pathlib.Path(__file__).resolve().parent.parent
VERSION='v22.16.0'
NAME=f'node-{VERSION}-linux-arm64.tar.xz'
SHA256='eab80cb88f8fda1e65f5e8d0420c9809bdb320b03fd34976ab7161b6e703b910'
URL=f'https://nodejs.org/dist/{VERSION}/{NAME}'
if __name__=='__main__':
    cache=ROOT/'cache';cache.mkdir(exist_ok=True);target=cache/NAME
    if not target.exists():
        part=target.with_suffix('.part')
        with urllib.request.urlopen(URL,timeout=120) as src,part.open('xb') as dst:
            while chunk:=src.read(1024**2):dst.write(chunk)
        if hashlib.sha256(part.read_bytes()).hexdigest()!=SHA256:raise SystemExit('Node checksum mismatch')
        part.replace(target)
    if hashlib.sha256(target.read_bytes()).hexdigest()!=SHA256:raise SystemExit('Cached Node checksum mismatch')
    (cache/'node-linux.json').write_text(json.dumps({'version':VERSION,'filename':NAME,'url':URL,'sha256':SHA256},indent=2)+'\n')
    print('Verified Linux ARM64 Node archive; never executed on the Mac')
