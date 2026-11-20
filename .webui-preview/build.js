/**
 * Build a screenshot harness for the AnCLI WebUI.
 *
 * The real page talks to the KernelSU exec bridge; here we stub `window.ksu` and
 * replay the payloads captured from the device (`.webui-preview/list.json`), so a
 * headless browser renders exactly what the manager's WebView shows.
 *
 * Usage: node .webui-preview/build.js
 */
const fs = require('fs');
const path = require('path');

const root = path.join(__dirname, '..');
const src = path.join(root, 'src', 'module', 'webroot', 'index.html');
const logo = path.join(root, 'src', 'module', 'webroot', 'logo.png');
const appIconsDir = path.join(root, 'src', 'module', 'webroot', 'assets', 'app-icons');
const profilesPath = path.join(root, 'src', 'container-profiles.json');
const listJson = fs.readFileSync(path.join(__dirname, 'list.json'), 'utf8');
const statusJson = fs.readFileSync(path.join(__dirname, 'status.json'), 'utf8');

const propText = fs.readFileSync(path.join(root, 'src', 'module', 'module.prop'), 'utf8');
// Read the version from module.prop so the preview follows the source release.
const ancliVersion = (propText.match(/^version=(.+)$/m) || [, 'unknown'])[1].trim().replace(/^v/, '');
const versionCode = Number((propText.match(/^versionCode=(\d+)$/m) || [, '63'])[1]);
const profilesCatalog = JSON.parse(fs.readFileSync(profilesPath, 'utf8')).profiles || {};
const capturedStatus = JSON.parse(statusJson);
const status = {
  ...capturedStatus,
  ancli_version: ancliVersion,
  version_code: versionCode,
  container: {
    ...(capturedStatus.container || {}),
    profiles: profilesCatalog,
  },
};
const containerStatus = {
  ancli_version: ancliVersion,
  config: (status.container && status.container.config) || {},
  profiles: profilesCatalog,
};
const listPayload = { ...JSON.parse(listJson), ancli_version: ancliVersion };

// A couple of scenarios so the console can be judged with real-looking output.
const scenarios = {
  default: [],
  busy: [
    "logLine('$ ancli update dsh  （后台执行）', 'cmd')",
    "logLine('==> 正在从官方 npm 源拉取 @deepseek-ai/dsh …')",
    "logLine('added 1 package, and audited 2 packages in 41s')",
    "logLine('✓ 完成', 'ok')",
    "logLine('$ ancli check', 'cmd')",
    "logLine('[!] github.com 连接超时，重试 1/3', 'err')",
    "logLine('Grok CLI: 官方 v1.0.41 [channel] -> 可更新')",
    "logLine('exit code: 1', 'err')",
  ],
};

const mock = `
<script>
(function () {
  const STATUS = ${JSON.stringify(status)};
  const CONTAINER_STATUS = ${JSON.stringify(containerStatus)};
  const LIST = ${JSON.stringify(listPayload)};
  const CHECK_MODULE = ${JSON.stringify({
    status: 'ok',
    channel: 'stable',
    current_version: 'v' + ancliVersion,
    current_version_code: versionCode,
    has_update: false,
    latest_version: 'v' + ancliVersion,
    latest_version_code: versionCode,
  })};
  const routes = [
    [/\\[ -x .*\\] && echo yes/, 'yes\\n'],
    [/container status --json/, JSON.stringify(CONTAINER_STATUS)],
    [/container vpn-status/, 'ANCLI_VPN=not_detected\\n'],
    [/container proxy-status/, 'ANCLI_PROXY_URL=192.0.2.10:7890\\nANCLI_VPN=not_detected\\n'],
    [/container mirror-ping/, 'ANCLI_MIRROR_PING_tuna=24.1\\nANCLI_MIRROR_PING_ustc=31.5\\nANCLI_MIRROR_PING_official=182.0\\n'],
    [/check-module/, JSON.stringify(CHECK_MODULE)],
    [/status --json/, JSON.stringify(STATUS)],
    [/list --json/, JSON.stringify(LIST)],
    [/find \\/data\\/local\\/tmp\\/ancli -maxdepth/, ''],
    [/__ANCLI_TASKS_END__/, 'boot-preview\\t1793150000\\n__ANCLI_TASKS_END__\\n'],
    [/tail -c/, ''],
    [/\\[ -f .* \\] && cat/, 'echo -n'],
    [/config --json/, JSON.stringify({
      lang: 'zh',
      default_cwd: '',
      effective_cwd: '/root/.ancli-work',
      proxy_mode: 'system',
      proxy_url: '',
      agent_memory: 'enabled',
      update_channel: 'stable',
      container_profile: 'standard',
      detected_proxy: '192.0.2.10:7890',
      proxy_connected: true
    })],
  ];
  function respond(cmd) {
    for (const [re, out] of routes) if (re.test(cmd)) return out;
    return '';
  }
  window.ksu = {
    exec: function (cmd, opts, cbName) {
      const out = respond(cmd);
      setTimeout(function () { window[cbName](0, out, ''); }, 3);
    },
  };
})();
</script>
`;

