import config
"""Project-owned AI accounting. Standard library only; never calls an LLM."""
from datetime import datetime,time,timedelta,timezone,date
from zoneinfo import ZoneInfo
import hashlib,json,math,re,sqlite3
from pathlib import Path
from decimal import Decimal,InvalidOperation
from contextlib import contextmanager
from urllib.parse import urlsplit
REPORT_ZONE=config.REPORTING_ZONE
AGENTS={a.upper():config.RUNTIME_PROFILES.get(a,'unconfigured-'+a) for a in ('mira','sage','seline','surge','spectra')}
TOKENS=('input_tokens','cached_input_tokens','output_tokens')
def period_bounds(period,start=None,end=None,now=None):
 """UTC half-open boundaries for configured calendar dates; custom end is inclusive."""
 today=(now or datetime.now(timezone.utc)).astimezone(REPORT_ZONE).date()
 if period=='today':first=last=today
 elif period=='yesterday':first=last=today-timedelta(days=1)
 elif period=='last7':first,last=today-timedelta(days=6),today
 elif period=='custom':first,last=date.fromisoformat(start or ''),date.fromisoformat(end or '')
 else:raise ValueError('Unknown date period')
 if first>last or (last-first).days>366:raise ValueError('Choose an ordered range of at most 367 days')
 return datetime.combine(first,time.min,REPORT_ZONE).timestamp(),datetime.combine(last+timedelta(days=1),time.min,REPORT_ZONE).timestamp(),f'{first.isoformat()} — {last.isoformat()} {config.REPORTING_TIMEZONE}'
def identifier(value):
 if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_./:@+~ -]{1,180}',value) or re.search(r'(?i)(sk-|bearer |password|secret|token=)',value):raise ValueError('Invalid metadata identifier')
 return value

def money(value):
 if value is None:return None
 if isinstance(value,bool):raise ValueError('Invalid cost')
 try:number=Decimal(str(value))
 except InvalidOperation:raise ValueError('Invalid cost') from None
 if not number.is_finite() or number<0 or number>Decimal('1000000000'):raise ValueError('Invalid cost')
 return format(number.normalize(),'f') if number else '0'
def count(value):
 if value is None:return None
 if type(value) is not int or not 0<=value<=2**53:raise ValueError('Invalid usage counter')
 return value

def readonly(path):
 p=Path(path).resolve()
 if not p.is_file():raise FileNotFoundError('Usage source unavailable')
 c=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=1')
 if c.execute('PRAGMA query_only').fetchone()[0]!=1:c.close();raise RuntimeError('Read-only guard failed')
 return c

