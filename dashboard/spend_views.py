import config
"""Pure preview projections. No authorization, I/O, collection or pricing.

Public output is a positive schema, not a redaction pass. Unknown fields are
never copied. CSRF is exclusively the existing native-control capability;
callers must authorize private_spend_report independently, never from raw roles.
All returned mutable containers are newly constructed. Invalid values drop.
"""
import re
from datetime import datetime, date, timezone
import math
from decimal import Decimal, InvalidOperation

AGENTS = frozenset(('MIRA', 'SAGE', 'SELINE', 'SURGE', 'SPECTRA'))
PROFILES = frozenset(config.RUNTIME_PROFILES.values())
COUNTS = ('invocation_count', 'included_invocations', 'unknown_cost_invocations',
          'legacy_bucket_count')
COVERAGE = frozenset(('Complete', 'Incomplete', 'Unavailable', 'Unknown',
                      'complete', 'incomplete', 'unavailable', 'unknown'))
BILLING = frozenset(('subscription_included', 'api', 'api_key', 'direct_api',
                     'subscription', 'unknown', 'unavailable'))
PRESETS = {'economy_chat':'Economy chat', 'research_writing':'Research and writing',
           'build_coding':'Build and coding', 'complex_planning':'Complex planning and coordination'}
MISSING = object()


def _dict(value):
    return value if type(value) is dict else {}


def _enum(*values):
    allowed = frozenset(values)
    return lambda value: value if type(value) is str and value in allowed else MISSING


def _bool(value):
    return value if type(value) is bool else MISSING


def _count(value):
    return value if type(value) is int and 0 <= value <= 2 ** 63 - 1 else MISSING


def _id(value):
    # Native identifiers are retained exactly, never normalized or repaired.
    return value if (type(value) is str and len(value) <= 256 and
                     re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:/+@|~-]*', value)) else MISSING


def _nullable_id(value):
    return None if value is None else _id(value)


def _timestamp(value):
    if type(value) in (int, float):
        return value if math.isfinite(value) and 946684800 <= value <= 253402300799 else MISSING
    if type(value) is not str or len(value) > 40:
        return MISSING
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is not None and parsed.year >= 2000:
            return value
    except ValueError:
        pass
    return MISSING


def _period(value):
    if type(value) is not str:
        return MISSING
    # Structured labels only; arbitrary server prose must not cross the boundary.
    if value in ('Today', f'Today ({config.REPORTING_TIMEZONE})', 'Unavailable', 'Unknown'):
        return value
    match = re.fullmatch(r'(\d{4}-\d{2}-\d{2}) — (\d{4}-\d{2}-\d{2}) ' + re.escape(config.REPORTING_TIMEZONE), value)
    if match:
        try:
            if date.fromisoformat(match[1]) <= date.fromisoformat(match[2]):
                return value
        except ValueError:
            pass
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}(?: \(' + re.escape(config.REPORTING_TIMEZONE) + r'\))?', value):
        try:
            date.fromisoformat(value[:10])
            return value
        except ValueError:
            pass
    return MISSING


def _project(raw, schema):
    raw = _dict(raw)
    result = {}
    for key, validate in schema.items():
        if key in raw:
            value = validate(raw[key])
            if value is not MISSING:
                result[key] = value
    return result


def _rows(value, project):
    return [project(row) for row in value if type(row) is dict] if type(value) is list else []


def _ids(value, validate=_id):
    return [x for item in value if (x := validate(item)) is not MISSING] if type(value) is list else []


def _agent_models(value):
    return {k: v for k, item in _dict(value).items() if k in AGENTS
            and (v := _id(item)) is not MISSING}


def _presets(value):
    return _project(value, dict.fromkeys(PRESETS, _nullable_id))


def _preset_labels(value):
    return _project(value, {k: _enum(v) for k, v in PRESETS.items()})


def _activity_models(value):
    if type(value) is not list:
        return []
    result = []
    for item in value:
        if type(item) is dict:
            result.append(_project(item, {'provider': _id, 'model': _id, 'key': _id}))
        elif (model := _id(item)) is not MISSING:
            result.append(model)
    return result


def _preferences(raw):
    return _project(raw, {'presets': _presets, 'draft_models': _agent_models,
                         'automatic_routing': _bool, 'costlier_fallback': _bool})


def _account(row):
    return _project(row, {
        'agent': _enum(*AGENTS), 'observed_at': _timestamp,
        'openai': lambda v: _project(v, {'connected': _bool, 'billing_mode': _enum(*BILLING)}),
        'nous': lambda v: _project(v, {'switch_available': _bool}),
    })


def _accounts(raw):
    return _project(raw, {'agents': lambda v: [r for r in _rows(v, _account) if 'agent' in r]})


MODEL_FIELDS = {k: _nullable_id for k in ('key', 'provider', 'model', 'pending_model',
                'pending_provider', 'session_model', 'session_provider')}
