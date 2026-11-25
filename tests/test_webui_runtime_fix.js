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
  assert.match(script,/let ANCLI = '\/data\/local\/tmp\/ancli\/bin\/ancli';/);
  const ctx=vm.createContext({ANCLI:'/data/local/tmp/ancli/bin/ancli',execCmd:async cmd=>{assert.match(cmd,/container status --json/);return {code:0,stdout:'/data/local/tmp/ancli/bin/ancli\n'};}});
  vm.runInContext(resolver,ctx); assert.equal(await ctx.resolveAncli(),'/data/local/tmp/ancli/bin/ancli');
});
test('only container install and app install/update are cancellable',()=>{
  const ctx=vm.createContext({});vm.runInContext(cancellable,ctx);
  for(const cmd of ['/data/adb/ksu/bin/ancli install codex-cli','ancli update agy','ancli container install --profile=minimal']) assert.equal(ctx.taskCancellable(cmd),true,cmd);
  for(const cmd of ['ancli reset','ancli uninstall codex-cli','ancli check','ancli container status --json']) assert.equal(ctx.taskCancellable(cmd),false,cmd);
});
test('successful module ZIP download automatically starts KernelSU installation',async()=>{
  const startAt=script.indexOf('async function startModuleDownload(');
  const start=script.slice(startAt,script.indexOf("document.getElementById('chanStable')",startAt));
  const els={
    aboutCheckResult:{},
    btnDownloadModule:{disabled:false,textContent:'',onclick:null},
    aboutDownloadProgress:{hidden:true},
    aboutDownloadMessage:{textContent:''},
  };
  let installPath='';
  const ctx=vm.createContext({
    document:{getElementById:id=>els[id]}, moduleDownloadBusy:false,
    data:{zip_url:'https://github.com/AHLLX/AnCLI/releases/download/v1.2.656-beta/ancli-68-v1.2.656-beta.zip'},
    currentModuleChannel:'beta', ANCLI:'ancli',
    setModuleCheckControls:()=>{}, setBusy:()=>()=>{}, uiText:key=>key,
    shQuote:value=>"'"+String(value).replace(/'/g,"'\\''")+"'",
    updateModuleDownloadProgress:()=>{}, consumeModuleDownloadOutput:()=>{},
    runBackground:async()=>({status:'done',moduleDownloadPath:'/sdcard/Download/ancli-68-v1.2.656-beta.zip'}),
    installDownloadedModule:async path=>{installPath=path;},
  });
  vm.runInContext(start,ctx);
  await ctx.startModuleDownload(ctx.data);
  assert.equal(installPath,'/sdcard/Download/ancli-68-v1.2.656-beta.zip');
  assert.equal(els.aboutDownloadMessage.textContent,'aboutDownloadDone');
});
test('KernelSU install stages the verified ZIP outside shared storage and keeps retry available',async()=>{
  const startAt=script.indexOf('async function installDownloadedModule(');
  const endAt=script.indexOf('async function startModuleDownload(',startAt);
  const install=script.slice(startAt,endAt);
  let command='';
  const button={disabled:false,textContent:'',onclick:null};
  const message={textContent:''};
  const ctx=vm.createContext({
    moduleDownloadBusy:false, Date, setTimeout:()=>{}, window:{location:{reload:()=>{}}},
    shQuote:value=>"'"+String(value).replace(/'/g,"'\\''")+"'",
    setModuleCheckControls:()=>{}, setBusy:()=>()=>{}, uiText:key=>key,
    setModuleUpdateIndicator:()=>{},
    runBackground:async cmd=>{command=cmd;return {status:'done'};},
  });
  vm.runInContext(install,ctx);
  await ctx.installDownloadedModule('/sdcard/Download/ancli-68-v1.2.656-beta.zip',button,message);
  assert.match(command,/cp '\/sdcard\/Download\/ancli-68-v1\.2\.656-beta\.zip' '\/data\/local\/tmp\/ancli-update-/);
  assert.match(command,/ksud module install '\/data\/local\/tmp\/ancli-update-/);
  assert.equal(message.textContent,'aboutInstallDone');
});
