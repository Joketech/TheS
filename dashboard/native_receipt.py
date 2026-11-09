import config
"""Read an exact native receipt through the supported existing-owner SDK.

This adapter never calls submit/resume/cancel/resolve or starts a gateway.
Native imports occur only in the already-configured native child interpreter.
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from chat_recovery import AGENTS,IDENTIFIER,MAX_RESULT

NATIVE_SCRIPT=r'''
import asyncio,json,logging,sys
logging.disable(logging.CRITICAL)
from hermes_constants import get_hermes_home
from hermes_cli.gateway_runtime import discover_gateway_endpoint,attachable
from hermes_cli.gateway_client import connect_gateway
async def main():
 data=json.load(sys.stdin)
 discovered=discover_gateway_endpoint(get_hermes_home())
 if not attachable(discovered) or discovered.endpoint is None:
  print(json.dumps({'unavailable':True}));return
 async with connect_gateway(endpoint=discovered.endpoint) as client:
  receipt=await client.rpc('prompt.receipt',session_id=data['session_id'],admission_id=data['admission_id'],include_result=True,_timeout=8)
  out={key:receipt.get(key) for key in ('admission_id','status','outcome')}
  result=receipt.get('result')
  if isinstance(result,dict) and isinstance(result.get('final_response'),str):
   out['result']={'final_response':result['final_response']}
  print(json.dumps(out))
try:asyncio.run(main())
except Exception:print(json.dumps({'unavailable':True}))
'''


def read_receipt(service,agent,session_id,admission_id):
    config.require_runtime()
    if agent not in AGENTS or not all(isinstance(v,str) and IDENTIFIER.fullmatch(v) for v in (session_id,admission_id)):
        raise ValueError('Invalid exact native binding')
    env=service.environment()
    # Match the existing CLI's profile home without copying credentials/selectors.
    home=config.profile_home(agent)
    env['HERMES_HOME']=str(home)
    if not config.HERMES_PYTHON:raise RuntimeError('Own-instance native interpreter unavailable')
    result=subprocess.run([config.HERMES_PYTHON,'-B','-c',NATIVE_SCRIPT],
        input=json.dumps({'session_id':session_id,'admission_id':admission_id}),
        capture_output=True,text=True,env=env,cwd=service.ledger.parent,timeout=15,shell=False)
    if result.returncode or len(result.stdout.encode())>MAX_RESULT+4096:raise RuntimeError('Native receipt unavailable')
    data=json.loads(result.stdout)
    if not isinstance(data,dict) or data.get('unavailable'):raise RuntimeError('Native receipt unavailable')
    return data
