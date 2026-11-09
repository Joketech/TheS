"""Instance configuration. No discovery of host profiles, accounts, or credentials."""
import json
import os
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo
ROOT = Path(__file__).resolve().parent
REPORTING_TIMEZONE = os.environ.get('REPORTING_TIMEZONE', 'UTC')
REPORTING_ZONE = ZoneInfo(REPORTING_TIMEZONE)  # Validate before creating instance data.

def absolute_env(name, default):
    value = Path(os.environ.get(name, str(default)))
    if not value.is_absolute() or '..' in value.parts:
        raise ValueError(name + ' must be an absolute path without traversal')
    return value.resolve()

DATA_DIR = absolute_env('DATA_DIR', ROOT / 'data')
CONTENT_DIR = absolute_env('CONTENT_DIR', DATA_DIR / 'content')
BOARD_PATH = DATA_DIR / 'board.db'
CRM_DIR = DATA_DIR / 'crm_runtime'
CHAT_PATH = DATA_DIR / 'chat-requests.db'
UPLOAD_DIR = DATA_DIR / 'chat-attachments'
WORKFLOW_DIR = DATA_DIR / 'task-workflow-data'
SPEND_PATH = DATA_DIR / 'ai_spend.db'
AGENT_LOG_DIR = DATA_DIR / 'agent-logs'
CATALOG_DIR = DATA_DIR / 'catalog'
EXTERNAL_RUNTIME_ENABLED = os.environ.get('EXTERNAL_RUNTIME_ENABLED') == '1'
HERMES_HOME = absolute_env('INSTANCE_HERMES_HOME', absolute_env('HERMES_HOME', DATA_DIR / 'disconnected-runtime')) if EXTERNAL_RUNTIME_ENABLED else None
RUNTIME_HOME = HERMES_HOME or DATA_DIR / 'disconnected-runtime'
HERMES_PYTHON = os.environ.get('HERMES_PYTHON', '')
NATIVE_ROOT = absolute_env('NATIVE_ROOT', DATA_DIR / 'unconfigured-native')
CONTROL_SOCKET = absolute_env('CONTROL_SOCKET', RUNTIME_HOME / 'unconfigured.sock')
PROFILE_ROUTES = {}
if EXTERNAL_RUNTIME_ENABLED:
    if not os.environ.get('HERMES_HOME'):
        raise ValueError('External runtime requires explicit HERMES_HOME')
    route_file = absolute_env('PROFILE_ROUTES', DATA_DIR / 'profile-routes.json')
    PROFILE_ROUTES = json.loads(route_file.read_text())
    if not isinstance(PROFILE_ROUTES, dict):
        raise ValueError('PROFILE_ROUTES must contain an object')
    for agent, route in PROFILE_ROUTES.items():
        if agent not in ('mira','sage','seline','surge','spectra') or not isinstance(route, dict):
            raise ValueError('Invalid profile route')
        home = Path(route.get('home',''))
        if not home.is_absolute() or '..' in home.parts or not home.resolve().is_relative_to(HERMES_HOME):
            raise ValueError('Profile home must be explicit and inside own HERMES_HOME')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', route.get('profile','')):
            raise ValueError('Explicit profile identity required')
RUNTIME_PROFILES = {a:r['profile'] for a,r in PROFILE_ROUTES.items()}
RUNTIME_HOMES = {a:str(Path(r['home']).resolve()) for a,r in PROFILE_ROUTES.items()}
GROUP_ID = os.environ.get('RUNTIME_GROUP_ID','') if EXTERNAL_RUNTIME_ENABLED else ''
THREADS = {a:str(r['thread_id']) for a,r in PROFILE_ROUTES.items() if 'thread_id' in r}
WORKFLOW_ORIGIN = os.environ.get('WORKFLOW_ORIGIN','unconfigured') if EXTERNAL_RUNTIME_ENABLED else 'unconfigured'
TLS_HOSTNAME = os.environ.get('TLS_HOSTNAME','')
if TLS_HOSTNAME and not re.fullmatch(r'[A-Za-z0-9.-]+', TLS_HOSTNAME):
    raise ValueError('Invalid TLS_HOSTNAME')
