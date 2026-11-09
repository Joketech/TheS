"""Real HTTP tests against two fresh, independent repository copies. No live integrations."""
from pathlib import Path
import copy
import http.client
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.parse import urlencode

REPO = Path(__file__).resolve().parents[1]

class Instance:
    def __init__(self, root, label, color):
        self.code = root / label / 'repository'
        self.data = root / label / 'instance-data'
        shutil.copytree(REPO, self.code, ignore=shutil.ignore_patterns('data','__pycache__','.git'))
        brand = json.loads((self.code/'config/brand.json').read_text())
        brand.update(name=label+' Mission',company=label+' Test Company',attribution=label+' Test Attribution',logo_text=label[:2].upper(),accent=color)
        brand['agents'][0]['label']=label+' Coordinator'
        (self.code/'config/brand.json').write_text(json.dumps(brand))
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));self.port=sock.getsockname()[1]
        assert self.port!=51763
        self.base='http://127.0.0.1:'+str(self.port)
        self.env={k:v for k,v in os.environ.items() if k in ('PATH','SYSTEMROOT','LANG','LC_ALL','TMPDIR','SSL_CERT_FILE')}
        self.env.update(DATA_DIR=str(self.data),PORT=str(self.port),BRAND_CONFIG=str(self.code/'config/brand.json'),EXTERNAL_RUNTIME_ENABLED='0',CHAT_SEND_ENABLED='0',PYTHONDONTWRITEBYTECODE='1',REPORTING_TIMEZONE='UTC')
        result=subprocess.run([sys.executable,'-B','initializer.py'],cwd=self.code,env=self.env,capture_output=True,text=True)
        if result.returncode:raise AssertionError(result.stdout+result.stderr)
        self.process=None;self.start()
    def start(self):
        self.log=open(self.code.parent/'server.log','ab')
        self.process=subprocess.Popen([sys.executable,'-B','server.py'],cwd=self.code,env=self.env,stdout=self.log,stderr=self.log)
        for _ in range(80):
            try:
                if self.request('GET','/api/board')[0]==200:return
            except OSError:pass
            if self.process.poll() is not None:raise AssertionError('Test server exited; inspect isolated server.log')
            time.sleep(.05)
        raise AssertionError('Isolated server startup timed out')
    def stop(self):
        if self.process is not None and self.process.poll() is None:
            self.process.send_signal(signal.SIGINT)
            try:self.process.wait(10)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait();raise
        if getattr(self,'log',None):self.log.close()
    def request(self,method,path,body=None,headers=None):
        headers=dict(headers or {})
        if isinstance(body,(dict,list)):
            body=json.dumps(body).encode();headers.setdefault('Content-Type','application/json')
        if method=='POST':headers.setdefault('Origin',self.base)
        connection=http.client.HTTPConnection('127.0.0.1',self.port,timeout=15)
        try:
            connection.request(method,path,body=body,headers=headers)
            response=connection.getresponse();data=response.read();content_type=response.getheader('Content-Type','')
            return response.status,json.loads(data) if 'application/json' in content_type else data,dict(response.getheaders())
        finally:connection.close()
    def get(self,path):
        status,value,_=self.request('GET',path)
        if status!=200:raise AssertionError((path,status,value))
        return value
    def post(self,path,body,headers=None):return self.request('POST',path,body,headers)
    def enroll(self,password):
        # Test-only own-instance setup; no real owner credential is used or printed.
        self.stop()
        script="from spend_owner_setup import enroll; import sys; password=sys.stdin.read(); enroll(prompt=lambda label:password,interactive=True)"
        result=subprocess.run([sys.executable,'-B','-c',script],cwd=self.code,env=self.env,input=password,capture_output=True,text=True)
        if result.returncode:raise AssertionError(result.stderr)
        self.start()