class Ledger:
 """Invocation events and legacy snapshots are separate, never additive sources."""
 def __init__(self,path):
  self.path=Path(path).resolve()
  if self.path.suffix!='.db' or not self.path.is_relative_to(config.DATA_DIR.resolve()):raise ValueError('Ledger must be a project-owned .db')
  self.path.parent.mkdir(parents=True,exist_ok=True)
  with self.connect() as c:
   c.executescript('''CREATE TABLE IF NOT EXISTS invocations(event_key TEXT PRIMARY KEY,event_id TEXT NOT NULL,provider TEXT NOT NULL,ended_at REAL NOT NULL,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS billing(provider TEXT NOT NULL,billing_id TEXT NOT NULL,event_id TEXT NOT NULL,amount TEXT NOT NULL,PRIMARY KEY(provider,billing_id));
CREATE TABLE IF NOT EXISTS legacy(record_key TEXT PRIMARY KEY,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS preferences(id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL);''')
 @contextmanager
 def connect(self):
  c=sqlite3.connect(self.path,timeout=8);c.row_factory=sqlite3.Row
  try:
   with c:yield c
  finally:c.close()
 def record(self,event):
  # Accept a closed metadata schema. Bodies, headers, messages and credentials are discarded.
  if event.get('agent') not in AGENTS:raise ValueError('Unknown agent')
  e={k:identifier(event[k]) for k in ['event_id','agent','provider','model','auth_method','billing_mode','cost_status','source']}
  e['run_id']=hashlib.sha256(str(event.get('run_id') or '').encode()).hexdigest()[:24] if event.get('run_id') else None
  ended=event.get('ended_at')
  if isinstance(ended,bool) or not isinstance(ended,(int,float)) or not math.isfinite(ended) or ended<=0:raise ValueError('Invalid event time')
  started=event.get('started_at')
  if started is not None and (isinstance(started,bool) or not isinstance(started,(int,float)) or not math.isfinite(started) or not 0<started<=ended):raise ValueError('Invalid start time')
  e['started_at']=started
  e['ended_at']=ended;e.update({key:count(event.get(key)) for key in TOKENS});e['estimate_usd']=money(event.get('estimate_usd'));e['actual_usd']=None
  e['pricing_source']=None;e['pricing_effective_date']=None
  if e['estimate_usd'] is not None:
   source=event.get('pricing_source');effective=event.get('pricing_effective_date')
   if not isinstance(source,str) or len(source)>500:raise ValueError('Dated pricing provenance required')
   parsed=urlsplit(source)
   if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:raise ValueError('Invalid pricing source')
   try:date.fromisoformat(effective)
   except (ValueError,TypeError):raise ValueError('Pricing effective date required') from None
   e['pricing_source']=source;e['pricing_effective_date']=effective
  # Only reconciliation through a unique provider billing line can confirm money.
  key=hashlib.sha256(json.dumps([e['agent'],e['provider'],e['event_id']],separators=(',',':')).encode()).hexdigest()
  with self.connect() as c:
   existing=c.execute('SELECT payload FROM invocations WHERE event_key=?',(key,)).fetchone()
   if existing:
    if json.loads(existing['payload'])!=e:raise ValueError('Conflicting duplicate invocation')
    return False
   c.execute('INSERT INTO invocations VALUES(?,?,?,?,?)',(key,e['event_id'],e['provider'],ended,json.dumps(e)))
  return True
 def confirm(self,provider,billing_id,event_id,amount):
  provider,billing_id,event_id=map(identifier,[provider,billing_id,event_id]);amount=money(amount)
  if amount is None:raise ValueError('Confirmed amount required')
  with self.connect() as c:
   rows=c.execute('SELECT payload FROM invocations WHERE provider=? AND event_id=?',(provider,event_id)).fetchall()
   if len(rows)!=1:raise ValueError('Billing line cannot be linked unambiguously')
   e=json.loads(rows[0]['payload'])
   if e['billing_mode']=='subscription_included':raise ValueError('Included plan use cannot become an API charge')
   prior=c.execute('SELECT event_id,amount FROM billing WHERE provider=? AND billing_id=?',(provider,billing_id)).fetchone()
   if prior:
    if tuple(prior)!=(event_id,amount):raise ValueError('Conflicting billing line')
    return False
   c.execute('INSERT INTO billing VALUES(?,?,?,?)',(provider,billing_id,event_id,amount));return True
 def import_legacy(self,rows):
  with self.connect() as c:
   for row in rows:
    if row.get('agent') not in AGENTS:raise ValueError('Unknown agent')
    c.execute('INSERT INTO legacy VALUES(?,?) ON CONFLICT(record_key) DO UPDATE SET payload=excluded.payload',(identifier(row['key']),json.dumps(row)))
 def summary(self,start,end):
  with self.connect() as c:
   events=[json.loads(r['payload']) for r in c.execute('SELECT payload FROM invocations WHERE ended_at>=? AND ended_at<?',(start,end))]
   billed={}
   for r in c.execute('SELECT provider,event_id,amount FROM billing'):billed.setdefault((r['provider'],r['event_id']),[]).append(Decimal(r['amount']))
   legacy=[json.loads(r['payload']) for r in c.execute('SELECT payload FROM legacy')]
  legacy=[r for r in legacy if r.get('last_seen',0)>=start and r.get('first_seen',end)<end]
  confirmed=[];estimates=[];included=unknown=0;groups={}
  for e in events:
   charges=billed.get((e['provider'],e['event_id']))
   inc=e['billing_mode']=='subscription_included';est=e['estimate_usd']
   actual=sum(charges,Decimal(0)) if charges else None
   if inc:included+=1;actual=None;est=None
   elif actual is not None:confirmed.append(actual);est=None
   elif est is not None:estimates.append(Decimal(est))
   else:unknown+=1
   k=(e['agent'],e['provider'],e['model'],e['auth_method'],e['billing_mode']);g=groups.setdefault(k,{'agent':k[0],'provider':k[1],'model':k[2],'auth_method':k[3],'billing_mode':k[4],'invocations':0,'confirmed_usd':None,'estimate_usd':None,'unknown_cost_invocations':0,**{t:None for t in TOKENS}})
   g['invocations']+=1
   for t in TOKENS:
    if e[t] is not None:g[t]=(g[t] or 0)+e[t]
   for field,value in [('confirmed_usd',actual),('estimate_usd',Decimal(est) if est is not None else None)]:
    if value is not None:g[field]=str(Decimal(g[field] or '0')+value)
   if not inc and actual is None and est is None:g['unknown_cost_invocations']+=1
  return {'confirmed_usd':str(sum(confirmed,Decimal(0))) if confirmed else None,'estimate_usd':str(sum(estimates,Decimal(0))) if estimates else None,'included_invocations':included,'unknown_cost_invocations':unknown,'invocation_count':len(events),'breakdown':list(groups.values()),'legacy_bucket_count':len(legacy),'legacy_buckets':legacy,'coverage':'Incomplete','coverage_reason':'No provider billing feed or live invocation observer is activated. Historical buckets are not daily invocation records.','subscription_label':'Included plan usage — monetary cost unavailable','updated_at':datetime.now(timezone.utc).isoformat(),'budget_kind':'alert only; not an enforceable cap'}

def collect_legacy(ledger,sources):
 """Read only approved numeric/model metadata, including cron and background sessions."""
 missing=[];rows=[]
 for agent,path in sources.items():
  if agent not in AGENTS:raise ValueError('Unknown agent')
  try:
   c=readonly(path)
   try:
    sql='''SELECT u.session_id,u.model,u.billing_provider,u.billing_mode,u.task,u.cost_status,u.first_seen,u.last_seen,u.input_tokens,u.cache_read_tokens,u.output_tokens,u.api_call_count,s.source FROM session_model_usage u JOIN sessions s ON s.id=u.session_id'''
    for r in c.execute(sql):
     key=hashlib.sha256(json.dumps([agent,r['session_id'],r['model'],r['billing_provider'],r['billing_mode'],r['task']],default=str).encode()).hexdigest()
     def safe(value):
      try:return identifier(value or 'Unavailable')
      except ValueError:return 'Unavailable'
     rows.append({'key':key,'agent':agent,'provider':safe(r['billing_provider']),'model':safe(r['model']),'billing_mode':safe(r['billing_mode']),'cost_status':safe(r['cost_status']),'source':safe(r['source']),'first_seen':r['first_seen'],'last_seen':r['last_seen'],'input_tokens':count(r['input_tokens']),'cached_input_tokens':count(r['cache_read_tokens']),'output_tokens':count(r['output_tokens']),'api_call_count':count(r['api_call_count'])})
   finally:c.close()
  except (OSError,sqlite3.Error,ValueError):missing.append(agent)
 ledger.import_legacy(rows)
 return {'missing_agents':missing,'legacy_buckets_imported':len(rows),'read_only':True}
