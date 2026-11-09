import config
"""Bounded native-runtime worker. Fixed actions/agents; no shell or arbitrary RPC.

Model metadata APIs are used instead of session.info/resume for existing group
conversations, because those APIs can return private prompts/history.
"""
import asyncio,contextlib,io,json,os,re,sqlite3,sys
from pathlib import Path
from .control_core import Requests,AGENTS
from .plugin import HOMES

GROUP=config.GROUP_ID
THREADS={a.upper():v for a,v in config.THREADS.items()}

@contextlib.contextmanager
def database(home):
    path=home/'state.db'
    if not path.is_file():raise LookupError('Native state unavailable')
    c=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=3);c.row_factory=sqlite3.Row
    try:c.execute('PRAGMA query_only=1');yield c
    finally:c.close()

def binding(agent):
    home=HOMES[agent]
    with database(home) as c:
        if os.environ.get('THE_S_AI_TEST_SCOPE')=='owner-approved-v135' and agent in ('SAGE','SURGE'):
            f=Path(str(config.DATA_DIR/'controlled-sessions'))/('controlled-session-'+agent.lower()+'.json')
            data=json.loads(f.read_text())
            if data.get('agent')!=agent or data.get('test_only') is not True:raise PermissionError('Invalid acceptance binding')
            row=c.execute("SELECT id,model,runtime_revision,runtime_generation FROM sessions WHERE id=? AND source IN ('cli','local')",(data['session_id'],)).fetchone()
        else:
            row=c.execute("SELECT id,model,runtime_revision,runtime_generation FROM sessions WHERE source='telegram' AND chat_id=? AND thread_id=? AND COALESCE(archived,0)=0 AND COALESCE(hidden,0)=0 ORDER BY started_at DESC,id DESC LIMIT 1",(GROUP,THREADS[agent])).fetchone()
        if not row:return None
        result=dict(row)
        from gateway.session_deferred_pick import _PREFIX
        pending=c.execute("SELECT json_extract(value,'$.payload.model'),json_extract(value,'$.payload.provider') FROM state_meta WHERE key=?",(_PREFIX+row['id'],)).fetchone()
        result['pending_model']=pending[0] if pending else None;result['pending_provider']=pending[1] if pending else None
        return result

async def metadata(client,agent):
    profile=await client.rpc('model.options',include_unconfigured=False)
    result={'provider':profile.get('provider'),'model':profile.get('model'),'has_group_session':False,
            'session_model':None,'session_provider':None,'pending_model':None,'pending_provider':None}
    target=binding(agent)
    if target:
        try:
            selected=await client.rpc('model.options',session_id=target['id'],include_unconfigured=False)
            result.update(has_group_session=True,session_model=selected.get('model'),session_provider=selected.get('provider'),
                          pending_model=target['pending_model'],pending_provider=target['pending_provider'])
        except Exception:
            result.update(has_group_session=True,session_model=target['model'],pending_model=target['pending_model'],pending_provider=target['pending_provider'])
    return result

async def manage_session(client,agent,store):
    with store.ledger.connect() as c:row=c.execute('SELECT session_id FROM control_sessions WHERE agent=?',(agent,)).fetchone()
    if row:return row['session_id']
    # Empty management conversation: never submit a prompt or build an AI agent.
    created=await client.rpc('session.create',request_id='the-s-ai-control-v135-'+agent,source='cli',
        cwd=str(HOMES[agent]/'workspace'),toolsets=[],follow_profile_config=True,hidden=True,
        title='Mission Control model control '+agent)
    sid=created['session_id']
    with store.ledger.connect() as c:c.execute('INSERT INTO control_sessions VALUES(?,?)',(agent,sid))
    return sid

async def run(data):
    config.require_runtime()
    if not isinstance(data,dict) or set(data)-{'agent','action','key','request_id'}:raise ValueError('Unknown native control fields')
    agent,action=data.get('agent'),data.get('action')
    if agent not in HOMES or action not in ('status','default','session','health'):raise ValueError('Unknown native control')
    home=HOMES[agent]
    if Path(os.environ.get('HERMES_HOME','')).resolve()!=home:raise PermissionError('Native agent/home mismatch')
    from hermes_cli.gateway_runtime import discover_gateway_endpoint
    from hermes_cli.gateway_client import connect_gateway
    found=discover_gateway_endpoint(home,timeout=5)
    if found.state!='ready' or found.endpoint is None or found.endpoint.profile_id!=str(home):
        raise RuntimeError('Existing gateway unavailable; no startup/restart attempted')
    async with connect_gateway(endpoint=found.endpoint) as client:
        if action=='status':return await metadata(client,agent)
        if action=='health':
            info=await client.rpc('plugins.list')
            rows=[p for p in info.get('plugins',[]) if p.get('name')=='the-s-ai-control']
            return {'active':len(rows)==1 and rows[0].get('enabled') is True and not rows[0].get('error') and rows[0].get('hooks')==3,
                    'instance_id':found.endpoint.instance_id}
        store=Requests();request_id=data.get('request_id');Requests.valid_id(request_id)
        if action=='default':
            request=store.read(request_id)
            if request['agent']!=agent:raise PermissionError('Control request belongs to another profile')
            if request['state']!='pending':return request.get('result') or {'status':'unconfirmed'}
            sid=await manage_session(client,agent,store)
            reply=await client.rpc('command.dispatch',session_id=sid,name='the-s-ai-control',arg=request_id,_timeout=35)
            if reply.get('type')!='exec':raise RuntimeError('Native guarded command did not execute')
            result=json.loads(reply.get('output',''))
            readback=store.read(request_id)
            return readback.get('result') or result
        from .model_control import Catalog
        from .account_policy import AccountPolicy
        from .control_core import CATALOG
        catalog=Catalog(CATALOG);selected=catalog.validate(data.get('key'),agent)
        AccountPolicy(json.loads((CATALOG/'existing-account-verification.json').read_text())).require_switch(agent,selected['provider'])
        target=binding(agent)
        if target is None:return {'status':'no_group_session','profile_default_only':True}
        receipt=await client.rpc('session.mutate',session_id=target['id'],request_id=request_id,operation='model',
            expected_revision=target['runtime_revision'],expected_generation=target['runtime_generation'],
            payload={'model':selected['model'],'provider':selected['provider']})
        pending=binding(agent)
        deferred=receipt.get('status')=='deferred'
        return {'status':'deferred' if deferred else 'committed' if receipt.get('model')==selected['model'] else 'unconfirmed',
                'model':selected['model'],'provider':selected['provider'],
                'pending_model':pending.get('pending_model') if pending else None,
                'revision':receipt.get('revision'),'execution_generation':receipt.get('execution_generation')}

if __name__=='__main__':
    try:
        body=sys.stdin.read(20001)
        if len(body)>20000:raise ValueError('Native request too large')
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            result=asyncio.run(run(json.loads(body)))
        print(json.dumps({'ok':True,'result':result},allow_nan=False))
    except Exception as exc:
        code=str(exc)
        safe=code if type(exc).__name__=='GatewayRPCError' and re.fullmatch(r'[a-z_]{1,80}',code) else type(exc).__name__
        print(json.dumps({'ok':False,'error':safe}));sys.exit(1)
