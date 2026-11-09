import config
"""Read-only raw cron reader + narrow installed Hermes edit CLI adapter.

Integration: ScheduleEditService(cron.home, python=CHAT_PYTHON,
    action_lock=cron.action_lock, invalidate=cron.invalidate).
load(exact_id) -> (status, {job: raw_record, revision, capabilities}).
save(exact_id, {revision: load_revision, changes: {name/prompt/schedule/deliver}})
    -> (status, JSON-safe result). title and instruction are input aliases.
Caller must enforce HTTP Host/Origin/CSRF/auth, body size and duplicate JSON keys.

No import of native load_jobs, no dashboard writes to native storage. Installation
source inspected: hermes_cli/subcommands/cron.py, hermes_cli/cron.py,
tools/cronjob_tools.py and cron/jobs.py in the installed fe1265ac runtime.
Docs: https://hermes-agent.nousresearch.com/docs/user-guide/features/cron
Timezone is NOT a supported edit flag. Configured Hermes timezone governs cron.
CLI updates have no CAS flag: optimistic checks cannot close the native-writer
race between last read and CLI lock acquisition. Callers must disclose that
limitation; neither unexpected outcomes nor timeouts are safe to retry blindly.
"""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import threading


class EditError(Exception):
    def __init__(self, status, message):
        self.status = status
        self.message = message


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate key')
        result[key] = value
    return result


def _nonfinite(value):
    raise ValueError('Non-finite number')


def _revision(job):
    return hashlib.sha256(json.dumps(job, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=True, allow_nan=False).encode()).hexdigest()


