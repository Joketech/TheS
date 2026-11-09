import config
"""Private stdlib CRM API: owner capability or signed, profile-bound agent calls."""
import hashlib,hmac,json,os,re,secrets,sqlite3,time
from pathlib import Path
from urllib.parse import parse_qs,urlsplit
from origin_policy import trusted_request,allowed_origins,secure_request
from .store import CRMStore,ROOT,AGENTS,Conflict,Duplicate
KEY_FILE=ROOT/'crm_runtime'/'crm.key'
def secret_key():
 if not KEY_FILE.is_file():return None
 if KEY_FILE.is_symlink() or KEY_FILE.stat().st_mode&0o077:raise PermissionError('Unsafe CRM key')
 key=KEY_FILE.read_bytes()
 if len(key)!=32:raise PermissionError('Invalid CRM key')
 return key
class CRMAPI:
 def __init__(self,store=None,secret=None,acceptance=None):
  self.store=store or CRMStore();self.secret=secret if secret is not None else secret_key();self.csrf=secrets.token_urlsafe(32);self.acceptance=acceptance
  with self.store.connect() as c:c.execute('CREATE TABLE IF NOT EXISTS agent_nonces(actor TEXT,nonce TEXT,stamp REAL,PRIMARY KEY(actor,nonce))')
 def body(self,h):
  lengths=h.headers.get_all('Content-Length',[])
  if h.headers.get_all('Transfer-Encoding') or len(lengths)!=1 or not lengths[0].isdigit() or not 0<int(lengths[0])<=65536 or h.headers.get_content_type()!='application/json':raise ValueError('Bounded JSON required')
  raw=h.rfile.read(int(lengths[0]))
  if len(raw)!=int(lengths[0]):raise ValueError('Incomplete body')
  def pairs(items):
   obj={}
   for k,v in items:
    if k in obj:raise ValueError('Duplicate fields')
    obj[k]=v
   return obj
  def constant(value):raise ValueError('Non-finite JSON is not supported')
  body=json.loads(raw,object_pairs_hook=pairs,parse_constant=constant)
  if not isinstance(body,dict) or set(body)!={'operation','data','request_id'}:raise ValueError('Exact operation, data and request_id fields required')
  return raw,body
 def authenticate_agent(self,h,raw):
  config.require_runtime()
  if self.secret is None:raise PermissionError('CRM integration not provisioned by own-instance owner')
  actor=h.headers.get('X-CRM-Agent');stamp=h.headers.get('X-CRM-Time','');nonce=h.headers.get('X-CRM-Nonce','')
  if actor not in AGENTS or not re.fullmatch(r'\d{10}',stamp) or abs(time.time()-int(stamp))>60 or not re.fullmatch('[a-f0-9]{32}',nonce):raise PermissionError('Invalid or expired agent authentication')
  expected=hmac.new(self.secret,actor.encode()+b'\n'+stamp.encode()+b'\n'+nonce.encode()+b'\n'+raw,hashlib.sha256).hexdigest()
  if not hmac.compare_digest(expected,h.headers.get('X-CRM-Signature','')):raise PermissionError('Invalid agent signature')
  ticket=h.headers.get('X-CRM-Acceptance')
  if self.acceptance is not None:
   if not ticket or not hmac.compare_digest(ticket,self.acceptance):raise PermissionError('CRM agent integration is staged; only authorised acceptance calls are enabled')
  elif ticket:raise PermissionError('Acceptance requests cannot address production CRM records')
  with self.store.connect() as c:
   try:c.execute('INSERT INTO agent_nonces VALUES(?,?,?)',(actor,nonce,time.time()))
   except sqlite3.IntegrityError:raise Conflict('Agent nonce already used') from None
   c.execute('DELETE FROM agent_nonces WHERE stamp<?',(time.time()-3600,))
  return actor
 def execute(self,actor,body):
  op=body['operation'];data=body['data']
  if not isinstance(op,str) or not isinstance(data,dict):raise ValueError('Invalid operation')
  if op=='state':
   if data:raise ValueError('State accepts no filters')
   return self.store.snapshot()
  if op=='read':
   if set(data)!={'entity','id'}:raise ValueError('Read requires entity and id')
   return self.store.read(data['entity'],data['id'])
  return self.store.command(actor,op,data,body['request_id'])
 def handle(self,h):
  def send(status,value):
   body=json.dumps(value,ensure_ascii=False,allow_nan=False).encode();h.send_response(status);h.send_header('Content-Type','application/json; charset=utf-8');h.send_header('Content-Length',str(len(body)));h.send_header('Cache-Control','no-store');h.send_header('X-Content-Type-Options','nosniff');h.end_headers();h.wfile.write(body)
  try:
   origins=allowed_origins(h)
   if not trusted_request(h):raise PermissionError('Untrusted origin or host')
   parsed=urlsplit(h.path)
   if h.command=='GET':
    if parsed.path=='/api/crm/state' and not parsed.query:
     state=self.store.snapshot();state['csrf']=self.csrf
     status_file=ROOT/'crm_runtime'/'agent-status.json'
     try:state['agent_access']=json.loads(status_file.read_text())
     except (OSError,ValueError):state['agent_access']={'state':'Not yet verified','profiles':[]}
     return send(200,state)
    if parsed.path=='/api/crm/record':
     q=parse_qs(parsed.query)
     if set(q)!={'entity','id'} or any(len(v)!=1 for v in q.values()):raise ValueError('Invalid read query')
     return send(200,self.store.read(q['entity'][0],q['id'][0]))
    return send(404,{'error':'CRM route unavailable'})
   if h.command!='POST' or parsed.query or parsed.path not in {'/api/crm/command','/api/crm/agent'}:return send(404,{'error':'CRM route unavailable'})
   if parsed.path=='/api/crm/command':
    if h.headers.get('Origin') not in origins or not hmac.compare_digest(h.headers.get('X-THE-S-CRM-CSRF',''),self.csrf):raise PermissionError('Same-origin owner control capability required')
   raw,body=self.body(h);actor=self.authenticate_agent(h,raw) if parsed.path.endswith('/agent') else 'OWNER'
   return send(200,self.execute(actor,body))
  except Duplicate as exc:return send(409,{'error':str(exc),'duplicates':exc.records,'review_required':True})
  except Conflict as exc:return send(409,{'error':str(exc)})
  except PermissionError as exc:return send(403,{'error':str(exc)})
  except LookupError as exc:return send(404,{'error':str(exc)})
  except (ValueError,TypeError,KeyError,sqlite3.IntegrityError) as exc:return send(400,{'error':str(exc) if isinstance(exc,ValueError) else 'Invalid CRM relationship or fields'})
  except (BrokenPipeError,ConnectionResetError):return
  except Exception:return send(503,{'error':'CRM operation outcome unavailable; inspect records before retrying'})
