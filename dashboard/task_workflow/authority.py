import config
"""Task-specific authority using the installed verified dashboard-session primitive.

No login, credential minting, financial-role reuse or client-role inference.
Production requires an existing provider plus independently approved subject/role
bindings. Defaults remain closed; positive test providers are labelled synthetic.
"""
from dataclasses import dataclass,field
from http.cookies import SimpleCookie
from types import SimpleNamespace
import hashlib,json,time
from .workflow import Actor,WorkflowError,closed_sqlite,canonical,ORIGIN
@dataclass(frozen=True)
class VerifiedIdentity:
 subject:str
 profile_id:str
 expires_at:float
 evidence_id:str
 synthetic:bool
 _credential:str=field(repr=False,compare=False)
class NativeDashboardIdentityProvider:
 def __init__(self,provider,*,profile_id,clock=time.time,cookie_reader=None,subject_builder=None,session_type=None,synthetic=False):
  if not synthetic and any(v is not None for v in (cookie_reader,subject_builder,session_type)):raise ValueError('Custom identity test hooks require explicit synthetic mode')
  self.provider=provider;self.profile_id=profile_id;self.clock=clock;self.cookie_reader=cookie_reader;self.subject_builder=subject_builder;self.session_type=session_type;self.synthetic=synthetic
 def hooks(self):
  if self.cookie_reader is not None:return self.cookie_reader,self.subject_builder,self.session_type
  # Actual installed primary interfaces, inspected before implementation.
  from hermes_cli.dashboard_auth.cookies import read_session_cookies
  from hermes_cli.dashboard_auth.base import Session,DashboardAuthProvider
  from gateway.session_identity import authenticated_subject
  if not isinstance(self.provider,DashboardAuthProvider):raise WorkflowError(503,'Existing native authentication provider is not available')
  return read_session_cookies,authenticated_subject,Session
 def _verify(self,credential):
  if self.provider is None:raise WorkflowError(503,'GATE: no existing verified task identity provider is connected')
  _,subject_builder,kind=self.hooks()
  try:session=self.provider.verify_session(access_token=credential)
  except Exception:raise WorkflowError(503,'Native identity verification unavailable')from None
  if not isinstance(session,kind)or not isinstance(session.expires_at,(int,float))or session.expires_at<=self.clock():raise WorkflowError(401,'Verified identity expired, revoked or invalid')
  subject=subject_builder({'user_id':session.user_id,'provider':session.provider,'issuer':session.issuer})
  if not isinstance(subject,str)or not subject:raise WorkflowError(401,'Verified identity subject unavailable')
  return subject,float(session.expires_at)
 def authenticate(self,request):
  if self.provider is None:raise WorkflowError(503,'GATE: no existing verified task identity provider is connected')
  raw=request.headers.get('Cookie','')
  if len(raw)>16384:raise WorkflowError(401,'Invalid authentication cookie')
  cookies=SimpleCookie()
  try:cookies.load(raw)
  except Exception:raise WorkflowError(401,'Invalid authentication cookie')from None
  reader,_,_=self.hooks();credential,_=reader(SimpleNamespace(cookies={k:v.value for k,v in cookies.items()}))
  if not isinstance(credential,str)or not credential:raise WorkflowError(401,'Verified native session required')
  subject,expires=self._verify(credential)
  return VerifiedIdentity(subject,self.profile_id,expires,hashlib.sha256(credential.encode()).hexdigest(),self.synthetic,credential)
 def check(self,identity):
  if not isinstance(identity,VerifiedIdentity)or identity.profile_id!=self.profile_id:raise WorkflowError(401,'No independently verified identity')
  if identity.expires_at<=self.clock():raise WorkflowError(401,'Identity expired')
  subject,expires=self._verify(identity._credential)
  if subject!=identity.subject or expires<=self.clock():raise WorkflowError(401,'Identity binding changed')
  return identity
