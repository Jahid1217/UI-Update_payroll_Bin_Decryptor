const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Element {
  constructor() { this.attributes = {}; this.children = []; this.value = ''; this.open = false; this.textContent = ''; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; this.textContent = ''; }
  setAttribute(k, v) { this.attributes[k] = v; }
  removeAttribute(k) { delete this.attributes[k]; }
  hasAttribute(k) { return k in this.attributes; }
  addEventListener() {}
  showModal() { this.open = true; }
  close() { this.open = false; }
}
const elements = new Map();
const element = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
const requests = [];
const context = vm.createContext({
  document: { getElementById: element, createElement: () => new Element(), createTextNode: text => ({ textContent: text }) },
  fetch: async url => {
    requests.push(url);
    let data = { files: [] };
    if (url.includes('/api/data/')) {
      const page = Number(new URL(url, 'http://localhost').searchParams.get('page'));
      data = { text: page === 0 ? '{"start":"first",' : '"end":"last"}', page, start: page * 64000, has_more: page === 0 };
    }
    if (url.includes('/api/progress/')) data = { stage: 'Decrypting file 1 of 1', percent: null };
    return { ok: true, json: async () => data };
  },
  XMLHttpRequest: class {
    constructor() { this.upload = {}; }
    open(method, url) { this.url = url; }
    setRequestHeader() {}
    send() {
      this.upload.onprogress({ lengthComputable: true, loaded: 5, total: 10 });
      assert.equal(element('upload-progress').value, 50);
      assert.match(element('progress-label').textContent, /Uploading: 50%/);
      this.upload.onload();
      setTimeout(() => { this.status = 200; this.responseText = '{"files":[]}'; this.onload(); }, 10);
    }
  },
  FormData: class { append() {} }, crypto: { randomUUID: () => 'test-job' },
  requestAnimationFrame: fn => setTimeout(fn, 0), setTimeout, clearTimeout,
});
vm.runInContext(fs.readFileSync('static/app.js', 'utf8'), context);
(async () => {
  await vm.runInContext('ready', context);
  await vm.runInContext('details("Large file", "/api/data/test?view=full")', context);
  assert.equal(element('details').open, true);
  assert.equal(element('detail-prev').disabled, true);
  assert.equal(element('detail-next').disabled, false);
  assert.match(element('detail-download').href, /download=1/);
  await element('detail-next').onclick();
  assert.equal(element('detail-prev').disabled, false);
  assert.equal(element('detail-next').disabled, true);
  assert.match(element('detail-page').textContent, /End of data/);
  assert.equal(element('detail-body').children.at(-1).textContent, '"end":"last"}');
  await element('process').onclick();
  assert.equal(element('upload-progress').value, 100);
  assert.equal(element('process').disabled, true);
  assert.match(element('progress-label').textContent, /results are ready/);
  assert(requests.some(url => url.includes('/api/progress/test-job')));
  console.log('Client controls passed: section navigation, final content, download, upload progress, polling, completion.');
})().catch(error => { console.error(error); process.exitCode = 1; });
