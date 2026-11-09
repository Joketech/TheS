import config
"""Private, bounded operator requests consumed by the existing native gateway.

No shell strings, passwords, prompts or arbitrary session/config paths are stored.
A command ID is not sufficient: native authenticated LOCAL control and matching
profile are required by the plugin entry point before a request can be claimed.
"""
import json
import os
import re
import time
from pathlib import Path
from .spend_control import Ledger, AGENTS

ROOT=config.DATA_DIR
DATABASE=ROOT/'ai_spend.db'
CATALOG=config.CATALOG_DIR

class Requests:
    def __init__(self,path=DATABASE):
        self.ledger=Ledger(path)
        with self.ledger.connect() as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS model_requests(
                request_id TEXT PRIMARY KEY, agent TEXT NOT NULL, model_key TEXT NOT NULL,
                expected TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL,
                state TEXT NOT NULL, result TEXT);
                CREATE TABLE IF NOT EXISTS observer_health(
                agent TEXT PRIMARY KEY,last_event REAL,captured INTEGER NOT NULL DEFAULT 0,
                discarded INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS control_sessions(agent TEXT PRIMARY KEY,session_id TEXT NOT NULL);''')
        os.chmod(self.ledger.path,0o600)

    @staticmethod
    def valid_id(value):
        if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,100}',value):
            raise ValueError('Invalid control request ID')

    def issue(self,request_id,agent,key,expected,ttl=120):
        self.valid_id(request_id)
        if agent not in AGENTS or not isinstance(key,str) or len(key)>360:
            raise ValueError('Invalid control target')
        if set(expected)!={'provider','model'} or not all(isinstance(v,str) and len(v)<181 for v in expected.values()):
            raise ValueError('Effective configuration evidence required')
        now=time.time()
        with self.ledger.connect() as c:
            previous=c.execute('SELECT * FROM model_requests WHERE request_id=?',(request_id,)).fetchone()
            if previous:
                if (previous['agent'],previous['model_key'],json.loads(previous['expected'])) != (agent,key,expected):
                    raise ValueError('Conflicting control request ID')
                return self.read(request_id)
            c.execute('INSERT INTO model_requests VALUES(?,?,?,?,?,?,?,NULL)',
                      (request_id,agent,key,json.dumps(expected,sort_keys=True),now,now+ttl,'pending'))
        return self.read(request_id)

    def claim(self,request_id,agent):
        self.valid_id(request_id)
        with self.ledger.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT * FROM model_requests WHERE request_id=?',(request_id,)).fetchone()
            if not row or row['agent']!=agent or row['state']!='pending' or row['expires']<time.time():
                raise PermissionError('No authorised pending selection for this profile')
            c.execute("UPDATE model_requests SET state='running' WHERE request_id=?",(request_id,))
            return {'request_id':request_id,'agent':agent,'key':row['model_key'],'expected':json.loads(row['expected'])}

    def finish(self,request_id,result):
        # Whitelist result shape too: native errors must not leak diagnostics/secrets.
        safe={k:result.get(k) for k in ('status','model','provider','error','previous_model','previous_provider') if k in result}
        if safe.get('status') not in ('verified','failed','unconfirmed'):
            raise ValueError('Invalid native control outcome')
        with self.ledger.connect() as c:
            c.execute('UPDATE model_requests SET state=?,result=? WHERE request_id=?',
                      (safe['status'],json.dumps(safe),request_id))
        return safe

    def read(self,request_id):
        self.valid_id(request_id)
        with self.ledger.connect() as c:
            row=c.execute('SELECT agent,model_key,state,result FROM model_requests WHERE request_id=?',(request_id,)).fetchone()
        if not row:raise LookupError('Control request unavailable')
        return {'agent':row['agent'],'key':row['model_key'],'state':row['state'],
                'result':json.loads(row['result']) if row['result'] else None}

    def health(self,agent,*,captured=False,discarded=False,stamp=None):
        with self.ledger.connect() as c:
            c.execute('''INSERT INTO observer_health VALUES(?,?,?,?) ON CONFLICT(agent) DO UPDATE SET
                         last_event=COALESCE(excluded.last_event,observer_health.last_event),
                         captured=observer_health.captured+excluded.captured,
                         discarded=observer_health.discarded+excluded.discarded''',
                      (agent,stamp,int(captured),int(discarded)))
