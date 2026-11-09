import config
"""Integrated AI routes for the existing stdlib dashboard, not a proxy server."""
import concurrent.futures,hmac,json,os,re,secrets,socket,stat,struct,threading,time
from pathlib import Path
from urllib.parse import parse_qs,urlsplit
from origin_policy import trusted_request,allowed_origins,secure_request
from .control_core import Requests,CATALOG,AGENTS
from .model_control import Catalog,Controls,PRESETS
from .account_policy import AccountPolicy
from .plugin import HOMES
from .service import ModelControls
from .spend_control import period_bounds,collect_legacy

ROLES={'MIRA':'Orchestrator','SAGE':'Research and Sourcing','SELINE':'Writing and Editing','SURGE':'Marketing, Growth, Monetization','SPECTRA':'Engineering, Automation, Integrations'}

def active_model(agent):
    """Read approved runtime metadata; never return session IDs/tools/task text."""
    try:
        config.require_runtime()
        path=config.CONTROL_SOCKET;node=path.stat()
        if not stat.S_ISSOCK(node.st_mode) or node.st_uid!=os.geteuid():raise PermissionError()
        with socket.socket(socket.AF_UNIX) as peer:
            peer.settimeout(4);peer.connect(str(path))
            if struct.unpack('3i',peer.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))[1]!=os.geteuid():raise PermissionError()
            peer.sendall((json.dumps({'protocol':1,'id':1,'verb':'active-work','params':{'profile':AGENTS[agent]}})+'\n').encode())
            raw=b''
            while b'\n' not in raw:
                data=peer.recv(65536)
                if not data or len(raw)+len(data)>1000000:raise ValueError()
                raw+=data
        reply=json.loads(raw.split(b'\n',1)[0]);units=reply.get('result',{}).get('active_work')
        if reply.get('ok') is not True or not isinstance(units,list):raise ValueError()
        models=sorted({unit['model'] for unit in units if isinstance(unit.get('model'),str) and re.fullmatch(r'[A-Za-z0-9_./:@+~ -]{1,180}',unit['model'])})
        return {'state':'WORKING' if units else 'IDLE','models':models,'source':'native active-work metadata'}
    except Exception:return {'state':'UNAVAILABLE','models':[],'source':'native activity unavailable'}

