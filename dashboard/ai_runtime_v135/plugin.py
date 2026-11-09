import config
"""Existing-gateway plugin: metadata hooks and authenticated LOCAL control only.

No tools, prompt sections, arbitrary shell execution, message injection or new
network listener are registered. Registration/status never selects a model.
"""
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import time
import uuid
from .control_core import Requests,CATALOG,AGENTS
from .collector import UsageCapture
from .default_bridge import DefaultBridge

HOMES={a.upper():Path(h) for a,h in config.RUNTIME_HOMES.items()}

class NativeDriver:
    def __init__(self,agent,home,store):
        self.agent,self.home,self.store=agent,home,store
        self.saved_model=None

    def config(self):
        from hermes_constants import get_hermes_home
        from hermes_cli.config import load_config_readonly
        if Path(get_hermes_home()).resolve()!=self.home:
            raise PermissionError('Native profile scope mismatch')
        return load_config_readonly()

    def read(self):
        model=self.config().get('model') or {}
        provider,value=model.get('provider'),model.get('default')
        if not isinstance(provider,str) or not isinstance(value,str):
            raise ValueError('Effective native configuration unavailable')
        return {'provider':provider,'model':value}

    def select(self,row):
        from hermes_cli.model_switch import switch_model
        from hermes_cli.config import get_compatible_custom_providers
        config=self.config();current=config.get('model') or {}
        result=switch_model(raw_input=row['model'],explicit_provider=row['provider'],
            current_provider=current.get('provider',''),current_model=current.get('default',''),
            current_base_url=current.get('base_url',''),current_api_key=current.get('api_key',''),
            is_global=True,user_providers=config.get('providers'),
            custom_providers=get_compatible_custom_providers(config))
        if not result.success or result.new_model!=row['model'] or result.target_provider!=row['provider']:
            raise ValueError('Native selection was not the exact approved route')
        return result

    def backup(self):
        from hermes_cli.config import read_user_config_raw
        path=self.home/'config.yaml'
        original=path.read_bytes()
        dest=self.home/'backups'/'the-s-ai-control'/(time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex)
        dest.mkdir(parents=True,mode=0o700)
        saved=dest/'config.yaml'
        with saved.open('xb') as stream:stream.write(original)
        os.chmod(saved,0o600)
        if hashlib.sha256(saved.read_bytes()).digest()!=hashlib.sha256(original).digest():
            raise RuntimeError('Native configuration backup could not be verified')
        # Only the old model section is restored, preserving unrelated concurrent edits.
        return copy.deepcopy(read_user_config_raw(path).get('model'))

    def persist(self,result):
        from hermes_cli.model_switch import persist_model_selection
        persist_model_selection(result,self.home/'config.yaml')

    def restore(self,old_model):
        from hermes_cli.config import _CONFIG_LOCK,read_user_config_raw,atomic_config_replace
        path=self.home/'config.yaml'
        with _CONFIG_LOCK:
            config=read_user_config_raw(path)
            config['model']=old_model
            atomic_config_replace(path,config)

    def bridge(self):
        from .account_policy import AccountPolicy
        from .model_control import Catalog
        policy=AccountPolicy(json.loads((CATALOG/'existing-account-verification.json').read_text()))
        return DefaultBridge(self.agent,self.store,Catalog(CATALOG),policy,
                             self.read,self.select,self.persist,self.backup,self.restore)


def register(ctx,*,store=None,driver_factory=NativeDriver):
    home=Path(ctx._manager.home_path).resolve()
    agent=next((key for key,path in HOMES.items() if path==home),None)
    if agent is None:raise PermissionError('Plugin home is not an approved fleet profile')
    store=store or Requests()
    capture=UsageCapture(agent,store)
    for name,handler in [('post_api_request',capture.main),('post_auxiliary_call',capture.auxiliary),('api_request_error',capture.error)]:
        ctx.register_hook(name,handler)
    driver=driver_factory(agent,home,store)
    async def command(raw_args,*,source=None,**context):
        platform=getattr(source,'platform',None)
        if getattr(platform,'value',platform)!='local':
            return json.dumps({'status':'failed','error':'Authenticated native LOCAL control required'})
        args=raw_args.strip()
        if args=='status':
            configured=await asyncio.to_thread(driver.read)
            return json.dumps({'plugin':'the-s-ai-control','agent':agent,'active':True,
                               'hooks':['post_api_request','post_auxiliary_call','api_request_error'],
                               'configured':configured,'model_selected':False})
        try:
            Requests.valid_id(args)
            result=await asyncio.to_thread(lambda:driver.bridge().run(args))
            return json.dumps(result)
        except Exception:
            return json.dumps({'status':'failed','error':'No authorised pending request for this profile; no arbitrary commands accepted'})
    ctx.register_command('the-s-ai-control',command,description='Private Mission Control metadata/model-control bridge',args_hint='<authorised-request-id>')
