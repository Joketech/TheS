import config
"""Additive facade consumed by the existing owner Unlock and existing Chat UI."""
from http.cookies import SimpleCookie
from pathlib import Path
import hashlib,json,re,time,threading
from urllib.parse import parse_qs
from origin_policy import allowed_origins,secure_request
from .bridge import Bridge,unique
from .workflow import WorkflowError,closed_sqlite,canonical
COOKIE='the_s_task_owner'
HELP='Task workflow in existing MIRA Chat. Unlock first: Agents → Daily AI Spend / Model Control → owner Unlock. Commands: /workflow create {"objective":"...","criteria":["..."],"assignee":"spectra","scope":"..."}; /workflow list {}; /workflow show {"task_id":"..."}; /workflow approve {"task_id":"...","contract_hash":"..."}; /workflow send {"task_id":"..."} starts real MIRA coordination; /workflow refresh {"task_id":"..."} reads exact receipts and advances the approved handoff; /workflow read {"task_id":"...","revision":1}; /workflow review {"task_id":"...","revision":1,"sha256":"...","decision":"accepted"} (or changes-requested/rejected with note); /workflow revise {"task_id":"...","revision":1,"sha256":"..."} starts bounded correction after changes-requested; /workflow revoke {"task_id":"...","note":"..."}. No acceptance is inferred from completed execution. Task sign-in lasts at most five minutes independently of financial auto-lock; /workflow lock {} explicitly signs out task access. No automatic resend. Refresh is explicit; no hidden AI polling.'
class Web:
 def __init__(self,root,auth,routes,*,factory=None,synthetic=False):
  self.root=Path(root);directory=self.root/'task-workflow-data';directory.mkdir(mode=0o700,exist_ok=True);self.bridge=Bridge(directory/'workflow.db',auth,routes,factory=factory,synthetic=synthetic);self.inflight=0;self.lock=threading.RLock()
 def signin(self,session,secure=False):
  ticket=self.bridge.authority.issue(session)
  return COOKIE+'='+ticket+'; Path=/api/chat; Max-Age=300; HttpOnly; SameSite=Strict'+('; Secure'if secure else'')
 def cookie(self,raw):
  if not isinstance(raw,str)or len(raw)>16384 or sum(piece.strip().startswith(COOKIE+'=')for piece in raw.split(';'))!=1:return ''
  try:parsed=SimpleCookie();parsed.load(raw);return parsed[COOKIE].value
  except Exception:return ''
 def actor(self,raw):return self.bridge.authority.owner(self.cookie(raw))
 def status(self,raw=''):
  try:self.actor(raw);valid=True
  except WorkflowError:valid=False
  return {'interface':'existing MIRA Chat /workflow commands','owner_task_authenticated':valid,'owner_signin_action':'Agents → Daily AI Spend / Model Control → existing owner Unlock, then return to MIRA Chat','task_purpose_enabled':True,'native_dispatch_requires_exact_owner_approval_and_attested_MIRA':True,'automatic_ai_polling':False,'pinned_agents':sorted(self.bridge.routes),'active_commands':self.inflight,'human_login_proven_by_permission':False}
 def download(self,h,path):
  try:
   actor=self.actor(h.headers.get('Cookie',''));match=re.fullmatch(r'/api/chat/workflow/artifact/([a-f0-9]{32})/([1-9][0-9]{0,8})',path)
   if not match:raise WorkflowError(404,'Exact artifact revision required')
   name,raw=self.bridge.store.artifact(actor,match[1],int(match[2]));h.send_response(200);h.send_header('Content-Type','text/markdown; charset=utf-8');h.send_header('Content-Disposition','attachment; filename="'+name+'"');h.send_header('Content-Length',str(len(raw)));h.send_header('Cache-Control','no-store');h.send_header('X-Content-Type-Options','nosniff');h.end_headers();h.wfile.write(raw)
  except WorkflowError as error:h.json_response(error.status,{'error':str(error)})
 def recover(self,h,parts):
  values=parse_qs(parts.query,keep_blank_values=True)
  if set(values)!={'agent','request_id'}or any(len(v)!=1 for v in values.values()):return False
  agent=values['agent'][0];key=values['request_id'][0]
  with closed_sqlite(self.bridge.path)as c:row=c.execute('SELECT reply FROM wf_chat WHERE request_id=? AND agent=?',(key,agent)).fetchone()
  if not row:return False
  try:
   self.actor(h.headers.get('Cookie',''));pending=row[0].startswith('Outcome pending/unconfirmed.');h.json_response(200,{'request_id':key,'agent':agent,'status':'unknown'if pending else'completed','result':None if pending else row[0],'source':'workflow_control_response','can_resend':False,'reason':'Read exact task state; no native work is cancelled or repeated'if pending else None})
  except WorkflowError as error:h.json_response(error.status,{'error':str(error)})
  return True
 def history(self,agent,raw,messages):
  try:self.actor(raw)
  except WorkflowError:return messages
  with closed_sqlite(self.bridge.path)as c:rows=c.execute('SELECT prompt,reply,stamp FROM wf_chat WHERE agent=? ORDER BY stamp DESC LIMIT 25',(agent,)).fetchall()
  extra=[]
  for prompt,reply,stamp in reversed(rows):extra.extend([{'role':'user','text':prompt,'ts':stamp},{'role':'assistant','text':reply,'ts':stamp+0.000001}])
  return sorted([*messages,*extra],key=lambda m:m.get('ts')or 0)[-50:]
 def history_count(self,agent,raw):
  try:self.actor(raw)
  except WorkflowError:return 0
  with closed_sqlite(self.bridge.path)as c:return 2*c.execute('SELECT COUNT(*) FROM wf_chat WHERE agent=?',(agent,)).fetchone()[0]
 def chat(self,h,body,key):
  text=body.get('text')if isinstance(body,dict)else None
  if not isinstance(text,str)or not re.match(r'^\s*/workflow(?:\s|$)',text):return False
  try:
   if h.headers.get('Origin')not in allowed_origins(h):raise WorkflowError(403,'Same-origin task controls required')
   if any(len(h.headers.get_all(name,[]))>1 for name in ('Host','Origin','Cookie','Content-Length','X-Request-ID')):raise WorkflowError(400,'Duplicate security header')
   if body.get('agent')!='mira'or set(body)-{'agent','text','attachments'}or body.get('attachments'):raise WorkflowError(400,'Use MIRA Chat for workflow commands without attachments')
   if not isinstance(key,str)or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}',key)or len(text.encode())>16384:raise WorkflowError(400,'Bounded command and stable request identifier required')
   parts=text.strip().split(None,2);op=parts[1]if len(parts)>1 else'help'
   if op=='help':
    self.actor(h.headers.get('Cookie',''));reply='[Workflow control — no AI turn]\n'+HELP;fingerprint=hashlib.sha256(canonical(body).encode()).hexdigest()
    with closed_sqlite(self.bridge.path)as c:
     old=c.execute('SELECT fingerprint FROM wf_chat WHERE request_id=?',(key,)).fetchone()
     if old and old[0]!=fingerprint:raise WorkflowError(409,'Request identifier already used')
     c.execute('INSERT OR IGNORE INTO wf_chat VALUES(?,?,?,?,?,?)',(key,'mira',fingerprint,text,reply,time.time()))
   else:
    owner=self.actor(h.headers.get('Cookie',''));data=json.loads(parts[2],object_pairs_hook=unique)if len(parts)>2 else{};fingerprint=hashlib.sha256(canonical(body).encode()).hexdigest()
    with self.bridge.store.lock:
     with closed_sqlite(self.bridge.path)as c:old=c.execute('SELECT fingerprint,reply FROM wf_chat WHERE request_id=?',(key,)).fetchone()
     if old:
      if old[0]!=fingerprint:raise WorkflowError(409,'Request identifier already belongs to another command')
      reply=old[1]
     else:
      with closed_sqlite(self.bridge.path)as c:c.execute('INSERT INTO wf_chat VALUES(?,?,?,?,?,?)',(key,'mira',fingerprint,text,'Outcome pending/unconfirmed. Read task state and refresh exact receipts; never resend accepted work.',time.time()))
      with self.lock:self.inflight+=1
      try:
       if op=='lock':
        if data!={}:raise WorkflowError(400,'Task sign-out accepts an empty object only')
        self.bridge.authority.revoke(owner.identity._credential);result={'task_signin':'locked','accepted_native_work':'not cancelled','financial_access':'unchanged'}
       else:result=self.bridge.command(owner,op,data,key)
       reply='[Workflow control — no AI turn]\n'+json.dumps(result,ensure_ascii=False,indent=2)
      except WorkflowError as error:
       reply='Command blocked: '+str(error)
       with closed_sqlite(self.bridge.path)as c:c.execute('UPDATE wf_chat SET reply=? WHERE request_id=?',(reply,key))
       raise
      finally:
       with self.lock:self.inflight-=1
      with closed_sqlite(self.bridge.path)as c:c.execute('UPDATE wf_chat SET reply=? WHERE request_id=?',(reply,key))
   raw=('\n'.join(json.dumps(e,ensure_ascii=False)for e in ({'type':'replace','text':reply},{'type':'done'}))+'\n').encode();h.send_response(200);h.send_header('Content-Type','application/x-ndjson; charset=utf-8');h.send_header('Cache-Control','no-store');h.send_header('Content-Length',str(len(raw)));h.send_header('X-Content-Type-Options','nosniff');h.end_headers();h.wfile.write(raw)
  except WorkflowError as error:h.json_response(error.status,{'error':str(error)})
  except (ValueError,TypeError,IndexError):h.json_response(400,{'error':'Use /workflow help or a command followed by one JSON object'})
  except (BrokenPipeError,ConnectionResetError,TimeoutError):pass # Accepted work remains journalled.
  return True
