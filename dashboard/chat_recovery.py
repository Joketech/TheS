import config
"""Project-owned exact-admission Chat recovery; stdlib only.

GET recovery never submits, resolves, cancels or changes a native task. Missing
correlation/receipts remain unknown. No newest-message or timestamp guessing.
"""
import hashlib
import json
import re
import sqlite3
import threading
import time
from pathlib import Path
from contextlib import contextmanager
from urllib.parse import parse_qs

AGENTS = frozenset(('mira','sage','seline','surge','spectra'))
IDENTIFIER = re.compile(r'[A-Za-z0-9_.:-]{1,160}\Z')
REQUEST = re.compile(r'[A-Za-z0-9_-]{8,100}\Z')
MAX_RESULT = 2 * 1024 * 1024

class RecoveryStore:
    def __init__(self,path):
        self.path=Path(path).absolute()
        self.lock=threading.RLock()
        # Additive project-owned table; no edits to legacy rows or native stores.
        with self.connection() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS chat_recovery_v1(request_id TEXT PRIMARY KEY,agent TEXT NOT NULL,session_id TEXT NOT NULL,input_id TEXT NOT NULL,admission_id TEXT,created_at REAL NOT NULL,updated_at REAL NOT NULL)')

    @contextmanager
    def connection(self,read_only=False):
        conn=sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True,timeout=5) if read_only else sqlite3.connect(self.path,timeout=5)
        conn.row_factory=sqlite3.Row
        try:
            if read_only:conn.execute('PRAGMA query_only=1')
            with conn:yield conn
        finally:conn.close()

    @staticmethod
    def input_id(request_id):return hashlib.sha256(request_id.encode()).hexdigest()[:32]

    @staticmethod
    def validate(agent,request_id):
        if agent not in AGENTS or not isinstance(request_id,str) or not REQUEST.fullmatch(request_id):raise ValueError('Invalid agent/request')

    def reserve(self,request_id,agent,session_id):
        self.validate(agent,request_id)
        if not isinstance(session_id,str) or not IDENTIFIER.fullmatch(session_id):raise ValueError('Invalid approved session')
        now=time.time()
        with self.lock,self.connection() as conn:
            conn.execute('INSERT INTO chat_recovery_v1 VALUES(?,?,?,?,NULL,?,?)',(request_id,agent,session_id,self.input_id(request_id),now,now))

    def bind(self,request_id,agent,session_id,input_id,admission_id):
        self.validate(agent,request_id)
        if not isinstance(admission_id,str) or not IDENTIFIER.fullmatch(admission_id):raise ValueError('Invalid native admission')
        with self.lock,self.connection() as conn:
            row=conn.execute('SELECT * FROM chat_recovery_v1 WHERE request_id=? AND agent=?',(request_id,agent)).fetchone()
            if not row or row['session_id']!=session_id or row['input_id']!=input_id:raise ValueError('Native admission ownership mismatch')
            if row['admission_id'] not in (None,admission_id):raise ValueError('Native admission changed')
            conn.execute('UPDATE chat_recovery_v1 SET admission_id=?,updated_at=? WHERE request_id=?',(admission_id,time.time(),request_id))

    def status(self,agent,request_id,reader):
        self.validate(agent,request_id)
        with self.connection(True) as conn:
            row=conn.execute('SELECT * FROM chat_recovery_v1 WHERE request_id=? AND agent=?',(request_id,agent)).fetchone()
            tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            legacy=conn.execute('SELECT status FROM chat_requests WHERE request_id=? AND agent=?',(request_id,agent)).fetchone() if 'chat_requests' in tables else None
        if not row and not legacy:raise KeyError('Request not found')
        out={'request_id':request_id,'agent':agent,'status':'unknown','result':None,'source':'unverified','can_resend':False}
        if not row or not row['admission_id']:
            out['reason']='Exact native admission is unavailable; view history without resending.'
            return out
        try:receipt=reader(row['session_id'],row['admission_id'])
        except Exception:
            out['reason']='Native receipt unavailable; completion is not confirmed.'
            return out
        if not isinstance(receipt,dict) or receipt.get('admission_id')!=row['admission_id']:
            out['reason']='Native receipt identity mismatch.'
            return out
        status=receipt.get('status');out['source']='exact_native_receipt'
        if status=='queued':out.update(status='waiting',phase='native_queue')
        elif status in ('started','running'):out.update(status='accepted',phase='native_execution')
        elif status=='terminal':
            outcome=receipt.get('outcome')
            result=receipt.get('result')
            if outcome=='completed' and isinstance(result,dict) and isinstance(result.get('final_response'),str) and len(result['final_response'].encode())<=MAX_RESULT:
                out.update(status='completed',result=result['final_response'])
            elif outcome in ('failed','cancelled','interrupted'):
                out.update(status='failed',phase='cancelled' if outcome!='failed' else 'native_failure')
            else:out['reason']='Terminal outcome/result is incomplete; inspect history.'
        else:out['reason']='Native outcome is unknown; silence is not failure.'
        return out


def handle_status(handler,parts):
    """Narrow GET facade. Host/origin policy remains the existing server's guard."""
    try:
        if not handler.trusted():return handler.json_response(403,{'error':'Untrusted host or origin'})
        if handler.command!='GET':return handler.json_response(405,{'error':'Read-only endpoint'})
        values=parse_qs(parts.query,keep_blank_values=True,strict_parsing=True)
        if set(values)!={'agent','request_id'} or any(len(v)!=1 for v in values.values()):raise ValueError()
        result=handler.server.app.chat.recovery_status(values['agent'][0],values['request_id'][0])
        return handler.json_response(200,result)
    except (ValueError,TypeError):return handler.json_response(400,{'error':'Invalid recovery lookup'})
    except KeyError:return handler.json_response(404,{'error':'Request not found'})
    except Exception:return handler.json_response(503,{'error':'Recovery metadata unavailable; do not resend'})


class RecoveryMixin:
    @property
    def recovery(self):
        # Existing constructor calls the additive setup before accepting requests.
        return self._chat_recovery

    def setup_recovery(self):self._chat_recovery=RecoveryStore(self.ledger)

    def recovery_record(self,job):
        self.recovery.reserve(job['request_id'],job['agent'],job['session_id'])

    def recovery_admission(self,job,record):
        self.recovery.bind(job['request_id'],job['agent'],record.get('session_id'),record.get('input_id'),record.get('admission_id'))

    def recovery_status(self,agent,request_id):
        from native_receipt import read_receipt
        return self.recovery.status(agent,request_id,lambda session,admission:read_receipt(self,agent,session,admission))
