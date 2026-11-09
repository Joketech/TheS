import config
"""Signed local CRM client; no secrets enter tool schemas or return values."""
import contextvars,hashlib,hmac,json,re,time,urllib.request,urllib.error,uuid
from pathlib import Path
from urllib.parse import urlsplit
ROOT=config.DATA_DIR;acceptance_ticket=contextvars.ContextVar('crm_acceptance',default=None)
class CRMClient:
 def __init__(self,role):self.role=role
 def call(self,body):
  endpoint=json.loads((ROOT/'crm_runtime/endpoint.json').read_text());ticket=acceptance_ticket.get();url=endpoint['url'];parsed=urlsplit(url)
  if parsed.scheme!='http' or parsed.hostname!='127.0.0.1' or parsed.path or parsed.query or parsed.username or parsed.fragment:raise PermissionError('CRM endpoint must remain local')
  if not endpoint.get('active'):
   proof=json.loads((ROOT/'crm_runtime/acceptance.json').read_text())
   if not ticket or not hmac.compare_digest(ticket,proof['ticket']) or time.time()>proof['expires_at']:raise PermissionError('CRM tools are staged; production access not activated')
  elif ticket:raise PermissionError('Acceptance tools cannot target production')
  raw=json.dumps(body,allow_nan=False).encode();stamp=str(int(time.time()));nonce=uuid.uuid4().hex;key=(ROOT/'crm_runtime/crm.key').read_bytes()
  signature=hmac.new(key,self.role.encode()+b'\n'+stamp.encode()+b'\n'+nonce.encode()+b'\n'+raw,hashlib.sha256).hexdigest()
  headers={'Content-Type':'application/json','X-CRM-Agent':self.role,'X-CRM-Time':stamp,'X-CRM-Nonce':nonce,'X-CRM-Signature':signature}
  if ticket:headers['X-CRM-Acceptance']=ticket
  request=urllib.request.Request(url+'/api/crm/agent',data=raw,headers=headers)
  try:
   with urllib.request.urlopen(request,timeout=20) as response:return {'ok':True,'result':json.load(response)}
  except urllib.error.HTTPError as error:
   with error:data=json.load(error)
   return {'ok':False,'status':error.code,**data}