class TaskAuthority:
 def __init__(self,path,identity_provider,bindings=None,*,clock=time.time):
  self.path=path;self.identity_provider=identity_provider;self.bindings=dict(bindings or {});self.clock=clock;self.approval_sources={}
  with closed_sqlite(self.path)as c:c.execute('CREATE TABLE IF NOT EXISTS task_authorisations(task_id TEXT PRIMARY KEY,body TEXT NOT NULL)')
 def authenticate(self,request):
  identity=self.identity_provider.authenticate(request);role=self.bindings.get((identity.subject,identity.profile_id))
  if role not in ('owner','mira','sage','seline','surge','spectra','native'):raise WorkflowError(403,'No approved task-specific role binding for this verified subject')
  return Actor(identity.subject,role,identity)
 def role(self,actor):
  if not isinstance(actor,Actor):raise WorkflowError(401,'Verified principal required')
  identity=self.identity_provider.check(actor.identity)
  if actor.id!=identity.subject or self.bindings.get((identity.subject,identity.profile_id))!=actor.role:raise WorkflowError(403,'Principal does not match the server role binding')
  return actor.role
 def grant(self,task):
  with closed_sqlite(self.path)as c:row=c.execute('SELECT body FROM task_authorisations WHERE task_id=?',(task['id'],)).fetchone()
  if not row:raise WorkflowError(403,'No genuine task-specific owner approval evidence')
  grant=json.loads(row[0])
  if grant['contract_hash']!=task['contract_hash']or grant['origin']!=task['origin']or grant['assignee']!=task['assignee']:raise WorkflowError(403,'Task authority scope mismatch')
  return grant
 def authorize(self,actor,op,data,task=None):
  role=self.role(actor)
  if op=='create':
   if role not in ('owner','mira'):raise WorkflowError(403,'Only owner/MIRA may draft an unapproved task')
   return
  if task is None or task['origin']!=ORIGIN:raise WorkflowError(403,'Task scope/origin unavailable')
  if op in ('approve','revoke','review'):
   if role!='owner':raise WorkflowError(403,'Only genuine bound owner may decide task authority or review')
   if op=='approve'and data.get('contract_hash')!=task['contract_hash']:raise WorkflowError(409,'Exact task contract approval required')
   if op=='review'and(not task['artifact']or data.get('revision')!=task['revision']or data.get('sha256')!=task['artifact']['sha256']):raise WorkflowError(409,'Review references a stale or different exact revision')
   return
  allowed={'dispatch':'mira','recover':'mira','move':'mira','ack':task['assignee'],'submit':task['assignee'],'observe':'native'}
  if role!=allowed.get(op):raise WorkflowError(403,'Principal lacks task-specific operation authority')
  if op in ('move','recover'):return # Reading an accepted receipt never resubmits/cancels it.
  grant=self.grant(task)
  if op=='dispatch':
   if grant['revoked']or grant['expires_at']<=self.clock()or task['approval']!='approved':raise WorkflowError(403,'Task dispatch approval expired or revoked')
   source=self.approval_sources.get(task['id'])
   if source is None:raise WorkflowError(503,'Approval identity must be freshly reverified after authority restart; no credentials are persisted')
   self.identity_provider.check(source)
  # Finishing accepted work is not cancelled by revocation of future dispatch.
 def after_command(self,actor,op,task):
  if op=='approve':
   identity=self.identity_provider.check(actor.identity)
   grant={'task_id':task['id'],'contract_hash':task['contract_hash'],'origin':task['origin'],'assignee':task['assignee'],'owner_subject':identity.subject,'owner_evidence':identity.evidence_id,'expires_at':identity.expires_at,'revoked':False,'synthetic':identity.synthetic}
   with closed_sqlite(self.path)as c:c.execute('INSERT INTO task_authorisations VALUES(?,?) ON CONFLICT(task_id) DO UPDATE SET body=excluded.body',(task['id'],canonical(grant)))
   self.approval_sources[task['id']]=identity
  elif op=='revoke':
   try:grant=self.grant(task)
   except WorkflowError:return
   grant['revoked']=True
   with closed_sqlite(self.path)as c:c.execute('UPDATE task_authorisations SET body=? WHERE task_id=?',(canonical(grant),task['id']))
   self.approval_sources.pop(task['id'],None)
 def dispatch_permit(self,actor,task):
  self.authorize(actor,'dispatch',{'task_id':task['id']},task);grant=self.grant(task)
  return {**grant,'coordinator_subject':actor.identity.subject,'profile_id':actor.identity.profile_id,'coordinator_evidence':actor.identity.evidence_id,'synthetic':bool(grant['synthetic']or actor.identity.synthetic)}
