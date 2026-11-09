import config
"""Isolated Stage B domain, durable outbox and immutable revision store (stdlib).

No native command is executed by this module. Identity comes from an injected
server authority map, NEVER JSON role fields. Default authority denies everyone.
Fixture principal IDs are explicitly synthetic and are not the owner's identity.
"""
from dataclasses import dataclass
from pathlib import Path
from contextlib import contextmanager
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
import datetime,hashlib,json,re,sqlite3,threading,uuid
AGENTS=('mira','sage','seline','surge','spectra')
ORIGIN=config.WORKFLOW_ORIGIN
FIXTURE_PRINCIPALS={r:'fixture-'+r for r in ('owner',*AGENTS,'native')}
EXECUTION={'not_started','queued','working','waiting','blocked','failed','completed','unknown'}
SCHEMA='''
CREATE TABLE IF NOT EXISTS wf_meta(version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS wf_tasks(id TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS wf_dispatches(task_id TEXT PRIMARY KEY, logical_id TEXT UNIQUE NOT NULL, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS wf_revisions(task_id TEXT NOT NULL, revision INTEGER NOT NULL, name TEXT NOT NULL, sha256 TEXT NOT NULL, content BLOB NOT NULL, evidence TEXT NOT NULL, author TEXT NOT NULL, PRIMARY KEY(task_id,revision));
CREATE TABLE IF NOT EXISTS wf_audit(seq INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, actor TEXT NOT NULL, operation TEXT NOT NULL, evidence TEXT NOT NULL, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS wf_commands(actor TEXT NOT NULL, key TEXT NOT NULL, fingerprint TEXT NOT NULL, result TEXT NOT NULL, PRIMARY KEY(actor,key));
'''
_LOCKS={};_LOCK=threading.Lock()
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True)
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def text(value,name,limit=4000):
 if not isinstance(value,str)or not value.strip()or len(value)>limit or '\x00'in value:raise WorkflowError(400,'Invalid '+name)
 return value
class WorkflowError(Exception):
 def __init__(self,status,message):super().__init__(message);self.status=status
@contextmanager
def closed_sqlite(path):
 """Commit/rollback and close are distinct SQLite responsibilities."""
 connection=sqlite3.connect(path)
 try:
  with connection:yield connection
 finally:connection.close()
@dataclass(frozen=True)
class Actor:
 id:str
 role:str
 identity:object=None

class NativeReleaseGate:
 """Default native boundary: no accidental paid/native execution.

A later release must inject an owner-authorised existing-gateway submitter and
exact receipt reader, with verified principal/origin binding. The Stage A
supported prompt.submit / prompt.receipt path is the integration target; this
candidate intentionally cannot activate it without that separate integration.
"""
 def preflight(self):raise WorkflowError(503,'RELEASE GATE: genuine task authority and existing native adapter integration are not enabled')
 def submit(self,task_id,logical_id,agent):self.preflight()
 def receipt(self,task_id,logical_id):return None

class MockNative:
 """Persistent deterministic native fixture; never imports SDK or sends work."""
 def __init__(self,path):
  self.path=Path(path);self.mode='delivered'
  with closed_sqlite(self.path)as c:c.executescript('CREATE TABLE IF NOT EXISTS mock_admissions(logical_id TEXT PRIMARY KEY,task_id TEXT NOT NULL,binding_id TEXT NOT NULL,agent TEXT NOT NULL);CREATE TABLE IF NOT EXISTS mock_calls(id INTEGER PRIMARY KEY,logical_id TEXT NOT NULL);')
 def preflight(self):pass # Explicitly synthetic; no native transport exists here.
 @property
 def calls(self):
  with closed_sqlite(self.path)as c:return c.execute('SELECT COUNT(*) FROM mock_calls').fetchone()[0]
 def submit(self,task_id,logical_id,agent):
  with closed_sqlite(self.path)as c:
   c.execute('INSERT INTO mock_calls(logical_id)VALUES(?)',(logical_id,))
   c.execute('INSERT OR IGNORE INTO mock_admissions VALUES(?,?,?,?)',(logical_id,task_id,'mock-'+uuid.uuid4().hex,agent))
  if self.mode=='unknown':raise TimeoutError('Synthetic acknowledgement loss after mock admission')
  if self.mode=='failed':return {'task_id':task_id,'logical_id':logical_id,'status':'failed','evidence':'Synthetic confirmed native failure'}
  return self.receipt(task_id,logical_id)
 def receipt(self,task_id,logical_id):
  with closed_sqlite(self.path)as c:row=c.execute('SELECT task_id,binding_id,agent FROM mock_admissions WHERE logical_id=?',(logical_id,)).fetchone()
  if not row:return None
  return {'task_id':row[0],'logical_id':logical_id,'binding_id':row[1],'agent':row[2],'status':'delivered','evidence':'Synthetic exact delivery receipt'}

