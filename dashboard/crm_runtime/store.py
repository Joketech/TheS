import config
"""Project-owned CRM. Transactions, explicit relationships and immutable audit entries."""
import contextlib,datetime,hashlib,json,re,sqlite3,uuid
from decimal import Decimal,InvalidOperation
from pathlib import Path
ROOT=config.DATA_DIR;AGENTS={'MIRA','SAGE','SURGE','SELINE','SPECTRA'}
STAGES=['New lead','Qualified','Meeting','Proposal','Won','Lost','Disqualified']
TABLES={'company':'companies','contact':'contacts','opportunity':'opportunities','action':'actions'}
RELATIONS={'companies':{},'contacts':{'company_id':'companies'},'opportunities':{'company_id':'companies'},'actions':{'opportunity_id':'opportunities'}}
FIELDS={'companies':{'name','domain','sector','evidence','uncertainty'},'contacts':{'company_id','name','email','role','evidence','uncertainty'},'opportunities':{'company_id','title','amount','currency','stage','agent','evidence','uncertainty','reason','confirmation','technical_scope','milestones','qualification'},'actions':{'opportunity_id','text','agent','due_at','done'}}
TABLES.update(payment='payments',note='notes',draft='drafts',approval='approvals')
RELATIONS.update(payments={'opportunity_id':'opportunities'},notes={'opportunity_id':'opportunities'},drafts={'opportunity_id':'opportunities'},approvals={'draft_id':'drafts'})
FIELDS.update(payments={'opportunity_id','amount','currency','confirmed','evidence','received_at'},notes={'opportunity_id','body','evidence','kind','uncertainty'},drafts={'opportunity_id','kind','content'},approvals={'draft_id','draft_version','decision','confirmed','evidence'})
class Conflict(Exception):pass
class Duplicate(Conflict):
 def __init__(self,records):self.records=records;super().__init__('Potential duplicate; review existing records before creating or merging')
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def identifier(value):
 if not isinstance(value,str) or not re.fullmatch(r'[a-f0-9]{32}',value):raise ValueError('Invalid CRM record ID')
 return value
def text(value,limit=4096,required=False):
 if not isinstance(value,str) or len(value)>limit or '\x00' in value or required and not value.strip():raise ValueError('Invalid or missing text')
 return value
def amount(value):
 if value is None:return None
 if isinstance(value,bool) or not isinstance(value,(str,int)):raise ValueError('Money must be a decimal string')
 try:n=Decimal(value)
 except InvalidOperation:raise ValueError('Invalid amount') from None
 if not n.is_finite() or n<0 or n>Decimal('1000000000000') or n.as_tuple().exponent < -2:raise ValueError('Amount must be nonnegative with at most two decimal places')
 return format(n.normalize(),'f')
def currency(value):
 if not isinstance(value,str) or not re.fullmatch('[A-Z]{3}',value):raise ValueError('Explicit three-letter currency required')
 return value
