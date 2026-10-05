const $ = id => document.getElementById(id);
let files = [], selected = [], busy = false;
const pages = {};
function node(tag, text, cls) { const el = document.createElement(tag); if (text !== undefined) el.textContent = text; if (cls) el.className = cls; return el; }
function pick(list) { selected = Array.from(list); $('selection').textContent = selected.length ? `${selected.length} file(s) selected · ${(selected.reduce((n,f)=>n+f.size,0)/1048576).toFixed(2)} MB` : 'No files selected'; $('process').disabled = busy || !selected.length; }
$('drop').onclick = () => { if (!busy) $('picker').click(); };
$('drop').onkeydown = e => { if (['Enter',' '].includes(e.key)) { e.preventDefault(); if (!busy) $('picker').click(); } };
$('picker').onchange = e => pick(e.target.files);
['dragover','dragleave','drop'].forEach(type => $('drop').addEventListener(type,e => { e.preventDefault(); $('drop').classList.toggle('drag',type==='dragover'); if(type==='drop' && !busy) pick(e.dataTransfer.files); }));
async function api(url, options={}) { const res = await fetch(url,options); const data = await res.json(); if(!res.ok) throw new Error(data.error || 'Request failed'); return data; }
function setBusy(value) { busy=value; $('process').disabled=value || !selected.length; $('clear').disabled=value; $('picker').disabled=value; $('process').textContent=value ? 'Decrypting…' : 'Decrypt & view data →'; }
$('process').onclick = async () => { const form = new FormData(); selected.forEach(f=>form.append('files',f)); setBusy(true); $('message').textContent=''; try { files=(await api('/api/upload',{method:'POST',headers:{'X-Viewer-Request':'1'},body:form})).files; pick([]); $('picker').value=''; render(); } catch(e) { $('message').textContent=e.message; } finally {setBusy(false);} };
$('clear').onclick = async () => { setBusy(true); try {await api('/api/clear',{method:'POST',headers:{'X-Viewer-Request':'1'}}); files=[]; selected=[]; $('picker').value=''; pick([]); $('details').close(); $('detail-body').textContent=''; $('message').textContent=''; render();} catch(e){$('message').textContent=e.message;} finally{setBusy(false);} };
function isWarningResult(value) { return /^(invalid\s*\/\s*non-fmr|other)$/i.test(String(value).trim()); }
function details(title,data) {
 $('detail-title').textContent=title;
 const body=$('detail-body'), json=JSON.stringify(data,null,2);body.replaceChildren();
 let start=0;
 for(const match of json.matchAll(/"(?:Invalid\s*\/\s*Non-FMR|Other)"/gi)){body.append(document.createTextNode(json.slice(start,match.index)),node('span',match[0],'warning-result'));start=match.index+match[0].length;}
 body.append(document.createTextNode(json.slice(start)));$('details').showModal();
}
$('close').onclick=()=>{$('details').close(); $('detail-body').textContent='';};
['search','role','status'].forEach(id=>$(id).addEventListener('input',()=>{Object.keys(pages).forEach(k=>delete pages[k]);render();}));
function render() {
 const good=files.filter(f=>!f.error), rows=good.flatMap(f=>f.rows), primary=rows.filter(r=>r.role==='BIN'), present=rows.filter(r=>r.fingerprints>0).length;
 $('file-count').textContent=files.length; $('success-count').textContent=`${good.length} successful · ${files.length-good.length} failed`;
 $('household-count').textContent=primary.length; $('coverage').textContent=rows.length ? `${Math.round(present/rows.length*100)}%` : '—';
 $('missing-count').textContent=`${rows.length-present} people missing fingerprints`; $('cycle-count').textContent=good.reduce((n,f)=>n+f.cycles,0);
 $('files').replaceChildren();
 if(!files.length){$('files').append(node('div','Your files will appear here after decryption.','empty'));return;}
 const query=$('search').value.toLowerCase().trim(), role=$('role').value, status=$('status').value;
 files.forEach(f=>{
 const section=node('article',undefined,'file'), head=node('div',undefined,'file-header'), title=node('div');
 title.append(node('h3',f.name),node('span',f.error?'Failed':'Decrypted',`pill${f.error?' error':''}`));
 if(!f.error){ const households=f.rows.filter(r=>r.role==='BIN').length, alternates=f.rows.filter(r=>r.role.startsWith('ALT-')).length;title.append(node('p',`${households} Households \u00b7 ${alternates} Alternates \u00b7 ${f.cycles} Payment Cycle Entries`,'file-count-summary')); }
 head.append(title);section.append(head);
 if(f.error){section.append(node('p',f.error,'error-text'));$('files').append(section);return;}
 const actions=node('div',undefined,'actions'), full=node('button','View full data','subtle');full.onclick=()=>details(f.name,f.data);actions.append(full);
 ['csv','xlsx'].forEach(kind=>{const a=node('a',kind==='csv'?'Download CSV':'Download Excel');a.href=`/api/export/${kind}?file=${encodeURIComponent(f.id)}`;actions.append(a);});head.append(actions);
 const roles=[...new Set(f.rows.map(r=>r.role))].sort((a,b)=>a==='BIN'?-1:b==='BIN'?1:a.localeCompare(b,undefined,{numeric:true}));
 const verification=roles.map(role=>{const group=f.rows.filter(r=>r.role===role), present=group.filter(r=>r.fingerprints>0).length;return `${role}: ${present}/${group.length} with fingerprints (${present===group.length?'MATCH':'NOT MATCH'})`;}).join(' \u00b7 ');
 const summary=node('p',verification,'fingerprint-summary');section.append(summary);

 const invalidCount=f.rows.reduce((n,r)=>n+r.invalid_templates,0);
 const templateSummary=node('p',undefined,'template-summary');templateSummary.append(document.createTextNode(`Template signature check: ${f.rows.reduce((n,r)=>n+r.fmr_count,0)} FMR signatures \u00b7 `),node('span',`${invalidCount} invalid / non-FMR`,invalidCount>0?'warning-result':undefined),document.createTextNode('. Signature check only.'));section.append(templateSummary);
 const filtered=f.rows.filter(r=>(!query || `${f.name} ${r.household} ${r.name} ${r.role}`.toLowerCase().includes(query)) && (!role || (role==='ALT'?r.role.startsWith('ALT-'):r.role===role)) && (!status || (status==='present'?r.fingerprints>0:r.fingerprints===0)));
 const page=Math.min(pages[f.id]||0,Math.max(0,Math.ceil(filtered.length/50)-1));pages[f.id]=page;
 const wrap=node('div',undefined,'table-wrap'), table=node('table'), thead=node('thead'), tr=node('tr');['Household','Type','Name','Photo','Fingerprints','Status','FMR signatures','Invalid / non-FMR','Details'].forEach(t=>{const th=node('th',t);th.scope='col';tr.append(th);});thead.append(tr);table.append(thead);const tbody=node('tbody');
 filtered.slice(page*50,page*50+50).forEach(r=>{const line=node('tr');[r.household,r.role,r.name,r.photo?'Present':'Missing',r.fingerprints].forEach(v=>line.append(node('td',v)));const cell=node('td');cell.append(node('span',r.status,`pill${isWarningResult(r.status)?' warning-result':r.fingerprints?'':' missing'}`));line.append(cell);line.append(node('td',r.fmr_count),node('td',r.invalid_templates,r.invalid_templates>0?'warning-result':undefined));const detail=node('td'), btn=node('button','View','detail-button');btn.onclick=()=>details(`${r.role} · ${r.name}`,{fingerprintTemplateChecks:r.template_checks,person:r.details});detail.append(btn);line.append(detail);tbody.append(line);});table.append(tbody);wrap.append(table);section.append(wrap);
 if(!filtered.length)section.append(node('div','No records match the filters.','empty'));
 const pager=node('div',undefined,'pagination');pager.append(node('span',`${filtered.length} matching records · Page ${page+1} of ${Math.max(1,Math.ceil(filtered.length/50))}`));
 [['Previous',-1],['Next',1]].forEach(([label,delta])=>{const b=node('button',label,'subtle');b.disabled=delta<0?page===0:(page+1)*50>=filtered.length;b.onclick=()=>{pages[f.id]=page+delta;render();};pager.append(b);});section.append(pager);$('files').append(section);
 });
}
api('/api/files').then(data=>{files=data.files;render();}).catch(e=>$('message').textContent=e.message);
