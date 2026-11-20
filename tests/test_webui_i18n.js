const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '../src/module/webroot/index.html'), 'utf8');
function between(a, b) { return html.slice(html.indexOf(a), html.indexOf(b)); }

test('language changes rerender cached status, catalog and check results', () => {
  const elements = Object.fromEntries(['stRootfs', 'stProot', 'stCount', 'stUpdates', 'checkInfo', 'apps'].map(id => [id, { textContent: '', className: '', innerHTML: '' }]));
  const ctx = vm.createContext({
    document: { documentElement: {}, getElementById: id => elements[id] || null, querySelectorAll: () => [] },
    localStorage: { setItem() {} },
    renderTaskTabs() {}, renderContainerProfileOptions() {}, syncModuleUpdateIndicatorText() {}, updateMainProxyBadge() {},
    renderTaskFeedback() {}, revealAppLog() {}, appIconMarkup: () => '',
    taskTabs: new Map(), expandedAppLogs: new Set(), busy: 0,
    currentApps: [{id: 'codex', name: 'Codex', installed: false}],
    latestStatus: {rootfs_ready: true, proot_deployed: true},
    supportsCheck: true, lastCheck: 0, lastCheckFailed: 0, manualCheckRequested: false,
  });
  const code = [
    between('function setStat(', 'let latestStatus = null;'),
    between('function renderStatusText(', 'async function loadStatus()'),
    between('function esc(', 'const UI_TEXT ='),
    between('const UI_TEXT =', "document.querySelectorAll('#langSwitch button').forEach(b => {\n  b.onclick"),
    between('function formatCount(', 'async function toggleAppLog('),
  ].join('\n');
  vm.runInContext(code, ctx);
  ctx.applyLanguage('en');
  assert.equal(elements.stRootfs.textContent, 'Ready');
  assert.equal(elements.stProot.textContent, 'Deployed');
  assert.equal(elements.stCount.textContent, '0');
  assert.equal(elements.stUpdates.textContent, 'None');
  assert.equal(elements.checkInfo.textContent, 'Official versions not checked yet');
  assert.match(elements.apps.innerHTML, /Not installed/);
  assert.doesNotMatch(elements.apps.innerHTML, /未安装/);
  ctx.currentApps = [{id: 'codex', name: 'Codex', installed: true, active: true,
    installed_version: '1.0', version_verified: false, update_available: true,
    cloud_version: '2.0', cloud_source: 'npm:codex', cloud_checked: true, executable: 'codex'}];
  ctx.applyLanguage('en');
  assert.equal(elements.stUpdates.textContent, '1');
  assert.match(elements.apps.innerHTML, /Installed.*Unverified/);
  assert.match(elements.apps.innerHTML, /Update available/);
  assert.match(elements.apps.innerHTML, /Official latest.*Official npm/);
  ctx.currentApps = [{id: 'codex', name: 'Codex', installed: false}];
  ctx.applyLanguage('zh');
  assert.equal(elements.stRootfs.textContent, '就绪');
  assert.equal(elements.stProot.textContent, '已部署');
  assert.equal(elements.stCount.textContent, '0 个');
  assert.equal(elements.stUpdates.textContent, '无');
  assert.equal(elements.checkInfo.textContent, '尚未检查官方版本');
  assert.match(elements.apps.innerHTML, /未安装/);
});
