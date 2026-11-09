import config
"""Account evidence gates; missing money/credits never becomes an invented zero."""
from datetime import datetime, timezone
from decimal import Decimal

AGENTS = {a.upper():config.RUNTIME_PROFILES.get(a,'unconfigured-'+a) for a in ('mira','sage','seline','surge','spectra')}

class AccountPolicy:
    def __init__(self, evidence):
        self.rows = {row['profile']: row for row in evidence['profiles']}
        self.protected_files_unchanged = evidence.get('protected_files_unchanged') is True

    def agent(self, agent):
        if agent not in AGENTS:
            raise ValueError('Unknown agent')
        row = self.rows.get(AGENTS[agent], {})
        codex = row.get('codex', {})
        keys = row.get('api_key_configured', {})
        nous = row.get('nous', {})
        included = codex.get('logged_in') is True and codex.get('auth_mode') == 'chatgpt'
        connected = nous.get('logged_in') is True and nous.get('inference_credential_present') is True
        credits = nous.get('usable_credits')
        funded = credits is not None and Decimal(str(credits)) > 0
        fresh = nous.get('account_status') == 'verified' and nous.get('credit_status_fresh') is True
        return {
            'agent': agent, 'observed_at': row.get('observed_at'),
            'configured': row.get('configured', {}),
            'openai': {
                'connected': codex.get('logged_in') is True,
                'provider': 'openai-codex',
                'auth_method': 'ChatGPT sign-in' if included else 'Unavailable',
                'billing_mode': 'subscription_included' if included else 'unknown',
                'api_key_configured': keys.get('openai-api') is True,
                'confirmed_daily_charge': None,
                'label': 'Included plan usage — monetary cost unavailable' if included else 'Unavailable',
            },
            'nous': {
                'connected': connected, 'account_status': nous.get('account_status', 'Unavailable'),
                'auth_method': 'Existing Nous sign-in' if connected else 'Unavailable',
                'usable_credits': credits, 'credit_status_fresh': fresh,
                'switch_available': connected and fresh and funded and nous.get('paid_service_access') is True,
                'status': ('Connected — out of credits' if connected and fresh and credits == 0
                           else 'Connected — credit verification required' if connected and not funded
                           else 'Connected — funded' if connected and funded and fresh else 'Unavailable'),
                'billing_mode': 'prepaid/plan credits — per-invocation billing attribution unverified',
                'automatic_top_up_enabled_by_dashboard': False,
            },
            'anthropic': {'api_key_configured': keys.get('anthropic') is True,
                          'new_connection_authorised': False},
        }

    def require_switch(self, agent, provider, *, now=None, max_age_seconds=900):
        row = self.agent(agent)
        stamp = row['observed_at']
        if not stamp:
            raise PermissionError('Account verification unavailable')
        observed = datetime.fromisoformat(stamp)
        now = now or datetime.now(timezone.utc)
        age = (now-observed).total_seconds()
        if age < -30 or age > max_age_seconds:
            raise PermissionError('Refresh account verification before switching')
        if provider == 'openai-codex' and row['openai']['billing_mode'] == 'subscription_included':
            return row['openai']
        if provider == 'nous' and row['nous']['switch_available']:
            return row['nous']
        if provider == 'nous':
            raise PermissionError('Nous is connected but usable credits are zero or unverified; no inference sent')
        raise PermissionError('Provider is not authorised/configured for this operation')

    def payload(self):
        return {
            'agents': [self.agent(agent) for agent in AGENTS],
            'budget': {'amount': None, 'currency': None, 'threshold_percent': None,
                       'status': 'Unset — awaiting owner confirmation', 'hard_cap': False,
                       'purchases_authorised': False},
            'fixed_costs': {'contabo': None, 'subscriptions': None, 'daily_allocation': None},
            'auth_changes_made': False,
            'protected_files_unchanged': self.protected_files_unchanged,
        }
