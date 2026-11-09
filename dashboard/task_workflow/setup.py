import config
"""Load project-pinned existing routing; never replace a native route/session."""
from pathlib import Path
import json
from .web import Web
from .native_adapter import Route
from .process_sdk import ProcessSDK
from .workflow import ORIGIN,AGENTS

def build(app,auth):
 config.require_runtime()
 data=json.loads((config.DATA_DIR/'workflow-pins.json').read_text())
 if set(data)!=set(AGENTS):raise ValueError('Five existing approved agent routes are required')
 routes={}
 for agent,entry in data.items():
  home=app.chat.reader.path(agent).parent.resolve();session=app.chat.reader.session(agent)
  if not session or session['source']!='telegram'or session['id']!=entry['session_id']or str(home)!=entry['home']or entry['origin']!=ORIGIN:raise ValueError('Existing canonical route changed; task activation fails closed')
  route=Route(agent,str(home),session['id'],ORIGIN);ProcessSDK().probe(route);routes[agent]=route
 return Web(app.data_dir,auth,routes)
