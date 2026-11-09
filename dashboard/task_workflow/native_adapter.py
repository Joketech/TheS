import config
"""Existing-gateway admission/receipt adapter; disabled unless explicitly authorised.

Production transport uses the installed first-party client with an EXPLICIT ready
endpoint. Never ensure/start/create/stop/reset/interrupt/cancel/resolve_unknown.
Every logical submission is journalled before its one possible submit attempt.
"""
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
import asyncio,hashlib,json,re,time
from .workflow import WorkflowError,closed_sqlite,canonical,ORIGIN,AGENTS
class AdmissionNotAttempted(WorkflowError):
 not_admitted=True
@dataclass(frozen=True)
class Route:
 agent:str
 home:str
 session_id:str
 origin:str
 @property
 def profile_id(self):return str(Path(self.home).resolve())
 def validate(self):
  if self.agent not in AGENTS or self.origin!=ORIGIN or not Path(self.home).is_absolute()or not re.fullmatch(r'[A-Za-z0-9_-]{1,160}',self.session_id):raise WorkflowError(503,'A pinned approved canonical route is required')
class ExistingGatewaySDK:
 """Production implementation, lazily imported only behind the release/identity gate."""
 fixture=False
 def probe(self,route):
  from hermes_constants import set_hermes_home_override,reset_hermes_home_override
  from hermes_cli.gateway_runtime import discover_gateway_endpoint,attachable
  token=set_hermes_home_override(Path(route.home))
  try:
   discovery=discover_gateway_endpoint(Path(route.home))
   if not attachable(discovery)or discovery.endpoint is None:raise WorkflowError(503,'Existing canonical gateway unavailable; no replacement is started')
   endpoint=discovery.endpoint
   if endpoint.profile_id!=route.profile_id:raise WorkflowError(403,'Existing gateway profile does not match pinned route')
   return endpoint
  finally:reset_hermes_home_override(token)
 @asynccontextmanager
 async def open(self,route,subject):
  from hermes_constants import set_hermes_home_override,reset_hermes_home_override
  from hermes_cli.gateway_client import connect_gateway
  token=set_hermes_home_override(Path(route.home))
  try:
   endpoint=self.probe(route)
   async with connect_gateway(endpoint=endpoint,subject=subject)as client:yield client
  finally:reset_hermes_home_override(token)
