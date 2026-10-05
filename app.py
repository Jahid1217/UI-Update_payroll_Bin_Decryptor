import base64
import csv
import io
import json
import os
import secrets
import time
from threading import RLock
from zipfile import ZipFile, ZIP_DEFLATED
from xml.sax.saxutils import escape

from flask import Flask, jsonify, request, session, send_file
from Bin import decrypt_aes_cbc_pkcs7, hex_to_bytes, check_fingerprints, check_fingerprint_templates, count_household_ids

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)
app.config.update(MAX_CONTENT_LENGTH=32 * 1024 * 1024, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict')
KEY = os.environ.get('BIN_AES_KEY', 'dd8de506bd4a90418afe9372f01b979b6f0df2e8aae22ec90b09a0bc197dc80c')
IV = os.environ.get('BIN_AES_IV', '4a6f8e2f8a0c5d3e4b6c8d9e0f1a2b3c')
cache = {}
lock = RLock()
HEADERS = ['File', 'Household', 'Role', 'Name', 'Photo', 'Fingerprints', 'Status', 'FMR signatures', 'Invalid / non-FMR templates']


def is_fmr(value: str) -> bool:
    try:
        decoded = base64.b64decode(value, validate=True)
        return len(decoded) >= 3 and decoded[:3] == b"FMR"
    except (ValueError, TypeError):
        return False


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
    return jsonify(error='Upload limit is 32 MB per batch.'), 413


@app.route('/')
def index():
    return app.send_static_file('index.html')


@app.get('/api/files')
def files():
    return jsonify(files=state()['files'])


@app.post('/api/upload')
def upload():
    incoming = request.files.getlist('files')
    if not incoming:
        return jsonify(error='Choose at least one BIN file.'), 400
    current = state()
    if len(current['files']) + len(incoming) > 50:
        return jsonify(error='Maximum 50 files. Clear results before uploading more.'), 400
    results = []
    for uploaded in incoming:
        name = (uploaded.filename or 'Unnamed').replace('\\', '/').split('/')[-1]
        result = {'id': secrets.token_hex(12), 'name': name, 'rows': [], 'cycles': 0}
        try:
            if not name.lower().endswith('.bin'):
                raise ValueError('Only .bin files are supported')
            payload = uploaded.read()
            if not payload or len(payload) % 16:
                raise ValueError('Empty or invalid AES BIN file')
            data = json.loads(decrypt_aes_cbc_pkcs7(payload, hex_to_bytes(KEY), hex_to_bytes(IV)))
            records = extract(data)
            rows = []
            for household in records:
                number = household.get('houseHoldNumber') or household.get('householdNumber') or 'N/A'
                rows.append(person_row(household, number, 'BIN'))
                alternates = household.get('payrollAlternates') or household.get('alternates') or []
                if not isinstance(alternates, list):
                    raise ValueError('Invalid alternate records')
                rows.extend(person_row(p, number, f'ALT-{i}') for i, p in enumerate(alternates, 1))
            result.update(rows=rows, cycles=count_household_ids(data), data=data)
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            result['error'] = 'Invalid file, decryption failed, or unsupported payroll structure.'
        results.append(result)
    with lock:
        current['files'].extend(results)
    return jsonify(files=current['files'])


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
