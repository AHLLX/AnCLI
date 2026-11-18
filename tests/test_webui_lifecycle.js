const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '../src/module/webroot/index.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const lifecycle = script.slice(script.indexOf('const TASK_WAIT_MS'), script.indexOf('// 2026-11-13: Keep task history'));
const recovery = script.slice(script.indexOf('async function restoreTasks'), script.indexOf('// Launch a declared service'));
const tabs = script.slice(script.indexOf('async function closeTask'), script.indexOf('async function selectTask'));
const quote = script.slice(script.indexOf('function shQuote'), script.indexOf('async function dshPortInUse'));

function page(execCmd, { realTime = false } = {}) {
  let now = 1794850000000;
  const DateStub = { now: () => now };
  const context = vm.createContext({
    Date: realTime ? Date : DateStub, TextEncoder, setTimeout, clearTimeout, execCmd,
    TASK_POLL_MS: realTime ? 15 : 450, BRIDGE_COMMAND_TIMEOUT_MS: 20000,
    sleep: realTime ? ms => new Promise(resolve => setTimeout(resolve, ms)) : async ms => { now += ms; },
    strip: value => value, appendTaskOutput(task, value) { (task.output ||= []).push(value); },
    visibleTaskOutput: (_, value) => value, uiText: key => key, toast() {},
    renderTaskTabs() {}, renderApps() {}, clearLogView() {}, appendLogRow() {}, selectTask() {},
    expandedAppLogs: new Set(), currentApps: [], taskTabs: new Map(), activeTaskId: 'session', logFollow: true,
  });
  const code = realTime ? lifecycle.replace('/data/local/tmp/ancli/.webui_', './.webui_') : lifecycle;
  vm.runInContext(code + recovery + tabs + quote, context);
  return context;
}

function result(stdout = '', code = 0) {
  return { stdout, stderr: '', code };
}

test('timeout retains the task across close, refresh and attempts to start another mutation', async () => {
  let record;
  let exit = 'pending';
  let launches = 0;
  const ctx = page(async command => {
    if (command.includes('boot_time=')) return result('boot-a\t\n' + (record ? exit + '\t' + JSON.stringify(record) + '\n' : '') + '__ANCLI_TASKS_END__\n');
    if (command.includes('nohup')) {
      launches++;
      record = { ...Array.from(ctx.taskTabs.values()).at(-1), bootId: 'boot-a' };
      return result();
    }
    assert.match(command, /echo pending/);
    return result(exit + '\n\n__ANCLI_POLL_END__');
  });
  const task = await ctx.runBackground('ancli repair', '', { timeoutMs: 1000 });
  assert.equal(task.status, 'unknown');
  await ctx.closeTask(task.id);
  assert.equal(ctx.taskTabs.has(task.id), true);
  assert.equal((await ctx.runBackground('ancli reset --yes')).status, 'blocked');
  assert.equal(launches, 1);
  ctx.taskTabs.clear(); // Reopening WebUI reconstructs the guard from disk.
  await ctx.restoreTasks();
  assert.equal(ctx.taskTabs.get(task.id).status, 'unknown');
  exit = '0';
  await ctx.restoreTasks();
  assert.equal(ctx.taskTabs.get(task.id).status, 'done');
  assert.equal((await ctx.runBackground('ancli repair')).status, 'done');
  assert.equal(launches, 2);
});

test('communication failures stop polling and a lost launch reply is never replayed', async () => {
  let polls = 0;
  let loseLaunch = false;
  const ctx = page(async (command, _, retryHttp) => {
    if (command.includes('boot_time=')) return result('boot-a\t\n__ANCLI_TASKS_END__\n');
    if (command.includes('nohup')) {
      assert.equal(retryHttp, false);
      if (loseLaunch) throw new Error('callback lost');
      return result();
    }
    polls++;
    throw new Error('connection lost');
  });
  const first = await ctx.runBackground('ancli repair');
  assert.equal(first.status, 'unknown');
  assert.equal(polls, 3);
  ctx.taskTabs.clear();
  loseLaunch = true;
  const second = await ctx.runBackground('ancli repair');
  assert.equal(second.status, 'unknown');
  assert.equal(polls, 3);
});

test('a new boot resolves interrupted tasks without inventing an exit code', async () => {
  const id = '1794850000000_123';
  const logFile = '/data/local/tmp/ancli/.webui_' + id + '.log';
  const meta = { id, command: 'ancli repair', logFile, bootId: 'old-boot' };
  const ctx = page(async () => result('new-boot\t\npending\t' + JSON.stringify(meta) + '\n__ANCLI_TASKS_END__\n'));
  await ctx.restoreTasks();
  assert.equal(ctx.taskTabs.get(id).status, 'failed');
  assert.equal(ctx.taskTabs.get(id).interrupted, true);
});

