import config
"""Finite owned viewer IPC; no interpreter/service/config changes or daemon start."""
from contextlib import asynccontextmanager
from pathlib import Path
import asyncio,json,os,subprocess
from .workflow import WorkflowError
class ProcessSDK:
 fixture=False
 def __init__(self,*,python=None,script=None):
  self.python=python or os.environ.get('HERMES_PYTHON');self.script=Path(script)if script else Path(__file__).with_name('native_rpc.py')
  if not self.python or not Path(self.python).is_file():raise WorkflowError(503,'Existing configured native interpreter unavailable')
 def env(self,route):
  return config.child_environment(route.home)
 def command(self,route):route.validate();return [self.python,'-B',str(self.script),route.home,route.session_id]
 def probe(self,route):
  p=subprocess.run([*self.command(route),'--probe'],capture_output=True,text=True,env=self.env(route),timeout=12)
  if p.returncode or len(p.stdout)>4096:raise WorkflowError(503,'Existing native endpoint probe failed; no replacement started')
  try:ready=json.loads(p.stdout).get('ready')is True
  except ValueError:ready=False
  if not ready:raise WorkflowError(503,'Pinned native profile is unavailable')
  return True
 @asynccontextmanager
 async def open(self,route,subject):
  if subject is not None:raise WorkflowError(403,'Entity witnesses cannot impersonate native login subjects')
  process=await asyncio.create_subprocess_exec(*self.command(route),stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,env=self.env(route),limit=262144)
  class Client:
   async def rpc(_,method,**params):
    if method not in ('session.resume','prompt.submit','prompt.receipt')or params.get('session_id')!=route.session_id:raise WorkflowError(403,'Only pinned supported task RPC is allowed')
    timeout=min(float(params.pop('_timeout',8)),15);raw=(json.dumps({'method':method,'params':params})+'\n').encode()
    if len(raw)>262144:raise WorkflowError(400,'Bounded native RPC required')
    async with asyncio.timeout(timeout+5):
     process.stdin.write(raw);await process.stdin.drain();line=await process.stdout.readline()
    if not line or len(line)>262144:raise WorkflowError(503,'Native RPC viewer disconnected; do not resubmit')
    try:value=json.loads(line)
    except ValueError:raise WorkflowError(503,'Native RPC response unavailable')from None
    if not isinstance(value,dict)or value.get('_transport_error'):raise WorkflowError(503,'Native SDK operation unavailable; reconcile exact receipt')
    return value
  try:yield Client()
  finally:
   if process.stdin:process.stdin.close()
   try:await asyncio.wait_for(process.wait(),3)
   except TimeoutError:
    # This handle is ONLY our newly created client/viewer, never the gateway,
    # agent worker or profile host. Viewer detachment cannot cancel admission.
    process.terminate()
    try:await asyncio.wait_for(process.wait(),2)
    except TimeoutError:process.kill();await process.wait()
