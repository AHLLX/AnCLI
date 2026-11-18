/**
 * 专项测试：WebUI busy 指令锁持有者隔离与弹窗确认按钮防双击
 *
 * 约束要求：
 * 1. 提取 src/module/webroot/index.html 真实页面函数测试，不另写一套被测实现
 * 2. 覆盖：两锁并发、重复释放、异常释放、按钮双击与异常恢复
 * 3. 验证禁止 setBusy(false) 盲目弹出最旧锁
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

// 1. 读取并提取真实 index.html 中的 <script>
const htmlPath = path.join(__dirname, '..', 'src', 'module', 'webroot', 'index.html');
const htmlContent = fs.readFileSync(htmlPath, 'utf8');
const scriptMatch = htmlContent.match(/<script>([\s\S]*?)<\/script>/);
if (!scriptMatch) {
  throw new Error('未在 src/module/webroot/index.html 中找到 <script> 标签');
}
const realScriptCode = scriptMatch[1];

// 2. 构造轻量 DOM 测试环境
function createTestContext() {
  const elements = new Map();
  function getEl(id) {
    if (!elements.has(id)) {
      elements.set(id, {
        id,
        hidden: id === 'busySpin',
        disabled: false,
        textContent: '',
        value: '',
        options: [{}, {}, {}],
        style: {},
        classList: {
          classes: new Set(),
          toggle(c, v) { if (v) this.classes.add(c); else this.classes.delete(c); },
          add(c) { this.classes.add(c); },
          remove(c) { this.classes.delete(c); },
          contains(c) { return this.classes.has(c); }
        },
        setAttribute() {},
        getAttribute() { return null; },
        addEventListener() {},
        appendChild() {},
        closest() { return null; },
        querySelectorAll() { return []; },
        focus() {}
      });
    }
    return elements.get(id);
  }

  const executedCmds = [];
  const toasts = [];

  const sandbox = {
    console,
    setTimeout,
    clearTimeout,
    Date,
    Math,
    String,
    Number,
    Boolean,
    JSON,
    Promise,
    Set,
    Map,
    TextEncoder,
    localStorage: { getItem: () => null, setItem: () => {} },
    window: {
      addEventListener() {},
      matchMedia() { return { addEventListener() {} }; },
      location: { reload() {} },
      ksu: {
        exec(cmd, opt, cb) {
          executedCmds.push(cmd);
          if (typeof cb === 'string' && typeof sandbox[cb] === 'function') {
            sandbox[cb](0, '', '');
          }
        }
      }
    },
    document: {
      documentElement: getEl('html'),
      body: {
        classList: {
          classes: new Set(),
          toggle(c, v) { if (v) this.classes.add(c); else this.classes.delete(c); },
          add(c) { this.classes.add(c); },
          remove(c) { this.classes.delete(c); },
          contains(c) { return this.classes.has(c); }
        },
        style: {}
      },
      getElementById(id) { return getEl(id); },
      querySelectorAll() {
        return [];
      },
      createElement(tag) { return getEl('el_' + Math.random().toString(36).slice(2, 6)); },
      createDocumentFragment() { return { appendChild() {} }; },
      addEventListener() {}
    },
    toast(msg, kind) {
      toasts.push({ msg, kind });
    }
  };
  sandbox.window.window = sandbox.window;
  sandbox.window.document = sandbox.document;

  // 注入只读测试检查钩子，直接读取被测真实作用域变量 busy 与 activeBusyLocks
  const harnessCode = realScriptCode + '\n;window.__getBusy = () => busy;\nwindow.__getActiveBusyLocks = () => activeBusyLocks;\nwindow.__getCurrentApps = () => currentApps;\n';

  vm.createContext(sandbox);
  vm.runInContext(harnessCode, sandbox);

  return {
    sandbox,
    elements,
    executedCmds,
    toasts,
    getEl,
    getBusy: () => sandbox.window.__getBusy(),
    getActiveBusyLocks: () => sandbox.window.__getActiveBusyLocks(),
    getCurrentApps: () => sandbox.window.__getCurrentApps()
  };
}

// -------------------- 测试套件 --------------------
console.log('>>> 开始执行 WebUI 锁与生命周期测试 (tests/test_webui_locks.js)...');

// 测试用例 1：两锁并发隔离（Task A 与 Task B 并发，A 释放不影响 B，禁止 setBusy(false) 盲目弹出）
(function testConcurrentLocks() {
  console.log('[Case 1] 验证两锁并发隔离与禁止盲目 setBusy(false)...');
  const { sandbox, getEl, getBusy, getActiveBusyLocks } = createTestContext();
  const setBusy = sandbox.setBusy;
  const btnRepair = getEl('btnRepair');
  const busySpin = getEl('busySpin');

  assert.strictEqual(getBusy(), 0, '初始 busy 必须为 0');
  assert.strictEqual(btnRepair.disabled, false, '初始按钮未禁用');
  assert.strictEqual(busySpin.hidden, true, '初始转圈隐藏');

  // Task A 获取锁
  const releaseA = setBusy(true, 'Task A');
  assert.strictEqual(getBusy(), 1, 'Task A 获取后 busy=1');
  assert.strictEqual(getActiveBusyLocks().size, 1);
  assert.strictEqual(btnRepair.disabled, true, '获取锁后按钮被禁用');
  assert.strictEqual(busySpin.hidden, false, '获取锁后显示转圈');

  // Task B 获取锁
  const releaseB = setBusy(true, 'Task B');
  assert.strictEqual(getBusy(), 2, 'Task B 获取后 busy=2');
  assert.strictEqual(getActiveBusyLocks().size, 2);

  // 恶意/旧式 setBusy(false) 盲目调用：必须被禁止弹出最旧锁！
  setBusy(false);
  assert.strictEqual(getBusy(), 2, '调用无 ownerId 的 setBusy(false) 必须不减少锁，不能释放 Task A 或 B');
  assert.strictEqual(getActiveBusyLocks().size, 2);

  // Task A 正常在 finally 中释放自己的锁
  releaseA();
  assert.strictEqual(getBusy(), 1, 'Task A 释放后 busy=1');
  assert.strictEqual(btnRepair.disabled, true, 'Task B 仍在运行，按钮必须保持禁用');
  assert.strictEqual(busySpin.hidden, false, 'Task B 仍在运行，转圈保持显示');

  // Task B 正常释放
  releaseB();
  assert.strictEqual(getBusy(), 0, 'Task B 释放后 busy=0');
  assert.strictEqual(getActiveBusyLocks().size, 0);
  assert.strictEqual(btnRepair.disabled, false, '全部释放后按钮恢复启用');
  assert.strictEqual(busySpin.hidden, true, '全部释放后转圈隐藏');
  console.log('  ✓ Case 1 通过');
})();

// 测试用例 2：重复释放幂等性（同一 release 调用多次不导致计数减为负数，也不影响后续任务）
(function testDuplicateRelease() {
  console.log('[Case 2] 验证重复释放幂等性（防超减、防负数、防误扣）...');
  const { sandbox, getBusy, getActiveBusyLocks } = createTestContext();
  const setBusy = sandbox.setBusy;

  const release1 = setBusy(true, 'Task 1');
  assert.strictEqual(getBusy(), 1);

  // 第一次释放
  release1();
  assert.strictEqual(getBusy(), 0);

  // 重复释放同个 release 函数多次
  release1();
  release1();
  release1();
  assert.strictEqual(getBusy(), 0, '多次重复释放不能导致 busy 变为负数');
  assert.strictEqual(getActiveBusyLocks().size, 0);

  // 再次启动新任务 Task 2
  const release2 = setBusy(true, 'Task 2');
  assert.strictEqual(getBusy(), 1, '新任务获取锁正常');

  // 此时调用旧的 release1：绝对不能影响 Task 2！
  release1();
  assert.strictEqual(getBusy(), 1, '旧任务 release 重复调用绝对不能误释放 Task 2 的锁');
  assert.strictEqual(getActiveBusyLocks().size, 1);

  release2();
  assert.strictEqual(getBusy(), 0);
  console.log('  ✓ Case 2 通过');
})();

// 测试用例 3：异常释放可靠性（执行过程中抛出 Error 时，finally 保证 release 执行且 UI 解锁）
(function testExceptionRelease() {
  console.log('[Case 3] 验证异常发生时 finally 可靠释放锁...');
  const { sandbox, getEl, getBusy } = createTestContext();
  const setBusy = sandbox.setBusy;
  const btnRepair = getEl('btnRepair');

  assert.strictEqual(getBusy(), 0);

  let caughtError = null;
  try {
    const release = setBusy(true, 'Faulty Task');
    try {
      assert.strictEqual(getBusy(), 1);
      assert.strictEqual(btnRepair.disabled, true);
      throw new Error('模拟宿主通信中断或语法崩溃');
    } finally {
      release();
    }
  } catch (err) {
    caughtError = err;
  }

  assert.ok(caughtError, '异常已成功捕获');
  assert.strictEqual(getBusy(), 0, '抛错后 finally 必须确保 busy 归零');
  assert.strictEqual(btnRepair.disabled, false, '抛错后按钮必须恢复可操作');
  console.log('  ✓ Case 3 通过');
})();

// 测试用例 4：弹窗确认按钮防重复点击与异常恢复
(function testConfirmButtonsAntiDoubleClick() {
  console.log('[Case 4] 验证弹窗确认按钮防双击与异常后可恢复...');

  // 4.1 测试 #repairConfirm 防双击与异常恢复
  {
    const { sandbox, getEl, getBusy } = createTestContext();
    const repairConfirm = getEl('repairConfirm');
    sandbox.promptRepair();
    assert.strictEqual(repairConfirm.disabled, false, '弹窗打开时确认按钮必须为 enabled');

    let resolveTask;
    let doRepairCalls = 0;
    sandbox.doRepair = async () => {
      doRepairCalls++;
      const release = sandbox.setBusy(true, '修复中');
      try {
        await new Promise(r => { resolveTask = r; });
      } finally {
        release();
      }
    };

    // 第一次点击：同步置 disabled 并触发 doRepair
    repairConfirm.onclick();
    assert.strictEqual(repairConfirm.disabled, true, '第一次点击后必须同步置为 disabled');
    assert.strictEqual(getBusy(), 1, '进入异步执行后 busy 应为 1');
    assert.strictEqual(doRepairCalls, 1, 'doRepair 已被调用 1 次');

    // 第二次连击（双击）：应当被同步拦截！
    repairConfirm.onclick();
    assert.strictEqual(doRepairCalls, 1, '双击必须被同步阻止，不能触发第二次 doRepair');

    // 异步任务完成
    resolveTask();
  }

  // 4.2 测试 #resetApply 输入校验失败后恢复 disabled=false
  {
    const { sandbox, getEl, getBusy } = createTestContext();
    const resetApply = getEl('resetApply');
    const resetConfirmInput = getEl('resetConfirm');
    sandbox.openReset();
    assert.strictEqual(resetApply.disabled, false, 'openReset 后 resetApply 必须为 enabled');

    // 输入错误的确认词
    resetConfirmInput.value = 'WRONG';
    resetApply.onclick();
    assert.strictEqual(resetApply.disabled, false, '校验失败后 resetApply 必须恢复为 enabled');
    assert.strictEqual(getBusy(), 0, '校验失败不进入忙碌态');

    // 输入正确确认词 'RESET'
    resetConfirmInput.value = 'RESET';
    let resetTriggered = 0;
    let resolveReset;
    sandbox.runBackground = async () => {
      resetTriggered++;
      await new Promise(r => { resolveReset = r; });
      return { status: 'done' };
    };
    resetApply.onclick();
    assert.strictEqual(resetApply.disabled, true, '输入正确点击后同步置为 disabled');
    assert.strictEqual(resetTriggered, 1, 'reset 任务触发');

    // 重复点击同一按钮：被拦截
    resetApply.onclick();
    assert.strictEqual(resetTriggered, 1, '重复点击被拦截');
    resolveReset();
  }

  // 4.3 测试 #cfgSave 防双击与未修改恢复
  {
    const { sandbox, getEl, getCurrentApps } = createTestContext();
    const cfgSave = getEl('cfgSave');
    getCurrentApps().push({ id: 'claude', name: 'Claude Code', required_env_vars: ['ANTHROPIC_API_KEY'] });
    sandbox.openConfig('claude');
    assert.strictEqual(cfgSave.disabled, false, 'openConfig 后 cfgSave 为 enabled');

    // 点击未修改任何配置，触发恢复
    cfgSave.onclick();
    assert.strictEqual(cfgSave.disabled, false, '未修改时按钮必须恢复可点击');
  }

  // 4.4 测试 #uninstallApply 防双击与校验失败恢复
  {
    const { sandbox, getEl, getBusy } = createTestContext();
    const uninstallApply = getEl('uninstallApply');
    const uninstallConfirmInput = getEl('uninstallConfirm');
    sandbox.openUninstall();
    assert.strictEqual(uninstallApply.disabled, false, 'openUninstall 后 uninstallApply 为 enabled');

    // 校验失败
    uninstallConfirmInput.value = 'NO';
    uninstallApply.onclick();
    assert.strictEqual(uninstallApply.disabled, false, '校验失败后恢复 enabled');
  }

  console.log('  ✓ Case 4 通过');
})();

console.log('>>> 所有测试用例执行完毕，全部通过！');
process.exit(0);