STATES = ('idle', 'working', 'unavailable', 'unknown', 'pending', 'reserved',
          'consumed', 'applied', 'queued', 'failed', 'cancelled', 'none',
          'IDLE', 'WORKING', 'UNAVAILABLE')


def _fleet_row(raw):
    return _project(raw, {
        **{k: v for k, v in MODEL_FIELDS.items() if k != 'key'},
        'name': _enum(*AGENTS),
        'role': _enum('engineering', 'research', 'writing', 'growth', 'orchestrator',
                      'coordinator', 'Engineer', 'Researcher', 'Writer', 'Growth',
                      'Orchestrator', 'Engineering', 'Research', 'Writing',
                      'Research and Sourcing', 'Writing and Editing',
                      'Marketing, Growth, Monetization', 'Engineering, Automation, Integrations'),
        'control_available': _bool, 'observed_at': _timestamp,
        'pending_group_update': _bool,
        'current_activity': lambda v: _project(v, {
            'state': _enum(*STATES), 'models': _activity_models,
            'source': _enum('native', 'runtime', 'gateway', 'unavailable', 'unknown',
                            'native active-work metadata', 'native activity unavailable')}),
        'one_task_choice': lambda v: _project(v, {**MODEL_FIELDS, 'model_key': _id,
                                                'state': _enum(*STATES)}),
    })


def _fleet(raw):
    def rows(value):
        return [r for r in _rows(value, _fleet_row) if 'name' in r]
    if type(raw) is list:
        return rows(raw)
    return _project(raw, {'agents': rows})


def _legacy(raw):
    return _project(raw, {
        'key': _id, 'agent': _enum(*AGENTS), 'provider': _id, 'model': _id,
        'billing_mode': _enum(*BILLING), 'auth_method': _enum('oauth', 'api_key', 'subscription', 'unknown'),
        'first_seen': _timestamp, 'last_seen': _timestamp,
        **{k: _count for k in ('api_call_count', 'input_tokens', 'output_tokens')},
    })


def public_ai_state(raw: dict) -> dict:
    """Public native-control metadata only; input role flags grant nothing."""
    result = _project(raw, {
        'period_label': _period,
        'summary': lambda v: _project(v, {**dict.fromkeys(COUNTS, _count),
            'subscription_cost_availability': _enum('Available', 'Unavailable', 'Unknown',
                                                     'available', 'unavailable', 'unknown'),
            'coverage': _enum(*COVERAGE), 'legacy_buckets': lambda x: _rows(x, _legacy),
            'breakdown': lambda x: _rows(x, lambda r: _project(r, {
                'agent': _enum(*AGENTS), 'provider': _id, 'model': _id,
                'billing_mode': _enum(*BILLING),
                **dict.fromkeys(('invocations','input_tokens','output_tokens','cached_input_tokens'), _count)}))}),
        'catalog': lambda v: _project(v, {'models': lambda x: _rows(x, lambda r: _project(r, {
            'key': _id, 'provider': _id, 'model': _id, 'selectable': _bool}))}),
        'preferences': _preferences, 'preset_labels': _preset_labels,
        'fleet': _fleet, 'accounts': _accounts,
        'model_scope': _enum('session', 'profile', 'group', 'one_task', 'fleet'),
        'csrf': _id,
        'collection': lambda v: _project(v, {'active_profiles': lambda x: _ids(x, _enum(*PROFILES)),
                                             'coverage': _enum(*COVERAGE)}),
        'import_status': lambda v: _project(v, {'missing_agents': lambda x: _ids(x, _enum(*AGENTS)),
                                               'read_only': _bool}),
    })
    result['live_mutations_enabled'] = _dict(raw).get('live_mutations_enabled') is True
    return result


RECEIPT_STATUS = ('ok', 'applied', 'queued', 'pending', 'reserved', 'consumed', 'unconfirmed',
                  'failed', 'error', 'unknown', 'unavailable', 'cancelled', 'rejected')
RECEIPT_FIELDS = {**MODEL_FIELDS, 'agent': _enum(*AGENTS),
                  'status': _enum(*RECEIPT_STATUS)}


def _receipt(raw, include_results=True):
    # Bounded depth: child receipts may have effective metadata, never results.
    schema = {**RECEIPT_FIELDS,
              'effective': lambda v: _project(v, MODEL_FIELDS)}
    if include_results:
        schema['results'] = lambda v: _rows(v, lambda r: _receipt(r, False))
    return _project(raw, schema)


def public_ai_result(path: str, raw: dict) -> dict:
    """Exact route allowlist; no errors, server prose or owner capabilities."""
    if type(path) is not str:
        return {}
    if path == '/api/ai/preferences':
        return _project(raw, {'preferences': _preferences,
                             'live_model_applied': _bool,
                             'scope': _enum('session', 'profile', 'group', 'one_task', 'fleet')})
    if path == '/api/ai/accounts/refresh':
        return _project(raw, {'accounts': _accounts})
    if path in ('/api/ai/models/apply', '/api/ai/models/fleet', '/api/ai/models/once'):
        return _receipt(raw)
    return {}