class NativeAdapter:
 def __init__(self,path,authority,routes,*,enabled=False,factory=None,clock=time.time):
  self.path=Path(path);self.authority=authority;self.routes=dict(routes);self.enabled=enabled;self.factory=factory or ExistingGatewaySDK();self.clock=clock;self.prepared={};self.recovery={}
  with closed_sqlite(self.path)as c:c.execute('CREATE TABLE IF NOT EXISTS native_bindings(logical_id TEXT PRIMARY KEY,task_id TEXT UNIQUE NOT NULL,body TEXT NOT NULL)')
 def route(self,task):
  route=self.routes.get(task['assignee'])
  if not isinstance(route,Route):raise WorkflowError(503,'Pinned existing-session route unavailable')
  route.validate()
  if route.agent!=task['assignee']or route.origin!=task['origin']:raise WorkflowError(403,'Task route/origin mismatch')
  return route
 def prepare(self,task,actor):
  if not self.enabled:raise WorkflowError(503,'Real transport release gate is disabled')
  permit=self.authority.dispatch_permit(actor,task);route=self.route(task)
  if permit['synthetic']and not getattr(self.factory,'fixture',False):raise WorkflowError(403,'Synthetic identities may never reach a real gateway transport')
  if hasattr(self.factory,'probe'):self.factory.probe(route)
  fields=('id','contract_hash','coordinator','assignee','objective','criteria','scope','origin','deadline','timezone','dependencies');prompt=canonical({k:task[k]for k in fields})
  if len(prompt.encode())>16384:raise WorkflowError(400,'Approved task exceeds the native prompt boundary; do not truncate or resubmit')
  self.prepared[task['id']]={'task':task,'actor':actor,'permit':permit,'route':route,'prompt':prompt}
 def preflight(self):
  if not self.enabled:raise WorkflowError(503,'Real transport release gate is disabled')
 def prepare_recovery(self,task,actor):
  if not self.enabled:raise WorkflowError(503,'Transport read gate is disabled')
  self.authority.authorize(actor,'recover',{'task_id':task['id']},task);route=self.route(task)
  if actor.identity.synthetic and not getattr(self.factory,'fixture',False):raise WorkflowError(403,'Synthetic identity cannot access real native receipts')
  self.recovery[task['id']]={'route':route,'subject':None} # Existing native peer authenticates itself; entity witness is not a login subject.
 def read(self,logical):
  with closed_sqlite(self.path)as c:row=c.execute('SELECT body FROM native_bindings WHERE logical_id=?',(logical,)).fetchone()
  return json.loads(row[0])if row else None
 def save(self,row):
  with closed_sqlite(self.path)as c:c.execute('UPDATE native_bindings SET body=? WHERE logical_id=?',(canonical(row),row['logical_id']))
 def submit(self,task_id,logical_id,agent):
  if not re.fullmatch(r'[a-f0-9]{32}',logical_id):raise WorkflowError(400,'Invalid logical dispatch identity')
  context=self.prepared.get(task_id)
  if context is None or context['task']['assignee']!=agent:raise WorkflowError(403,'Prepared exact task authority required')
  # Reverify expiry, revocation, contract and principal at the external seam.
  self.authority.dispatch_permit(context['actor'],context['task']);route=context['route']
  row={'task_id':task_id,'logical_id':logical_id,'agent':agent,'profile_id':route.profile_id,'session_id':route.session_id,'input_id':hashlib.sha256(logical_id.encode()).hexdigest()[:32],'contract_hash':context['task']['contract_hash'],'admission_id':None,'attempted':False,'native':None,'synthetic':context['permit']['synthetic']}
  with closed_sqlite(self.path)as c:
   existing=c.execute('SELECT logical_id,body FROM native_bindings WHERE task_id=?',(task_id,)).fetchone()
   if existing:
    if existing[0]!=logical_id:raise WorkflowError(409,'Task already owns a different logical native dispatch')
    current=json.loads(existing[1]);return self.project(current)if current['native']else None
   cursor=c.execute('INSERT OR IGNORE INTO native_bindings VALUES(?,?,?)',(logical_id,task_id,canonical(row)))
   if cursor.rowcount!=1:raise WorkflowError(409,'Existing native intent must be recovered, never resubmitted')
  return asyncio.run(self._attempt(row,context))
 async def _attempt(self,row,context):
  try:return await self._submit(row,context)
  except Exception:
   if not row['attempted']:raise AdmissionNotAttempted(503,'Native admission was not attempted: authority, route or transport blocked')from None
   raise
 async def _resume(self,client,route):
  snapshot=await client.rpc('session.resume',session_id=route.session_id,omit_messages=True,_timeout=8)
  if not isinstance(snapshot,dict)or snapshot.get('stored_session_id',snapshot.get('session_id'))!=route.session_id:raise WorkflowError(409,'Canonical session resume returned another target')
  return snapshot
 async def _submit(self,row,context):
  async with self.factory.open(context['route'],context['permit']['coordinator_subject'])as client:
   await self._resume(client,context['route'])
   self.authority.dispatch_permit(context['actor'],context['task'])
   row['attempted']=True;self.save(row) # Durable BEFORE the only prompt.submit call.
   receipt=await client.rpc('prompt.submit',session_id=row['session_id'],input_id=row['input_id'],text=context['prompt'],finite=True,_timeout=8)
   return self.accept(row,receipt)
 def accept(self,row,receipt):
  if not isinstance(receipt,dict):raise WorkflowError(409,'Native receipt is unavailable')
  ref=receipt.get('ref')
  if not isinstance(ref,dict)or ref.get('profile_id')!=row['profile_id']or ref.get('session_id')!=row['session_id']:raise WorkflowError(409,'Wrong-profile or cross-session native receipt')
  aid=receipt.get('admission_id');status=receipt.get('status');seq=receipt.get('sequence');epoch=receipt.get('authority_epoch');generation=receipt.get('execution_generation')
  if not isinstance(aid,str)or not re.fullmatch(r'[A-Za-z0-9_-]{1,160}',aid)or status not in ('queued','started','unknown','terminal')or type(seq)is not int or type(epoch)is not int or epoch<0 or(generation is not None and type(generation)is not int):raise WorkflowError(409,'Invalid native admission contract')
  if row['admission_id']and row['admission_id']!=aid:raise WorkflowError(409,'Native admission binding changed')
  previous=row['native']
  if previous:
   if seq!=previous['sequence']or epoch<previous['authority_epoch']:raise WorkflowError(409,'Stale native receipt sequence/epoch')
   if previous['status']=='terminal'and(status!='terminal'or receipt.get('outcome')!=previous.get('outcome')):raise WorkflowError(409,'Stale native receipt cannot roll back settlement')
   if previous['status']=='started'and status=='queued':raise WorkflowError(409,'Stale queued receipt after execution began')
   old_generation=previous.get('execution_generation')
   if isinstance(old_generation,int)and isinstance(generation,int)and generation<old_generation:raise WorkflowError(409,'Stale execution generation')
  row['admission_id']=aid;row['native']={k:receipt.get(k)for k in ('admission_id','status','outcome','sequence','authority_epoch','execution_generation')};self.save(row);return self.project(row)
 def project(self,row):
  native=row['native']
  if not native:return None
  return {'task_id':row['task_id'],'logical_id':row['logical_id'],'binding_id':row['admission_id'],'agent':row['agent'],'status':'delivered','native_status':native['status'],'native_outcome':native.get('outcome'),'evidence':('Synthetic HTTP native contract'if row['synthetic']else'Exact authenticated native receipt')}
 def receipt(self,task_id,logical_id):
  row=self.read(logical_id);context=self.recovery.get(task_id)
  if not row or row['task_id']!=task_id:raise WorkflowError(409,'No exact native task/dispatch binding')
  if context is None:raise WorkflowError(403,'Authenticated recovery context required')
  route=context['route']
  if row['profile_id']!=route.profile_id or row['session_id']!=route.session_id:raise WorkflowError(409,'Recovery route changed; do not borrow a session')
  return asyncio.run(self._receipt(row,context))
 async def _receipt(self,row,context):
  async with self.factory.open(context['route'],context['subject'])as client:
   snapshot=await self._resume(client,context['route'])
   if row['admission_id']is None:
    lookup=getattr(self,'exact_lookup',None)
    if lookup:
     exact=lookup(context['route'],row)
     if exact:row['admission_id']=exact
   if row['admission_id']is None:
    matches=[p for p in snapshot.get('pending',[])if isinstance(p,dict)and p.get('input_id')==row['input_id']]
    if len(matches)!=1:return None # No newest-message guess or submit-on-recovery.
    self.accept(row,matches[0])
   receipt=await client.rpc('prompt.receipt',session_id=row['session_id'],admission_id=row['admission_id'],include_result=False,_timeout=8)
   return self.accept(row,receipt)