BRAND_PATH = absolute_env('BRAND_CONFIG', ROOT / 'config' / 'brand.json')
BRAND = json.loads(BRAND_PATH.read_text()) if BRAND_PATH.is_file() else {'name':'Mission Control','company':'Your Company','attribution':'Mission Control','logo_text':'MC','logo_url':'','accent':'#d4a66a','accent_soft':'#b37a4c','agents':[{'id':a,'label':label,'role':role,'code':f'A-0{i}','initials':initials} for i,(a,label,role,initials) in enumerate([('mira','Coordinator','Coordination','CO'),('sage','Researcher','Research','RE'),('seline','Writer','Writing','WR'),('surge','Growth','Marketing','GR'),('spectra','Engineer','Engineering','EN')])]}
if not isinstance(BRAND,dict) or set(BRAND) != {'name','company','attribution','logo_text','logo_url','accent','accent_soft','agents'}:
    raise ValueError('Invalid branding fields')
for key in ('name','company','attribution','logo_text','logo_url','accent','accent_soft'):
    if not isinstance(BRAND[key],str) or len(BRAND[key]) > 500:
        raise ValueError('Invalid brand text')
logo=BRAND['logo_url']
if logo and (not re.fullmatch(r'/assets/[A-Za-z0-9_./-]+',logo) or '..' in logo.split('/') or '//' in logo):
    raise ValueError('logo_url must be a safe local /assets/ path')
if any(not re.fullmatch(r'#[0-9a-fA-F]{6}', BRAND[k]) for k in ('accent','accent_soft')):
    raise ValueError('Invalid brand color')
if not isinstance(BRAND['agents'],list) or sorted(a.get('id','') for a in BRAND['agents']) != sorted(['mira','sage','seline','surge','spectra']):
    raise ValueError('Branding must retain the five internal agent IDs')
for agent in BRAND['agents']:
    if set(agent) != {'id','label','role','code','initials'} or any(not isinstance(v,str) or len(v)>180 for v in agent.values()):
        raise ValueError('Invalid agent branding')

def require_runtime():
    if not EXTERNAL_RUNTIME_ENABLED:
        raise PermissionError('External runtime unavailable: explicitly configure your own instance first')

def profile_home(agent):
    require_runtime()
    if agent.lower() not in RUNTIME_HOMES:
        raise PermissionError('Profile route unavailable')
    return Path(RUNTIME_HOMES[agent.lower()])

def child_environment(home):
    require_runtime()
    selected=Path(home).resolve()
    if str(selected) not in RUNTIME_HOMES.values():
        raise PermissionError('Child home must match an explicit own-instance profile route')
    if not HERMES_PYTHON or not Path(HERMES_PYTHON).is_absolute() or not Path(HERMES_PYTHON).is_file():
        raise PermissionError('Explicit native Python executable unavailable')
    names=('PATH','HOME','LANG','LC_ALL','TZ','TMPDIR','SSL_CERT_FILE','SSL_CERT_DIR','HTTP_PROXY','HTTPS_PROXY','NO_PROXY','PROFILE_ROUTES','RUNTIME_GROUP_ID','WORKFLOW_ORIGIN')
    env={k:os.environ[k] for k in names if k in os.environ}
    env.update(DATA_DIR=str(DATA_DIR),BRAND_CONFIG=str(BRAND_PATH),EXTERNAL_RUNTIME_ENABLED='1',
        INSTANCE_HERMES_HOME=str(HERMES_HOME),HERMES_HOME=str(selected),HERMES_PYTHON=HERMES_PYTHON,
        NATIVE_ROOT=str(NATIVE_ROOT),CONTROL_SOCKET=str(CONTROL_SOCKET),PYTHONPATH=str(ROOT)+os.pathsep+str(NATIVE_ROOT/'workspace'),PYTHONDONTWRITEBYTECODE='1')
    return env