class CRMStore:
 def __init__(self,path=None):
  ROOT.mkdir(parents=True,exist_ok=True)
  self.path=Path(path or ROOT/'crm.db').absolute()
  if '..' in self.path.parts or not self.path.resolve().is_relative_to(ROOT.resolve()) or self.path.suffix!='.db' or any(p.is_symlink() for p in [self.path,*self.path.parents]):raise ValueError('CRM database must be project-owned')
  with self.connect() as c:
   c.executescript('''CREATE TABLE IF NOT EXISTS migrations(version INTEGER PRIMARY KEY,applied_at TEXT NOT NULL);
   CREATE TABLE IF NOT EXISTS companies(id TEXT PRIMARY KEY,version INTEGER NOT NULL,archived INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,payload TEXT NOT NULL);
   CREATE INDEX IF NOT EXISTS company_domain ON companies(json_extract(payload,'$.domain_key'));
   CREATE TABLE IF NOT EXISTS activity(id TEXT PRIMARY KEY,actor TEXT NOT NULL,operation TEXT NOT NULL,entity TEXT NOT NULL,record_id TEXT NOT NULL,before_json TEXT,after_json TEXT,created_at TEXT NOT NULL);
   CREATE TABLE IF NOT EXISTS requests(actor TEXT NOT NULL,request_id TEXT NOT NULL,digest TEXT NOT NULL,result TEXT NOT NULL,PRIMARY KEY(actor,request_id));''')
   for table,relations in RELATIONS.items():
    if table=='companies':continue
    fk=','.join(f'{key} TEXT NOT NULL REFERENCES {parent}(id)' for key,parent in relations.items())
    c.execute(f'CREATE TABLE IF NOT EXISTS {table}(id TEXT PRIMARY KEY,version INTEGER NOT NULL,archived INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,payload TEXT NOT NULL,{fk})')
   for version in [1,2,3]:c.execute('INSERT OR IGNORE INTO migrations VALUES(?,?)',(version,now()))
 @contextlib.contextmanager
 def connect(self):
  c=sqlite3.connect(self.path,timeout=10);c.row_factory=sqlite3.Row
  try:
   c.execute('PRAGMA foreign_keys=ON');c.execute('PRAGMA busy_timeout=10000')
   with c:yield c
  finally:c.close()
 @staticmethod
 def decode(row):
  if row is None:raise LookupError('CRM record not found')
  result=dict(row);payload=json.loads(result.pop('payload'));return {**payload,**result,'archived':bool(result['archived'])}
 def _read(self,c,entity,id):
  if entity not in RELATIONS:raise ValueError('Unknown CRM entity')
  identifier(id);return self.decode(c.execute(f'SELECT * FROM {entity} WHERE id=?',(id,)).fetchone())
 def read(self,entity,id):
  with self.connect() as c:return self._read(c,entity,id)
 def snapshot(self):
  with self.connect() as c:
   c.execute('BEGIN')  # One consistent read snapshot across related tables.
   result={table:[self.decode(r) for r in c.execute(f'SELECT * FROM {table} ORDER BY created_at,id')] for table in RELATIONS}
   result['activity']=[dict(r) for r in c.execute('SELECT * FROM activity ORDER BY created_at DESC,id DESC')]
  active=[r for r in result['opportunities'] if not r['archived'] and r['stage'] not in ['Lost','Disqualified']]
  opened=[r for r in active if r['stage']!='Won'];totals={}
  for r in opened:
   if r['amount'] is not None:totals[r['currency']]=totals.get(r['currency'],Decimal(0))+Decimal(r['amount'])
  cash={}
  for row in result['payments']:cash[row['currency']]=cash.get(row['currency'],Decimal(0))+Decimal(row['amount'])
  reviews={(r['draft_id'],r['draft_version']):r for r in result['approvals']}
  for row in result['drafts']:
   review=reviews.get((row['id'],row['version']));row['approved']=bool(review and review['decision']=='approved' and review['content_hash']==hashlib.sha256(row['content'].encode()).hexdigest())
  stage_values={stage:{} for stage in STAGES}
  for row in result['opportunities']:
   if not row['archived'] and row['amount'] is not None:
    bucket=stage_values[row['stage']];bucket[row['currency']]=bucket.get(row['currency'],Decimal(0))+Decimal(row['amount'])
  live_ids={r['id'] for r in result['opportunities'] if not r['archived']}
  result['summary']={'open_value':{k:format(v.normalize(),'f') for k,v in totals.items()},'unvalued':sum(r['amount'] is None for r in opened),'opportunities':len(active),'cash_received':{k:format(v.normalize(),'f') for k,v in cash.items()},'stage_values':{stage:{code:format(value.normalize(),'f') for code,value in values.items()} for stage,values in stage_values.items()},'next_actions':sum(not r['archived'] and not r['done'] and r['opportunity_id'] in live_ids for r in result['actions'])}
  return result
 def _permission(self,actor,table,create,data,before):
  if actor=='OWNER':return
  if table in ['companies','contacts']:
   if actor not in {'MIRA','SAGE','SURGE'}:raise PermissionError('Research/company updates belong to MIRA, SAGE or SURGE')
  elif table=='opportunities':
   fields=set(data)-{'id','expected_version'}
   if before and before['stage']=='Won' and fields&{'company_id','title','amount','currency','stage','confirmation'}:raise PermissionError('Only the owner may revise confirmed won commercial terms')
   if create:
    if actor not in {'MIRA','SAGE','SURGE'}:raise PermissionError('Opportunity creation belongs to MIRA, SAGE or SURGE')
    if actor=='SAGE' and fields&{'amount','stage','qualification'}:raise PermissionError('Research cannot assert commercial value or qualification')
   elif actor=='SAGE' and fields-{'evidence','uncertainty'}:raise PermissionError('SAGE may update research evidence and uncertainty only')
   elif actor=='SPECTRA' and fields-{'evidence','technical_scope','milestones'}:raise PermissionError('SPECTRA may update technical feasibility and milestones only')
   elif actor=='SELINE':raise PermissionError('Use linked drafts/notes, not commercial record edits')
   if 'agent' in data and actor!='MIRA' and data['agent']!=actor:raise PermissionError('MIRA coordinates agent assignment')
  elif table=='actions':
   if actor!='MIRA' and (before and before['agent']!=actor or data.get('agent',actor) not in {actor,'MIRA'}):raise PermissionError('Only MIRA coordinates other agents’ next actions')
  elif table in {'payments','approvals'}:raise PermissionError('Only the owner may confirm payments or approve a particular draft version')
  elif table=='drafts' and actor!='SELINE':raise PermissionError('SELINE prepares linked outreach and proposal drafts')
 def _values(self,c,table,data,before,actor):
  create=before is None;allowed=FIELDS[table]
  if set(data)-allowed-({'id','expected_version'} if not create else set()):raise ValueError('Unknown CRM fields')
  self._permission(actor,table,create,data,before)
  defaults={'companies':{'name':'','domain':'','sector':'','evidence':'','uncertainty':'Unverified'},'contacts':{'name':'','email':'','role':'','evidence':'','uncertainty':'Unverified'},'opportunities':{'title':'','amount':None,'currency':None,'stage':'New lead','agent':actor if actor in AGENTS else 'MIRA','evidence':'','uncertainty':'Unverified','reason':'','confirmation':'','technical_scope':'','milestones':'','qualification':''},'actions':{'text':'','agent':actor if actor in AGENTS else 'MIRA','due_at':None,'done':False}}
  defaults.update(payments={'amount':None,'currency':None,'confirmed':False,'evidence':'','received_at':now()},notes={'body':'','evidence':'','kind':'note','uncertainty':'Unverified'},drafts={'kind':'outreach','content':''},approvals={'draft_version':None,'decision':'approved','confirmed':False,'evidence':''})
  values={**(defaults[table] if create else {k:before.get(k) for k in allowed}),**{k:v for k,v in data.items() if k in allowed}}
  for key,parent in RELATIONS[table].items():
   value=values.get(key);target=self._read(c,parent,value)
   if target['archived']:raise Conflict('Parent record is archived')
   if before and value!=before[key]:raise ValueError('Parent relationships change only through an owner-reviewed merge')
  for key in set(values)-{'amount','currency','due_at','done','confirmed','draft_version'}:
   if key not in RELATIONS[table]:
    # Full proposals remain bounded; only draft content has the larger ceiling.
    limit=180 if key in {'name','domain','sector','email','role','title','agent','stage'} else 32000 if table=='drafts' and key=='content' else 8000
    text(values[key],limit,key in {'name'} and table=='companies' or key in {'title','text','body','content'})
  if table=='companies':
   domain=values['domain']
   if domain and not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?',domain):raise ValueError('Domain must be a hostname, not a URL')
   values['domain_key']=domain.casefold()
   if domain:self._duplicates(c,table,'domain_key',values['domain_key'],before)
  if table=='contacts':
   if not any(values.get(k) for k in ['name','email','role']):raise ValueError('Contact name, public business email or role required')
   if values['email'] and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',values['email']):raise ValueError('Invalid business email')
   values['identity_key']=values['email'].casefold() or (values['company_id']+'|'+values['name'].casefold()+'|'+values['role'].casefold())
   self._duplicates(c,table,'identity_key',values['identity_key'],before)
  if table=='opportunities':
   if values['stage'] not in STAGES or values['agent'] not in AGENTS:raise ValueError('Unknown stage or agent')
   values['amount']=amount(values['amount'])
   if values['currency'] is not None:values['currency']=currency(values['currency'])
   if values['amount'] is not None and not values['currency']:raise ValueError('Currency required for valuation')
   values['coordinator']='MIRA'
   if values['stage']=='Won' and (create or before['stage']!='Won') and (actor!='OWNER' or not values['confirmation'].strip()):raise PermissionError('Won requires recorded owner confirmation; it does not record payment')
   if values['stage'] in ['Lost','Disqualified'] and not values['reason'].strip():raise ValueError('Closing reason required')
  if table=='actions':
   if values['agent'] not in AGENTS or type(values['done']) is not bool:raise ValueError('Invalid action assignment or status')
   if values['due_at']:
    value=datetime.datetime.fromisoformat(values['due_at'])
    if value.tzinfo is None:raise ValueError('Action deadline must have an explicit timezone')
    values['due_at']=value.astimezone(datetime.timezone.utc).isoformat()
  if table=='payments':
   values['amount']=amount(values['amount']);values['currency']=currency(values['currency'])
   if not values['amount'] or Decimal(values['amount'])<=0 or values['confirmed'] is not True or not values['evidence'].strip():raise ValueError('Positive amount, explicit confirmation and receipt evidence required')
   received=datetime.datetime.fromisoformat(values['received_at'])
   if received.tzinfo is None:raise ValueError('Receipt time requires a timezone')
   values['received_at']=received.astimezone(datetime.timezone.utc).isoformat()
  if table=='drafts' and values['kind'] not in {'outreach','proposal','follow-up'}:raise ValueError('Unknown draft kind')
  if table=='approvals':
   draft=self._read(c,'drafts',values['draft_id'])
   if type(values['draft_version']) is not int or values['draft_version']!=draft['version']:raise Conflict('Draft changed; review the current version')
   if values['confirmed'] is not True or values['decision'] not in {'approved','rejected'} or not values['evidence'].strip():raise ValueError('Specific owner review confirmation required')
   values['content_hash']=hashlib.sha256(draft['content'].encode()).hexdigest()
  return values
 def _duplicates(self,c,table,key,value,before):
  rows=[self.decode(r) for r in c.execute(f"SELECT * FROM {table} WHERE archived=0 AND id<>? AND json_extract(payload,'$.{key}')=?",(before['id'] if before else '',value))]
  if rows:raise Duplicate(rows)
 def command(self,actor,operation,data,request_id):
  if actor not in AGENTS|{'OWNER'}:raise PermissionError('Unknown CRM actor')
  if not isinstance(data,dict) or not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}',request_id):raise ValueError('A bounded idempotency key and object are required')
  digest=hashlib.sha256(json.dumps([operation,data],sort_keys=True,allow_nan=False).encode()).hexdigest()
  with self.connect() as c:
   c.execute('BEGIN IMMEDIATE');prior=c.execute('SELECT digest,result FROM requests WHERE actor=? AND request_id=?',(actor,request_id)).fetchone()
   if prior:
    if prior['digest']!=digest:raise Conflict('Idempotency key reused for different input')
    return json.loads(prior['result'])
   parts=operation.split('.')
   if len(parts)!=2 or parts[0] not in TABLES or parts[1] not in {'create','update','archive','merge'}:raise ValueError('Unsupported CRM operation')
   if parts[1] in {'archive','merge'}:
    result=self._special(c,actor,operation,data)
    c.execute('INSERT INTO requests VALUES(?,?,?,?)',(actor,request_id,digest,json.dumps(result)));return result
   table=TABLES[parts[0]];create=parts[1]=='create';before=None if create else self._read(c,table,data.get('id'))
   if not create and table in {'payments','approvals','notes'}:raise PermissionError('This is an append-only record')
   if before and (before['archived'] or type(data.get('expected_version')) is not int or data['expected_version']!=before['version']):raise Conflict('Record changed or archived; reload before updating')
   values=self._values(c,table,data,before,actor);record_id=uuid.uuid4().hex if create else before['id'];stamp=now()
   if create:
    keys=['id','version','archived','created_at','updated_at','payload',*RELATIONS[table]]
    args=[record_id,1,0,stamp,stamp,json.dumps(values),*[values[k] for k in RELATIONS[table]]]
    c.execute(f"INSERT INTO {table}({','.join(keys)}) VALUES({','.join('?' for _ in keys)})",args)
   else:c.execute(f'UPDATE {table} SET payload=?,version=version+1,updated_at=? WHERE id=? AND version=?',(json.dumps(values),stamp,record_id,before['version']))
   result=self._read(c,table,record_id)
   c.execute('INSERT INTO activity VALUES(?,?,?,?,?,?,?,?)',(uuid.uuid4().hex,actor,operation,table,record_id,json.dumps(before),json.dumps(result),stamp))
   c.execute('INSERT INTO requests VALUES(?,?,?,?)',(actor,request_id,digest,json.dumps(result)))
   return result

 def _audit(self,c,actor,operation,table,before,result):
  c.execute('INSERT INTO activity VALUES(?,?,?,?,?,?,?,?)',(uuid.uuid4().hex,actor,operation,table,result['id'],json.dumps(before),json.dumps(result),now()))

 def _special(self,c,actor,operation,data):
  if actor!='OWNER':raise PermissionError('Archiving and merging require owner review')
  entity,verb=operation.split('.');table=TABLES[entity]
  if verb=='archive':
   if table not in {'companies','contacts','opportunities','actions','drafts'} or set(data)!={'id','expected_version','reason'}:raise ValueError('Invalid archive request')
   old=self._read(c,table,data['id'])
   if type(data['expected_version']) is not int or old['version']!=data['expected_version']:raise Conflict('Record changed before archiving')
   text(data['reason'],4096,True)
   if table=='companies':
    for child in ['contacts','opportunities']:
     if c.execute(f'SELECT 1 FROM {child} WHERE company_id=? AND archived=0 LIMIT 1',(old['id'],)).fetchone():raise Conflict('Archive related contacts/opportunities first, or use a reviewed merge')
   payload=json.loads(c.execute(f'SELECT payload FROM {table} WHERE id=?',(old['id'],)).fetchone()[0]);payload['archive_reason']=data['reason']
   c.execute(f'UPDATE {table} SET archived=1,version=version+1,payload=?,updated_at=? WHERE id=?',(json.dumps(payload),now(),old['id']))
   result=self._read(c,table,old['id']);self._audit(c,actor,operation,table,old,result);return result
  if operation!='company.merge' or set(data)!={'source_id','target_id','source_version','target_version','confirmation'}:raise ValueError('Only reviewed company merges are supported')
  source=self._read(c,'companies',data['source_id']);target=self._read(c,'companies',data['target_id'])
  if source['id']==target['id'] or source['archived'] or target['archived'] or type(data['source_version']) is not int or type(data['target_version']) is not int or source['version']!=data['source_version'] or target['version']!=data['target_version']:raise Conflict('Merge records changed or are not eligible')
  text(data['confirmation'],4096,True)
  for child in ['contacts','opportunities']:
   for row in c.execute(f'SELECT * FROM {child} WHERE company_id=?',(source['id'],)).fetchall():
    before=self.decode(row);payload=json.loads(row['payload']);payload['company_id']=target['id']
    if child=='contacts' and not payload.get('email'):payload['identity_key']=target['id']+'|'+payload['name'].casefold()+'|'+payload['role'].casefold()
    c.execute(f'UPDATE {child} SET company_id=?,payload=?,version=version+1,updated_at=? WHERE id=?',(target['id'],json.dumps(payload),now(),row['id']))
    self._audit(c,actor,'company.merge.relationship',child,before,self._read(c,child,row['id']))
  payload=json.loads(c.execute('SELECT payload FROM companies WHERE id=?',(source['id'],)).fetchone()[0]);payload.update(merged_into=target['id'],merge_confirmation=data['confirmation'])
  c.execute('UPDATE companies SET archived=1,version=version+1,payload=?,updated_at=? WHERE id=?',(json.dumps(payload),now(),source['id']))
  c.execute('UPDATE companies SET version=version+1,updated_at=? WHERE id=?',(now(),target['id']))
  result=self._read(c,'companies',target['id']);self._audit(c,actor,operation,'companies',source,self._read(c,'companies',source['id']));self._audit(c,actor,'company.merge.target','companies',target,result);return result
