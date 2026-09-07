"""Run on the Pi after tests. Backup, guarded install, health check and rollback."""
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import urllib.request

ROOT=Path('/home/edi/printer-light-studio')
LIVE=Path('/home/edi/printer-led-mcp/server.py')
expected=hashlib.sha256((ROOT/'reference/server.py').read_bytes()).hexdigest()
assert hashlib.sha256(LIVE.read_bytes()).hexdigest()==expected, 'Live source changed; reconcile instead of overwriting'
staged=(ROOT/'server.staged.py').read_bytes(); ast.parse(staged)
assert not (ROOT/'data/layout.json').exists(), 'An existing studio layout needs an update-specific installation'
backup=LIVE.with_name('server.py.pre-print-tide-'+time.strftime('%Y%m%d-%H%M%S'))
shutil.copy2(LIVE,backup)
temp=LIVE.with_name('server.py.print-tide-tmp');temp.write_bytes(staged);shutil.copymode(LIVE,temp);os.replace(temp,LIVE)
try:
    subprocess.run(['systemctl','--user','restart','printer-led-mcp.service'],check=True,timeout=100)
    success=False
    for _ in range(20):
        try:
            with urllib.request.urlopen('http://100.65.17.33:8772/api/state',timeout=2) as response:
                data=json.load(response)
            if len(data['config']['slots'])==7 and not data['demo']:
                success=True;break
        except Exception: pass
        time.sleep(1)
    if not success: raise RuntimeError('UI health check failed')
    receipt=dict(installed_at=time.strftime('%Y-%m-%d %H:%M:%S %Z'),backup=str(backup),source_sha256=hashlib.sha256(staged).hexdigest(),ui='http://100.65.17.33:8772/',status='installed')
    (ROOT/'deployment/install-receipt.json').write_text(json.dumps(receipt,indent=2))
    print(json.dumps(receipt))
except Exception:
    shutil.copy2(backup,LIVE)
    subprocess.run(['systemctl','--user','restart','printer-led-mcp.service'],check=True,timeout=100)
    raise RuntimeError('Installation failed; original source restored and service restarted') from None
