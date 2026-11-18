const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const html = fs.readFileSync(path.join(__dirname, '../src/module/webroot/index.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const catalogs = script.slice(script.indexOf('const DEFAULT_CONTAINER_PROFILES'), script.indexOf('function updateContainerProfileHint'));
const install = script.slice(script.indexOf('async function installContainerFromUi'), script.indexOf('async function openSettings'));
function setup(result) {
  const calls = [], notices = [];
  const ctx = vm.createContext({ busy: 0, ANCLI: 'ancli', uiLang: 'zh', uiText: k => k,
    document: {getElementById: id => id === 'setContainerProfile' ? {value:'standard'} : {disabled:true}},
    setBusy: () => () => {}, toast: (...args) => notices.push(args),
    runBackground: async cmd => {calls.push(cmd); return {status:'done'};},
    loadContainerSettings: async () => result, loadStatus: async () => { calls.push('status'); },
    loadApps: async () => { calls.push('apps'); } });
  vm.runInContext(catalogs + install, ctx);
  return {ctx,calls,notices};
}
test('install carries the current selection and verifies the applied profile', async () => {
  const {ctx,calls,notices} = setup({profile:'standard',mirror:'tuna'});
  await ctx.installContainerFromUi();
  assert.deepEqual(calls,['ancli container install --profile=standard --mirror=tuna','status','apps']);
  assert.equal(notices[0][0], '容器规格应用完成');
});
test('first-run selection is passed explicitly and mismatch is an error', async () => {
  const {ctx,calls,notices} = setup({profile:'minimal'});
  await ctx.installContainerFromUi('developer');
  assert.deepEqual(calls,['ancli container install --profile=developer --mirror=tuna','status','apps']);
  assert.equal(notices[0][1], 'err');
});
test('all profile package lists match the source catalog', () => {
  const {ctx} = setup({});
  const actual = vm.runInContext('JSON.stringify(CONTAINER_PACKAGES)', ctx);
  const catalog = JSON.parse(fs.readFileSync(path.join(__dirname, '../src/container-profiles.json'), 'utf8'));
  for (const [id, packages] of Object.entries(JSON.parse(actual))) assert.deepEqual(packages, catalog.profiles[id].packages);
});
test('status refresh re-renders the initialization card after readiness changes', async () => {
  const loadStatus = script.slice(script.indexOf('async function loadStatus()'), script.indexOf('let lastCheck ='));
  const rendered = [];
  const ctx = vm.createContext({ latestStatus: {rootfs_ready:false}, currentApps:[{id:'codex-cli'}],
    ancli: async () => JSON.stringify({rootfs_ready:true, ancli_version:'test', installed_count:0}),
    document:{getElementById:()=>({textContent:''})}, renderStatusText:()=>{}, setStat:()=>{},
    formatCount: x=>x, renderContainerSettings:()=>{}, renderApps: apps=>rendered.push(apps), logLine:()=>{} });
  vm.runInContext(loadStatus, ctx);
  await ctx.loadStatus();
  assert.equal(ctx.latestStatus.rootfs_ready, true);
  assert.equal(rendered.length, 1);
  assert.equal(rendered[0][0].id, 'codex-cli');
});