class TwoBrandIsolation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory(dir=os.environ.get('TEST_ROOT'))
        cls.root=Path(cls.temporary.name)
        cls.a=Instance(cls.root,'Aurora','#55aaff')
        try:cls.b=Instance(cls.root,'Cedar','#66cc99')
        except Exception:cls.a.stop();cls.temporary.cleanup();raise
    @classmethod
    def tearDownClass(cls):
        cls.a.stop();cls.b.stop();cls.temporary.cleanup()

    def test_01_fresh_empty_and_honestly_disconnected(self):
        for instance in (self.a,self.b):
            self.assertEqual(instance.get('/api/board'),[])
            state=instance.get('/api/crm/state')
            for key in ('companies','contacts','opportunities','actions','notes','drafts'):self.assertEqual(state[key],[],key)
            snapshot=instance.get('/api/state')
            self.assertTrue(all(a['state']=='UNAVAILABLE' for a in snapshot['fleet']))
            self.assertEqual(snapshot['models'],[])
            self.assertEqual(snapshot['hermes_cron_status'],'unavailable')
            self.assertFalse(instance.get('/api/ai/access/status')['access']['configured'])
            self.assertFalse((instance.data/'private').exists())
            self.assertEqual(instance.get('/api/content'),[])
            self.assertFalse(instance.get('/api/chat/workflow/status')['task_purpose_enabled'])

    def test_02_brand_config_and_full_frontend_assets(self):
        for instance,label,color in [(self.a,'Aurora','#55aaff'),(self.b,'Cedar','#66cc99')]:
            brand=instance.get('/api/brand')
            self.assertEqual(brand['name'],label+' Mission');self.assertEqual(brand['accent'],color)
            html=instance.get('/')
            self.assertEqual(html,instance.get('/template'))
            self.assertTrue(b"const tabs=['Overview','Agents','Office','Tasks','Schedule','Chat','Content','CRM','Docs']" in html,'All nine dynamically rendered navigation tabs must remain')
            for path in ['/assets/brand.js','/assets/brand.css']:
                self.assertEqual(instance.request('GET',path)[0],200)
            self.assertEqual(instance.get('/config/brand.json'),brand)

    def test_03_board_create_update_delete_isolation(self):
        status,row,_=self.a.post('/api/board',{'title':'FICTIONAL isolation task'})
        self.assertEqual(status,201)
        self.assertEqual(self.b.get('/api/board'),[])
        status,updated,_=self.a.post('/api/board/update?id='+str(row['id']),{'status':'in_progress'})
        self.assertEqual(status,200);self.assertEqual(updated['status'],'in_progress')
        self.assertEqual(self.a.get('/api/board')[0]['id'],row['id'])
        self.assertEqual(self.a.post('/api/board/delete?id='+str(row['id']),{})[0],200)
        self.assertEqual(self.a.get('/api/board'),[])

    def test_04_crm_persistence_versioning_idempotency_and_isolation(self):
        csrf=self.a.get('/api/crm/state')['csrf'];headers={'X-THE-S-CRM-CSRF':csrf}
        body={'operation':'company.create','data':{'name':'FICTIONAL isolation company'},'request_id':'fixture-company-create-0001'}
        status,record,_=self.a.post('/api/crm/command',body,headers)
        self.assertEqual(status,200);self.assertEqual(record['version'],1)
        self.assertEqual(self.a.post('/api/crm/command',body,headers)[1]['id'],record['id'])
        self.assertEqual(len(self.a.get('/api/crm/state')['companies']),1)
        self.assertEqual(self.b.get('/api/crm/state')['companies'],[])
        update={'operation':'company.update','data':{'id':record['id'],'expected_version':1,'sector':'FICTIONAL'},'request_id':'fixture-company-update-0001'}
        self.assertEqual(self.a.post('/api/crm/command',update,headers)[1]['version'],2)
        update['request_id']='fixture-company-conflict-0001'
        self.assertEqual(self.a.post('/api/crm/command',update,headers)[0],409)
        query=urlencode({'entity':'companies','id':record['id']})
        self.assertEqual(self.a.get('/api/crm/record?'+query)['version'],2)
        self.assertEqual(self.b.request('GET','/api/crm/record?'+query)[0],404)

    def test_05_content_write_read_delete_and_isolation(self):
        query='?agent=SAGE&file=2026-01-01_isolation-fixture.md'
        body=b'# FICTIONAL isolated document\n\nNo client data.\n'
        self.assertEqual(self.a.post('/api/content/save'+query,body,{'Content-Type':'text/markdown'})[0],200)
        self.assertEqual(self.a.get('/api/content/read'+query)['text'],body.decode())
        self.assertEqual(self.b.request('GET','/api/content/read'+query)[0],404)
        self.assertEqual(self.a.post('/api/content/delete'+query,None)[0],200)
        self.assertEqual(self.a.request('GET','/api/content/read'+query)[0],404)

    def test_06_host_origin_csrf_and_private_path_security(self):
        self.assertEqual(self.a.request('GET','/api/state',headers={'Host':'attacker.example'})[0],403)
        self.assertEqual(self.a.post('/api/board',{'title':'forbidden'},{'Origin':'https://attacker.example'})[0],403)
        csrf=self.a.get('/api/crm/state')['csrf']
        body={'operation':'company.create','data':{'name':'Forbidden'},'request_id':'fixture-denied-0001'}
        self.assertEqual(self.b.post('/api/crm/command',body,{'X-THE-S-CRM-CSRF':csrf})[0],403)
        self.assertEqual(self.a.post('/api/crm/command',body)[0],403)
        for path in ['/data/crm.db','/private/spend-auth.json','/config.py','/.env','/assets/../config.py']:
            self.assertEqual(self.a.request('GET',path)[0],404,path)
        self.assertEqual(self.a.post('/api/content/save?agent=SAGE&file=../../escape.md',b'# bad',{'Content-Type':'text/markdown'})[0],400)
        self.assertEqual(self.a.post('/api/board',{'title':'forbidden'},{'Sec-Fetch-Site':'cross-site'})[0],403)

    def test_07_no_native_actions_or_owner_self_enrollment(self):
        for instance in (self.a,self.b):
            self.assertEqual(instance.request('GET','/api/ai/spend/report')[0],403)
            access=instance.get('/api/ai/access/status')
            self.assertEqual(instance.post('/api/ai/access/setup',{'password':secrets.token_urlsafe(24)},{'X-THE-S-CSRF':access['csrf']})[0],403)
            status,_,_=instance.post('/api/cron/action',{'id':'not-a-real-job','action':'run'})
            self.assertIn(status,(400,403,503))
            self.assertEqual(instance.post('/api/crm/agent',{'operation':'state','data':{},'request_id':'fixture-no-authority-01'})[0],403)
            self.assertEqual(instance.post('/api/ai/model',{}, {'X-THE-S-CSRF':access['csrf']})[0],403)
        self.assertEqual(self.b.get('/api/board'),[])

    def test_08_independent_owner_auth_cookie_and_lock(self):
        password_a=secrets.token_urlsafe(24);password_b=secrets.token_urlsafe(24)
        self.a.enroll(password_a);self.b.enroll(password_b)
        access_a=self.a.get('/api/ai/access/status');access_b=self.b.get('/api/ai/access/status')
        self.assertTrue(access_a['access']['configured']);self.assertTrue(access_b['access']['configured'])
        status,_,headers=self.a.post('/api/ai/access/unlock',{'password':password_a},{'X-THE-S-CSRF':access_a['csrf']})
        self.assertEqual(status,200)
        # HTTP library merges duplicate headers in a dictionary; pick owner cookie explicitly via raw unlock.
        connection=http.client.HTTPConnection('127.0.0.1',self.a.port,timeout=15)
        connection.request('POST','/api/ai/access/unlock',json.dumps({'password':password_a}),headers={'Origin':self.a.base,'Content-Type':'application/json','X-THE-S-CSRF':access_a['csrf']})
        response=connection.getresponse();response.read();cookies=response.getheaders();connection.close()
        owner=next(v for k,v in cookies if k.lower()=='set-cookie' and v.startswith('the_s_spend_owner='))
        self.assertIn('HttpOnly',owner);self.assertIn('SameSite=Strict',owner)
        cookie=owner.split(';',1)[0]
        self.assertEqual(self.a.request('GET','/api/ai/access/status',headers={'Cookie':cookie})[1]['access']['role'],'owner')
        self.assertEqual(self.b.request('GET','/api/ai/access/status',headers={'Cookie':cookie})[1]['access']['role'],'viewer')
        self.assertEqual(self.b.post('/api/ai/access/unlock',{'password':password_a},{'X-THE-S-CSRF':access_b['csrf']})[0],401)
        self.assertEqual(self.b.request('GET','/api/ai/spend/report',headers={'Cookie':cookie})[0],403)
        self.assertEqual(self.a.post('/api/ai/access/lock',{}, {'X-THE-S-CSRF':access_a['csrf'],'Cookie':cookie})[0],200)
        self.assertEqual(self.a.request('GET','/api/ai/access/status',headers={'Cookie':cookie})[1]['access']['role'],'viewer')
        self.assertNotEqual((self.a.data/'crm_runtime/crm.key').read_bytes(),(self.b.data/'crm_runtime/crm.key').read_bytes())

if __name__=='__main__':unittest.main(verbosity=2)
