# WebUI 截图预检（开发用）

改 `src/module/webroot/index.html` 之后，不用刷机、不用开管理器，就能在电脑上把页面
真实渲染出来看效果：这个 harness 把 `window.ksu`（KernelSU 的 exec 桥）替换成本地桩，
并用**从真机抓下来的真实数据**回放（同时内联 `logo.png`、`assets/app-icons/*` 与 `src/container-profiles.json`），所以浏览器里看到的就是管理器 WebView 里的样子。

## 用法

```bash
# 1) 抓一份真机数据（设备在的时候；两个文件都写入本目录）
adb shell su -c '...python3 /data/local/tmp/ancli/bin/ancli-core.py list --json'   > .webui-preview/list.json
adb shell su -c '...python3 /data/local/tmp/ancli/bin/ancli-core.py status --json' > .webui-preview/status.json

# 2) 生成预览页（浅色/深色/输出面板/配置弹窗/设置/记忆/关于/重置）
node .webui-preview/build.js
```

### Linux / macOS 截图

```bash
chrome --headless --disable-gpu --hide-scrollbars --virtual-time-budget=4000 \
       --user-data-dir=/tmp/ancli-chrome-profile \
       --window-size=412,900 --force-device-scale-factor=2 \
       --screenshot=.webui-preview/shot-light.png \
       file:///<绝对路径>/.webui-preview/preview-light.html
```

### Windows PowerShell 截图（手机窄屏 + 平板宽屏）

在 Windows PowerShell 中直接执行 `chrome.exe` 会立即返回，不等 PNG 落盘；请使用 `Start-Process -Wait` 并配合 `--headless=new` 与独立 `--user-data-dir`：

```powershell
$chrome = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
$prof = Join-Path $PWD '.webui-preview\chrome-profile'
$uri = ([System.Uri](Resolve-Path '.webui-preview\preview-light.html').Path).AbsoluteUri

# 手机窄屏 (412x900)
Start-Process -FilePath $chrome -Wait -ArgumentList @(
  '--headless=new', '--disable-gpu', '--hide-scrollbars',
  '--virtual-time-budget=4000', '--window-size=412,900',
  '--force-device-scale-factor=2',
  "--user-data-dir=$prof",
  '--screenshot=.webui-preview/shot-phone.png',
  $uri
)

# 平板宽屏 (1280x800)
Start-Process -FilePath $chrome -Wait -ArgumentList @(
  '--headless=new', '--disable-gpu', '--hide-scrollbars',
  '--virtual-time-budget=4000', '--window-size=1280,800',
  '--force-device-scale-factor=1',
  "--user-data-dir=$prof",
  '--screenshot=.webui-preview/shot-tablet.png',
  $uri
)
```

要点与坑：

- **必须带 `--virtual-time-budget`**（例如 4000），否则截图会停在异步流程中途（列表还是骨架屏）。
- **harness 里关掉了 CSS 过渡**：虚拟时间不推进 transition，否则会拍到弹窗的半透明中间态。
- 每个 Chrome 进程都要给独立的 `--user-data-dir`（已在 `.gitignore` 里），否则会复用现有浏览器会话而什么都不输出。
- 真机上还要跑一遍：部署 `webroot/index.html`（走 `scripts/device_deploy_helper.py`，容器内写模块目录）后在管理器里打开页面，确认能列出工具、按钮能点。

生成的 `preview-*.html` / `shot-*.png` / `chrome-profile/` 都不入库。
