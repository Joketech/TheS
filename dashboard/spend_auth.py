import config
"""Spend authorization, with no HTTP routes or automatic enrollment.

configure_owner/provision_mira_key are trusted local-administrator APIs ONLY.
Never expose either operation through an HTTP handler. Instances own in-memory
sessions; use one long-lived instance per service. Restart invalidates sessions.
"""
import contextlib
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import secrets
import stat
import threading
import time

__all__ = ['SpendAuth', 'RateLimited']


class RateLimited(Exception):
    """Five authentication attempts have exhausted the current window."""


class SpendAuth:
    def __init__(self, root: Path, clock=time.monotonic,
                 wall_clock=time.time, ttl=300):
        self.root = Path(root).absolute()
        self.clock, self.wall_clock = clock, wall_clock
        if not isinstance(ttl, (int, float)) or not math.isfinite(ttl) or ttl <= 0:
            raise ValueError('TTL must be finite and positive')
        self.ttl = ttl
        self._lock = threading.RLock()
        self._sessions = {}
        self._attempts = []

    @contextlib.contextmanager
    def _private(self, create=False):
        # Walk using directory descriptors: neither parent nor final symlinks
        # are followed, including during concurrent path replacement.
        with contextlib.ExitStack() as stack:
            fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
            stack.callback(os.close, fd)
            for part in self.root.parts[1:]:
                if part == '..':
                    raise ValueError('Unsafe root')
                fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                             dir_fd=fd)
                stack.callback(os.close, fd)
            if create:
                try:
                    os.mkdir('private', 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            fd = os.open('private', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                         dir_fd=fd)
            stack.callback(os.close, fd)
            info = os.fstat(fd)
            if stat.S_IMODE(info.st_mode) != 0o700 or info.st_uid != os.geteuid():
                raise PermissionError('Unsafe private directory')
            yield fd

    def _read(self, fd, name):
        handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=fd)
        with os.fdopen(handle, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid != os.geteuid()
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 4096):
                raise ValueError('Unsafe credential file')
            return stream.read(4097)

    @staticmethod
    def _unique(pairs):
        data = {}
        for key, value in pairs:
            if key in data:
                raise ValueError('Duplicate configuration field')
            data[key] = value
        return data

    def _load(self):
        try:
            with self._private() as fd:
                data = json.loads(self._read(fd, 'spend-auth.json'),
                                  object_pairs_hook=self._unique)
            if set(data) not in ({'version', 'owner'}, {'version', 'owner', 'mira'}):
                return None
            if (type(data['version']) is not int or data['version'] != 1
                    or set(data['owner']) != {'salt', 'verifier'}):
                return None
            owner = data['owner']
            if (len(owner['salt']) != 32 or len(bytes.fromhex(owner['salt'])) != 16
                    or len(owner['verifier']) != 64
                    or len(bytes.fromhex(owner['verifier'])) != 32):
                return None
            if 'mira' in data and (len(data['mira']) != 64
                                  or len(bytes.fromhex(data['mira'])) != 32):
                return None
            return data
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            return None

    def _write(self, fd, name, payload, replace=False):
        temporary = '.auth-' + secrets.token_hex(16)
        handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                         | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        try:
            with os.fdopen(handle, 'wb') as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            if replace:
                self._read(fd, name)  # Refuse an unsafe existing target.
                os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
            else:
                os.link(temporary, name, src_dir_fd=fd, dst_dir_fd=fd,
                        follow_symlinks=False)
            os.fsync(fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=fd)
            except FileNotFoundError:
                pass

    @staticmethod
    def _password(password):
        if not isinstance(password, str):
            raise ValueError('Password must be text')
        raw = password.encode('utf-8')
        if not 12 <= len(raw) <= 256:
            raise ValueError('Password must be 12 through 256 UTF-8 bytes')
        return raw

    @staticmethod
    def _derive(raw, salt):
        return hashlib.scrypt(raw, salt=salt, n=16384, r=8, p=1, dklen=32)

    @property
    def configured(self):
        return self._load() is not None

    def configure_owner(self, password: str):
        """Trusted local administrator only; never overwrites enrollment."""
        raw = self._password(password)
        salt = secrets.token_bytes(16)
        data = {'version': 1, 'owner': {'salt': salt.hex(),
                'verifier': self._derive(raw, salt).hex()}}
        with self._lock, self._private(create=True) as fd:
            self._write(fd, 'spend-auth.json', json.dumps(data).encode())

    def authenticate(self, password: str, client='loopback'):
        with self._lock:
            # Global per-instance window intentionally ignores untrusted client labels.
            now = self.clock()
            self._attempts[:] = [t for t in self._attempts if now - t < 300]
            if len(self._attempts) >= 5:
                raise RateLimited('Authentication temporarily rate limited')
            self._attempts.append(now)
            data = self._load()
            if data is None:
                return None
            try:
                raw = self._password(password)
            except (ValueError, UnicodeError):
                return None
            expected = bytes.fromhex(data['owner']['verifier'])
            actual = self._derive(raw, bytes.fromhex(data['owner']['salt']))
            if not hmac.compare_digest(actual, expected):
                return None
            result = {'token': secrets.token_urlsafe(32),
                      'csrf': secrets.token_urlsafe(32),
                      'expires_at': self.wall_clock() + self.ttl}
            # A single owner session: every successful login rotates all sessions.
            self._sessions.clear()
            self._sessions[result['token']] = (self.clock() + self.ttl,
                {'role': 'owner', 'csrf': result['csrf'],
                 'expires_at': result['expires_at']})
            return result

    def resolve(self, token):
        with self._lock:
            if not isinstance(token, str) or len(token) > 128:
                return None
            entry = self._sessions.get(token)
            if entry is None:
                return None
            if self.clock() >= entry[0] or not self.configured:
                self._sessions.pop(token, None)
                return None
            return dict(entry[1])

    def remaining(self, token):
        """Read the existing monotonic lifetime; never renew or grant a session."""
        with self._lock:
            session = self.resolve(token)
            if session is None or session.get('role') != 'owner':
                return None
            return max(0.0, min(float(self.ttl),
                               self._sessions[token][0] - self.clock()))

    def revoke(self, token):
        with self._lock:
            if isinstance(token, str):
                self._sessions.pop(token, None)

    def provision_mira_key(self) -> str:
        """Trusted local admin only. Refuse overwrite; no implicit rotation.

        A crash between the two atomic file commits can leave an orphan key;
        that key authorizes nothing. Local admin intervention is then required.
        """
        with self._lock:
            data = self._load()
            if data is None:
                raise ValueError('Owner enrollment required')
            if 'mira' in data:
                raise FileExistsError('Report capability already provisioned')
            key = 'mira_' + secrets.token_urlsafe(32)
            data['mira'] = hashlib.sha256(key.encode('ascii')).hexdigest()
            with self._private() as fd:
                self._write(fd, 'mira-spend-read.key', (key + '\n').encode())
                try:
                    self._write(fd, 'spend-auth.json', json.dumps(data).encode(),
                                replace=True)
                except BaseException:
                    os.unlink('mira-spend-read.key', dir_fd=fd)
                    raise
            return key

    def mira_authorized(self, raw_key) -> bool:
        if not isinstance(raw_key, str) or not 1 <= len(raw_key) <= 128:
            return False
        try:
            candidate = hashlib.sha256(raw_key.encode('ascii')).digest()
        except UnicodeError:
            return False
        data = self._load()
        if data is None or 'mira' not in data:
            return False
        return hmac.compare_digest(candidate, bytes.fromhex(data['mira']))
