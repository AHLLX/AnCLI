const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const test=require('node:test');
const assert=require('node:assert/strict');
const html=fs.readFileSync(path.join(__dirname,'../src/module/webroot/index.html'),'utf8');
const script=html.match(/<script>([\s\S]*?)<\/script>/)[1];
const resolver=script.slice(script.indexOf('async function resolveAncli()'),script.indexOf('let currentApps'));
const cancellable=script.slice(script.indexOf('function taskCancellable('),script.indexOf('function taskReadOnly('));
test('first load probes executable host path instead of relying on PATH',async()=>{
  const ctx=vm.createContext({ANCLI:'ancli',execCmd:async cmd=>{assert.match(cmd,/container status --json/);return {code:0,stdout:'/data/adb/ksu/bin/ancli\n'};}});
  vm.runInContext(resolver,ctx); assert.equal(await ctx.resolveAncli(),'/data/adb/ksu/bin/ancli');
});
test('only container install and app install/update are cancellable',()=>{
  const ctx=vm.createContext({});vm.runInContext(cancellable,ctx);
  for(const cmd of ['/data/adb/ksu/bin/ancli install codex-cli','ancli update agy','ancli container install --profile=minimal']) assert.equal(ctx.taskCancellable(cmd),true,cmd);
  for(const cmd of ['ancli reset','ancli uninstall codex-cli','ancli check','ancli container status --json']) assert.equal(ctx.taskCancellable(cmd),false,cmd);
});
