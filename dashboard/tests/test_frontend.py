"""Sanitized frontend invariants; browser integration evidence is generated separately."""
from pathlib import Path
import re
import unittest

ROOT=Path(__file__).resolve().parents[1]

class FrontendContract(unittest.TestCase):
    def test_all_template_entrypoints_are_the_full_same_ui(self):
        main=(ROOT/'index.html').read_bytes()
        self.assertEqual(main,(ROOT/'the-s-mission-control-template.html').read_bytes())
        self.assertEqual(main,(ROOT/'templates/dashboard.template.html').read_bytes())
        self.assertTrue(b"const tabs=['Overview','Agents','Office','Tasks','Schedule','Chat','Content','CRM','Docs']" in main)
        for route in ['/api/board','/api/content/save','/api/crm/command','/api/chat/send','/api/cron/action','/api/ai/state']:
            self.assertTrue(route.encode() in main,route)

    def test_metallic_brand_accents_are_not_fixed_to_original_orange(self):
        text=(ROOT/'index.html').read_text()
        self.assertFalse('linear-gradient(125deg,#7b2c14,#ad421d' in text,'Metallic panel gradients must follow public accent configuration')

    def test_timestamp_formatter_has_no_invalid_placeholder_zone(self):
        text=(ROOT/'index.html').read_text()
        self.assertFalse("timeZone:'Local time'" in text,'Local time is not a valid IANA timezone')

    def test_no_unconditional_connected_or_live_heading(self):
        text=(ROOT/'index.html').read_text()
        self.assertFalse('>LIVE • ORCHESTRATION LAYER<' in text,'Disconnected clone must not imply LIVE operation')

    def test_package_contains_no_persisted_records_or_secrets(self):
        for p in ROOT.rglob('*'):
            if not p.is_file():continue
            self.assertFalse(p.suffix.lower() in {'.db','.sqlite','.sqlite3','.pem','.key','.p12'},p.name)
            self.assertFalse(p.name in {'.env','pins.json','endpoint.json','auth.json','sessions.json','spend-auth.json','gateway_state.json'},p.name)
        for file in ['index.html','templates/dashboard.template.html','the-s-mission-control-template.html']:
            text=(ROOT/file).read_text()
            for forbidden in ['atob(','base64','PRIVATE_OWNER_FIXTURE_NOT_ALLOWED']:
                self.assertFalse(forbidden in text,forbidden)
            self.assertFalse(re.search(r'/(?:root|home)/[^\\s<>]+',text),'No machine-local home paths')
            self.assertFalse('.ts.net' in text,'No inherited private network destination')
        self.assertFalse(re.search(r'https?://[^\s<>"\x27`\\)]+',(ROOT/'index.html').read_text()))

if __name__=='__main__':unittest.main(verbosity=2)
