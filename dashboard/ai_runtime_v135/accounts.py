import config
"""Read-only existing-account refresh, executed by the installed native runtime.

No login, token refresh, funding or model call is initiated. Fixed allowlisted
profiles only. Outputs contain authentication status/credit metadata, not IDs,
addresses, tokens, account names or credentials.
"""
import concurrent.futures
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT=config.CATALOG_DIR
NATIVE=Path(str(config.NATIVE_ROOT))
HOMES={config.RUNTIME_PROFILES[a]:Path(h) for a,h in config.RUNTIME_HOMES.items()}


def refresh_existing():
    config.require_runtime()
    def protected():
        return {(profile,name):hashlib.sha256((home/name).read_bytes()).hexdigest()
                if (home/name).exists() else None
                for profile,home in HOMES.items() for name in ('config.yaml','auth.json','.env')}
    before=protected()
    def probe(profile):
        env=config.child_environment(HOMES[profile])
        response=subprocess.run([config.HERMES_PYTHON,'-B',str(Path(__file__).parent/'catalog'/'probe_existing_accounts.py'),profile],
                                env=env,cwd=str(config.ROOT),capture_output=True,text=True,timeout=25)
        if response.returncode or len(response.stdout)>20000:
            return {'profile':profile,'probe_error':'Existing account verification unavailable'}
        value=json.loads(response.stdout)
        if not isinstance(value,dict) or value.get('profile')!=profile:
            raise ValueError('Native account binding mismatch')
        return value
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        rows=list(pool.map(probe,HOMES))
    if before!=protected():
        raise RuntimeError('Read-only account refresh changed protected state; result not published')
    evidence={'profiles':rows,'protected_files_unchanged':True}
    target=ROOT/'existing-account-verification.json'
    # Preserve the working evidence before atomic replacement.
    if target.exists():
        stamp=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H-%M-%S-%f')
        backup=ROOT/('account-verification-before-'+stamp+'.json')
        with backup.open('xb') as stream:stream.write(target.read_bytes())
    temp=ROOT/('.account-verification-'+os.urandom(8).hex()+'.json')
    try:
        with temp.open('x') as stream:json.dump(evidence,stream,indent=2,allow_nan=False)
        os.replace(temp,target)
    finally:
        temp.unlink(missing_ok=True)
    return evidence