class ScheduleEditService:
    """Trusted constructor paths only; no caller-supplied commands or home overrides."""
    def __init__(self, home, *, python, runner=None, env=None, action_lock=None, invalidate=None):
        self.home = Path(home).absolute()
        self.python = str(python)
        self.runner = subprocess.run if runner is None else runner
        self.env = dict(os.environ if env is None else env)
        self.action_lock = threading.Lock() if action_lock is None else action_lock
        self.invalidate = invalidate

    def _read(self):
        config.require_runtime()
        directory = os.open(self.home / 'cron', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            fd = os.open('jobs.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory)
        finally:
            os.close(directory)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            limit = 8 * 1024 * 1024
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                raise ValueError('Unsafe source')
            raw = stream.read(limit + 1)
            if len(raw) > limit:
                raise ValueError('Oversized source')
        data = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique, parse_constant=_nonfinite)
        # Also reject overflowed numeric literals (1e999), including sibling records.
        json.dumps(data, allow_nan=False)
        jobs = data.get('jobs') if isinstance(data, dict) else data
        if not isinstance(jobs, list):
            raise ValueError('Jobs list required')
        seen = set()
        for job in jobs:
            if not isinstance(job, dict):
                raise ValueError('Job object required')
            ident = job.get('id')
            self._ident(ident)
            if ident in seen:
                raise ValueError('Duplicate ID')
            seen.add(ident)
        return jobs

    @staticmethod
    def _ident(ident):
        if (not isinstance(ident, str) or not ident or len(ident) > 128
                or ident.startswith('-') or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in ident)
                or '/' in ident or '\\' in ident or ident in ('.', '..')):
            raise EditError(400, 'Invalid exact cron job ID')

    def _current(self, ident):
        self._ident(ident)
        jobs = self._read()
        job = next((j for j in jobs if j['id'] == ident), None)
        if job is None:
            raise EditError(404, 'Cron job not found')
        return jobs, job

    @staticmethod
    def _changes(payload):
        import re
        if not isinstance(payload, dict) or set(payload) != {'revision', 'changes'}:
            raise EditError(400, 'Expected revision and changes only')
        token = payload['revision']
        if not isinstance(token, str) or not re.fullmatch('[0-9a-f]{64}', token):
            raise EditError(400, 'A current revision is required')
        raw = payload['changes']
        if not isinstance(raw, dict) or not raw or len(raw) > 4:
            raise EditError(400, 'Supply nonempty editable changes')
        result = {}
        aliases = {'title': 'name', 'instruction': 'prompt'}
        bounds = {'name': 256, 'prompt': 32768, 'schedule': 256, 'deliver': 256}
        for field, value in raw.items():
            key = aliases.get(field, field)
            if key not in bounds or key in result:
                raise EditError(400, 'Unsupported or duplicate editable field; timezone is not supported')
            if (not isinstance(value, str) or not value.strip()
                    or len(value.encode('utf-8', errors='replace')) > bounds[key]
                    or any((ord(c) < 32 and not (key == 'prompt' and c in '\n\t\r'))
                           or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in value)):
                raise EditError(400, 'Invalid editable field value')
            # Intentionally narrower than native fanout/bot-chat grammar. No new
            # profile discovery, home-channel resolution or destination inference.
            if key == 'deliver' and not re.fullmatch(r'(?:local|origin|telegram(?::-?[0-9]+(?::[0-9]+)?)?)', value):
                raise EditError(400, 'Supported delivery values: local, origin, telegram[:chat[:topic]]')
            result[key] = value
        return token, result

    @staticmethod
    def _schedule(value):
        """Conservative grammar; native CLI remains final authority, never inferred TZ."""
        import re
        from datetime import datetime
        match = re.fullmatch(r'(?:every )?([1-9][0-9]{0,5})(m|h|d)', value)
        if match:
            minutes = int(match[1]) * {'m': 1, 'h': 60, 'd': 1440}[match[2]]
            if minutes > 525600:
                raise EditError(400, 'Interval exceeds supported one-year limit')
            return {'kind': 'interval', 'minutes': minutes, 'display': f'every {minutes}m'}
        parts = value.split(' ')
        if len(parts) == 5:
            for part, (low, high) in zip(parts, [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]):
                for term in part.split(','):
                    if not re.fullmatch(r'(?:\*|[0-9]{1,2}(?:-[0-9]{1,2})?)(?:/[1-9][0-9]?)?', term):
                        raise EditError(400, 'Invalid numeric five-field cron')
                    base, _, step = term.partition('/')
                    if step and int(step) > high - low + 1:
                        raise EditError(400, 'Invalid cron step')
                    if base != '*':
                        values = list(map(int, base.split('-')))
                        if any(v < low or v > high for v in values) or values[0] > values[-1]:
                            raise EditError(400, 'Cron field out of range')
            return {'kind': 'cron', 'expr': value, 'display': value}
        try:
            if 'T' not in value:
                raise ValueError()
            when = datetime.fromisoformat(value)
            if when.tzinfo is None or when.utcoffset() is None:
                raise ValueError()
            return {'kind': 'once', 'run_at': when.isoformat(),
                    'display': f"once at {when.strftime('%Y-%m-%d %H:%M')}"}
        except ValueError:
            raise EditError(400, 'Use an interval, numeric five-field cron, or offset-aware ISO timestamp') from None

    @staticmethod
    def _preservation_guard(jobs, job, changes):
        for row in jobs:
            repeat = row.get('repeat')
            if isinstance(repeat, dict) and 'completed' in repeat and (
                    type(repeat['completed']) is not int or repeat['completed'] < 0):
                raise EditError(409, 'Native loader would repair storage; editing is disabled')
        skills = job.get('skills')
        if (not isinstance(skills, list) or any(not isinstance(s, str) or not s or s != s.strip() for s in skills)
                or len(set(skills)) != len(skills) or 'skill' not in job
                or job['skill'] != (skills[0] if skills else None)):
            raise EditError(409, 'Native edit would normalize unspecified skill fields')
        if type(job.get('enabled')) is not bool or job.get('state') not in ('paused', 'scheduled', 'completed', 'error'):
            raise EditError(409, 'Unrecognized lifecycle; cannot guarantee preservation')
        if job['enabled'] and job['state'] != 'paused' and not job.get('next_run_at'):
            raise EditError(409, 'Native edit would fill an unspecified next-run field')
        expected = dict(job, **changes)
        if 'schedule' in changes:
            # Installed tool's schedule update activates any non-paused record;
            # jobs.update_job also recomputes next_run, clears quota/pending state
            # and may rederive repeat. Reject rather than compensating afterwards.
            if job['state'] != 'paused' or job['enabled'] is not False:
                raise EditError(409, 'Schedule edits require an already-paused job to preserve next-run and lifecycle')
            if any(k in job for k in ('pending_slot', 'quota_hold_until', 'quota_hold_cron_expr')):
                raise EditError(409, 'Schedule edit would clear unspecified scheduler state')
            schedule = ScheduleEditService._schedule(changes['schedule'])
            if not isinstance(job.get('schedule'), dict) or job['schedule'].get('kind') != schedule['kind']:
                raise EditError(409, 'Schedule kind changes would alter repeat semantics')
            expected['schedule'] = schedule
            expected['schedule_display'] = schedule['display']
        return expected

    def save(self, ident, payload):
        acquired = False
        try:
            self._ident(ident)
            token, changes = self._changes(payload)
            if (not self.env.get('HERMES_HOME')
                    or Path(self.env['HERMES_HOME']).expanduser().resolve() != self.home.resolve()):
                raise EditError(503, 'Hermes cron environment does not match reader home')
            if not self.action_lock.acquire(blocking=False):
                raise EditError(409, 'A cron mutation is already in progress')
            acquired = True
            jobs, before = self._current(ident)
            if token != _revision(before):
                raise EditError(409, 'Cron job changed; reload before saving')
            changes = {k: v for k, v in changes.items() if before.get(k) != v}
            if not changes:
                return 200, {'ok': True, 'job': before, 'revision': token, 'changed': False}
            expected = self._preservation_guard(jobs, before, changes)
            argv = [self.python, '-B', '-m', 'hermes_cli.main', 'cron', 'edit', ident]
            argv += ['--' + key + '=' + value for key, value in sorted(changes.items())]
            latest, _ = self._current(ident)
            if _revision(latest) != _revision(jobs):
                raise EditError(409, 'Cron storage changed before dispatch; reload before saving')
            safe_env = {key: value for key, value in self.env.items() if key in (
                'PATH', 'HOME', 'LANG', 'LC_ALL', 'TZ', 'TMPDIR', 'SSL_CERT_FILE',
                'SSL_CERT_DIR', 'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY', 'HERMES_HOME')}
            try:
                result = self.runner(argv, shell=False, stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    env=safe_env, timeout=30)
                _, after = self._current(ident)
                if result.returncode != 0 or _revision(after) != _revision(expected):
                    return 409, {'error': 'Edit outcome unconfirmed; inspect current job before retrying', 'outcome': 'unconfirmed'}
                return 200, {'ok': True, 'job': after, 'revision': _revision(after), 'changed': True}
            finally:
                if self.invalidate is not None:
                    self.invalidate()
        except EditError as exc:
            return exc.status, {'error': exc.message}
        except (OSError, ValueError, UnicodeError, RecursionError, subprocess.TimeoutExpired):
            return 503, {'error': 'Edit outcome unknown; inspect current job before retrying', 'outcome': 'unknown'}
        finally:
            if acquired:
                self.action_lock.release()

    def load(self, ident):
        try:
            _, job = self._current(ident)
            schedule_editable = (job.get('state') == 'paused' and job.get('enabled') is False
                and not any(key in job for key in ('pending_slot', 'quota_hold_until', 'quota_hold_cron_expr')))
            return 200, {'job': job, 'revision': _revision(job),
                         'capabilities': {'name': True, 'prompt': True, 'schedule': schedule_editable,
                                          'deliver': True, 'timezone': False},
                         'limits': {
                             'schedule': 'Only already-paused, same-kind interval/numeric-five-field-cron/offset-aware-ISO edits; next_run_at is preserved',
                             'delivery': 'local, origin, telegram[:chat[:topic]] only; other native delivery values remain unchanged unless edited',
                             'timezone': 'No installed per-job timezone edit flag; cron follows configured Hermes timezone',
                             'concurrency': 'Optimistic revision checks only; native CLI has no atomic CAS. Never automatically retry an unknown outcome'}}
        except EditError as exc:
            return exc.status, {'error': exc.message}
        except (OSError, ValueError, UnicodeError, RecursionError):
            return 503, {'error': 'Hermes cron source unavailable'}
