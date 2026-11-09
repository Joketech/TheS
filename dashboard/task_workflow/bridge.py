import config
"""Existing-interface task workflow: owner commands and receipt-attested AI entities.

No client role headers, new native session, observer daemon or automatic AI retry.
Only an explicit authenticated `send` starts production coordination. Refresh is
read/reconciliation, except executing the already-approved exact MIRA dispatch.
"""
from pathlib import Path
import asyncio,hashlib,json,re,time
from .workflow import Store,WorkflowError,closed_sqlite,canonical,ORIGIN
from .purpose import PurposeAuthority
from .native_adapter import NativeAdapter,ExistingGatewaySDK
from .process_sdk import ProcessSDK
PROTOCOL='the-s-workflow-v1'
def unique(pairs):
 out={}
 for k,v in pairs:
  if k in out:raise WorkflowError(400,'Duplicate command field')
  out[k]=v
 return out
class AgentAdapter(NativeAdapter):
 def prepare(self,task,actor):
  super().prepare(task,actor)
  self.prepared[task['id']]['prompt']+='\nReturn only a JSON object with protocol="'+PROTOCOL+'", task_id="'+task['id']+'", contract_hash="'+task['contract_hash']+'", acknowledged=true ONLY if you explicitly acknowledge this assignment, and artifact={name: safe Markdown filename, content: actual completed Markdown output, evidence: actual verification/limits}. Do not claim owner acceptance. No fabricated work.'
  if len(self.prepared[task['id']]['prompt'].encode())>16384:raise WorkflowError(400,'Approved assignment plus protocol exceeds native boundary; do not truncate')
