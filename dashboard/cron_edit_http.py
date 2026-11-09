import config
"""Fixed scheduler-edit HTTP facade; no commands or arbitrary fields accepted.
Keep raw scheduler records in the adapter; only editor fields reach the browser.
"""
import hmac,json
from urllib.parse import parse_qs,urlsplit
from origin_policy import trusted_request,allowed_origins,secure_request

class CronEditHTTP:
    def __init__(self,service,csrf):self.service=service;self.csrf=csrf

    @staticmethod
    def project(data):
        allowed=('ok','changed','revision','capabilities','limits','error','outcome')
        out={k:data[k] for k in allowed if k in data}
        if isinstance(data.get('job'),dict):
            j=data['job'];job={k:j[k] for k in ('id','name','prompt','deliver','model','provider','timezone','enabled','state','schedule_display') if k in j and (j[k] is None or type(j[k]) in (str,bool,int,float))}
            if isinstance(j.get('schedule'),dict):job['schedule']={k:j['schedule'][k] for k in ('kind','minutes','expr','run_at','display') if k in j['schedule'] and (j['schedule'][k] is None or type(j['schedule'][k]) in (str,int,float))}
            elif isinstance(j.get('schedule'),str):job['schedule']=j['schedule']
            out['job']=job
        return out

    @staticmethod
    def send(h,status,data):
        raw=json.dumps(data,ensure_ascii=False,allow_nan=False).encode();h.send_response(status);h.send_header('Content-Type','application/json; charset=utf-8');h.send_header('Content-Length',str(len(raw)));h.send_header('Cache-Control','no-store');h.send_header('X-Content-Type-Options','nosniff');h.send_header('X-Frame-Options','DENY');h.send_header('Connection','close');h.end_headers();h.close_connection=True;h.wfile.write(raw)

    @staticmethod
    def unique(pairs):
        out={}
        for k,v in pairs:
            if k in out:raise ValueError()
            out[k]=v
        return out

    def handle(self,h):
        try:
            origins=allowed_origins(h);origin=h.headers.get('Origin')
            if not trusted_request(h) or h.headers.get('Authorization'):
                return self.send(h,403,{'error':'Untrusted request or unrelated capability'})
            p=urlsplit(h.path)
            if p.path!='/api/cron/edit':return self.send(h,404,{'error':'Not found'})
            q=parse_qs(p.query,keep_blank_values=True,strict_parsing=True)
            if set(q)!={'id'} or len(q['id'])!=1:raise ValueError()
            ident=q['id'][0]
            if not ident or len(ident)>128 or ident.startswith('-') or '/' in ident or '\\' in ident or any(c.isspace() or ord(c)<32 or ord(c)==127 for c in ident):raise ValueError()
            if h.command=='GET':status,data=self.service.load(ident)
            elif h.command=='POST':
                if origin not in origins or h.headers.get_all('X-THE-S-CSRF',[])!=[self.csrf]:return self.send(h,403,{'error':'Same-origin edit capability required'})
                lengths=h.headers.get_all('Content-Length',[])
                if h.headers.get('Transfer-Encoding') or len(lengths)!=1 or not lengths[0].isdigit() or h.headers.get_content_type()!='application/json':raise ValueError()
                size=int(lengths[0])
                if not 0<size<=131072:raise ValueError()
                raw=h.rfile.read(size)
                if len(raw)!=size:raise ValueError()
                data=json.loads(raw,object_pairs_hook=self.unique)
                if not isinstance(data,dict) or set(data)!={'revision','changes'} or not isinstance(data['changes'],dict):raise ValueError()
                status,data=self.service.save(ident,data)
            else:return self.send(h,405,{'error':'Method not allowed'})
            result=self.project(data)
            if status==200:result['csrf']=self.csrf
            return self.send(h,status,result)
        except (ValueError,TypeError,KeyError,UnicodeError):return self.send(h,400,{'error':'Invalid scheduler edit request'})
        except (BrokenPipeError,ConnectionResetError,TimeoutError):return
        except Exception:return self.send(h,503,{'error':'Edit service unavailable; no automatic retry was made'})