def _money(value):
    """Observed finite amounts only. Never infer prices from usage counts.

    Bound representations before fixed-point formatting to reject malformed or
    hostile exponents without allocating enormous decimal strings.
    """
    if type(value) not in (int, float, str, Decimal):
        return None
    if type(value) is int and value.bit_length() > 425:
        return None
    text = str(value)
    if len(text) > 128 or not re.fullmatch(r'-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?', text):
        return None
    try:
        number = Decimal(text)
        if not number.is_finite() or abs(number.as_tuple().exponent) > 100:
            return None
        return format(number, 'f')
    except (InvalidOperation, ValueError, OverflowError):
        return None


def _threshold(value):
    if type(value) not in (int, float):
        return None
    amount = _money(value)
    return value if amount is not None and 0 <= Decimal(amount) <= 100 else None


def _historical_usage(summary):
    """Aggregate dated historical metadata, never export per-session payloads."""
    buckets = summary.get('legacy_buckets')
    count = _count(summary.get('legacy_bucket_count'))
    if type(buckets) is not list or not buckets or count != len(buckets):
        return {}
    dates = []
    for bucket in buckets:
        bucket = _dict(bucket)
        first, last = (_timestamp(bucket.get(k)) for k in ('first_seen', 'last_seen'))
        if first is MISSING or last is MISSING:
            return {}
        first_dt = datetime.fromtimestamp(first, timezone.utc) if type(first) in (int,float) else datetime.fromisoformat(first.replace('Z', '+00:00'))
        last_dt = datetime.fromtimestamp(last, timezone.utc) if type(last) in (int,float) else datetime.fromisoformat(last.replace('Z', '+00:00'))
        if first_dt > last_dt:
            return {}
        dates.append((first_dt, last_dt, first, last))
    result = {'scope': 'historical_aggregate_not_daily', 'bucket_count': count,
              'first_seen': min(dates, key=lambda d: d[0])[2],
              'last_seen': max(dates, key=lambda d: d[1])[3]}
    for key in ('api_call_count', 'input_tokens', 'output_tokens'):
        counts = [_count(_dict(b).get(key)) for b in buckets]
        if all(c is not MISSING for c in counts):
            result[key] = sum(counts)
    return result


def private_spend_report(raw: dict) -> dict:
    """Narrow private report; the caller MUST separately authorize MIRA access.

    Period counts use a structured dated period label as their scope evidence;
    Today/unknown labels do not establish a date. Subscription invocations are
    usage, not charges. Historical aggregates are never assigned to that day.
    Budget status intentionally makes no compliance claim, even for complete
    inputs. No secret, native-control capability or raw prose is exported.
    """
    raw = _dict(raw)
    summary = _dict(raw.get('summary'))
    budget = _dict(raw.get('budget'))
    period = _period(raw.get('period_label'))
    coverage = _enum(*COVERAGE)(summary.get('coverage'))
    updated = _timestamp(summary.get('updated_at'))
    subscription = _enum('Available', 'Unavailable', 'Unknown',
                         'available', 'unavailable', 'unknown')(
                             summary.get('subscription_cost_availability'))
    usage = {}
    if period is not MISSING and re.match(r'^\d{4}-\d{2}-\d{2}', period):
        usage = _project(summary, dict.fromkeys(COUNTS[:3], _count))
        if 'included_invocations' in usage:
            usage['subscription_included_invocations'] = usage.pop('included_invocations')
        if usage:
            usage.update(scope='reported_period', period_label=period)
    native_budget = 'budget_usd' in budget
    amount = _money(budget.get('budget_usd') if native_budget else budget.get('amount'))
    if amount is not None and Decimal(amount) < 0:
        amount = None
    hard_cap = _bool(budget.get('hard_cap'))
    # No invented zero or compliance inference when observations are absent.
    status = 'unavailable'
    if (native_budget and budget['budget_usd'] is None) or (budget.get('status') == 'unset' and amount is None):
        status = 'unset'
    elif (native_budget or budget.get('status') == 'configured') and amount is not None:
        status = 'configured'
    return {
        'period_label': None if period is MISSING else period,
        'confirmed_usd': _money(summary.get('confirmed_usd')),
        'estimate_usd': _money(summary.get('estimate_usd')),
        'usage': usage, 'historical_usage': _historical_usage(summary),
        'coverage': 'Unavailable' if coverage is MISSING else coverage,
        'updated_at': None if updated is MISSING else updated,
        'subscription_cost_availability': 'Unavailable' if subscription is MISSING else subscription,
        'budget': {'amount': amount, 'threshold_percent': _threshold(budget.get('threshold_percent')),
                   'status': status, 'hard_cap': None if hard_cap is MISSING else hard_cap},
    }
