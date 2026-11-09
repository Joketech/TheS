import config
"""One native admission override. No persistent config write or implicit resume."""
class OnceClient:
    def __init__(self,client,choice,emit):
        if not isinstance(choice,dict) or set(choice)!={'model','provider','session_id','request_id'}:
            raise ValueError('Invalid one-task transport binding')
        if not all(isinstance(v,str) and v and len(v)<=180 for v in choice.values()):
            raise ValueError('Invalid one-task metadata')
        self.client,self.choice,self.emit=client,choice,emit
        self.used=False

    def __getattr__(self,name):return getattr(self.client,name)

    async def rpc(self,method,**params):
        if method!='prompt.submit':return await self.client.rpc(method,**params)
        request=self.choice['request_id']
        if self.used or params.get('session_id')!=self.choice['session_id']:
            self.emit({'type':'ai_once_admission','request_id':request,'accepted':False,'definitive':not self.used})
            raise ValueError('One-task target changed or was already submitted; no new task sent')
        override={'model':self.choice['model'],'provider':self.choice['provider']}
        if params.get('model_override') not in (None,override):raise ValueError('Conflicting native one-task choice')
        self.used=True;params['model_override']=override
        self.emit({'type':'ai_once_attempt','request_id':request,'native_input_id':params.get('input_id') or params.get('submission_id')})
        try:
            receipt=await self.client.rpc(method,**params)
        except Exception as exc:
            # A received native refusal is definite. Transport failure is not.
            if type(exc).__name__=='GatewayRPCError':
                self.emit({'type':'ai_once_admission','request_id':request,'accepted':False,'definitive':True})
            raise
        accepted=bool(receipt.get('admission_id'))
        self.emit({'type':'ai_once_admission','request_id':request,'accepted':accepted,'definitive':accepted})
        return receipt
