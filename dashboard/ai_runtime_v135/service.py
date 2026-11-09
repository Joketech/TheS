import config
"""Dashboard-side native control orchestration, private data only, stdlib only."""
import concurrent.futures,json,os,re,subprocess,threading,time,uuid
from pathlib import Path
from .control_core import Requests,AGENTS,CATALOG
from .plugin import HOMES

NATIVE=Path(str(config.NATIVE_ROOT))

def native_call(agent,action,**params):
    config.require_runtime()
    if agent not in HOMES:raise ValueError('Unknown agent')
    env=config.child_environment(HOMES[agent])
    result=subprocess.run([config.HERMES_PYTHON,'-B','-m','ai_runtime_v135.native_worker'],
        input=json.dumps({'agent':agent,'action':action,**params}),text=True,capture_output=True,
        env=env,cwd=str(config.ROOT),timeout=50,shell=False)
    try:value=json.loads(result.stdout)
    except (ValueError,TypeError):raise RuntimeError('Native control response unavailable; inspect before retrying') from None
    if result.returncode or value.get('ok') is not True:
        raise RuntimeError('Native control refused: '+str(value.get('error','Unavailable')))
    return value['result']

def account_policy():
    from .account_policy import AccountPolicy
    return AccountPolicy(json.loads((CATALOG/'existing-account-verification.json').read_text()))

