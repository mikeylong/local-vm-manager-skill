#!/usr/bin/python3
"""Runs in Linux only; no host browsers, shares, or arbitrary shell input."""
import hashlib,json,os,pathlib,subprocess,sys,time
out=pathlib.Path('/tmp/lvm-evidence')/sys.argv[1]
out.mkdir(parents=True,exist_ok=False)
url=sys.argv[2]
if url=='pilot':
    fixture=out/'fixture.html'
    fixture.write_text('''<!doctype html><html><head><title>Local VM pilot</title></head>
<body style="font:24px sans-serif;padding:40px"><h1>Disposable Linux browser</h1>
<label>Note <input id="note" value="Evidence survives"></label><button id="save">Save</button>
<p id="result">Waiting</p><script>
const input=document.querySelector('#note');
document.querySelector('#save').onclick=()=>{localStorage.setItem('pilot-note',input.value);
document.querySelector('#result').textContent=localStorage.getItem('pilot-note');
document.body.dataset.pilotState='saved';}; document.querySelector('#save').click();
</script></body></html>''')
    url=fixture.as_uri()
cmd=['chromium','--headless','--disable-dev-shm-usage','--no-first-run',
     '--no-default-browser-check','--window-size=1280,720','--timeout=12000',
     '--virtual-time-budget=5000', # Allow bounded async SPA rendering before DOM and PNG capture.
     '--user-data-dir='+str(out/'profile'),'--screenshot='+str(out/'page.png'),'--dump-dom',url]
started=time.time()
with (out/'page.html').open('wb') as dom,(out/'browser.log').open('wb') as log:
    p=subprocess.run(cmd,stdout=dom,stderr=log,timeout=50)
html=(out/'page.html').read_text(errors='replace')
result={'exit_code':p.returncode,'url':sys.argv[2], 'elapsed_seconds':time.time()-started,
        'browser':subprocess.check_output(['chromium','--version'],text=True).strip(),
        'os_release':pathlib.Path('/etc/os-release').read_text(),
        'architecture':os.uname().machine,'sandbox_disabled':False,'render_budget_ms':5000,
        'screenshot_bytes':(out/'page.png').stat().st_size if (out/'page.png').exists() else 0,
        'pilot_saved': 'data-pilot-state="saved"' in html if sys.argv[2]=='pilot' else None}
result['passed']=p.returncode==0 and result['screenshot_bytes']>1000 and (sys.argv[2]!='pilot' or result['pilot_saved'])
(out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
# Browser profiles and SDKs are deliberately absent from the export allowlist.
print(json.dumps(result))
sys.exit(0 if result['passed'] else 1)
