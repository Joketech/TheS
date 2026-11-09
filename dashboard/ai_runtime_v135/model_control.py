import config
"""Catalog-qualified model drafts and project preferences. No live fleet mutations."""
import json
from decimal import Decimal
from pathlib import Path
from .spend_control import AGENTS,identifier,money
PRESETS={'economy_chat':'Economy chat','research_writing':'Research and writing','build_coding':'Build and coding','complex_planning':'Complex planning and coordination'}
class Catalog:
 def __init__(self,root):
  self.rows={};self.allowed={};self.observed_at=None
  self.unconfigured=[{'provider':'unconfigured','reason':'External runtime and account catalog unavailable; configure your own instance'}]
  if not config.EXTERNAL_RUNTIME_ENABLED:return
  root=Path(root);probes=[json.loads(line) for line in (root/'provider-probes.jsonl').read_text().splitlines()]
  self.observed_at=probes[0]['observed_at'];self.allowed={}
  for probe in probes:
   profile=probe['profile'];self.allowed[profile]=set()
   for m in probe.get('codex_catalog',{}).get('models',[]):
    key='openai-codex|'+m['id'];self.allowed[profile].add(key)
    self.rows[key]={'key':key,'provider':'openai-codex','model':identifier(m['id']),'auth_method':'subscription sign-in','selectable':True,'availability':'Account catalog verified; inference/quota not tested','limitations':'Codex route only; not evidence of separate API entitlement','price_label':'Included plan usage — monetary cost unavailable','pricing_source':None,'pricing_effective_date':None,'context_length':m.get('context_window')}
  for m in json.loads((root/'nous-model-metadata.json').read_text()):
   id=identifier(m['id']);key='nous|'+id;tool_support='tools' in (m.get('supported_parameters') or []);text_output='text' in (m.get('architecture',{}).get('output_modalities') or []);eligible=tool_support and text_output and ':batch' not in id and not id.startswith('~')
   self.rows[key]={'key':key,'provider':'nous','model':id,'auth_method':'subscription sign-in; model billing mode unverified','selectable':eligible,'availability':'Catalog lists tools/text; quota not tested' if eligible else 'Unavailable for agent control','limitations':'Remaining entitlement and speed-tier limits unverified' if eligible else 'Batch, dynamic alias, embedding/media or tool compatibility not verified','price_label':'Price unavailable: effective date/plan attribution unverified','pricing_source':'https://inference-api.nousresearch.com/v1/models','pricing_effective_date':None,'catalog_rates_usd_per_token':m.get('pricing'),'context_length':m.get('context_length')}
   if eligible:
    for values in self.allowed.values():values.add(key)
  self.unconfigured=[{'provider':'openai-api','reason':'Direct OpenAI API key not detected; ChatGPT sign-in is a different billing route'},{'provider':'anthropic','reason':'Direct Anthropic API key not detected; Claude via Nous is a separate route'},{'provider':'local','reason':'No local endpoint/model availability verified'}]
 def lookup(self,key):
  if not isinstance(key,str) or key not in self.rows:raise ValueError('Model is not in the verified catalog')
  return dict(self.rows[key])
 def validate(self,key,agent=None):
  row=self.lookup(key)
  if not row['selectable'] or (agent is not None and (agent not in AGENTS or key not in self.allowed.get(AGENTS[agent],set()))):raise ValueError('Model unavailable for selected agent')
  return row
 def payload(self):return {'observed_at':self.observed_at,'models':list(self.rows.values()),'unconfigured_providers':self.unconfigured,'login_enabled':False,'login_note':'Live login is disabled in staging. Use native provider authentication only after separate approval; do not submit credentials to this preview.'}

class Controls:
 def __init__(self,ledger,catalog,*,budget_authorised=False):self.ledger=ledger;self.catalog=catalog;self.budget_authorised=budget_authorised
 def read(self):
  with self.ledger.connect() as c:row=c.execute('SELECT payload FROM preferences WHERE id=1').fetchone()
  result=json.loads(row['payload']) if row else {'presets':{key:None for key in PRESETS},'budget_usd':None,'threshold_percent':80 if self.budget_authorised else None,'automatic_routing':False,'costlier_fallback':False,'draft_models':{}}
  if not self.budget_authorised:result.update(budget_usd=None,threshold_percent=None)
  return result
 def save(self,patch):
  if not isinstance(patch,dict) or set(patch)-{'presets','budget_usd','threshold_percent','draft_models','automatic_routing','costlier_fallback'}:raise ValueError('Unknown preference')
  if not self.budget_authorised and any(patch.get(field) is not None for field in ['budget_usd','threshold_percent']):raise ValueError('Budget amount, currency and threshold require owner confirmation')
  updated=self.read()
  for flag in ['automatic_routing','costlier_fallback']:
   if flag in patch and patch[flag] is not False:raise ValueError('Routing and costlier fallback activation are disabled in staging')
  if 'budget_usd' in patch:updated['budget_usd']=money(patch['budget_usd'])
  if 'threshold_percent' in patch:
   value=patch['threshold_percent']
   if value is not None and (type(value) is not int or not 1<=value<=100):raise ValueError('Alert threshold must be 1–100 percent')
   updated['threshold_percent']=value
  for field,allow in [('presets',PRESETS),('draft_models',AGENTS)]:
   if field not in patch:continue
   if not isinstance(patch[field],dict) or set(patch[field])-set(allow):raise ValueError('Unknown preset or agent')
   for name,key in patch[field].items():
    if key is not None:self.catalog.validate(key,name if field=='draft_models' else None)
    updated[field][name]=key
  with self.ledger.connect() as c:c.execute('INSERT INTO preferences VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',(json.dumps(updated),))
  return updated
 def apply_live(self,agent,key):
  self.catalog.validate(key,agent)
  raise PermissionError('Live model/login changes disabled in staging; a saved draft is not an applied or queued model')
 def budget(self,summary):
  prefs=self.read();budget=prefs['budget_usd']
  known=sum((Decimal(summary.get(key) or '0') for key in ['confirmed_usd','estimate_usd']),Decimal(0))
  threshold=Decimal(budget)*Decimal(prefs['threshold_percent'])/100 if budget and prefs['threshold_percent'] is not None else None
  observed=any(summary.get(key) is not None for key in ['confirmed_usd','estimate_usd'])
  return {'enabled':budget is not None,'threshold_reached_on_observed_cost':observed and threshold is not None and known>=threshold,'status':'Incomplete coverage — actual spending may already exceed observed amounts','hard_cap':False,'kind':'alert only','budget_usd':budget,'threshold_percent':prefs['threshold_percent']}
