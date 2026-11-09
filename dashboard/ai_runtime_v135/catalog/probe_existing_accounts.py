import config
"""Sanitized inspection of existing auth, with no login/refresh/purchase/inference."""
import contextlib
import datetime
import io
import json
import sys


def inspect_existing(profile):
    import config as instance_config
    instance_config.require_runtime()
    import os
    if profile not in instance_config.RUNTIME_PROFILES.values() or os.environ.get('HERMES_HOME') not in instance_config.RUNTIME_HOMES.values():
        raise PermissionError('Unconfigured own-instance profile')
    result = {'profile': profile, 'observed_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}
    # Native imports can log; never forward credential-bearing diagnostic text.
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        from hermes_cli.auth import get_codex_auth_status, get_api_key_provider_status
        from hermes_cli.auth_nous import get_nous_auth_status_local
        from hermes_cli.config import load_config_readonly
        config = load_config_readonly()
        model = config.get('model') or {}
        result['configured'] = {k: model.get(k) for k in ('provider', 'default')}
        result['codex'] = {k: v for k, v in get_codex_auth_status().items()
                           if k in ('logged_in', 'auth_mode', 'rate_limited')}
        result['api_key_configured'] = {p: bool(get_api_key_provider_status(p).get('configured'))
                                        for p in ('openai-api', 'anthropic')}
        result['nous'] = {k: v for k, v in get_nous_auth_status_local().items()
                          if k in ('logged_in', 'free_tier', 'relogin_required')}
        # Read stored auth once; never invoke resolve_nous_access_token (may refresh).
        from hermes_cli.auth import get_provider_auth_state
        from hermes_cli.nous_account import (
            _info_from_fetched_account, _info_from_account_payload,
            _select_nous_pool_entry, _pool_entry_is_portal_oauth,
            _pool_entry_inference_url,
        )
        state = get_provider_auth_state('nous') or {}
        token = state.get('access_token')
        entry = _select_nous_pool_entry()
        result['nous']['inference_credential_present'] = bool(
            state.get('agent_key') or (entry and (entry.runtime_api_key or entry.access_token)))
        if not token and entry and _pool_entry_is_portal_oauth(entry):
            token = entry.access_token
            state = {'access_token': token, 'portal_base_url': entry.portal_base_url,
                     'inference_base_url': _pool_entry_inference_url(entry)}
        result['nous']['account_status'] = 'Unavailable — no Portal OAuth billing grant'
        if token:
            try:
                info = _info_from_fetched_account(token, state, state.get('portal_base_url'))
                access = info.paid_service_access_info
                result['nous'].update({
                    'account_status': 'verified' if info.fresh and info.source == 'account_api' else 'Unavailable',
                    'credit_status_fresh': info.fresh,
                    'paid_service_access': info.paid_service_access,
                    'usable_credits': access.total_usable_credits if access else None,
                    'reason': access.reason if access else None,
                })
            except Exception as exc:
                result['nous']['account_status'] = 'Unavailable — ' + type(exc).__name__
    return result

if __name__ == '__main__':
    try:
        print(json.dumps(inspect_existing(sys.argv[1]), allow_nan=False))
    except Exception as exc:
        print(json.dumps({'profile': sys.argv[1], 'probe_error': type(exc).__name__}))
        sys.exit(1)
