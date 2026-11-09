"""Create only empty instance schema/directories; never contact an integration."""
import os
from pathlib import Path
import config

def initialize():
    for path in (config.DATA_DIR,config.CONTENT_DIR,config.CRM_DIR,config.AGENT_LOG_DIR,config.CATALOG_DIR,config.WORKFLOW_DIR):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    from crm_runtime.store import CRMStore
    from task_workflow.workflow import Store
    from ai_runtime_v135.control_core import Requests
    CRMStore()
    Store(config.WORKFLOW_DIR/'workflow.db')
    Requests()

def initialize_crm_key(root=config.DATA_DIR):
    """Called only by explicit first-owner enrollment; exclusive, no overwrite."""
    directory=Path(root)/'crm_runtime'
    directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    path=directory/'crm.key'
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as stream:
        stream.write(os.urandom(32));stream.flush();os.fsync(stream.fileno())

if __name__=='__main__':
    initialize()
    print('Empty instance schemas initialized; external integrations disconnected.')
