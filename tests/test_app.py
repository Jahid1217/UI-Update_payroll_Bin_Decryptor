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

    def test_large_full_data_sections_and_download(self):
        data = {'metadata': 'Unicode: \u09ac\u09be\u0982\u09b2\u09be', 'payrollHouseHolds': [
            {'houseHoldNumber': f'H{i}', 'firstName': f'Person {i}',
             'biometric': {'photo': 'x' * 70000}} for i in range(3)], 'lastField': 'END'}
        payload = AES.new(bytes.fromhex(KEY), AES.MODE_CBC, bytes.fromhex(IV)).encrypt(
            pad(json.dumps(data).encode(), 16))
        result = self.client.post('/api/upload?compact=1&job=large-test',
            data={'files': (io.BytesIO(payload), 'large.bin')}, headers={'X-Viewer-Request': '1'})
        self.assertEqual(result.status_code, 200)
        file = result.json['files'][0]
        self.assertNotIn('data', file)
        self.assertNotIn('details', file['rows'][0])
        self.assertEqual(len(file['rows']), 3)
        self.assertLess(len(result.data), 10000)
        url = '/api/data/' + file['id']
        sections = []
        for page in range(20):
            section = self.client.get(url + f'?page={page}').json
            self.assertLessEqual(len(section['text']), 64000)
            sections.append(section['text'])
            if not section['has_more']:
                break
        self.assertGreater(len(sections), 3)
        self.assertEqual(json.loads(''.join(sections)), data)
        download = self.client.get(url + '?download=1')
        self.assertEqual(json.loads(download.data), data)
        self.assertIn('attachment', download.headers['Content-Disposition'])
        person = self.client.get(url + '?row=2').json
        # A person's large photo also spans sections without being truncated.
        person_next = self.client.get(url + '?row=2&page=1').json
        self.assertEqual(json.loads(person['text'] + person_next['text'])['person'], data['payrollHouseHolds'][2])
        self.assertEqual(self.client.get(url + '?row=-1').status_code, 400)
        self.assertEqual(self.client.get(url + '?page=-1').status_code, 400)
        self.assertEqual(self.client.get('/api/progress/large-test').json,
                         {'stage': 'Processing complete', 'percent': 100})
        stranger = app.test_client()
        self.assertEqual(stranger.get(url).status_code, 404)
        self.assertIsNone(stranger.get('/api/progress/large-test').json['percent'])
        self.client.post('/api/clear', headers={'X-Viewer-Request': '1'})
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_upload_limits(self):
        self.assertEqual(self.client.post('/api/upload', headers={'X-Viewer-Request':'1'}).status_code,400)
        original_limit = app.config['MAX_CONTENT_LENGTH']
        self.assertEqual(original_limit, 1024 * 1024 * 1024)
        app.config['MAX_CONTENT_LENGTH']=100
        try:
            response = self.upload()
            self.assertEqual(response.status_code,413)
            self.assertEqual(response.json['error'], 'Upload limit is 1 GB per batch.')
        finally:
            app.config['MAX_CONTENT_LENGTH']=original_limit


if __name__ == '__main__':
    unittest.main()
