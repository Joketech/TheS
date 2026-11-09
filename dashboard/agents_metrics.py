import config
"""Pure projections of upstream-scoped retained Mission Control group logs; no I/O."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

AGENTS = ('mira', 'sage', 'seline', 'surge', 'spectra')
WEEKDAYS = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')


def project_week(events, available_agents, now):
    """Project already-scoped, redacted run dicts without accessing any sources.

    ``now`` is Unix seconds. The reporting interval is [start, end), the
    seven completed reporting dates preceding today's local midnight. Agents and
    availability are canonical lowercase IDs. Duplicate (agent, id) records
    retain the first occurrence. Recent records preserve the six input fields,
    including exact timestamps/model values, even when their dates are unusable.

    Unusable timestamps (missing, invalid, naive, nonpositive epoch, future)
    count as undated, sort last by ID/agent, and never affect period metrics.
    Known counts cover readable sources; fleet metrics require all five sources.
    Shares describe period logged runs, not finished runs or live workload.
    """
    zone = config.REPORTING_ZONE
    available = set(available_agents).intersection(AGENTS)
    complete = len(available) == len(AGENTS)
    end = datetime.fromtimestamp(now, zone).replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=7)
    last = end - timedelta(days=1)
    label = f'{start.day} {start:%b} – {last.day} {last:%b %Y}'
    dates = sorted((start + timedelta(days=i) for i in range(7)), key=lambda d: d.weekday())
    result = {
        'period': dict(start=start.isoformat(), end=end.isoformat(), timezone=config.REPORTING_TIMEZONE, label=label, days=7),
        'coverage': dict(all_sources_available=complete, available_agents=[a for a in AGENTS if a in available],
                         missing_agents=[a for a in AGENTS if a not in available], undated_runs=0,
                         historical_complete=False, scope_note='Retained Mission Control group logs only; not lifetime or complete historical activity.'),
        'summary': dict(logged_runs=0, known_logged_runs=0, finished_runs=0, completed=0, failed=0, interrupted=0, success_rate=None),
        'agents': [dict(agent=a, logged_runs=0, known_logged_runs=0, finished_runs=0, success_rate=None, share_pct=None) for a in AGENTS],
        'heatmap': dict(rows=[dict(weekday=d.weekday(), label=WEEKDAYS[d.weekday()], date=d.date().isoformat(),
                                  hours=[dict(hour=h, count=0, known_count=0) for h in range(24)]) for d in dates],
                        peak_hour=None, quietest_hour=None, weekday_average=0, average_unit='logged runs per weekday'),
        'distribution': dict(total=0, known_total=0, rows=[]),
        'recent': [], 'recent_count': 0, 'history_limit': 20,
    }
    seen = set()
    retained = []
    per_agent = {a: [] for a in AGENTS}
    summary = result['summary']
    for source in events:
        agent = source['agent']
        key = (agent, source['id'])
        if agent not in available or key in seen:
            continue
        seen.add(key)
        run = {field: source.get(field) for field in
               ('id', 'agent', 'submitted_at', 'status', 'model', 'task')}
        try:
            date = datetime.fromisoformat(run['submitted_at'])
            if date.tzinfo is None or date.utcoffset() is None:
                raise ValueError('timezone-aware timestamp required')
            if not 0 < date.timestamp() <= now:
                raise ValueError('epoch placeholder or future timestamp')
            date = date.astimezone(zone)
        except (ValueError, TypeError, OverflowError, OSError):
            date = None
        retained.append((run, date))
        if date is None:
            result['coverage']['undated_runs'] += 1
        elif start <= date < end:
            per_agent[agent].append(run)
            summary['known_logged_runs'] += 1
            if run['status'] in ('completed', 'failed', 'interrupted'):
                summary[run['status']] += 1
            cell = result['heatmap']['rows'][date.weekday()]['hours'][date.hour]
            cell['known_count'] += 1
            cell['count'] += 1
    summary['logged_runs'] = summary['known_logged_runs']
    summary['finished_runs'] = sum(summary[s] for s in ('completed', 'failed', 'interrupted'))
    if summary['finished_runs']:
        summary['success_rate'] = summary['completed'] / summary['finished_runs'] * 100
    for row in result['agents']:
        runs = per_agent[row['agent']]
        row['logged_runs'] = row['known_logged_runs'] = len(runs)
        row['finished_runs'] = sum(r['status'] in ('completed', 'failed', 'interrupted') for r in runs)
        if row['finished_runs']:
            row['success_rate'] = sum(r['status'] == 'completed' for r in runs) / row['finished_runs'] * 100
    retained.sort(key=lambda pair: (pair[1] is None,
                  -pair[1].timestamp() if pair[1] is not None else 0,
                  pair[0]['id'], pair[0]['agent']))
    result['recent'] = [run for run, date in retained[:20]]
    result['recent_count'] = len(result['recent'])
    result['distribution']['total'] = result['distribution']['known_total'] = summary['known_logged_runs']
    if summary['known_logged_runs']:
        hours = [sum(row['hours'][h]['known_count'] for row in result['heatmap']['rows']) for h in range(24)]
        peak = max(range(24), key=lambda h: hours[h])
        quiet = min(range(24), key=lambda h: hours[h])
        result['heatmap']['peak_hour'] = dict(hour=peak, count=hours[peak])
        result['heatmap']['quietest_hour'] = dict(hour=quiet, count=hours[quiet])
    result['heatmap']['weekday_average'] = sum(
        cell['known_count'] for row in result['heatmap']['rows'][:5] for cell in row['hours']) / 5
    total = summary['known_logged_runs']
    if complete and total:
        # Integer tenths avoid floating-point errors in largest remainders.
        units = [a['known_logged_runs'] * 1000 // total for a in result['agents']]
        order = sorted(range(len(AGENTS)), key=lambda i: (
            -(result['agents'][i]['known_logged_runs'] * 1000 % total), i))
        for i in order[:1000 - sum(units)]:
            units[i] += 1
        for row, value in zip(result['agents'], units):
            row['share_pct'] = value / 10
    if not complete:
        for key in summary:
            if key != 'known_logged_runs':
                summary[key] = None
        for row in result['agents']:
            if row['agent'] not in available:
                row['logged_runs'] = row['finished_runs'] = row['success_rate'] = None
        result['distribution']['total'] = None
        for row in result['heatmap']['rows']:
            for cell in row['hours']:
                cell['count'] = None
        for key in ('peak_hour', 'quietest_hour', 'weekday_average'):
            result['heatmap'][key] = None
    if total:
        result['distribution']['rows'] = [
            dict(agent=row['agent'], count=row['logged_runs'], share_pct=row['share_pct'])
            for row in result['agents']]
    return result
