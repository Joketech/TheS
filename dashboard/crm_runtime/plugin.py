import config
"""Profile-scoped native CRM tool. No message injection, model change or tool override."""
import json,time,hmac
from pathlib import Path
from .client import CRMClient,acceptance_ticket,ROOT
HOMES={a.upper():Path(h) for a,h in config.RUNTIME_HOMES.items()}
READ={'state','read'}
OPS={a:READ|{'note.create','action.create','action.update'} for a in ('MIRA','SAGE','SURGE','SELINE','SPECTRA')}
for a in ['MIRA','SAGE','SURGE']:OPS[a]|={'company.create','company.update','contact.create','contact.update','opportunity.create','opportunity.update'}
OPS['SELINE']|={'draft.create','draft.update'};OPS['SPECTRA']|={'opportunity.update'}
def register(ctx,*,client_factory=CRMClient):
 home=Path(ctx._manager.home_path).resolve();actor=next((a for a,p in HOMES.items() if p==home),None)
 if actor is None:raise PermissionError('CRM profile is not authorised')
 client=client_factory(actor)
 description='Shared authoritative Mission Control CRM records. MIRA coordinates all work. Research uncertainty and source evidence must be recorded. Writes require a stable request_id; updates require expected_version. No outreach, commitments, money movement, payment confirmation or owner approvals are available through this tool.'
 def handler(args,**kwargs):
  if not isinstance(args,dict) or set(args)!={'operation','data','request_id'} or args['operation'] not in OPS[actor] or not isinstance(args['data'],dict):raise ValueError('Unsupported CRM fields or role operation')
  return json.dumps(client.call(args),ensure_ascii=False)
 schema={'name':'the_s_crm','description':description,'parameters':{'type':'object','properties':{'operation':{'type':'string','enum':sorted(OPS[actor])},'data':{'type':'object','description':'Validated CRM fields. Use state/read to discover IDs and versions. Company: name/domain/sector/evidence/uncertainty. Contact: company_id/name/email/role/evidence. Opportunity: company_id/title/evidence/uncertainty; commercial fields only MIRA/SURGE; technical_scope/milestones only SPECTRA. Actions: opportunity_id/text/agent/due_at/done. Notes: opportunity_id/body/evidence/kind. Drafts: opportunity_id/kind/content; never Sent.'},'request_id':{'type':'string','description':'Unique stable retry key, 8–100 letters/digits/hyphens/underscores. Reuse only for the exact same operation and data.'}},'required':['operation','data','request_id'],'additionalProperties':False}}
 ctx.register_tool(name='the_s_crm',toolset='the_s_crm',schema=schema,handler=handler,description=description)
 def command(raw_args,*,source=None,**context):
  platform=getattr(source,'platform',None)
  if getattr(platform,'value',platform)!='local':return json.dumps({'ok':False,'error':'Authenticated LOCAL CRM bridge only'})
  if raw_args.strip()=='status':return json.dumps({'agent':actor,'tool':'the_s_crm','registered':True,'outreach_enabled':False})
  token=None
  try:
   body=json.loads(raw_args)
   if set(body)-{'operation','data','request_id','acceptance_ticket'}:raise ValueError()
   ticket=body.pop('acceptance_ticket',None)
   if ticket:
    proof=json.loads((ROOT/'crm_runtime/acceptance.json').read_text())
    if time.time()>proof['expires_at'] or not hmac.compare_digest(ticket,proof['ticket']):raise PermissionError()
    token=acceptance_ticket.set(ticket)
   return ctx.dispatch_tool('the_s_crm',body)
  except Exception:return json.dumps({'ok':False,'error':'CRM request not accepted; inspect records before retrying'})
  finally:
   if token is not None:acceptance_ticket.reset(token)
 ctx.register_command('the-s-crm',command,description='Guarded LOCAL bridge to the profile-scoped CRM tool',args_hint='<CRM operation JSON>')
