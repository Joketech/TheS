import config
"""Authorised default selections through injected/native supported mechanisms."""
import threading

class DefaultBridge:
    def __init__(self,agent,store,catalog,policy,read,select,persist,backup,restore):
        self.agent,self.store,self.catalog,self.policy=agent,store,catalog,policy
        self.read,self.select,self.persist,self.backup,self.restore=read,select,persist,backup,restore
        self.lock=threading.Lock()

    def run(self,request_id):
        with self.lock:
            request=self.store.claim(request_id,self.agent)
            previous=self.read();saved=None;selected=None
            try:
                if previous!=request['expected']:raise ValueError('configuration_changed')
                row=self.catalog.validate(request['key'],self.agent)
                self.policy.require_switch(self.agent,row['provider'])
                selected={'provider':row['provider'],'model':row['model']}
                resolved=self.select(row)
                saved=self.backup()  # A failed/unverified backup must raise.
                self.persist(resolved)
                actual=self.read()
                if actual!=selected:raise RuntimeError('readback_mismatch')
                return self.store.finish(request_id,{'status':'verified',**actual,
                    'previous_model':previous['model'],'previous_provider':previous['provider']})
            except Exception:
                actual=self.read();status='failed'
                if actual!=previous:
                    # Never overwrite a third party's concurrent model change.
                    if saved is not None and actual==selected:
                        try:self.restore(saved);actual=self.read()
                        except Exception:status='unconfirmed'
                    else:status='unconfirmed'
                if actual!=previous:status='unconfirmed'
                return self.store.finish(request_id,{'status':status,**actual,
                    'error':'Selection failed; previous route restored' if actual==previous else 'Selection outcome requires inspection; no automatic retry',
                    'previous_model':previous['model'],'previous_provider':previous['provider']})