class Store:
 def __init__(self,path,adapter=None,*,principals=None,authority=None):
  self.path=Path(path).resolve();self.path.parent.mkdir(parents=True,exist_ok=True)
  self.adapter=adapter or NativeReleaseGate();self.principals=dict(principals or {});self.authority=authority
  with _LOCK:self.lock=_LOCKS.setdefault(str(self.path),threading.RLock())
  with self.db()as c:
   existing=c.execute("SELECT 1 FROM sqlite_master WHERE name='wf_meta'").fetchone()
   if existing:
    versions=[tuple(r)for r in c.execute('SELECT version FROM wf_meta')]
    if versions not in ([],[(1,)]):raise WorkflowError(503,'Unsupported workflow schema; no destructive migration')
   c.executescript(SCHEMA)
   if not c.execute('SELECT 1 FROM wf_meta').fetchone():c.execute('INSERT INTO wf_meta VALUES(1)')
 @contextmanager
 def db(self):
  c=sqlite3.connect(self.path,timeout=10);c.row_factory=sqlite3.Row
  try:yield c;c.commit()
  except BaseException:c.rollback();raise
  finally:c.close()
 def role(self,actor):
  if self.authority is not None:return self.authority.role(actor)
  if not isinstance(actor,Actor)or self.principals.get(actor.role)!=actor.id:raise WorkflowError(403,'Trusted server identity required')
  return actor.role
 def allow(self,actor,*roles):
  if self.role(actor)not in roles:raise WorkflowError(403,'This principal cannot perform that action')
 def raw(self,c,tid):
  row=c.execute('SELECT body FROM wf_tasks WHERE id=?',(tid,)).fetchone()
  if not row:raise WorkflowError(404,'Task not found')
  return json.loads(row[0])
 def visible(self,actor,task):self.allow(actor,'owner','mira','native',task['assignee'])
 def save(self,c,task):
  task['updated_at']=now();c.execute('INSERT INTO wf_tasks VALUES(?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body',(task['id'],canonical(task)))
 def audit(self,c,actor,op,task,evidence):c.execute('INSERT INTO wf_audit(task_id,actor,operation,evidence,at)VALUES(?,?,?,?,?)',(task['id'],actor.id,op,canonical(evidence),now()))
 def view(self,c,task):
  result=json.loads(canonical(task));result['revisions']=[dict(r)for r in c.execute('SELECT revision,name,sha256,evidence,author FROM wf_revisions WHERE task_id=? ORDER BY revision',(task['id'],))]
  result['review_history']=[{'actor':r['actor'],'at':r['at'],'evidence':json.loads(r['evidence'])}for r in c.execute("SELECT actor,at,evidence FROM wf_audit WHERE task_id=? AND operation='review' ORDER BY seq",(task['id'],))]
  result['dependency_blocks']=[dep for dep in task['dependencies']if self.raw(c,dep)['review']!='accepted']
  result['dispatch_gate']='Owner approval required'if task['approval']!='approved'else('Dependencies not accepted'if result['dependency_blocks']else None)
  return result
 def get(self,actor,tid):
  with self.db()as c:task=self.raw(c,tid);self.visible(actor,task);return self.view(c,task)
 def list(self,actor):
  role=self.role(actor)
  with self.db()as c:
   tasks=[json.loads(r[0])for r in c.execute('SELECT body FROM wf_tasks ORDER BY id')]
   return [self.view(c,t)for t in tasks if role in ('owner','mira','native')or t['assignee']==role]
 def artifact(self,actor,tid,revision):
  with self.db()as c:
   self.visible(actor,self.raw(c,tid));r=c.execute('SELECT name,content FROM wf_revisions WHERE task_id=? AND revision=?',(tid,revision)).fetchone()
   if not r:raise WorkflowError(404,'Revision not found')
   return r['name'],bytes(r['content'])
 def fields(self,data,required,optional=()):
  if not isinstance(data,dict)or set(data)-set(required)-set(optional)or set(required)-set(data):raise WorkflowError(400,'Invalid command fields; client authority claims are not accepted')
 def binding(self,task,data):
  expected=task.get('binding')
  if not expected or any(data.get(k)!=expected[k]for k in ('logical_id','binding_id')):raise WorkflowError(409,'Receipt does not bind this task and exact dispatch')
 def command(self,actor,op,data,key):
  self.role(actor)
  if not isinstance(key,str)or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}',key):raise WorkflowError(400,'Stable idempotency key required')
  fingerprint=hashlib.sha256(canonical({'op':op,'data':data}).encode()).hexdigest()
  with self.lock:
   if self.authority is not None:
    if not isinstance(data,dict):raise WorkflowError(400,'Invalid command data')
    task=None
    if op!='create':
     with self.db()as c:task=self.raw(c,data.get('task_id'))
    self.authority.authorize(actor,op,data,task) # Before replay cache: revoked authority stays revoked.
   with self.db()as c:
    old=c.execute('SELECT fingerprint,result FROM wf_commands WHERE actor=? AND key=?',(actor.id,key)).fetchone()
    if old:
     if old['fingerprint']!=fingerprint:raise WorkflowError(409,'Idempotency key belongs to another operation')
     return json.loads(old['result'])
   if op=='dispatch':result=self.dispatch(actor,data)
   elif op=='recover':result=self.recover(actor,data)
   else:
    with self.db()as c:result=self.mutate(c,actor,op,data)
   if self.authority is not None:self.authority.after_command(actor,op,result)
   with self.db()as c:c.execute('INSERT INTO wf_commands VALUES(?,?,?,?)',(actor.id,key,fingerprint,canonical(result)))
   return result
 def mutate(self,c,actor,op,data):
  if op=='create':
   self.allow(actor,'mira','owner');self.fields(data,('objective','criteria','assignee','origin','scope'),('deadline','timezone','dependencies'))
   objective=text(data['objective'],'objective');scope=text(data['scope'],'approval scope')
   criteria=data['criteria']
   if not isinstance(criteria,list)or not 1<=len(criteria)<=20:raise WorkflowError(400,'Acceptance criteria required')
   for criterion in criteria:text(criterion,'acceptance criterion')
   if data['assignee']not in AGENTS or data['origin']!=ORIGIN:raise WorkflowError(400,'Assignee/origin not approved')
   deadline=data.get('deadline');timezone=data.get('timezone')
   if bool(deadline)!=bool(timezone):raise WorkflowError(400,'Deadline and timezone must both be owner-supplied or omitted')
   if deadline:
    try:
     stamp=datetime.datetime.fromisoformat(deadline);ZoneInfo(timezone)
     if stamp.tzinfo is None:raise ValueError('Timezone-aware deadline required')
    except (ValueError,TypeError,ZoneInfoNotFoundError):raise WorkflowError(400,'Invalid owner deadline/timezone')
   dependencies=data.get('dependencies',[])
   if not isinstance(dependencies,list)or len(dependencies)>20 or len(set(dependencies))!=len(dependencies):raise WorkflowError(400,'Invalid dependencies')
   for dep in dependencies:self.raw(c,text(dep,'dependency',100))
   contract={'coordinator':'mira','assignee':data['assignee'],'objective':objective,'criteria':criteria,'origin':data['origin'],'scope':scope,'deadline':deadline,'timezone':timezone,'dependencies':dependencies}
   task={'id':uuid.uuid4().hex,**contract,'contract_hash':hashlib.sha256(canonical(contract).encode()).hexdigest(),'approval':'unapproved','approved_by':None,'delivery':'not-dispatched','acknowledgement':'unacknowledged','execution':'not_started','wait_kind':None,'execution_evidence':None,'native_sequence':0,'binding':None,'review':'not-submitted','revision':0,'artifact':None,'accepted_revision':None,'decided_by':None,'created_at':now()}
  else:
   self.fields(data,('task_id',),{'approve':('contract_hash',),'revoke':('note',),'ack':('logical_id','binding_id'),'observe':('logical_id','binding_id','sequence','state','evidence','wait_kind'),'submit':('name','content','evidence'),'review':('revision','sha256','decision','note'),'move':('column',)}.get(op,()))
   task=self.raw(c,data['task_id']);self.visible(actor,task)
   if op=='approve':
    self.allow(actor,'owner')
    if data.get('contract_hash')!=task['contract_hash']:raise WorkflowError(409,'Approval does not match the exact task contract')
    task.update(approval='approved',approved_by=actor.id)
   elif op=='revoke':self.allow(actor,'owner');text(data.get('note'),'revocation evidence');task.update(approval='revoked',approved_by=None)
   elif op=='move':
    self.allow(actor,'owner','mira');column=data.get('column')
    if column not in ('Inbox','In progress','Review','Done'):raise WorkflowError(400,'Invalid display column')
    task['display_column']=column # Presentation only; deliberately no lifecycle mutation.
   elif op=='ack':
    self.allow(actor,task['assignee']);self.binding(task,data)
    if task['delivery']!='delivered':raise WorkflowError(409,'Delivery evidence required before acknowledgement')
    task['acknowledgement']='acknowledged'
   elif op=='observe':
    self.allow(actor,'native');self.binding(task,data)
    if task['delivery']!='delivered':raise WorkflowError(409,'Unconfirmed dispatch cannot start execution')
    seq=data.get('sequence');state=data.get('state');evidence=text(data.get('evidence'),'execution evidence')
    if type(seq)is not int or seq<=task['native_sequence']:raise WorkflowError(409,'Stale/replayed native evidence sequence')
    if state not in EXECUTION-{'not_started'}:raise WorkflowError(400,'Invalid execution state')
    if task['execution']in ('completed','failed')and state!=task['execution']:raise WorkflowError(409,'Terminal execution requires a separately approved new task, not evidence replay')
    kind=data.get('wait_kind')
    if state=='waiting'and kind not in ('input','tool','provider','approval','dependency'):raise WorkflowError(400,'Waiting needs its actual observed boundary')
    if state!='waiting'and kind is not None:raise WorkflowError(400,'Wait boundary only belongs to waiting execution')
    task.update(execution=state,execution_evidence=evidence,native_sequence=seq,wait_kind=kind)
   elif op=='submit':
    self.allow(actor,task['assignee'])
    if task['acknowledgement']!='acknowledged'or task['execution']!='completed':raise WorkflowError(409,'Acknowledged completed work required before final output submission')
    if task['review']not in ('not-submitted','changes-requested'):raise WorkflowError(409,'Revision requires an explicit correction request')
    name=data.get('name');content=text(data.get('content'),'artifact content',65536);evidence=text(data.get('evidence'),'artifact evidence')
    if not isinstance(name,str)or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}\.md',name):raise WorkflowError(400,'Safe Markdown artifact name required')
    revision=task['revision']+1;raw=content.encode('utf-8');sha=hashlib.sha256(raw).hexdigest();c.execute('INSERT INTO wf_revisions VALUES(?,?,?,?,?,?,?)',(task['id'],revision,name,sha,raw,evidence,actor.id));task.update(revision=revision,artifact={'name':name,'sha256':sha,'reference':'/api/chat/workflow/artifact/'+task['id']+'/'+str(revision)},review='submitted'if revision==1 else 'resubmitted',accepted_revision=None,decided_by=None)
   elif op=='review':
    self.allow(actor,'owner')
    if type(data.get('revision'))is not int or data['revision']!=task['revision']or not task['artifact']or data.get('sha256')!=task['artifact']['sha256']:raise WorkflowError(409,'Review must name the exact current immutable revision and hash')
    decision=data.get('decision')
    if decision not in ('accepted','rejected','changes-requested'):raise WorkflowError(400,'Invalid review decision')
    if task['review']not in ('submitted','resubmitted','accepted','rejected'):raise WorkflowError(409,'No submitted revision awaiting this review')
    if decision!='accepted':text(data.get('note'),'review/correction note')
    note=data.get('note','')
    if not isinstance(note,str)or len(note)>4000:raise WorkflowError(400,'Invalid review note')
    task.update(review=decision,review_note=note,accepted_revision=task['revision']if decision=='accepted'else None,decided_by=actor.id)
   else:raise WorkflowError(404,'Unknown workflow action')
  self.save(c,task);evidence={'revision':task['revision'],'review':task['review']}
  if op in ('review','revoke'):evidence.update(note=data.get('note',''),artifact_sha256=task['artifact']['sha256']if task['artifact']else None)
  self.audit(c,actor,op,task,evidence);return self.view(c,task)
 def dispatch(self,actor,data):
  self.allow(actor,'mira');self.fields(data,('task_id',))
  with self.db()as c:
   task=self.raw(c,data['task_id'])
   if task['approval']!='approved':raise WorkflowError(403,'Owner approval is missing or revoked')
   if self.view(c,task)['dependency_blocks']:raise WorkflowError(409,'Dependencies must have exact accepted revisions')
   old=c.execute('SELECT body FROM wf_dispatches WHERE task_id=?',(task['id'],)).fetchone()
   if old:return self.view(c,task) # Includes pending/unknown: NEVER resubmit.
   if hasattr(self.adapter,'prepare'):self.adapter.prepare(task,actor)
   self.adapter.preflight() # Known unavailable integration is a gate, not fake unknown work.
   logical=uuid.uuid4().hex;intent={'task_id':task['id'],'logical_id':logical,'agent':task['assignee'],'status':'pending'}
   c.execute('INSERT INTO wf_dispatches VALUES(?,?,?)',(task['id'],logical,canonical(intent)));task.update(delivery='pending',execution='unknown');self.save(c,task);self.audit(c,actor,'dispatch-intent',task,{'logical_id':logical})
  # The intent is durably committed BEFORE the external boundary.
  try:receipt=self.adapter.submit(task['id'],logical,task['assignee'])
  except Exception as error:
   known_not_admitted=getattr(error,'not_admitted',False)is True
   with self.db()as c:
    task=self.raw(c,task['id']);task.update(delivery='failed'if known_not_admitted else'unknown',execution='blocked'if known_not_admitted else'unknown')
    if known_not_admitted:task['execution_evidence']='Native admission not attempted; authorisation/route/transport blocked. No retry was issued.'
    self.save(c,task);self.audit(c,actor,'dispatch-blocked'if known_not_admitted else'dispatch-unknown',task,{'logical_id':logical});return self.view(c,task)
  return self.apply_receipt(actor,task['id'],logical,receipt)
 def apply_receipt(self,actor,tid,logical,receipt):
  with self.db()as c:
   task=self.raw(c,tid)
   if not isinstance(receipt,dict)or receipt.get('task_id')!=tid or receipt.get('logical_id')!=logical or receipt.get('agent',task['assignee'])!=task['assignee']:raise WorkflowError(409,'Wrong-task, stale or cross-agent receipt rejected; dispatch remains uncertain')
   if task['binding']and receipt.get('binding_id')!=task['binding']['binding_id']:raise WorkflowError(409,'An existing native admission binding cannot be replaced')
   if task['delivery']=='failed'and receipt.get('status')!='failed':raise WorkflowError(409,'Stale delivery cannot overwrite a confirmed failure')
   if receipt.get('status')=='failed':task.update(delivery='failed',execution='failed',execution_evidence=text(receipt.get('evidence'),'native failure evidence'))
   elif receipt.get('status')=='delivered'and isinstance(receipt.get('binding_id'),str)and receipt['binding_id']:
    task.update(delivery='delivered',binding={'logical_id':logical,'binding_id':receipt['binding_id']})
    if task['execution']=='unknown'and task['native_sequence']==0:task['execution']='not_started'
   else:raise WorkflowError(409,'Receipt has no exact delivered/failed evidence')
   if 'native_status'in receipt:
    state=receipt['native_status'];outcome=receipt.get('native_outcome')
    execution={'queued':'queued','started':'working','unknown':'unknown'}.get(state)
    if state=='terminal':execution={'completed':'completed','failed':'failed','cancelled':'blocked','interrupted':'blocked'}.get(outcome,'unknown')
    if execution is None:raise WorkflowError(409,'Unknown native execution contract')
    task.update(execution=execution,execution_evidence=text(receipt.get('evidence'),'native receipt evidence'))
   self.save(c,task);c.execute('UPDATE wf_dispatches SET body=? WHERE task_id=?',(canonical(receipt),tid));self.audit(c,actor,'receipt',task,{'status':task['delivery']});return self.view(c,task)
 def recover(self,actor,data):
  self.allow(actor,'mira');self.fields(data,('task_id',))
  with self.db()as c:
   task=self.raw(c,data['task_id']);row=c.execute('SELECT logical_id FROM wf_dispatches WHERE task_id=?',(task['id'],)).fetchone()
   if not row:raise WorkflowError(409,'No existing dispatch to recover')
   logical=row['logical_id']
  if hasattr(self.adapter,'prepare_recovery'):self.adapter.prepare_recovery(task,actor)
  receipt=self.adapter.receipt(task['id'],logical)
  if receipt is None:return self.get(actor,task['id'])
  return self.apply_receipt(actor,task['id'],logical,receipt)
