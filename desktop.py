"""Native, local-only payroll viewer. Run with python desktop.py."""
import csv
import json
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from app import KEY, IV, HEADERS, extract, person_row, safe_cell, xlsx
from Bin import decrypt_aes_cbc_pkcs7, hex_to_bytes, count_household_ids


def load_file(path):
    result = {'name': Path(path).name, 'rows': [], 'cycles': 0}
    try:
        if Path(path).suffix.lower() != '.bin':
            raise ValueError('Choose a .bin file.')
        if Path(path).stat().st_size > 32 * 1024 * 1024:
            raise ValueError('File exceeds the 32 MB limit.')
        payload = Path(path).read_bytes()
        if not payload or len(payload) % 16:
            raise ValueError('Empty or invalid AES BIN file.')
        data = json.loads(decrypt_aes_cbc_pkcs7(payload, hex_to_bytes(KEY), hex_to_bytes(IV)))
        rows = []
        for household in extract(data):
            number = household.get('houseHoldNumber') or household.get('householdNumber') or 'N/A'
            rows.append(person_row(household, number, 'BIN'))
            alternates = household.get('payrollAlternates') or household.get('alternates') or []
            if not isinstance(alternates, list):
                raise ValueError('Invalid alternate records.')
            rows.extend(person_row(person, number, f'ALT-{index}') for index, person in enumerate(alternates, 1))
        result.update(rows=rows, cycles=count_household_ids(data), data=data)
    except OSError:
        result['error'] = 'Could not read this file.'
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        result['error'] = 'Invalid file, decryption failed, or unsupported payroll structure.'
    return result


