import config
"""Short-lived, local-administrator-prepared owner enrollment. No public signup.
The raw administrative capability never goes in a URL, API body or console.
"""
from pathlib import Path
import hashlib,json,re,secrets,time
from spend_auth import SpendAuth
from spend_gateway import SpendGateway

def prepare(root,ttl=900):
    auth=SpendAuth(Path(root))
    if auth.configured:raise FileExistsError('Owner already configured; no automatic reset')
    if type(ttl) is not int or not 1<=ttl<=900:raise ValueError('Invalid setup lifetime')
    token=secrets.token_urlsafe(32)
    with auth._private(create=True) as fd:
        auth._write(fd,'spend-setup.key',(token+'\n').encode())
        auth._write(fd,'spend-setup.json',json.dumps({'version':1,'digest':hashlib.sha256(token.encode()).hexdigest(),'expires_at':time.time()+ttl}).encode())

def build_gateway(native,root):
    auth=SpendAuth(Path(root));digest=None;expires=0
    if not auth.configured:
        try:
            with auth._private() as fd:meta=json.loads(auth._read(fd,'spend-setup.json'),object_pairs_hook=auth._unique)
            if (set(meta)=={'version','digest','expires_at'} and meta['version']==1
                and isinstance(meta['digest'],str) and re.fullmatch('[a-f0-9]{64}',meta['digest'])
                and type(meta['expires_at']) in (int,float) and time.time()<meta['expires_at']<=time.time()+901):
                digest=meta['digest'];expires=meta['expires_at']
        except (OSError,ValueError,TypeError,KeyError):pass
    return SpendGateway(native,auth,allow_native_mutations=config.EXTERNAL_RUNTIME_ENABLED,setup_digest=digest,setup_expires=expires)
