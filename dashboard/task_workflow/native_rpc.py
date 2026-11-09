import config
"""Run only in the dashboard's existing managed native child interpreter.

Fixed RPC allowlist and exact session. No ensure/start/create/cancel/mutate,
credentials export, profile configuration or model calls except explicitly
requested approved prompt.submit. EOF only detaches this authenticated viewer.
"""
import asyncio,json,logging,re,sys
from pathlib import Path
logging.disable(logging.CRITICAL)
async def main():
 config.require_runtime()
 from hermes_constants import set_hermes_home_override,reset_hermes_home_override
 from hermes_cli.gateway_runtime import discover_gateway_endpoint,attachable
 from hermes_cli.gateway_client import connect_gateway
 home=Path(sys.argv[1]).resolve();sid=sys.argv[2]
 if str(home) not in config.RUNTIME_HOMES.values():raise PermissionError('Unconfigured native profile home')
 if not re.fullmatch(r'[A-Za-z0-9_-]{1,160}',sid):raise ValueError('Exact session required')
 token=set_hermes_home_override(home)
 try:
  discovery=discover_gateway_endpoint(home);ready=attachable(discovery)and discovery.endpoint is not None and discovery.endpoint.profile_id==str(home)
  if '--probe'in sys.argv:print(json.dumps({'ready':bool(ready)}),flush=True);return
  if not ready:raise RuntimeError('Existing endpoint unavailable')
  async with connect_gateway(endpoint=discovery.endpoint)as client:
   while True:
    line=await asyncio.to_thread(sys.stdin.buffer.readline,262145)
    if not line:return
    if len(line)>262144:raise ValueError('Bounded frame required')
    try:
     frame=json.loads(line);method=frame['method'];params=frame['params']
     if method not in ('session.resume','prompt.submit','prompt.receipt')or params.get('session_id')!=sid:raise ValueError('Fixed pinned task RPC required')
     if method=='session.resume'and params.get('omit_messages')is not True:raise ValueError('History is not requested')
     if method=='prompt.submit'and(not isinstance(params.get('text'),str)or len(params['text'].encode())>16384):raise ValueError('Bounded approved prompt required')
     result=await client.rpc(method,**params,_timeout=8)
     if method=='session.resume':
      # Do not carry system prompts, private inventory, history or queued text
      # across the IPC boundary. Only exact binding/recovery metadata is used.
      result={k:result.get(k)for k in ('session_id','stored_session_id')}
     else:
      original=result;result={k:original.get(k)for k in ('admission_id','ref','sequence','status','outcome','authority_epoch','execution_generation')}
      if method=='prompt.receipt'and params.get('include_result'):
       value=original.get('result')
       if isinstance(value,dict)and isinstance(value.get('final_response'),str):result['result']={'final_response':value['final_response']}
     raw=json.dumps(result)
     if len(raw.encode())>262144:raise ValueError('Bounded result required')
     print(raw,flush=True)
    except Exception as error:print(json.dumps({'_transport_error':type(error).__name__}),flush=True)
 finally:reset_hermes_home_override(token)
if __name__=='__main__':
 try:asyncio.run(main())
 except Exception as error:print(json.dumps({'_transport_error':type(error).__name__}),flush=True);raise SystemExit(1)