test('empty history works when the bridge strips trailing whitespace; incomplete history blocks mutations', async () => {
  let response = 'boot-a\t123\n__ANCLI_TASKS_END__';
  let launched = 0;
  const ctx = page(async command => {
    if (command.includes('boot_time=')) return result(response);
    if (command.includes('nohup')) { launched++; return result(); }
    return result('0\n\n__ANCLI_POLL_END__');
  });
  assert.equal((await ctx.runBackground('ancli install codex-cli')).status, 'done');
  assert.equal(launched, 1);
  ctx.taskTabs.clear();
  response = 'boot-a\t123'; // A successful but truncated bridge response.
  assert.equal((await ctx.runBackground('ancli repair')).status, 'blocked');
  assert.equal(launched, 1);
});

test('an expired command in the bridge queue is not dispatched when an earlier command completes', async () => {
  let now = 0;
  let finishEarlier;
  const dispatched = [];
  const context = vm.createContext({
    Date: { now: () => now }, setTimeout, clearTimeout,
    execCmdDirect(command) {
      dispatched.push(command);
      if (command === 'earlier') return new Promise(resolve => { finishEarlier = resolve; });
      return Promise.resolve(result());
    },
  });
  const queueCode = script.slice(script.indexOf('let bridgeExecTail'), script.indexOf('const sh = execCmd;'));
  const directStart = queueCode.indexOf('async function execCmdDirect');
  const directEnd = queueCode.indexOf('function execCmd(cmd');
  vm.runInContext(queueCode.slice(0, directStart) + queueCode.slice(directEnd), context);
  const earlier = context.execCmd('earlier');
  const expired = context.execCmd('must not dispatch', 1);
  const rejection = assert.rejects(expired, /超时/);
  await Promise.resolve();
  now = 2;
  finishEarlier(result());
  await earlier;
  await rejection;
  await context.execCmd('refresh');
  assert.deepEqual(dispatched, ['earlier', 'refresh']);
});

const bash = process.platform === 'win32'
  ? path.join(process.env.ProgramFiles || 'C:/Program Files', 'Git/bin/bash.exe')
  : '/bin/bash';

test('real shell publishes metadata and preserves exit codes, percent signs and compound command output', { skip: !fs.existsSync(bash) }, async () => {
  const cwd = fs.mkdtempSync(path.join(os.tmpdir(), 'ancli-webui-'));
  function shell(command) {
    return new Promise((resolve, reject) => {
      const child = spawn(bash, ['-s'], { cwd });
      let stdout = '', stderr = '';
      child.stdout.setEncoding('utf8').on('data', chunk => { stdout += chunk; });
      child.stderr.setEncoding('utf8').on('data', chunk => { stderr += chunk; });
      child.on('error', reject);
      child.on('close', code => resolve({ code, stdout, stderr }));
      child.stdin.end(command + '\n');
    });
  }
  try {
    const ctx = page(shell, { realTime: true });
    const good = await ctx.runBackground("if true; then printf '%s\\n' '100% ready'; fi", '', { timeoutMs: 8000 });
    assert.equal(good.status, 'done');
    assert.match(good.output.join(''), /100% ready/);
    const meta = JSON.parse(fs.readFileSync(path.join(cwd, path.basename(good.metaFile)), 'utf8'));
    assert.equal(meta.command, good.command);
    const bad = await ctx.runBackground('if true; then exit 7; fi', '', { timeoutMs: 8000 });
    assert.equal(bad.status, 'failed');
    assert.equal(fs.readFileSync(path.join(cwd, path.basename(bad.exitFile)), 'utf8').trim(), '7');
    ctx.taskTabs.clear();
    await ctx.restoreTasks();
    assert.equal(ctx.taskTabs.get(good.id).status, 'done');
    assert.equal(ctx.taskTabs.get(bad.id).status, 'failed');
  } finally {
    for (const file of fs.readdirSync(cwd)) fs.unlinkSync(path.join(cwd, file));
    fs.rmdirSync(cwd);
  }
});

test('uninstall runner retains active log and schedules cleanup only after success', async () => {
  const { spawnSync } = require('node:child_process');
  const bash = process.platform === 'win32' ? 'C:/Program Files/Git/bin/bash.exe' : '/bin/bash';
  let launch = '';
  const ctx = page(async command => {
    if (command.includes('boot_time=')) return result('boot-a\t1234567890\n__ANCLI_TASKS_END__\n');
    if (command.includes('ANCLI_UNINSTALL_LOG=')) { launch = command; return result(); }
    if (command.includes('echo pending')) return result('0\n\n__ANCLI_POLL_END__');
    throw new Error('unexpected command: ' + command);
  });
  const task = await ctx.runBackground('( echo uninstall )', '', {moduleUninstall: true, timeoutMs: 2000});
  assert.equal(task.status, 'done');
  assert.match(launch, /ANCLI_UNINSTALL_LOG=/);
  assert.match(launch, /sleep 60/);
  assert.match(launch, /\$rc/);
  const syntax = spawnSync(bash, ['-n', '-c', launch], {encoding: 'utf8'});
  assert.equal(syntax.status, 0, syntax.stderr);
});