class AIEndpoints:
    def __init__(self,*,store=None,models=None,sources=None):
        self.store=store or Requests();self.catalog=Catalog(CATALOG)
        self.preferences=Controls(self.store.ledger,self.catalog)
        self.models=models or ModelControls(self.store,self.catalog)
        self.sources=sources if sources is not None else {name:home/'state.db' for name,home in HOMES.items()}
        self.csrf=secrets.token_urlsafe(32);self.cache={};self.cache_lock=threading.Lock()

    def accounts(self):
        if not config.EXTERNAL_RUNTIME_ENABLED:return AccountPolicy({'profiles':[],'protected_files_unchanged':False}).payload()
        return AccountPolicy(json.loads((CATALOG/'existing-account-verification.json').read_text())).payload()

    def model_snapshot(self,agent):
        with self.cache_lock:entry=self.cache.get(agent)
        if entry and time.monotonic()-entry[0]<8:return entry[1]
        try:
            value=self.models.status(agent)
            health=self.models.worker(agent,'health')
            value.update(control_available=health.get('active') is True,observed_at=time.time())
        except Exception:
            value={'provider':None,'model':None,'control_available':False,'observed_at':None,
                   'session_model':None,'session_provider':None,'pending_model':None,'pending_provider':None}
        with self.cache_lock:self.cache[agent]=(time.monotonic(),value)
        return value

    def state(self,query):
        q=parse_qs(query,keep_blank_values=True)
        if set(q)-{'period','start','end'} or any(len(v)!=1 for v in q.values()):raise ValueError('Ambiguous date filter')
        first,last,label=period_bounds(q.get('period',['today'])[0],q.get('start',[''])[0],q.get('end',[''])[0])
        imported=collect_legacy(self.store.ledger,self.sources);summary=self.store.ledger.summary(first,last)
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            statuses=dict(zip(AGENTS,pool.map(self.model_snapshot,AGENTS)))
        # The shared ledger also serves the old staged prototype. Its static
        # no-observer caption must not misdescribe this activated integration.
        summary['coverage_reason']='Provider billing charges are unavailable. Dated native records are shown separately; historical aggregate buckets cannot establish complete daily usage.'
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            activity=dict(zip(AGENTS,pool.map(active_model,AGENTS)))
        choices=self.models.choices()
        fleet=[{'name':agent,'role':ROLES[agent],**statuses[agent],
                'current_activity':activity[agent],'one_task_choice':choices.get(agent)} for agent in AGENTS]
        with self.store.ledger.connect() as c:
            health=[dict(r) for r in c.execute('SELECT agent,last_event,captured,discarded FROM observer_health ORDER BY agent')]
        return {'period_label':label,'summary':summary,'catalog':self.catalog.payload(),
            'preferences':self.preferences.read(),'preset_labels':PRESETS,'budget':self.preferences.budget(summary),
            'fleet':fleet,'accounts':self.accounts(),'import_status':imported,'csrf':self.csrf,
            'live_mutations_enabled':config.EXTERNAL_RUNTIME_ENABLED,'collection':{'active_profiles':[a for a in AGENTS if statuses[a]['control_available']],
                'health':health,'coverage':'Incomplete — collection began at plugin activation; historical per-call and hidden SDK retry coverage is not guaranteed'},
            'model_scope':'Profile default plus approved Mission Control group session; explicit private-session/job overrides are not rewritten'}

    @staticmethod
    def json_body(handler):
        lengths=handler.headers.get_all('Content-Length',[])
        if handler.headers.get_all('Transfer-Encoding') or len(lengths)!=1 or not lengths[0].isdigit():raise ValueError('Ambiguous request size')
        size=int(lengths[0])
        if not 0<size<=20000 or handler.headers.get_content_type()!='application/json':raise ValueError('Bounded JSON required')
        raw=handler.rfile.read(size)
        if len(raw)!=size:raise ValueError('Incomplete request')
        def unique(pairs):
            result={}
            for key,value in pairs:
                if key in result:raise ValueError('Duplicate JSON field')
                result[key]=value
            return result
        body=json.loads(raw,object_pairs_hook=unique)
        if not isinstance(body,dict):raise ValueError('JSON object required')
        return body

    def post(self,path,body):
        if path=='/api/ai/preferences':
            result=self.preferences.save(body)
            if result!=self.preferences.read():raise RuntimeError('Preference readback failed')
            return {'preferences':result,'live_model_applied':False,'scope':'project preferences; native model controls are separate'}
        if path=='/api/ai/accounts/refresh':
            if body!={}:raise ValueError('Account refresh accepts no credentials or changes')
            from .accounts import refresh_existing
            evidence=refresh_existing()
            return {'accounts':AccountPolicy(evidence).payload(),'live_model_applied':False}
        if path=='/api/ai/models/apply':
            if set(body)!={'agent','key','request_id'}:raise ValueError('Invalid model request fields')
            result=self.models.apply(body['agent'],body['key'],body['request_id'])
        elif path=='/api/ai/models/fleet':
            if set(body)!={'key','request_id'}:raise ValueError('Invalid fleet request fields')
            result=self.models.fleet(body['key'],body['request_id'])
        elif path=='/api/ai/models/once':
            if set(body)!={'agent','key'}:raise ValueError('Invalid one-task request fields')
            result=self.models.once(body['agent'],body['key'])
        else:raise LookupError('Outside AI control scope')
        with self.cache_lock:self.cache.clear()
        return result

    def handle(self,h):
        def send(status,value):
            raw=json.dumps(value,allow_nan=False,ensure_ascii=False).encode()
            h.send_response(status);h.send_header('Content-Type','application/json; charset=utf-8')
            h.send_header('Content-Length',str(len(raw)));h.send_header('Cache-Control','no-store')
            h.send_header('X-Content-Type-Options','nosniff');h.end_headers();h.wfile.write(raw)
        try:
            origin=h.headers.get('Origin');same=allowed_origins(h)
            if not trusted_request(h):return send(403,{'error':'Untrusted origin or host'})
            parsed=urlsplit(h.path)
            if h.command=='GET':
                if parsed.path!='/api/ai/state':return send(404,{'error':'Not found'})
                return send(200,self.state(parsed.query))
            if h.command!='POST':return send(405,{'error':'Method not allowed'})
            if origin not in same or not hmac.compare_digest(h.headers.get('X-THE-S-CSRF',''),self.csrf):
                return send(403,{'error':'Same-origin authenticated control capability required'})
            if parsed.query:raise ValueError('Control query parameters are not accepted')
            h.connection.settimeout(120)
            return send(200,self.post(parsed.path,self.json_body(h)))
        except PermissionError as exc:return send(403,{'error':str(exc)})
        except (ValueError,TypeError,KeyError):return send(400,{'error':'Invalid or unavailable selection; no unsupported account or command accepted'})
        except LookupError:return send(404,{'error':'Control target unavailable'})
        except Exception:return send(503,{'error':'Operation outcome unavailable; inspect effective settings before retrying'})
