import json
from contextlib import contextmanager
import secrets
import unittest
from pathlib import Path
from unittest.mock import patch

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from desktop import KEY, IV, Viewer, load_file


@contextmanager
def fixture_directory():
    root = Path(__file__).resolve().parent
    folder = root / ('fixture_' + secrets.token_hex(8))
    folder.mkdir()
    try:
        yield folder
    finally:
        assert folder.resolve().parent == root
        for path in folder.iterdir():
            path.unlink()
        folder.rmdir()


class DesktopTests(unittest.TestCase):
    def test_load_filter_export_and_clear(self):
        data = [{'householdNumber': 'TEST-01', 'firstName': 'Fixture', 'biometric': {'leftThumb': 'test'}, 'alternates': [{'firstName': 'Alternate'}]}]
        payload = AES.new(bytes.fromhex(KEY), AES.MODE_CBC, bytes.fromhex(IV)).encrypt(pad(json.dumps(data).encode(), 16))
        with fixture_directory() as folder:
            source = Path(folder) / 'fixture.bin'
            source.write_bytes(payload)
            result = load_file(source)
            self.assertNotIn('error', result)
            self.assertEqual(len(result['rows']), 2)
            bad = Path(folder) / 'bad.bin'
            bad.write_bytes(b'bad')
            self.assertIn('error', load_file(bad))
            view = Viewer()
            view.withdraw()
            try:
                view.files = [result]
                view.refresh_files()
                view.update_idletasks()
                self.assertEqual(len(view.table.get_children()), 2)
                self.assertEqual(view.verification.get(), 'BIN: 1/1 with fingerprints (MATCH) · ALT-1: 0/1 with fingerprints (NOT MATCH)')
                view.status.set('Missing')
                self.assertEqual(len(view.table.get_children()), 1)
                self.assertIn('BIN: 1/1', view.verification.get())
                self.assertIn('1 invalid / non-FMR', view.template_summary.get())
                view.status.set('Has FMR signature')
                self.assertEqual(len(view.table.get_children()), 0)
                view.status.set('Invalid / non-FMR')
                self.assertEqual(len(view.table.get_children()), 1)
                output = Path(folder) / 'export.csv'
                with patch('desktop.filedialog.asksaveasfilename', return_value=str(output)):
                    view.export('csv')
                self.assertIn('Fixture', output.read_text(encoding='utf-8-sig'))
                self.assertIn('Alternate', output.read_text(encoding='utf-8-sig'))
                view.show_details('Fixture', data)
                view.clear()
                self.assertEqual(view.files, [])
                self.assertEqual(len(view.table.get_children()), 0)
                self.assertEqual(view.details_windows, [])
                self.assertEqual(view.verification.get(), '')
            finally:
                view.destroy()


if __name__ == '__main__':
    unittest.main()
