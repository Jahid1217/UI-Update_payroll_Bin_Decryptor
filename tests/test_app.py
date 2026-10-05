import io
import json
import unittest
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from app import app, cache, KEY, IV


class ViewerTests(unittest.TestCase):
    def setUp(self):
        cache.clear()
        app.config['TESTING'] = True
        self.client = app.test_client()
        data = {'payrollHouseHolds': [{'houseHoldNumber': 'H001', 'firstName': '=TEST()', 'biometric': {'leftThumb': 'fixture', 'photo': 'fixture'}, 'payments': [{'householdId': 'H001'}], 'payrollAlternates': [{'firstName': 'Alternate', 'biometric': {}}]}]}
        self.payload = AES.new(bytes.fromhex(KEY), AES.MODE_CBC, bytes.fromhex(IV)).encrypt(pad(json.dumps(data).encode(),16))

    def upload(self, client=None):
        return (client or self.client).post('/api/upload', data={'files': [(io.BytesIO(self.payload),'valid.bin'), (io.BytesIO(b'invalid'),'broken.bin')]}, headers={'X-Viewer-Request':'1'})

    def test_batch_and_exports(self):
        result = self.upload()
        self.assertEqual(result.status_code,200)
        good,bad = result.json['files']
        self.assertEqual(good['cycles'],1)
        self.assertEqual([r['fingerprints'] for r in good['rows']],[1,0])
        self.assertEqual(good['rows'][1]['household'],'H001')
        self.assertIn('error',bad)
        csv = self.client.get('/api/export/csv')
        self.assertIn("'=TEST()",csv.data.decode('utf-8-sig'))
        self.assertIn('valid.bin',csv.data.decode('utf-8-sig'))
        excel = self.client.get('/api/export/xlsx')
        self.assertEqual(excel.status_code,200)
        with ZipFile(io.BytesIO(excel.data)) as z:
            for name in z.namelist():
                ElementTree.fromstring(z.read(name))
            self.assertNotIn(b'<f>',z.read('xl/worksheets/sheet1.xml'))

    def test_sessions_clear_and_request_protection(self):
        self.upload()
        self.assertEqual(app.test_client().get('/api/files').json['files'],[])
        self.assertEqual(self.client.post('/api/clear').status_code,403)
        self.assertEqual(self.client.post('/api/clear',headers={'X-Viewer-Request':'1','Origin':'http://elsewhere.test'}).status_code,403)
        self.assertEqual(self.client.get('/api/files',headers={'Host':'elsewhere.test'}).status_code,403)
        self.client.post('/api/clear',headers={'X-Viewer-Request':'1'})
        self.assertEqual(self.client.get('/api/files').json['files'],[])
        self.assertEqual(self.client.get('/api/export/csv').status_code,404)

    def test_real_sample_structure(self):
        samples = list(Path('.').glob('*.bin'))
        if not samples:
            self.skipTest('No local sample')
        with samples[0].open('rb') as source:
            result=self.client.post('/api/upload', data={'files': (source,'sample.bin')},headers={'X-Viewer-Request':'1'})
        self.assertEqual(result.status_code,200)
        self.assertNotIn('error',result.json['files'][0])
        self.assertGreater(len(result.json['files'][0]['rows']),0)

    def test_upload_limits(self):
        self.assertEqual(self.client.post('/api/upload', headers={'X-Viewer-Request':'1'}).status_code,400)
        app.config['MAX_CONTENT_LENGTH']=100
        try:
            self.assertEqual(self.upload().status_code,413)
        finally:
            app.config['MAX_CONTENT_LENGTH']=32*1024*1024


if __name__ == '__main__':
    unittest.main()