class Bridge:
 def __init__(self,path,owner_auth,routes,*,factory=None,synthetic=False):
  self.path=Path(path);self.routes=dict(routes);self.factory=factory or ProcessSDK()
  if bool(synthetic)!=bool(getattr(self.factory,'fixture',False)):raise ValueError('Synthetic authority and real native transport cannot be mixed')
  self.authority=PurposeAuthority(path,owner_auth,synthetic=synthetic);self.authority.runtime_verifier=self.verify_witness;self.adapter=AgentAdapter(path,self.authority,routes,enabled=True,factory=self.factory);self.adapter.exact_lookup=self.recover_admission_id;self.store=Store(path,self.adapter,authority=self.authority)
  with closed_sqlite(path)as c:
   c.executescript('CREATE TABLE IF NOT EXISTS wf_runtime_intents(id TEXT PRIMARY KEY,task_id TEXT NOT NULL,purpose TEXT NOT NULL,body TEXT NOT NULL,UNIQUE(task_id,purpose));CREATE TABLE IF NOT EXISTS wf_chat(request_id TEXT PRIMARY KEY,agent TEXT NOT NULL,fingerprint TEXT NOT NULL,prompt TEXT NOT NULL,reply TEXT NOT NULL,stamp REAL NOT NULL);')
 def load_intent(self,tid,purpose='coordinate'):
  with closed_sqlite(self.path)as c:r=c.execute('SELECT body FROM wf_runtime_intents WHERE task_id=? AND purpose=?',(tid,purpose)).fetchone()
  return json.loads(r[0])if r else None
 def save_intent(self,row):
  with closed_sqlite(self.path)as c:c.execute('INSERT INTO wf_runtime_intents VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body',(row['id'],row['task_id'],row['purpose'],canonical(row)))
 def task(self,actor,tid):return self.store.get(actor,tid)
 def validate_receipt(self,row,r,route):
  ref=r.get('ref',{})if isinstance(r,dict)else{}
  if ref!={'profile_id':route.profile_id,'session_id':route.session_id}or not isinstance(r.get('admission_id'),str)or r.get('status')not in ('queued','started','unknown','terminal')or type(r.get('authority_epoch'))is not int or type(r.get('sequence'))is not int:raise WorkflowError(409,'Exact native receipt profile/session/shape mismatch')
  if row.get('admission_id')not in (None,r['admission_id']):raise WorkflowError(409,'Native admission changed')
  previous=row.get('native')
  if previous and(r['sequence']!=previous['sequence']or r['authority_epoch']<previous['authority_epoch']or(previous['status']=='terminal'and(r['status']!='terminal'or r.get('outcome')!=previous.get('outcome')))):raise WorkflowError(409,'Stale native receipt')
  if previous and((previous['status']=='started'and r['status']=='queued')or(type(previous.get('execution_generation'))is int and type(r.get('execution_generation'))is int and r['execution_generation']<previous['execution_generation'])):raise WorkflowError(409,'Stale native execution generation/state')
  return r
 async def rpc(self,route,method,**params):
  route.validate()
  async with asyncio.timeout(20):
   async with self.factory.open(route,None)as client:return await client.rpc(method,**params,_timeout=8)
 def read_receipt(self,route,aid):
  def read():return asyncio.run(self.rpc(route,'prompt.receipt',session_id=route.session_id,admission_id=aid,include_result=True))
  try:asyncio.get_running_loop()
  except RuntimeError:return read()
  # Authority revalidation also runs inside the submit coroutine. Keep this
  # exact bounded read in its own loop, never nest asyncio.run or leak a coro.
  from concurrent.futures import ThreadPoolExecutor
  with ThreadPoolExecutor(max_workers=1)as executor:return executor.submit(read).result(timeout=22)
 def recover_admission_id(self,route,row):
  # Exact persisted input/session lookup only. Never a latest-message/time guess.
  import sqlite3
  database=Path(route.home)/'state.db'
  if not database.is_file():return None
  c=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)
  try:
   c.execute('PRAGMA query_only=1');rows=c.execute('SELECT admission_id FROM session_admissions WHERE request_id=? AND target_session_id=?',(row['input_id'],route.session_id)).fetchall()
   return rows[0][0]if len(rows)==1 else None
  except sqlite3.Error:return None
  finally:c.close()
 def read_intent(self,row):
  route=self.routes[row['agent']]
  if not row.get('admission_id'):
   aid=self.recover_admission_id(route,row)
   if not aid:return None
   row['admission_id']=aid
  receipt=self.validate_receipt(row,self.read_receipt(route,row['admission_id']),route);row['native']={k:receipt.get(k)for k in ('sequence','status','outcome','authority_epoch','execution_generation')};self.save_intent(row);return receipt
 def packet(self,receipt,tid,contract_hash):
  if receipt.get('status')!='terminal'or receipt.get('outcome')!='completed':raise WorkflowError(409,'Native work has not completed successfully')
  text=(receipt.get('result')or{}).get('final_response')
  if not isinstance(text,str)or len(text.encode())>131072:raise WorkflowError(409,'No bounded native task result')
  match=re.fullmatch(r'\s*```(?:json)?\s*(\{.*\})\s*```\s*',text,re.S)
  if match:text=match.group(1)
  try:body=json.loads(text,object_pairs_hook=unique)
  except (ValueError,TypeError):raise WorkflowError(409,'Native result must return the explicit workflow JSON protocol; no arbitrary reply is an action')from None
  if not isinstance(body,dict)or body.get('protocol')!=PROTOCOL or body.get('task_id')!=tid or body.get('contract_hash')!=contract_hash:raise WorkflowError(409,'Native action belongs to another task/contract/protocol')
  return body
 def verify_witness(self,identity):
  proof=identity.proof;tid=identity.task_id
  with self.store.db()as c:task=self.store.raw(c,tid)
  if proof['source']=='coordinate':
   if identity.role!='mira'or set(identity.operations)-{'dispatch','recover'}:raise WorkflowError(403,'Coordination evidence is not assignee/native/owner authority')
   row=self.load_intent(tid,proof.get('purpose','coordinate'))
   if not row or row['admission_id']!=proof['admission_id']or identity.profile_id!=self.routes['mira'].profile_id:raise WorkflowError(403,'No pinned MIRA coordination intent')
   receipt=self.read_intent(row);body=self.packet(receipt,tid,task['contract_hash'])
   if body.get('action')!='dispatch':raise WorkflowError(403,'MIRA did not explicitly authorise the approved dispatch')
   if row.get('revision')and body.get('revision')!=row['revision']:raise WorkflowError(409,'MIRA correction revision does not match')
  elif proof['source']=='revision':
   if identity.role!=task['assignee']or set(identity.operations)-{'submit'}:raise WorkflowError(403,'Correction evidence is only assignee submission authority')
   row=self.load_intent(tid,proof['purpose']);route=self.routes[task['assignee']]
   if not row or row['admission_id']!=proof['admission_id']or identity.profile_id!=route.profile_id:raise WorkflowError(403,'No exact new correction admission')
   receipt=self.read_intent(row);body=self.packet(receipt,tid,task['contract_hash'])
   if body.get('revision')!=row['revision']or body.get('acknowledged')is not True:raise WorkflowError(403,'New revision and acknowledgement must be explicit')
  else:
   if proof['source']!='assignee'or identity.role not in ('native',task['assignee'])or set(identity.operations)-({'observe'}if identity.role=='native'else{'ack','submit'}):raise WorkflowError(403,'Wrong native evidence purpose')
   row=self.adapter.read(proof['logical_id']);route=self.routes[task['assignee']]
   if not row or row['task_id']!=tid or row['admission_id']!=proof['admission_id']or identity.profile_id!=route.profile_id:raise WorkflowError(403,'No exact assignee admission witness')
   receipt=self.read_receipt(route,row['admission_id']);self.adapter.accept(row,receipt)
   if identity.role!='native':
    body=self.packet(receipt,tid,task['contract_hash'])
    if body.get('acknowledged')is not True:raise WorkflowError(403,'Assignee acknowledgement not explicit')
  return receipt
 def witness(self,role,task,proof,ops):
  profile=self.routes['mira'if role=='mira'else task['assignee']].profile_id
  return self.authority.runtime(role,task['id'],profile,proof,ops)
 def coordinate(self,owner,task,purpose='coordinate',revision=None):
  self.authority.role(owner);grant=self.authority.grant(task)
  if task['approval']!='approved'or grant['revoked']or grant['expires_at']<=self.authority.clock()or self.task(owner,task['id'])['dependency_blocks']:raise WorkflowError(403,'Current exact owner approval and accepted dependencies required')
  row=self.load_intent(task['id'],purpose)
  if row:return row
  route=self.routes.get('mira')
  if route is None:raise WorkflowError(503,'Pinned existing MIRA route unavailable')
  route.validate();self.factory.probe(route)
  key=hashlib.sha256((task['id']+'|'+purpose+'|'+task['contract_hash']).encode()).hexdigest()[:32];row={'id':key,'task_id':task['id'],'purpose':purpose,'agent':'mira','input_id':key,'admission_id':None,'attempted':False,'native':None,'revision':revision};self.save_intent(row)
  async def submit():
   async with asyncio.timeout(20):
    async with self.factory.open(route,None)as client:
     snapshot=await client.rpc('session.resume',session_id=route.session_id,omit_messages=True,_timeout=8)
     if snapshot.get('stored_session_id')!=route.session_id:raise WorkflowError(409,'MIRA route changed')
     self.authority.check(owner.identity);current=self.authority.grant(task)
     if current['revoked']or current['expires_at']<=self.authority.clock():raise WorkflowError(403,'Owner approval expired before coordination submit')
     prompt='Coordinate this exact owner-approved assignment. Return only JSON {"protocol":"'+PROTOCOL+'","task_id":"'+task['id']+'","contract_hash":"'+task['contract_hash']+'","action":"dispatch"} if within approved scope. This requests real production work, not a test. Do not claim assignee acknowledgement or owner acceptance. Task: '+canonical({k:task[k]for k in ('objective','criteria','assignee','scope','origin','dependencies','deadline','timezone')})
     if revision:prompt+=' This is owner-requested correction revision '+str(revision)+'. Include integer revision='+str(revision)+' in the JSON. Exact correction note: '+task.get('review_note','')
     if len(prompt.encode())>16384:raise WorkflowError(400,'Approved coordination prompt exceeds the native boundary; no truncation')
     row['attempted']=True;self.save_intent(row);return await client.rpc('prompt.submit',session_id=route.session_id,input_id=key,text=prompt,finite=True,_timeout=8)
  try:
   r=self.validate_receipt(row,asyncio.run(submit()),route);row['admission_id']=r['admission_id'];row['native']={k:r.get(k)for k in ('sequence','status','outcome','authority_epoch','execution_generation')};self.save_intent(row)
  except Exception:
   row['state']='unknown'if row['attempted']else'blocked-not-attempted';self.save_intent(row);raise WorkflowError(503,'Coordination outcome '+row['state']+'; refresh exact receipt, never resend automatically')from None
  return row
 def refresh_revision(self,owner,task,coordinator):
  receipt=self.read_intent(coordinator)
  if not receipt or receipt['status']!='terminal':return {**self.task(owner,task['id']),'revision_coordination_status':receipt['status']if receipt else'unknown'}
  mira=self.witness('mira',task,{'source':'coordinate','purpose':coordinator['purpose'],'admission_id':coordinator['admission_id']},('dispatch',))
  revision=coordinator['revision'];purpose='revision:'+str(revision);row=self.load_intent(task['id'],purpose)
  if not row:
   self.authority.dispatch_permit(mira,task);route=self.routes[task['assignee']];self.factory.probe(route);key=hashlib.sha256((task['id']+'|'+purpose).encode()).hexdigest()[:32];row={'id':key,'task_id':task['id'],'purpose':purpose,'agent':task['assignee'],'revision':revision,'input_id':key,'admission_id':None,'attempted':False,'native':None};self.save_intent(row)
   async def submit():
    async with asyncio.timeout(20):
     async with self.factory.open(route,None)as client:
      snap=await client.rpc('session.resume',session_id=route.session_id,omit_messages=True,_timeout=8)
      if snap.get('stored_session_id')!=route.session_id:raise WorkflowError(409,'Correction route changed')
      self.authority.dispatch_permit(mira,task)
      prompt='Complete only the owner-requested correction. Return ONLY JSON with protocol="'+PROTOCOL+'", task_id="'+task['id']+'", contract_hash="'+task['contract_hash']+'", revision='+str(revision)+', acknowledged=true only if acknowledged, and artifact={name: safe .md filename, content: actual completed Markdown, evidence: actual checks/limits}. Do not claim owner acceptance. Original objective: '+task['objective']+'\nScope: '+task['scope']+'\nExact correction note: '+task.get('review_note','')
      prompt+='\nOriginal acceptance/deadline: '+canonical({k:task[k]for k in ('criteria','deadline','timezone')})
      if len(prompt.encode())>16384:raise WorkflowError(400,'Correction prompt exceeds native boundary; no truncation')
      row['attempted']=True;self.save_intent(row)
      return await client.rpc('prompt.submit',session_id=route.session_id,input_id=key,text=prompt,finite=True,_timeout=8)
   try:
    r=self.validate_receipt(row,asyncio.run(submit()),route);row['admission_id']=r['admission_id'];row['native']={k:r.get(k)for k in ('sequence','status','outcome','authority_epoch','execution_generation')};self.save_intent(row)
   except Exception:
    row['state']='unknown'if row['attempted']else'blocked-not-attempted';self.save_intent(row);raise WorkflowError(503,'Correction admission uncertain/blocked; exact refresh only, no resend')from None
  receipt=self.read_intent(row)
  if not receipt or receipt['status']!='terminal':return {**self.task(owner,task['id']),'revision_execution':receipt['status']if receipt else'unknown'}
  packet=self.packet(receipt,task['id'],task['contract_hash']);actor=self.witness(task['assignee'],task,{'source':'revision','purpose':purpose,'admission_id':row['admission_id']},('submit',));artifact=packet.get('artifact')
  if not isinstance(artifact,dict)or set(artifact)!={'name','content','evidence'}:raise WorkflowError(409,'Correction needs an explicit safe Markdown artifact')
  return self.store.command(actor,'submit',{'task_id':task['id'],**artifact},'revision-'+row['id'])
 def refresh(self,owner,task):
  if task['review']=='changes-requested':
   revision=self.load_intent(task['id'],'coordinate-revision:'+str(task['revision']+1))
   if revision:return self.refresh_revision(owner,task,revision)
   return {**task,'correction_next_action':'Use revise with exact reviewed revision/hash to request bounded rework; old output is never resubmitted'}
  coordinator=self.load_intent(task['id'])
  if not coordinator:return self.task(owner,task['id'])
  receipt=self.read_intent(coordinator)
  if not receipt or receipt['status']!='terminal':return {**self.task(owner,task['id']),'coordination_status':receipt['status']if receipt else'unknown'}
  mira=self.witness('mira',task,{'source':'coordinate','admission_id':coordinator['admission_id']},('dispatch','recover'))
  current=self.task(owner,task['id'])
  if current['delivery']=='not-dispatched':
   current=self.store.command(mira,'dispatch',{'task_id':task['id']},'dispatch-'+coordinator['id'])
  if current['delivery']in ('unknown','pending'):current=self.store.command(mira,'recover',{'task_id':task['id']},'recover-'+hashlib.sha256(str(time.time_ns()).encode()).hexdigest()[:24])
  if not current['binding']:return current
  row=self.adapter.read(current['binding']['logical_id']);route=self.routes[task['assignee']]
  if not row.get('admission_id'):
   row['admission_id']=self.recover_admission_id(route,row)
   if not row['admission_id']:return current
  native_receipt=self.read_receipt(route,row['admission_id']);self.adapter.accept(row,native_receipt);proof={'source':'assignee','admission_id':row['admission_id'],'logical_id':row['logical_id']};native=self.witness('native',task,proof,('observe',));status=native_receipt['status'];state={'queued':'queued','started':'working','unknown':'unknown','terminal':'completed'if native_receipt.get('outcome')=='completed'else'failed'}.get(status,'unknown');seq=native_receipt['authority_epoch']*10**12+(native_receipt.get('execution_generation')or 0)*100+{'queued':1,'started':2,'unknown':3,'terminal':4}[status]
  if seq>current['native_sequence']:current=self.store.command(native,'observe',{'task_id':task['id'],**current['binding'],'sequence':seq,'state':state,'evidence':'Exact authenticated native receipt; execution is not owner acceptance'},'observe-'+hashlib.sha256((row['admission_id']+str(seq)).encode()).hexdigest()[:32])
  if state=='completed'and current['review']=='not-submitted':
   packet=self.packet(native_receipt,task['id'],task['contract_hash']);assignee=self.witness(task['assignee'],task,proof,('ack','submit'));current=self.store.command(assignee,'ack',{'task_id':task['id'],**current['binding']},'ack-'+hashlib.sha256(row['admission_id'].encode()).hexdigest()[:32]);artifact=packet.get('artifact')
   if not isinstance(artifact,dict)or set(artifact)!={'name','content','evidence'}:raise WorkflowError(409,'Explicit Markdown artifact/evidence required')
   current=self.store.command(assignee,'submit',{'task_id':task['id'],**artifact},'submit-'+hashlib.sha256(row['admission_id'].encode()).hexdigest()[:32])
  return current
 def command(self,owner,op,data,key):
  self.authority.role(owner)
  if owner.role!='owner':raise WorkflowError(403,'Owner task purpose required for interface commands')
  if not isinstance(data,dict):raise WorkflowError(400,'JSON object required')
  with self.store.lock:
   if op=='create':return self.store.command(owner,op,{'origin':ORIGIN,**data},key)
   if op=='list':
    if data:raise WorkflowError(400,'List takes no fields')
    return self.store.list(owner)
   if op=='revise':
    if set(data)!={'task_id','revision','sha256'}:raise WorkflowError(400,'Exact reviewed task/revision/hash required')
    task=self.task(owner,data['task_id'])
    if task['review']!='changes-requested'or data['revision']!=task['revision']or data['sha256']!=task['artifact']['sha256']or task['approval']=='revoked':raise WorkflowError(409,'Correction must bind the current owner-requested revision/hash')
    task=self.store.command(owner,'approve',{'task_id':task['id'],'contract_hash':task['contract_hash']},'rework-approval-'+hashlib.sha256(key.encode()).hexdigest()[:32])
    return self.coordinate(owner,task,'coordinate-revision:'+str(task['revision']+1),task['revision']+1)
   if op in ('show','send','refresh'):
    if set(data)!={'task_id'}:raise WorkflowError(400,'Exact task_id required')
    task=self.task(owner,data['task_id'])
    if op=='show':return task
    return self.coordinate(owner,task)if op=='send'else self.refresh(owner,task)
   if op=='read':
    if set(data)!={'task_id','revision'}or type(data['revision'])is not int:raise WorkflowError(400,'Exact task_id and integer revision required')
    name,body=self.store.artifact(owner,data['task_id'],data['revision']);return {'name':name,'content':body.decode(),'sha256':hashlib.sha256(body).hexdigest(),'revision':data['revision']}
   if op not in ('approve','revoke','review'):raise WorkflowError(400,'Unknown workflow operation')
   return self.store.command(owner,op,data,key)
