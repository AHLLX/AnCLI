const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(require('node:path').join(__dirname, '../src/module/webroot/index.html'), 'utf8');
const fn = html.slice(html.indexOf('async function installDownloadedModule('), html.indexOf('  moduleDownloadBusy = true;', html.indexOf('async function installDownloadedModule(')));
const match = fn.match(/if \(!\/(.+?)\/\.test\(path \|\| ''\)\)/);
test('module update installer accepts only named ZIPs in Download', () => {
  assert.ok(match, 'download path validation was not found');
  const re = vm.runInNewContext('/' + match[1] + '/');
  assert.ok(re.test('/sdcard/Download/ancli-43-v1.2.631-beta.zip'));
  assert.equal(re.test('/sdcard/Download/ancli-43-v1.2.631-beta.zip/../../other'), false);
  assert.equal(re.test('/sdcard/Download/../../ancli-43-v1.2.631-beta.zip'), false);
  assert.equal(re.test('/data/local/tmp/ancli/module-updates/ancli-43-v1.2.631-beta.zip'), false);
});
