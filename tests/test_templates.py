import unittest
from Bin import is_fmr
from app import person_row, is_fmr as web_is_fmr


class TemplateTests(unittest.TestCase):
    def test_signature_and_malformed_values(self):
        for value in ['Rk1S', 'Rk1SAA==', 'Rk1SADAzMAAAAAE4AAEAAAABKf']:
            self.assertTrue(is_fmr(value))
            self.assertTrue(web_is_fmr(value))
        for value in [None, '', 123, {}, 'Rk1S!bad', 'Rk1S A==', 'R', 'YWJj', 'Rk1S☃']:
            self.assertFalse(is_fmr(value))
            self.assertFalse(web_is_fmr(value))

    def test_presence_is_separate_from_signature(self):
        row = person_row({'biometric': {'leftThumb': 'Rk1SAA==', 'rightThumb': 'not base64', 'leftIndex': ''}}, 'H1', 'BIN')
        self.assertEqual(row['fingerprints'], 2)
        self.assertEqual(row['fmr_count'], 1)
        self.assertEqual(row['invalid_templates'], 1)
        self.assertEqual(row['template_checks']['rightThumb'], 'Invalid / non-FMR')