const trailer = (scenario, theme, dialog, settings, memory, tab) => `
<style>
/* Screenshot harness only: headless virtual-time does not advance CSS
   transitions, so shots would otherwise catch the mid-transition state. */
* { transition: none !important; animation: none !important; }
</style>
<script>
(function () {
  applyTheme(${JSON.stringify(theme)});
  ${scenarios[scenario].join('\n  ')}
  ${dialog ? `setTimeout(function () { openConfig(${JSON.stringify(dialog)}); }, 400);` : ''}
  ${settings ? `setTimeout(function () { openSettings(); ${tab ? `switchSettingsTab(${JSON.stringify(tab)});` : ''} }, 400);` : ''}
  ${memory ? `setTimeout(function () { openMemoryPrompt(); }, 400);` : ''}
  ${scenario === 'busy' ? `setTimeout(function () { document.getElementById('log').closest('section').scrollIntoView({ block: 'center' }); }, 700);` : ''}
})();
</script>
`;

let html = fs.readFileSync(src, 'utf8');
const webuiProfileMarker = 'const DEFAULT_CONTAINER_PROFILES = /* ANCLI_PROFILE_CATALOG */ {};';
if (!html.includes(webuiProfileMarker)) {
  throw new Error('WebUI profile catalog marker missing');
}
html = html.replace(
  webuiProfileMarker,
  'const DEFAULT_CONTAINER_PROFILES = ' + JSON.stringify(profilesCatalog) + ';'
);

// Keep the local screenshot self-contained when the WebUI references its
// packaged logo and app icons beside index.html.
if (fs.existsSync(logo)) {
  const logoData = 'data:image/png;base64,' + fs.readFileSync(logo).toString('base64');
  html = html.replace(/src="logo\.png(?:\?v=[^"]+)?"/g, `src="${logoData}"`);
}
if (fs.existsSync(appIconsDir)) {
  const mimeTypes = {
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.svg': 'image/svg+xml',
    '.ico': 'image/x-icon',
  };
  const iconDataMap = {};
  for (const file of fs.readdirSync(appIconsDir)) {
    const ext = path.extname(file).toLowerCase();
    if (!mimeTypes[ext]) continue;
    iconDataMap['./assets/app-icons/' + file] =
      'data:' + mimeTypes[ext] + ';base64,' + fs.readFileSync(path.join(appIconsDir, file)).toString('base64');
  }
  html = html.replace(
    "const asset = theme.asset ? './assets/app-icons/' + theme.asset : '';",
    'const __PREVIEW_ICONS = ' + JSON.stringify(iconDataMap) + ";\n  const asset = theme.asset ? (__PREVIEW_ICONS['./assets/app-icons/' + theme.asset] || './assets/app-icons/' + theme.asset) : '';"
  );
}

// 2026-11-13: Accept LF and CRLF source files; the preview harness must not
// depend on the editor's line-ending choice when injecting the bridge mock.
html = html.replace(/<script>\r?\n\/\* =+/, mock + '<script>\n/* ====');
if (!html.includes('window.ksu')) throw new Error('mock injection failed');

const targets = [
  { file: 'preview-light.html', scenario: 'default', theme: 'light', dialog: null },
  { file: 'preview-dark.html', scenario: 'default', theme: 'dark', dialog: null },
  { file: 'preview-log.html', scenario: 'busy', theme: 'light', dialog: null },
  { file: 'preview-dialog.html', scenario: 'default', theme: 'dark', dialog: 'claude-code' },
  { file: 'preview-settings.html', scenario: 'default', theme: 'light', dialog: null, settings: true },
  { file: 'preview-memory.html', scenario: 'default', theme: 'light', dialog: null, settings: false, memory: true },
  { file: 'preview-about.html', scenario: 'default', theme: 'light', dialog: null, settings: true, memory: false, tab: 'about' },
  { file: 'preview-reset.html', scenario: 'default', theme: 'light', dialog: null, settings: true, memory: false, tab: 'reset' },
];

for (const t of targets) {
  fs.writeFileSync(path.join(__dirname, t.file), html + trailer(t.scenario, t.theme, t.dialog, t.settings, t.memory, t.tab), 'utf8');
}
console.log('built: ' + targets.map(t => t.file).join(', '));
