"""Portable regression gates: no real runtime, private source or native calls."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]

class PortabilityRegressions(unittest.TestCase):
    def run_case(self, code, extra=None):
        with tempfile.TemporaryDirectory(dir=os.environ.get('TEST_ROOT')) as temporary:
            env = {**os.environ, 'DATA_DIR': str(Path(temporary)/'data'),
                   'EXTERNAL_RUNTIME_ENABLED':'0', 'PYTHONDONTWRITEBYTECODE':'1', **(extra or {})}
            result = subprocess.run([sys.executable,'-B','-c',code],cwd=REPO,env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_disconnected_crm_plugin_import_and_explicit_denial(self):
        self.run_case('''import config
from crm_runtime import plugin
from types import SimpleNamespace
ctx=SimpleNamespace(_manager=SimpleNamespace(home_path=config.RUNTIME_HOME))
try:plugin.register(ctx)
except PermissionError:pass
else:raise AssertionError('Unconfigured profile must fail closed')
''')

    def test_board_rejects_cross_instance_symlink_without_writing_target(self):
        self.run_case('''import config,server,sqlite3,hashlib
root=config.DATA_DIR;root.mkdir()
other=root.parent/'other';other.mkdir();target=other/'board.db'
sqlite3.connect(target).close()
before=target.read_bytes()
(root/'board.db').symlink_to(target)
try:server.Board(root/'board.db')
except ValueError:pass
else:raise AssertionError('Cross-instance board symlink accepted')
assert target.read_bytes()==before,'Other instance modified'
''')

    def test_reporting_day_and_public_projection_use_instance_timezone(self):
        self.run_case('''from datetime import datetime,timezone
from zoneinfo import ZoneInfo
from ai_runtime_v135.spend_control import period_bounds
from spend_views import _period
from agents_metrics import project_week
start,end,label=period_bounds('today',now=datetime(2026,1,1,1,tzinfo=timezone.utc))
assert datetime.fromtimestamp(start,ZoneInfo('America/New_York')).isoformat()=='2025-12-31T00:00:00-05:00',label
assert label=='2025-12-31 — 2025-12-31 America/New_York',label
assert _period(label)==label,'Configured timezone incorrectly redacted'
p=project_week([],[],datetime(2026,1,1,1,tzinfo=timezone.utc).timestamp())
assert p['period']['timezone']=='America/New_York'
''',{'REPORTING_TIMEZONE':'America/New_York'})

if __name__ == '__main__':
    unittest.main(verbosity=2)