class ModelControls:
    def __init__(self,store,catalog,policy=account_policy,worker=native_call):
        self.store,self.catalog,self.policy,self.worker=store,catalog,policy,worker
        self.locks={agent:threading.RLock() for agent in AGENTS}
        with store.ledger.connect() as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS task_choices(agent TEXT PRIMARY KEY,model_key TEXT NOT NULL,
                created REAL NOT NULL,state TEXT NOT NULL,request_id TEXT);
                CREATE TABLE IF NOT EXISTS model_operation_results(request_id TEXT PRIMARY KEY,result TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS task_choice_attempts(agent TEXT,request_id TEXT PRIMARY KEY,native_input_id TEXT);''')

    def check(self,agent,key):
        if agent not in AGENTS:raise ValueError('Unknown agent')
        row=self.catalog.validate(key,agent);self.policy().require_switch(agent,row['provider'])
        return row

    def status(self,agent):
        if agent not in AGENTS:raise ValueError('Unknown agent')
        return self.worker(agent,'status')

    def apply(self,agent,key,request_id):
        self.check(agent,key);Requests.valid_id(request_id)
        if len(request_id)>80:raise ValueError('Control request ID too long')
        with self.locks[agent]:
            with self.store.ledger.connect() as c:
                prior=c.execute('SELECT result FROM model_operation_results WHERE request_id=?',(request_id,)).fetchone()
            if prior:
                previous=json.loads(prior['result'])
                if previous.get('agent')!=agent or previous.get('key')!=key:raise ValueError('Conflicting request ID')
                return {**previous,'replayed':True}
            try:
                old=self.store.read(request_id)
            except LookupError:old=None
            if old:
                if old['agent']!=agent or old['key']!=key:raise ValueError('Conflicting request ID')
                return {'status':'unconfirmed','agent':agent,'key':key,'request_id':request_id,
                        'error':'A prior attempt exists; inspect effective state before a new request'}
            before=self.status(agent)
            expected={'provider':before.get('provider'),'model':before.get('model')}
            if not all(isinstance(v,str) and v for v in expected.values()):raise RuntimeError('Effective profile configuration unavailable')
            rollback_group=None
            if before.get('has_group_session'):
                if not before.get('session_model') or not before.get('session_provider'):
                    raise RuntimeError('Existing session route cannot be backed up safely')
                rollback_group=before['session_provider']+'|'+before['session_model']
                self.catalog.validate(rollback_group,agent)
            # Validate rollback before changing either authority.
            self.catalog.validate(expected['provider']+'|'+expected['model'],agent)
            self.store.issue(request_id,agent,key,expected)
            session=None
            try:
                session=self.worker(agent,'session',key=key,request_id=request_id+'-session')
                if session.get('status') not in ('committed','deferred','no_group_session'):
                    raise RuntimeError('Native session selection not confirmed')
                result=self.worker(agent,'default',request_id=request_id)
                after=self.status(agent)
                wanted=self.catalog.validate(key,agent)
                if result.get('status')!='verified' or (after.get('provider'),after.get('model'))!=(wanted['provider'],wanted['model']):
                    raise RuntimeError('Native profile-default readback not confirmed')
                outcome={'status':'queued' if session.get('status')=='deferred' else 'applied',
                    'agent':agent,'key':key,'request_id':request_id,'provider':after['provider'],'model':after['model'],
                    'session_status':session['status'],'previous':expected,'restart_required':False,
                    'scope':'profile default and approved Mission Control group conversation; explicit job/private-session overrides remain separate'}
            except Exception:
                rollback='not_required'
                if session and session.get('status') in ('committed','deferred') and rollback_group:
                    try:
                        restored=self.worker(agent,'session',key=rollback_group,request_id=request_id+'-rollback')
                        rollback=restored.get('status','unconfirmed')
                    except Exception:rollback='unconfirmed'
                try:actual=self.status(agent)
                except Exception:actual={}
                unchanged=(actual.get('provider'),actual.get('model'))==(expected['provider'],expected['model'])
                outcome={'status':'failed' if unchanged and rollback!='unconfirmed' else 'unconfirmed','agent':agent,'key':key,
                    'request_id':request_id,'rollback_session_status':rollback,'profile_unchanged':unchanged,
                    'error':'Selection failed; inspect effective state before retrying','previous':expected}
                if old is None:
                    self.store.finish(request_id,{'status':outcome['status'],
                        'provider':actual.get('provider'),'model':actual.get('model'),'error':outcome['error']})
            with self.store.ledger.connect() as c:
                c.execute('INSERT INTO model_operation_results VALUES(?,?)',(request_id,json.dumps(outcome)))
            return outcome

    def fleet(self,key,request_id):
        Requests.valid_id(request_id)
        before={}
        # All preflight checks complete before any fleet write.
        for agent in AGENTS:
            self.check(agent,key);state=self.status(agent)
            previous=state.get('provider','')+'|'+state.get('model','')
            self.catalog.validate(previous,agent);before[agent]=previous
        results=[];applied=[]
        for index,agent in enumerate(AGENTS):
            result=self.apply(agent,key,request_id+'-'+str(index));results.append(result)
            if result['status'] not in ('applied','queued'):
                rollback=[]
                for restored in reversed(applied):
                    rollback.append(self.apply(restored,before[restored],uuid.uuid4().hex))
                return {'status':'failed','results':results,'rollback':rollback,'atomic':False,
                        'note':'Native profiles are separate authorities; inspect every result'}
            applied.append(agent)
        return {'status':'queued' if any(r['status']=='queued' for r in results) else 'applied','results':results,'atomic':False}

    def once(self,agent,key):
        if agent not in AGENTS:raise ValueError('Unknown agent')
        if key is not None:self.check(agent,key)
        with self.store.ledger.connect() as c:
            current=c.execute('SELECT state FROM task_choices WHERE agent=?',(agent,)).fetchone()
            if current and current['state'] in ('claimed','attempted'):raise RuntimeError('A dashboard task owns this choice; inspect its admission before replacing it')
            if key is None:c.execute('DELETE FROM task_choices WHERE agent=?',(agent,))
            else:c.execute('INSERT INTO task_choices VALUES(?,?,?,?,NULL) ON CONFLICT(agent) DO UPDATE SET model_key=excluded.model_key,created=excluded.created,state=excluded.state,request_id=NULL',(agent,key,time.time(),'pending'))
        return {'agent':agent,'key':key,'scope':'next dashboard Chat task only','profile_default_changed':False}

    def claim_once(self,agent,request_id):
        with self.store.ledger.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT * FROM task_choices WHERE agent=?',(agent,)).fetchone()
            if not row:return None
            if row['state']!='pending':raise RuntimeError('One-task choice is already claimed')
            selected=self.check(agent,row['model_key'])
            c.execute("UPDATE task_choices SET state='claimed',request_id=? WHERE agent=?",(request_id,agent))
        return {'model':selected['model'],'provider':selected['provider']}

    def once_attempt(self,agent,request_id,native_input_id):
        if not isinstance(native_input_id,str) or not native_input_id:raise ValueError('Native input identity unavailable')
        with self.store.ledger.connect() as c:
            c.execute("UPDATE task_choices SET state='attempted' WHERE agent=? AND request_id=?",(agent,request_id))
            c.execute('INSERT OR IGNORE INTO task_choice_attempts VALUES(?,?,?)',(agent,request_id,native_input_id))

    def settle_once(self,agent,request_id,accepted,*,definitive=False):
        with self.store.ledger.connect() as c:
            if accepted:c.execute('DELETE FROM task_choices WHERE agent=? AND request_id=?',(agent,request_id))
            elif definitive:c.execute("UPDATE task_choices SET state='pending',request_id=NULL WHERE agent=? AND request_id=?",(agent,request_id))
            else:c.execute("UPDATE task_choices SET state='pending',request_id=NULL WHERE agent=? AND request_id=? AND state='claimed'",(agent,request_id))

    def choices(self):
        with self.store.ledger.connect() as c:
            return {r['agent']:{'key':r['model_key'],'state':r['state']} for r in c.execute('SELECT agent,model_key,state FROM task_choices')}