class Viewer(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('Payroll BIN File Viewer — Desktop')
        self.geometry('1180x760')
        self.minsize(850, 550)
        self.configure(bg='#f4f7f6')
        self.files = []
        self.visible_rows = {}
        self.inbox = queue.Queue()
        self.busy = False
        self.details_windows = []
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('TFrame', background='#f4f7f6')
        style.configure('TLabel', background='#f4f7f6', foreground='#20302c', font=('Segoe UI', 10))
        style.configure('Title.TLabel', font=('Segoe UI', 24, 'bold'))
        style.configure('Summary.TLabel', font=('Segoe UI', 11, 'bold'), foreground='#14745d')
        style.configure('TButton', font=('Segoe UI', 10), padding=8)
        style.configure('Treeview', rowheight=29, font=('Segoe UI', 10))
        style.configure('Treeview.Heading', font=('Segoe UI', 10, 'bold'))
        outer = ttk.Frame(self, padding=22)
        outer.pack(fill='both', expand=True)
        ttk.Label(outer, text='Payroll BIN File Viewer', style='Title.TLabel').pack(anchor='w')
        ttk.Label(outer, text='DESKTOP  •  Local processing  •  No server required').pack(anchor='w', pady=(4, 18))
        actions = ttk.Frame(outer)
        actions.pack(fill='x')
        self.upload = ttk.Button(actions, text='Open BIN files…', command=self.choose)
        self.upload.pack(side='left')
        self.clear_button = ttk.Button(actions, text='Clear all', command=self.clear)
        self.clear_button.pack(side='left', padx=8)
        ttk.Button(actions, text='Export CSV…', command=lambda: self.export('csv')).pack(side='right')
        ttk.Button(actions, text='Export Excel…', command=lambda: self.export('xlsx')).pack(side='right', padx=8)
        self.summary = tk.StringVar(value='Open one or multiple BIN files to get started.')
        ttk.Label(outer, textvariable=self.summary, style='Summary.TLabel', wraplength=1050).pack(anchor='w', pady=18)
        filters = ttk.Frame(outer)
        filters.pack(fill='x', pady=(0, 14))
        ttk.Label(filters, text='Search:').pack(side='left')
        self.query = tk.StringVar()
        ttk.Entry(filters, textvariable=self.query, width=30).pack(side='left', padx=8)
        ttk.Label(filters, text='Type:').pack(side='left')
        self.role = tk.StringVar(value='All')
        ttk.Combobox(filters, textvariable=self.role, values=['All', 'BIN', 'ALT-1', 'ALT-2', 'All alternates'], state='readonly', width=14).pack(side='left', padx=8)
        self.status = tk.StringVar(value='All fingerprints')
        ttk.Combobox(filters, textvariable=self.status, values=['All fingerprints', 'Present', 'Missing', 'Has FMR signature', 'Invalid / non-FMR'], state='readonly', width=19).pack(side='left')
        for variable in (self.query, self.role, self.status):
            variable.trace_add('write', lambda *_: self.render_rows())
        panes = ttk.Panedwindow(outer, orient='horizontal')
        panes.pack(fill='both', expand=True)
        left = ttk.Frame(panes)
        right = ttk.Frame(panes)
        panes.add(left, weight=1)
        panes.add(right, weight=4)
        ttk.Label(left, text='UPLOADED FILES').pack(anchor='w', pady=(0, 8))
        self.file_list = tk.Listbox(left, font=('Segoe UI', 10), width=30, exportselection=False)
        self.file_list.pack(fill='both', expand=True)
        self.file_list.bind('<<ListboxSelect>>', lambda _: self.render_rows())
        ttk.Button(left, text='View full file data', command=self.full_details).pack(fill='x', pady=8)
        self.file_summary = tk.StringVar()
        ttk.Label(right, textvariable=self.file_summary, wraplength=800).pack(anchor='w', pady=(0, 8))
        self.verification = tk.StringVar()
        ttk.Label(right, textvariable=self.verification, style='Summary.TLabel', wraplength=760).pack(anchor='w', pady=(0, 12))
        self.template_summary = tk.StringVar()
        ttk.Label(right, textvariable=self.template_summary, wraplength=760).pack(anchor='w', pady=(0, 10))
        table_frame = ttk.Frame(right)
        table_frame.pack(fill='both', expand=True)
        columns = ('Household', 'Role', 'Name', 'Photo', 'Fingerprints', 'Status', 'FMR signatures', 'Invalid / non-FMR')
        self.table = ttk.Treeview(table_frame, columns=columns, show='headings', selectmode='browse')
        for column in columns:
            self.table.heading(column, text=column)
            self.table.column(column, width=160 if column in ('Name', 'Status') else 100, minwidth=70)
        vertical = ttk.Scrollbar(table_frame, orient='vertical', command=self.table.yview)
        horizontal = ttk.Scrollbar(table_frame, orient='horizontal', command=self.table.xview)
        self.table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.table.grid(row=0, column=0, sticky='nsew')
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        self.table.bind('<Double-1>', lambda _: self.row_details())
        ttk.Button(right, text='View selected person details', command=self.row_details).pack(anchor='e', pady=8)
        self.notice = tk.StringVar(value='Exports include all records in the selected file, or all files when Combined is selected.')
        ttk.Label(outer, textvariable=self.notice, wraplength=1050).pack(anchor='w', pady=(12, 0))
        self.after(100, self.poll)
        self.protocol('WM_DELETE_WINDOW', self.destroy)
        self.refresh_files()

    def choose(self):
        paths = filedialog.askopenfilenames(title='Open payroll BIN files', filetypes=[('BIN files', '*.bin'), ('All files', '*.*')])
        if not paths:
            return
        if len(paths) + len(self.files) > 50:
            messagebox.showerror('File limit', 'Maximum 50 files. Clear results before opening more.')
            return
        self.busy = True
        self.upload.state(['disabled'])
        self.clear_button.state(['disabled'])
        self.notice.set(f'Processing {len(paths)} file(s)…')
        def work():
            for path in paths:
                self.inbox.put(('file', load_file(path)))
            self.inbox.put(('done', None))
        threading.Thread(target=work, daemon=True).start()

    def poll(self):
        try:
            while True:
                kind, result = self.inbox.get_nowait()
                if kind == 'file':
                    self.files.append(result)
                    self.refresh_files()
                else:
                    self.busy = False
                    self.upload.state(['!disabled'])
                    self.clear_button.state(['!disabled'])
                    self.notice.set('Processing complete. Double-click a person to view full details.')
        except queue.Empty:
            pass
        self.after(100, self.poll)

    def refresh_files(self):
        chosen = self.file_list.curselection()
        self.file_list.delete(0, 'end')
        self.file_list.insert('end', 'Combined — all successful files')
        for item in self.files:
            self.file_list.insert('end', ('FAILED: ' if item.get('error') else 'OK: ') + item['name'])
        self.file_list.selection_set(chosen[0] if chosen and chosen[0] <= len(self.files) else 0)
        good = [f for f in self.files if not f.get('error')]
        rows = [r for f in good for r in f['rows']]
        count = sum(r['fingerprints'] > 0 for r in rows)
        coverage = f'{count / len(rows):.0%}' if rows else '—'
        self.summary.set(f'{len(self.files)} files · {len(good)} successful · {len(self.files)-len(good)} failed   |   {sum(r["role"] == "BIN" for r in rows)} households   |   Fingerprints: {coverage}   |   {sum(f["cycles"] for f in good)} payment cycle entries')
        self.render_rows()

    def selection(self):
        chosen = self.file_list.curselection()
        if chosen and chosen[0] > 0:
            return [self.files[chosen[0] - 1]]
        return self.files

    def render_rows(self):
        if not hasattr(self, 'table'):
            return
        self.table.delete(*self.table.get_children())
        self.visible_rows.clear()
        selected = self.selection()
        all_rows = [row for file in selected if not file.get('error') for row in file['rows']]
        roles = sorted({row['role'] for row in all_rows}, key=lambda role: (role != 'BIN', int(role.split('-')[1]) if role.startswith('ALT-') else 0))
        verification = []
        for role in roles:
            group = [row for row in all_rows if row['role'] == role]
            present = sum(row['fingerprints'] > 0 for row in group)
            status = 'MATCH' if present == len(group) else 'NOT MATCH'
            verification.append(f'{role}: {present}/{len(group)} with fingerprints ({status})')
        self.verification.set(' · '.join(verification))
        self.template_summary.set(f"Template signature check: {sum(row['fmr_count'] for row in all_rows)} FMR · {sum(row['invalid_templates'] for row in all_rows)} invalid / non-FMR. Signature check only." if all_rows else '')
        if len(selected) == 1 and selected[0].get('error'):
            self.file_summary.set(selected[0]['error'])
            return
        query = self.query.get().lower().strip()
        matches = []
        for f in selected:
            for row in f['rows']:
                if query and query not in f'{f["name"]} {row["household"]} {row["name"]} {row["role"]}'.lower():
                    continue
                if self.role.get() == 'All alternates' and not row['role'].startswith('ALT-'):
                    continue
                if self.role.get() not in ('All', 'All alternates', row['role']):
                    continue
                if self.status.get() == 'Present' and not row['fingerprints'] or self.status.get() == 'Missing' and row['fingerprints']:
                    continue
                if self.status.get() == 'Has FMR signature' and not row['fmr_count'] or self.status.get() == 'Invalid / non-FMR' and not row['invalid_templates']:
                    continue
                matches.append((f, row))
        self.file_summary.set(f'{len(matches)} matching records. Source filenames appear in person details.')
        for index, (f, row) in enumerate(matches):
            iid = str(index)
            self.visible_rows[iid] = (f, row)
            self.table.insert('', 'end', iid=iid, values=(row['household'], row['role'], row['name'], 'Present' if row['photo'] else 'Missing', row['fingerprints'], row['status'], row['fmr_count'], row['invalid_templates']))

    def show_details(self, title, data):
        window = tk.Toplevel(self)
        self.details_windows.append(window)
        window.title(title)
        window.geometry('850x620')
        text = tk.Text(window, wrap='word', font=('Consolas', 10))
        scroll = ttk.Scrollbar(window, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        text.pack(fill='both', expand=True)
        text.insert('1.0', json.dumps(data, indent=2, ensure_ascii=False))
        text.configure(state='disabled')

    def row_details(self):
        chosen = self.table.selection()
        if chosen:
            f, row = self.visible_rows[chosen[0]]
            self.show_details(f'{f["name"]} — {row["role"]} — {row["name"]}', {'fingerprintTemplateChecks': row['template_checks'], 'person': row['details']})

    def full_details(self):
        chosen = self.file_list.curselection()
        if not chosen or chosen[0] == 0:
            messagebox.showinfo('Select a file', 'Select an individual file to view its full data.')
            return
        item = self.files[chosen[0] - 1]
        if item.get('error'):
            messagebox.showerror('File failed', item['error'])
        else:
            self.show_details(item['name'], item['data'])

    def clear(self):
        self.files.clear()
        self.visible_rows.clear()
        for window in self.details_windows:
            if window.winfo_exists():
                window.destroy()
        self.details_windows.clear()
        self.refresh_files()
        self.notice.set('All loaded files and decrypted results cleared from this app.')

    def export(self, kind):
        good = [f for f in self.selection() if not f.get('error')]
        if not good:
            messagebox.showinfo('Nothing to export', 'Open a valid BIN file first.')
            return
        path = filedialog.asksaveasfilename(title='Save extracted data', defaultextension='.' + kind, initialfile='payroll.' + kind, filetypes=[('CSV' if kind == 'csv' else 'Excel workbook', '*.' + kind)])
        if not path:
            return
        rows = [HEADERS] + [[f['name'], r['household'], r['role'], r['name'], 'Present' if r['photo'] else 'Missing', r['fingerprints'], r['status'], r['fmr_count'], r['invalid_templates']] for f in good for r in f['rows']]
        try:
            if kind == 'csv':
                with open(path, 'w', newline='', encoding='utf-8-sig') as output:
                    csv.writer(output).writerows([[safe_cell(v) for v in row] for row in rows])
            else:
                Path(path).write_bytes(xlsx(rows).getvalue())
            self.notice.set('Export saved: ' + path)
        except OSError as error:
            messagebox.showerror('Export failed', str(error))


if __name__ == '__main__':
    Viewer().mainloop()
