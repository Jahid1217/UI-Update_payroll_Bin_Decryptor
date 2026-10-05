const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
class Element {
 constructor() { this.children=[]; this.textContent=''; this.value=''; this.style={}; this.classList={toggle(){}}; }
 append(...items) { this.children.push(...items); }
 replaceChildren(...items) { this.children=items; }
 addEventListener() {}
}
const elements={};
const context=vm.createContext({
 document:{getElementById:id=>elements[id] ||= new Element(),createElement:()=>new Element(),createTextNode:text=>({textContent:text})},
 fetch:()=>new Promise(()=>{}),
});
vm.runInContext(fs.readFileSync('static/app.js','utf8'),context);
const input=JSON.parse(fs.readFileSync(0,'utf8'));
context.input=input;
vm.runInContext('files=input.files;render();',context);
function collect(el,cls) { return (el.className===cls?[el.textContent]:[]).concat(...el.children?.map(child=>collect(child,cls)) || []); }
assert.deepEqual(collect(elements.files,'file-count-summary'),input.counts);
assert.deepEqual(collect(elements.files,'fingerprint-summary'),input.verification);
// Table filters must not change the source summaries.
elements.search.value='no matching person';
elements.role.value='ALT-2';
elements.status.value='missing';
vm.runInContext('render();',context);
assert.deepEqual(collect(elements.files,'file-count-summary'),input.counts);
assert.deepEqual(collect(elements.files,'fingerprint-summary'),input.verification);
