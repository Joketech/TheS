import config
"""Metadata-only native hook subscriber. Never stores provider bodies or secrets."""
import hashlib
import sqlite3
from urllib.parse import urlsplit
from .spend_control import count


def billing_evidence(provider,base_url):
    # Auth class AND actual endpoint are evidence; provider name alone is not.
    if provider=='openai-codex' and isinstance(base_url,str):
        parsed=urlsplit(base_url)
        if parsed.scheme=='https' and parsed.hostname in ('chatgpt.com','chat.openai.com') and not parsed.username:
            from hermes_cli.auth import get_codex_auth_status
            status=get_codex_auth_status()
            if status.get('logged_in') is True and status.get('auth_mode')=='chatgpt':
                return {'auth_method':'chatgpt_sign_in','billing_mode':'subscription_included'}
    return {'auth_method':'unverified','billing_mode':'unknown'}


class UsageCapture:
    def __init__(self,agent,store,*,billing=billing_evidence):
        self.agent,self.store,self.billing=agent,store,billing

    def capture(self,family,payload):
        try:
            request=payload.get('api_request_id')
            if not isinstance(request,str) or not request:raise ValueError('Missing request identity')
            attempt=payload.get('retry_count')
            if attempt is None:attempt=0
            if type(attempt) is not int or attempt<0:raise ValueError('Invalid attempt metadata')
            route=self.billing(payload.get('provider'),payload.get('base_url'))
            usage=payload.get('usage')
            if not isinstance(usage,dict):usage={}
            def bucket(*names):
                for name in names:
                    if name in usage:return count(usage[name])
                return None
            platform=payload.get('platform')
            source=platform if platform in ('cron','subagent','background','oneshot','telegram','local') else 'local' if platform in ('cli','gui','tui','acp') else family
            event={
                'event_id':hashlib.sha256(f'{family}:{request}:{attempt}'.encode()).hexdigest(),
                'agent':self.agent,'provider':payload.get('provider') or 'Unavailable',
                'model':payload.get('response_model') or payload.get('model') or 'Unavailable',
                'source':source,'auth_method':route['auth_method'],'billing_mode':route['billing_mode'],
                'cost_status':'included' if route['billing_mode']=='subscription_included' else 'unknown',
                'run_id':payload.get('session_id') or payload.get('task_id'),
                'started_at':payload.get('started_at'),'ended_at':payload.get('ended_at'),
                'input_tokens':bucket('input_tokens','prompt_tokens'),
                'cached_input_tokens':bucket('cached_input_tokens','cache_read_tokens'),
                'output_tokens':bucket('output_tokens','completion_tokens'),
            }
            written=self.store.ledger.record(event)
            if written:self.store.health(self.agent,captured=True,stamp=event['ended_at'])
            return written
        except (ValueError,TypeError,KeyError,OSError,sqlite3.Error):
            # Health failure cannot fail an agent turn. Never log payload/exception text.
            try:self.store.health(self.agent,discarded=True)
            except (OSError,sqlite3.Error):pass
            return False

    def main(self,**payload):return self.capture('main',payload)
    def auxiliary(self,**payload):return self.capture('auxiliary',payload)
    def error(self,**payload):return self.capture('error',payload)
