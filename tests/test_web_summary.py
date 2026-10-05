import io
import json
import subprocess
import unittest
from pathlib import Path

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from app import app, cache, KEY, IV


class WebSummaryTests(unittest.TestCase):
    def check_upload(self, data, counts, verification):
        cache.clear()
        payload = AES.new(bytes.fromhex(KEY), AES.MODE_CBC, bytes.fromhex(IV)).encrypt(
            pad(json.dumps(data).encode(), 16))
        with app.test_client() as client:
            response = client.post('/api/upload', data={'files': (io.BytesIO(payload), 'source.bin')},
                                   headers={'X-Viewer-Request': '1'})
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('error', response.json['files'][0])
            # Exercise the actual JS renderer with the actual upload response.
            result = subprocess.run(['node', 'tests/check_web_summary.js'], input=json.dumps({
                'files': response.json['files'], 'counts': [counts], 'verification': [verification]
            }), text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_source_data(self):
        sources = list(Path('decrypted_json').glob('*.json'))
        if not sources:
            self.skipTest('Local decrypted source unavailable')
        for source in sources:
            data = json.loads(source.read_text(encoding='utf-8-sig'))
            households = data['payrollHouseHolds']
            groups = {'BIN': households}
            for household in households:
                for index, person in enumerate(household.get('payrollAlternates', []), 1):
                    groups.setdefault(f'ALT-{index}', []).append(person)
            # Independent calculation from source fields, without app counting helpers.
            cycles = sum(len(payroll['payrollDataBinDtos']) for payroll in data['payrollDtos'])
            alternates = sum(len(group) for role, group in groups.items() if role != 'BIN')
            fingers = ['leftIndex', 'leftMiddle', 'leftRing', 'leftSmall', 'leftThumb',
                       'rightIndex', 'rightMiddle', 'rightRing', 'rightSmall', 'rightThumb']
            verification = []
            for role, people in groups.items():
                present = sum(any(person.get('biometric', {}).get(finger) for finger in fingers)
                              for person in people)
                verification.append(f"{role}: {present}/{len(people)} with fingerprints ({'MATCH' if present == len(people) else 'NOT MATCH'})")
            self.check_upload(data, f'{len(households)} Households · {alternates} Alternates · {cycles} Payment Cycle Entries',
                              ' · '.join(verification))

    def test_changed_data_and_role_statuses(self):
        self.check_upload({'payrollHouseHolds': [
            {'biometric': {'leftThumb': 'Rk1S'}, 'payrollAlternates': [
                {'biometric': {}}, {'biometric': {'rightThumb': 'Rk1S'}}]},
            {'biometric': {}, 'payments': [{'householdId': 'test'}]},
        ]}, '2 Households · 2 Alternates · 1 Payment Cycle Entries',
            'BIN: 1/2 with fingerprints (NOT MATCH) · ALT-1: 0/1 with fingerprints (NOT MATCH) · ALT-2: 1/1 with fingerprints (MATCH)')

    def test_empty_file(self):
        self.check_upload([], '0 Households · 0 Alternates · 0 Payment Cycle Entries', '')
