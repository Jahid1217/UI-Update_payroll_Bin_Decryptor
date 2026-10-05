# Payroll BIN File Viewer

## Native desktop app

Double-click **Launch Desktop.vbs** (requires Python on PATH), or run:

```powershell
python desktop.py
```

The native Tkinter app supports multiple BIN files, separate file selection, combined summaries, search/type/fingerprint filters, full file/person details, CSV and Excel exports, and Clear all. Decryption runs on a worker thread. It does not start a server or open a browser. Choose Combined to export all successful files, or choose one file to export only that file; exports include all rows regardless of filters. Results stay in memory until cleared or the app closes. Downloaded exports remain on disk. Install `requirements.txt` first; Tkinter is included with standard Windows Python installations.

The launcher is a Python desktop app, not a standalone `.exe`.

Above the desktop table, fingerprint verification shows each role's coverage, for example `BIN: 9/11 with fingerprints (NOT MATCH) · ALT-1: 8/8 with fingerprints (MATCH)`. Counts use all records in the selected file or Combined selection and remain unchanged by search/table filters.

Fingerprint templates are Base64-decoded with character validation (omitted trailing padding is accepted) and checked for the decoded `FMR` signature. Desktop columns and exports show FMR signature and invalid/non-FMR counts; person details show checks per finger. Filter for FMR signatures or invalid/non-FMR templates. Existing presence counts remain separate. This checks the signature only, not the complete template format or biometric quality.

## Web app

Run from this directory with Python 3.10 or newer:

```powershell
python -m pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:3000. Upload or drop multiple `.bin` files, then click **Decrypt & view data**. Each file has separate results and errors. Search, record type, and fingerprint filters apply to the tables; tables show 50 rows per page. Full data opens in a dialog. Per-file and combined CSV/Excel downloads include all extracted summary rows, regardless of active filters, with the source filename in every row.

The combined summary counts primary households, fingerprint coverage across all people, and occurrences of `householdId` (the existing script's payment-cycle metric). Per-file verification compares the count of people with fingerprints against the total for each BIN/alternate role. A missing fingerprint indicates missing biometric data, not a decryption failure.

The viewer reuses `Bin.py` AES-CBC/PKCS7 decryption and fingerprint checks. The existing key and IV remain the defaults; override with the `BIN_AES_KEY` and `BIN_AES_IV` environment variables if required. The original CLI still works.

Uploads are processed in memory; the web viewer does not write decrypted files. Results belong to a browser session, expire after 30 minutes of inactivity (purged on the next data request), and disappear on Clear all or server restart. Downloaded exports remain on your computer. Limits: 32 MB per upload batch and 50 files per session. Use one local server process. Do not expose it via a public proxy or change the loopback bind without adding authentication and access controls. No external fonts, scripts, or services are used.

Run backend checks:

```powershell
python -m unittest discover -s tests -v
```
