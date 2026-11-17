import csv
import io
import json
import os
import secrets
import time
from threading import RLock
from zipfile import ZipFile, ZIP_DEFLATED
from xml.sax.saxutils import escape

from flask import Flask, jsonify, request, session, send_file, Response
from Bin import decrypt_aes_cbc_pkcs7, hex_to_bytes, check_fingerprints, check_fingerprint_templates, count_household_ids, is_fmr

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)
app.config.update(MAX_CONTENT_LENGTH=1024 * 1024 * 1024, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict')
KEY = os.environ.get('BIN_AES_KEY', 'dd8de506bd4a90418afe9372f01b979b6f0df2e8aae22ec90b09a0bc197dc80c')
IV = os.environ.get('BIN_AES_IV', '4a6f8e2f8a0c5d3e4b6c8d9e0f1a2b3c')
cache = {}
lock = RLock()
HEADERS = ['File', 'Household', 'Role', 'Name', 'Photo', 'Fingerprints', 'Status', 'FMR signatures', 'Invalid / non-FMR templates']


def state():
    with lock:
        now = time.monotonic()
        for key in list(cache):
            if now - cache[key]['touched'] > 1800:
                del cache[key]
        token = session.get('token')
        if token not in cache:
            token = secrets.token_urlsafe(32)
            session['token'] = token
            cache[token] = {'files': [], 'touched': now}
        cache[token]['touched'] = now
        return cache[token]


def extract(data):
    records = data if isinstance(data, list) else next((data[k] for k in ['payrollHouseHolds', 'payrollDtos', 'data', 'households', 'records'] if isinstance(data, dict) and isinstance(data.get(k), list)), None)
    if records is None or any(not isinstance(r, dict) for r in records):
        raise ValueError('Unsupported household record structure')
    return records


def person_row(person, household, role):
    if not isinstance(person, dict):
        raise ValueError('Invalid person record')
    biometric = person.get('biometric') or {}
    if not isinstance(biometric, dict):
        raise ValueError('Invalid biometric record')
    count, fingers = check_fingerprints(biometric)
    templates = check_fingerprint_templates(biometric)
    valid = sum(value == 'FMR signature' for value in templates.values())
    name = ' '.join(str(person.get(k) or person.get(k.lower()) or '') for k in ['firstName', 'lastName']).strip() or 'Unknown'
    return {'household': str(person.get('houseHoldNumber') or person.get('householdNumber') or household), 'role': role, 'name': name, 'photo': bool(biometric.get('photo')), 'fingerprints': int(count), 'fmr_count': valid, 'invalid_templates': len(fingers) - valid, 'template_checks': templates, 'status': 'Present' if fingers else 'Missing fingerprints', 'details': person}


@app.before_request
def local_only():
    if request.host.split(':')[0] not in ('localhost', '127.0.0.1'):
        return jsonify(error='Use localhost or 127.0.0.1 to access this local viewer.'), 403
    if request.method == 'POST':
        if request.headers.get('X-Viewer-Request') != '1':
            return jsonify(error='Invalid viewer request'), 403
        if request.headers.get('Origin') and request.headers['Origin'] != request.host_url.rstrip('/'):
            return jsonify(error='Invalid request origin'), 403


@app.after_request
def private(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    return response


@app.errorhandler(413)
def too_large(error):
    return jsonify(error='Upload limit is 1 GB per batch.'), 413


@app.route('/')
def index():
    return app.send_static_file('index.html')


@app.get('/favicon.ico')
def favicon():
    return app.send_static_file('favicon.svg')


@app.get('/api/files')
def files():
    return jsonify(files=visible_files(state()))


def visible_files(current):
    if request.args.get('compact') != '1':
        return current['files']
    return [{**{k: v for k, v in f.items() if k not in ('data', 'rows')},
             'rows': [{k: v for k, v in row.items() if k != 'details'} for row in f['rows']]}
            for f in current['files']]


@app.get('/api/progress/<job>')
def progress(job):
    return jsonify(state().get('progress', {}).get(job, {'stage': 'Receiving upload', 'percent': None}))


@app.get('/api/data/<file_id>')
def full_data(file_id):
    item = next((f for f in state()['files'] if f['id'] == file_id and not f.get('error')), None)
    if item is None:
        return jsonify(error='File is no longer available. Upload it again.'), 404
    data = item['data']
    if 'row' in request.args:
        row = request.args.get('row', type=int)
        if row is None or row < 0 or row >= len(item['rows']):
            return jsonify(error='Invalid record'), 400
        record = item['rows'][row]
        data = {'fingerprintTemplateChecks': record['template_checks'], 'person': record['details']}
    encoder = json.JSONEncoder(ensure_ascii=True, indent=2)
    if request.args.get('download') == '1':
        return Response(encoder.iterencode(data), mimetype='application/json',
                        headers={'Content-Disposition': 'attachment; filename="decrypted-data.json"'})
    page = request.args.get('page', default=0, type=int)
    if page is None or page < 0:
        return jsonify(error='Invalid page'), 400
    size = 64000
    start, end = page * size, (page + 1) * size
    parts, position, more = [], 0, False
    for chunk in encoder.iterencode(data):
        chunk_end = position + len(chunk)
        if chunk_end > start and position < end:
            parts.append(chunk[max(0, start - position):end - position])
        position = chunk_end
        if position > end:
            more = True
            break
    return jsonify(text=''.join(parts), page=page, has_more=more, start=start)


@app.post('/api/upload')
def upload():
    current = state()
    job = request.args.get('job', '')[:80]
    with lock:
        current['progress'] = {job: {'stage': 'Receiving upload', 'percent': None}}
    def report(stage, percent=None):
        with lock:
            current['progress'][job] = {'stage': stage, 'percent': percent}
    incoming = request.files.getlist('files')
    if not incoming:
        return jsonify(error='Choose at least one BIN file.'), 400
    current = state()
    if len(current['files']) + len(incoming) > 50:
        return jsonify(error='Maximum 50 files. Clear results before uploading more.'), 400
    results = []
    for file_index, uploaded in enumerate(incoming):
        name = (uploaded.filename or 'Unnamed').replace('\\', '/').split('/')[-1]
        result = {'id': secrets.token_hex(12), 'name': name, 'rows': [], 'cycles': 0}
        try:
            if not name.lower().endswith('.bin'):
                raise ValueError('Only .bin files are supported')
            report(f'Reading file {file_index + 1} of {len(incoming)}')
            payload = uploaded.read()
            if not payload or len(payload) % 16:
                raise ValueError('Empty or invalid AES BIN file')
            report(f'Decrypting file {file_index + 1} of {len(incoming)}')
            decoded = decrypt_aes_cbc_pkcs7(payload, hex_to_bytes(KEY), hex_to_bytes(IV))
            del payload
            report(f'Parsing file {file_index + 1} of {len(incoming)}')
            data = json.loads(decoded)
            del decoded
            records = extract(data)
            rows = []
            for record_index, household in enumerate(records):
                if record_index % 100 == 0:
                    report(f'Processing file {file_index + 1} of {len(incoming)}: household {record_index + 1} of {len(records)}',
                           100 * (file_index + record_index / max(1, len(records))) / len(incoming))
                number = household.get('houseHoldNumber') or household.get('householdNumber') or 'N/A'
                rows.append(person_row(household, number, 'BIN'))
                alternates = household.get('payrollAlternates') or household.get('alternates') or []
                if not isinstance(alternates, list):
                    raise ValueError('Invalid alternate records')
                rows.extend(person_row(p, number, f'ALT-{i}') for i, p in enumerate(alternates, 1))
            report(f'Counting payment entries in file {file_index + 1} of {len(incoming)}')
            result.update(rows=rows, cycles=count_household_ids(data), data=data)
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            result['error'] = 'Invalid file, decryption failed, or unsupported payroll structure.'
        results.append(result)
    with lock:
        current['files'].extend(results)
    report('Processing complete', 100)
    return jsonify(files=visible_files(current))


@app.post('/api/clear')
def clear():
    with lock:
        cache.pop(session.get('token'), None)
        session.clear()
    return jsonify(ok=True)


def safe_cell(value):
    value = str(value)
    return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value


def xlsx(rows):
    output = io.BytesIO()
    body = ''.join('<row>' + ''.join(f'<c t="inlineStr"><is><t xml:space="preserve">{escape(str(v))}</t></is></c>' for v in row) + '</row>' for row in rows)
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        archive.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Payroll" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr('xl/worksheets/sheet1.xml', '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + body + '</sheetData></worksheet>')
    output.seek(0)
    return output


@app.get('/api/export/<kind>')
def export(kind):
    selected = [f for f in state()['files'] if not f.get('error') and (not request.args.get('file') or f['id'] == request.args['file'])]
    if not selected:
        return jsonify(error='No successful files to export'), 404
    rows = [HEADERS] + [[f['name'], r['household'], r['role'], r['name'], 'Present' if r['photo'] else 'Missing', r['fingerprints'], r['status'], r['fmr_count'], r['invalid_templates']] for f in selected for r in f['rows']]
    if kind == 'csv':
        output = io.StringIO(newline='')
        csv.writer(output).writerows([[safe_cell(v) for v in row] for row in rows])
        return send_file(io.BytesIO(output.getvalue().encode('utf-8-sig')), mimetype='text/csv', as_attachment=True, download_name='payroll.csv')
    if kind == 'xlsx':
        return send_file(xlsx(rows), mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name='payroll.xlsx')
    return jsonify(error='Unsupported export format'), 400


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=3000, debug=False)
