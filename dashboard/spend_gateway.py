import config
"""Owner-only AI finance access; existing native control implementation stays separate.
Loopback only. Viewer responses are allowlisted; MIRA capability is report-read-only.
Real owner provisioning is an administrative operation, never first-visitor signup.
"""
import hashlib,hmac,json,re,threading,time
from urllib.parse import urlsplit
from origin_policy import trusted_request,allowed_origins,secure_request
from spend_auth import RateLimited
from spend_views import public_ai_state,public_ai_result,private_spend_report

OWNER_COOKIE='the_s_spend_owner'
SETUP_COOKIE='the_s_spend_setup'

# Owner-approved temporary, value-free per-request diagnostic; not an auth gate.
_SIGNIN_DIAGNOSTIC_UNTIL=1794015229.4342701

class SpendGateway:
    def __init__(self,native,auth,*,allow_native_mutations=False,setup_digest=None,setup_expires=0,task_signin=None):
        self.native=native;self.auth=auth;self.allow_native_mutations=allow_native_mutations
        self.setup_digest=setup_digest;self.setup_expires=setup_expires;self.setup_used=False;self.setup_lock=threading.Lock()
        self.task_signin=task_signin  # Explicit owner-approved, separate task purpose only.

    @staticmethod
    def cookie(h,name):
        values=[]
        for piece in h.headers.get('Cookie','').split(';'):
            key,sep,value=piece.strip().partition('=')
            if sep and key==name:values.append(value)
        return values[0] if len(values)==1 and re.fullmatch(r'[A-Za-z0-9_-]{12,256}',values[0]) else ''

    def owner(self,h):return self.auth.resolve(self.cookie(h,OWNER_COOKIE))
    def enrollment(self,h):
        if not self.setup_digest or self.setup_used or self.auth.configured or time.time()>=self.setup_expires:return False
        value=self.cookie(h,SETUP_COOKIE)
        return bool(value) and hmac.compare_digest(hashlib.sha256(value.encode()).hexdigest(),self.setup_digest)

    def access(self,h):
        session=self.owner(h)
        return {'role':'owner' if session else 'viewer','configured':self.auth.configured,
                'enrollment':self.enrollment(h),'expires_at':session.get('expires_at') if session else None,
                'expires_in_seconds':self.auth.remaining(self.cookie(h,OWNER_COOKIE)) if session else None}

    @staticmethod
    def send(h,status,value,*,cookie=None,extra_cookie=None):
        raw=json.dumps(value,ensure_ascii=False,allow_nan=False).encode()
        h.send_response(status);h.send_header('Content-Type','application/json; charset=utf-8')
        h.send_header('Content-Length',str(len(raw)));h.send_header('Cache-Control','no-store')
        h.send_header('Vary','Cookie, Authorization');h.send_header('X-Content-Type-Options','nosniff')
        h.send_header('X-Frame-Options','DENY')
        if cookie:h.send_header('Set-Cookie',cookie)
        if extra_cookie:h.send_header('Set-Cookie',extra_cookie)
        h.end_headers();h.wfile.write(raw)

    @staticmethod
    def set_cookie(name,value,age,*,secure=False):
        # Secure only for the exact approved HTTPS front door; local HTTP retained.
        return name+'='+value+'; Path=/api/ai; Max-Age='+str(age)+'; HttpOnly; SameSite=Strict'+('; Secure' if secure else '')

    def signin_diagnostic(self,h,recognized):
        remaining=_SIGNIN_DIAGNOSTIC_UNTIL-time.time()
        if remaining<=0:return None
        count=sum(piece.strip().partition('=')[0]==OWNER_COOKIE for piece in h.headers.get('Cookie','').split(';'))
        state='missing' if count==0 else 'duplicate' if count>1 else 'recognized' if recognized else 'unrecognized'
        # Never return values, header text, credentials, session IDs or counts.
        return {'cookie_state':state,'valid_for_seconds':min(86400,remaining)}

    def state(self,h,query):
        raw=self.native.state(query);public=public_ai_state(raw)
        public['csrf']=self.native.csrf;public['live_mutations_enabled']=bool(raw.get('live_mutations_enabled')) and self.allow_native_mutations
        public['access']={'role':'viewer','configured':self.auth.configured,'enrollment':self.enrollment(h),'expires_at':None}
        session=self.owner(h);diagnostic=self.signin_diagnostic(h,bool(session))
        if diagnostic is not None:public['signin_diagnostic']=diagnostic
        if not session:return public
        result=dict(raw);result['csrf']=self.native.csrf;result['live_mutations_enabled']=public['live_mutations_enabled']
        result['access']=self.access(h);result['public_metadata']=public
        if diagnostic is not None:result['signin_diagnostic']=diagnostic
        return result

    @staticmethod
    def password_body(h):
        lengths=h.headers.get_all('Content-Length',[])
        if h.headers.get_all('Transfer-Encoding') or len(lengths)!=1 or not lengths[0].isdigit():raise ValueError()
        size=int(lengths[0])
        if not 0<size<=2048 or h.headers.get_content_type()!='application/json':raise ValueError()
        raw=h.rfile.read(size)
        if len(raw)!=size:raise ValueError()
        def unique(pairs):
            out={}
            for k,v in pairs:
                if k in out:raise ValueError()
                out[k]=v
            return out
        body=json.loads(raw,object_pairs_hook=unique)
        if not isinstance(body,dict) or set(body)!={'password'} or not isinstance(body['password'],str) or len(body['password'].encode())>256:raise ValueError()
        return body['password']

    def handle(self,h):
        try:
            origin=h.headers.get('Origin');same=allowed_origins(h)
            if not trusted_request(h):return self.send(h,403,{'error':'Untrusted origin or host'})
            parsed=urlsplit(h.path);path=parsed.path
            authorization=h.headers.get('Authorization','')
            if h.command=='GET':
                if path=='/api/ai/spend/report':
                    key=authorization[7:] if authorization.startswith('Bearer ') else ''
                    if not self.owner(h) and not self.auth.mira_authorized(key):return self.send(h,403,{'error':'Protected financial report'})
                    if parsed.query and any(k in parsed.query for k in ['role=','token=','key=']):raise ValueError()
                    result=private_spend_report(self.native.state(parsed.query));result['access']='owner' if self.owner(h) else 'mira_read_only'
                    return self.send(h,200,result)
                if authorization:return self.send(h,403,{'error':'Report capability is valid only on the read-only report route'})
                if path=='/api/ai/access/status':
                    if parsed.query:raise ValueError()
                    return self.send(h,200,{'access':self.access(h),'csrf':self.native.csrf})
                if path=='/api/ai/state':return self.send(h,200,self.state(h,parsed.query))
                return self.send(h,404,{'error':'Not found'})
            if h.command!='POST':return self.send(h,405,{'error':'Method not allowed'})
            if authorization:return self.send(h,403,{'error':'Report access is read-only'})
            if origin not in same or not hmac.compare_digest(h.headers.get('X-THE-S-CSRF',''),self.native.csrf):
                return self.send(h,403,{'error':'Same-origin control capability required'})
            if parsed.query:raise ValueError()
            if path=='/api/ai/access/lock':
                self.auth.revoke(self.cookie(h,OWNER_COOKIE))
                return self.send(h,200,{'access':{'role':'viewer','configured':self.auth.configured}},cookie=self.set_cookie(OWNER_COOKIE,'',0,secure=secure_request(h)))
            if path in ('/api/ai/access/unlock','/api/ai/access/setup'):
                password=self.password_body(h)
                if path.endswith('/setup'):
                    with self.setup_lock:
                        if not self.enrollment(h):return self.send(h,403,{'error':'Administrative owner setup required'})
                        self.auth.configure_owner(password);self.setup_used=True
                        # Owner-approved separate report credential: never return it to a browser.
                        self.auth.provision_mira_key()
                session=self.auth.authenticate(password,client=h.client_address[0])
                if not session:return self.send(h,401,{'error':'Unable to unlock'})
                # Credential/session tokens appear only in an HttpOnly cookie, never JSON.
                task_cookie=self.task_signin(session,secure=secure_request(h)) if self.task_signin else None
                return self.send(h,200,{'access':{'role':'owner','configured':True,'expires_at':session['expires_at']}},cookie=self.set_cookie(OWNER_COOKIE,session['token'],300,secure=secure_request(h)),extra_cookie=task_cookie)
            if path.startswith('/api/ai/access/'):return self.send(h,404,{'error':'Not found'})
            if not self.allow_native_mutations:return self.send(h,403,{'error':'Native changes are disabled in this preview'})
            result=self.native.post(path,self.native.json_body(h))
            return self.send(h,200,result if self.owner(h) else public_ai_result(path,result))
        except RateLimited:return self.send(h,429,{'error':'Too many unlock attempts; try later'})
        except (ValueError,TypeError,KeyError,UnicodeError):return self.send(h,400,{'error':'Invalid request'})
        except (PermissionError,LookupError):return self.send(h,403,{'error':'Action unavailable'})
        except Exception:return self.send(h,503,{'error':'Service unavailable; no automatic retry was made'})
