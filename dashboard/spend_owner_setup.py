import config
"""One-time local owner enrollment. Run in an existing trusted SSH TTY.
Never accepts passwords through arguments, environment, pipes or Telegram.
"""
from pathlib import Path
import getpass,hmac,os,sys
from spend_auth import SpendAuth
ROOT=config.DATA_DIR

def enroll(root=ROOT,*,prompt=getpass.getpass,interactive=False):
    if not interactive:raise PermissionError('Use a private interactive SSH terminal')
    Path(root).mkdir(parents=True,exist_ok=True)
    auth=SpendAuth(root)
    if auth.configured:raise FileExistsError('Owner already configured; no reset performed')
    password=prompt('New owner password (12–256 UTF-8 bytes; hidden): ')
    repeat=prompt('Repeat owner password (hidden): ')
    if not hmac.compare_digest(password.encode('utf-8'),repeat.encode('utf-8')):raise ValueError('Passwords do not match; nothing configured')
    auth._password(password);auth.configure_owner(password)
    del password,repeat
    # Independent reporting capability, never the owner's password.
    # Agent reporting is separately provisioned by the administrator.
    from initializer import initialize_crm_key
    initialize_crm_key(Path(root))
    with auth._private() as fd:
        for name in ('spend-setup.key','spend-setup.json'):
            try:auth._read(fd,name);os.unlink(name,dir_fd=fd)
            except FileNotFoundError:pass
    return True

if __name__=='__main__':
    if len(sys.argv)!=1:raise SystemExit('No password arguments are accepted. Run this command without arguments.')
    try:
        enroll(interactive=sys.stdin.isatty() and sys.stderr.isatty())
        print('Owner password set. Agent reporting remains disconnected. Return to Agents → Daily AI Spend and unlock. No restart needed.')
    except (PermissionError,FileExistsError,ValueError) as e:raise SystemExit(str(e))
    except (KeyboardInterrupt,EOFError):raise SystemExit('Setup cancelled; verify configuration before retrying.')
    except Exception:raise SystemExit('Setup could not complete. Contact SPECTRA; do not send your password. No automatic reset or retry was made.')
