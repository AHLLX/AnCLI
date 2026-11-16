# AI Agent Guidelines (AGENTS.md)

Welcome, fellow AI Assistant. If you are reading this, you have been tasked with modifying or maintaining the **AnCLI** project. This document contains critical context, constraints, and architectural details to help you immediately understand the codebase and avoid common Android-specific pitfalls.

## 1. Project Context & Purpose
AnCLI is a unified, systemless environment manager and plugin-based installer designed to bring full Linux command-line tools (like Node.js, Go, or Python-based AI agents) to rooted Android devices.
Because Android uses the Bionic C library, native Linux binaries often crash. AnCLI solves this by using `proot` to run a pure Ubuntu `glibc` base system transparently on Android.

## 当前交接（2026-11-16，v1.2.621-beta）

- 当前主分支提交为 `68e1de3`，标签为 `v1.2.621-beta`，`versionCode=33`。对应的不可变发布包是 `ancli-33-v1.2.621-beta.zip`；发布工作流已经验证通过。
- 保持单一 `proot-ubuntu` 后端。容器内提供原生 `/usr/local/bin/ancli`，宿主 bash 兼容入口仍使用 `$MODPATH/system/bin/bash`（Path A）和 `/data/adb/ksu/bin/bash`、`/data/adb/ap/bin/bash`（Path B）。
- 容器 wrapper 的 PATH 仍为 `/usr/local/bin:/usr/bin:/bin:/data/adb/ksu/bin:/data/adb/ap/bin:/system/bin`，`/system/bin` 只能作为 Android 安全只读工具的回退路径。
- PRoot 容器命令不能启用 Linux User Namespace 或 Seccomp sandbox；Android 内核下会产生 `EINVAL`。不要引入嵌套 PRoot。
- WebUI 的渠道检查、首页更新箭头和应用版本检查均为后台异步流程；检查无更新要明确提示，有更新要显示版本、简介、下载进度和安装按钮。设置弹窗需保持手机、平板可滚动，顶部深色容器和弹窗使用完整圆角。
- 应用目录当前包含 `codex-cli`，使用官方 npm 包 `@openai/codex`，并随模块打包 OpenAI 图标；图标损坏或缺失时必须回退到字母徽章。
- `update.json` 目前仍指向稳定版 `v1.2.612`。Beta 版本通过 GitHub tag/release 直接测试，除非明确要切换稳定渠道，否则不要把 beta 写入稳定 manifest。

## 默认实施流程（每次改动都按这个顺序）

1. **先确认范围和状态**：阅读本文件，运行 `git status --short`、`git log -3 --oneline --decorate`，确认没有把旧的设备补丁、截图、下载包或私密配置带入提交。
2. **只改源码目录**：核心逻辑改 `src/ancli-core.py`，应用目录改 `src/registry.json`，脚本改 `scripts/`，模块页面改 `src/module/webroot/`。`src/module/ancli/` 是构建同步目录，由 `scripts/build_module.py` 生成，不要手工维护。
3. **先做离线检查**：注册表用 `python -m json.tool src/registry.json` 校验；Shell 用 `sh -n`；Python 用 `python -m pytest tests/ -q`；WebUI 改动要提取 `<script>` 后执行 `node --check`，并按 `.webui-preview/README.md` 渲染手机和平板尺寸截图。
4. **再构建包**：运行 `python scripts/build_module.py`，确认 ZIP 根目录直接包含 `module.prop`，并确认版本号、版本码和资产文件名一致。
5. **有明确设备授权时再验证**：先运行 `adb devices -l` 识别当前序列号，不能假定上一台手机仍在线。只读验证优先使用 `ancli --version`、`ancli list --json`、`ancli status --json`；不要手动启动 TUI 版本探针。
6. **需要热补丁时走容器**：将文件推送到 `/data/local/tmp/*.new` 后，通过 `scripts/device_deploy_helper.py` 在 AnCLI 容器内替换运行文件。不要用单条 `su -c` 直接写 `/data/local/tmp/ancli` 或 `/data/adb/modules/ancli`。
7. **最后再提交发布**：检查 `git diff --check` 和 `git diff --stat`，只暂存本次相关文件；提交、打 tag、推送后等待 KernelSU workflow 完成，再检查 GitHub Release 资产。不要把未验证的设备结果写成已刷写或已安装。

### 当前设备验证模板

```powershell
# 设备序列号必须来自本次输出；平板更换后不要复用旧手机序列号。
adb devices -l
$serial = '<serial>' # 替换为本次 adb devices -l 输出中的设备序列号

# 只读检查，每个 su -c 单独执行，避免 KernelSU 只解析第一段 PATH 的限制。
adb -s $serial shell su -c '/data/adb/ksu/bin/ancli --version'
adb -s $serial shell su -c '/data/adb/ksu/bin/ancli list --json'
adb -s $serial shell su -c '/data/adb/ksu/bin/ancli status --json'

# 热补丁：先按 helper 文件名推送，再把 helper 通过 PRoot 容器执行。
adb -s $serial push src/ancli-core.py /data/local/tmp/ancli-core.py.new
adb -s $serial push src/registry.json /data/local/tmp/ancli-registry.new
adb -s $serial push src/module/ancli_env.sh /data/local/tmp/ancli-env.sh.new
adb -s $serial push src/runtime-context/AGENTS.md /data/local/tmp/ancli-runtime-context.md.new
adb -s $serial push src/module/module.prop /data/local/tmp/ancli-module.prop.new
adb -s $serial push src/module/service.sh /data/local/tmp/service.sh.new
adb -s $serial push src/module/webroot/index.html /data/local/tmp/ancli-webui.html.new
adb -s $serial push src/module/webroot/assets/app-icons /data/local/tmp/ancli-app-icons

$helper = Get-Content -Raw scripts/device_deploy_helper.py
$proot = '/data/local/tmp/ancli/bin/proot -r /data/local/tmp/ancli/rootfs -b /data/local/tmp -b /data/adb/modules/ancli -b /data/adb/ksu -b /data/adb/ap /usr/bin/env PATH=/usr/local/bin:/usr/bin:/bin python3 -'
$helper | & adb -s $serial shell su -c $proot
```

## 2. Core Architecture
You must understand these three pillars before writing any code:

1. **`src/module/customize.sh` (The Magisk Module Installer)**: 
   - This is the module flash hook executed by Magisk/KernelSU/APatch when the ZIP is installed. `src/install.sh` remains a separate optional bootstrap that downloads the ZIP; it is not called by the manager.
   - Downloads PRoot and a minimal `ubuntu-base` tarball, extracting it to `/data/local/tmp/ancli/rootfs`.
   - Bootstraps APT dependencies (`python3`, `nodejs`, `npm`) during the flash process.
2. **`src/ancli-core.py` (The Package Manager Brain)**: 
   - Runs *inside* the Proot Ubuntu container.
   - Loads the registry bundled with the module.
   - Generates Bash wrappers and injects them into the Android host.
   - `ancli check` resolves each installed tool's *real* version (`version_cmd`) and the
     vendor's *official* latest version (`latest` in the registry), caching both in
     `/data/local/tmp/ancli/.update_cache.json` for the WebUI.
   - **CONSTRAINT**: Must rely strictly on the Python Standard Library (e.g., use `urllib.request`, NOT `requests`).
3. **`src/module/ancli_env.sh` (The Environment Bridger)**:
   - Sourced dynamically by the wrapper script before PRoot execution.
   - Detects Android system proxies (via `dumpsys`) and exports them.
   - Fixes permission locks and mounts `TMPDIR=/tmp`.
4. **`src/registry.json` (The Plugin Schema)**:
   - Contains the installation logic (`pip`, `npm`, `apt`) for third-party CLIs.

## 3. The "Dual-Injection" Systemless Hack (CRITICAL)
Android's `/system/bin` is strictly Read-Only. To expose a newly installed tool (like `aider`) globally to the user's `$PATH`, you **CANNOT** write to `/system/bin`.
Instead, `ancli-core.py` uses a Dual-Injection method:
- **Path A**: `/data/adb/modules/ancli/system/bin/<tool>` (Standard Systemless Module path, takes effect after the phone reboots).
- **Path B**: `/data/adb/ksu/bin/<tool>` or `/data/adb/ap/bin/<tool>` (Dynamic KernelSU/Apatch paths, takes effect instantly without reboot).

*Rule: If you are generating a new wrapper or shortcut, it MUST be written to both Path A and Path B.*

## 4. Technical Constraints & Red Lines (CRITICAL)
When modifying the script execution logic or registry installer commands, you MUST strictly adhere to the following rules:

1. **NO Nested PRoot execution**:
   - `ancli-core.py` runs *inside* the PRoot container. Therefore, do NOT wrap any commands executed by `run_cmd` inside another `proot` invocation block. Doing so causes nested virtualization ptrace lockups, leading to crashes like `/system/bin/sh: bash: inaccessible or not found`.
2. **Bypass ADB Shell Escaping using Python**:
   - Complex installation scripts (e.g. `curl | bash -s -- --dir`) fail when run through adb due to shell escaping bugs where characters like `|` or `--` get corrupted. If a tool installation script contains complex pipe execution, override it to download the script file natively via Python's `urllib.request` inside `/tmp` and then execute it.
3. **Always Force-Delete files before writing wrappers**:
   - Android's root and shell users create files with conflicting ownerships. When generating wrapper scripts, you MUST check if the file already exists and explicitly delete it using `os.remove` before recreating, otherwise the operation will fail with `[Errno 13] Permission denied`.
4. **Command Whitelist Validation**:
   - Any execution inside the container must be explicitly whitelisted in `ALLOWED_CMD_PREFIXES` in `ancli-core.py`. Add `bash ` and `sh ` if running local shell script installers.
   - Registry `version_cmd` probes (`claude --version`) go through `_capture_cmd(..., _probe_prefixes(...))`: the executable names come from the registry and extend the whitelist for that one call only (`validate_cmd` default stays strict). Because a probe runs *automatically* (WebUI page load, `ancli check`), it is stricter than `install_cmd`: `_probe_cmd_allowed()` requires the first token to equal the app's own `executable` and passes `allow_operators=False`, so no `&&`/`||`/`|` chain can be smuggled into a registry entry.
5. **Do NOT force delete container data during Manager uninstall (CRITICAL)**:
   - Magisk/KSU Manager uninstallations trigger `uninstall.sh` in a non-interactive background shell (there is **no TTY detection** in that script — do not claim there is). It defaults to KEEP: only `$ANCLI_DIR/bin` is wiped, while `/data/local/tmp/ancli/rootfs`, `installed.json`, `secrets/`, `registry.json` and `.update_cache.json` survive. A full purge only happens when `/data/local/tmp/ancli_force_purge` exists. This ensures Python dependencies (like `gitpython`) and API keys are not lost during module upgrades.
6. **`--json` stdout must stay parseable**: `_stdout_to_stderr()` redirects both `sys.stdout` and file descriptor 1 while data is gathered, so diagnostics from `os.system`/subprocess helpers land on stderr. Keep new diagnostics on stderr and never print to stdout from a helper used by `list --json` / `check --json`.

## 5. Physical Layout Mapping
When debugging paths or writing cleanup logic, refer to this exact physical mapping (Host Android perspective):

- **Ubuntu Rootfs**: `/data/local/tmp/ancli/rootfs/`
- **NPM Globals**: `/data/local/tmp/ancli/rootfs/usr/local/lib/node_modules/`
- **Pip Globals**: `/data/local/tmp/ancli/rootfs/usr/local/lib/python3.12/dist-packages/`
- **Core Script**: `/data/local/tmp/ancli/bin/ancli-core.py`
- **Update Cache**: `/data/local/tmp/ancli/.update_cache.json` (official latest versions from `ancli check`)
- **Shared Skills**: `/root/.agents/skills` inside the container (i.e. `$ROOTFS/root/.agents/skills`); `ancli skills` / `ancli repair` create it and symlink each tool's own path (`~/.claude/skills`, `~/.grok/skills`, `~/.mimocode/skills`, `~/.config/opencode/skills`, `~/.dsh/skills`) to it
- **Magisk Module**: `/data/adb/modules/ancli/`
- **App-data channel**: `/mnt/ancli-data` (host bind of the real `/data`, visible in the container as `/mnt/ancli-data/data/<pkg>`)
- **Global settings (2026-11-13)**: `/root/.ancli-config.json` accepts `lang`, `default_cwd`, `proxy_mode` (`system|manual|direct`) and `proxy_url`. `ancli config` with no app id prints/updates them; `ancli config --json` reports the supported keys. Wrappers read `default_cwd` and proxy settings at launch, so changing them needs no wrapper regeneration. The WebUI's Settings dialog is the visual editor for the same file and shows the backend, data channel, effective directory, and proxy mode.
- **Runtime context**: `ancli agent-init` renders the bundled `src/runtime-context/AGENTS.md` template into the guest-wide `/root/.agents/AGENTS.md`; the fresh file requires the first Agent session to probe the device and run `ancli agent-init --finalize`, which writes measured paths and capabilities back and removes the prompt. `ancli reset --yes` restores that pending template. `ancli repair`, `ancli skills`, and successful installs ensure it too. It links both `/root/.gemini/AGENTS.md` and `/root/.gemini/GEMINI.md` for Antigravity, because deployed builds use either filename. Existing project `AGENTS.md` files are kept. The generated global `AGENTS.md` contains environment paths and guidance, never secret values.
- **AGY host command compatibility**: Android normally has `/system/bin/sh` but no host-side `bash`. The module may install a marked POSIX forwarding script at `/data/adb/ksu/bin/bash` and `/data/adb/ap/bin/bash` so AGY's remote `bash -c` probe can start. It supports only POSIX syntax, preserves a real user-provided bash, never overlays `/system/bin`, and never re-enters PRoot. `ancli doctor --json` reports `host_compatibility.bash`; `ancli repair` restores the entrypoint when it is missing.

**Why `/data/data` can look empty.** An `su` started from an app (Termius, an SSH-server app) runs in
that app's mount namespace, where Android masks `/data/data` with a tmpfs (`mode 751`, only the app's
own dirs). This is **not** a proot limitation — the same shell from `adb shell` / a global `su` sees
all ~752 packages, and `test -x` works there too. Do not "fix" it by adding proot binds: the mask
lives in the parent namespace. Every wrapper instead does an idempotent,
non-recursive `mount --bind /data /mnt/ancli-data` before launching — a non-recursive bind does not
inherit submounts, so the mask is left behind, and `/mnt` is already bound into the container.
`ensure_android_data()` (called by `ancli repair`) re-checks it; `status --json` reports `android_data`.

## 6. Adding New Apps to Registry
To add support for a new CLI tool, do not modify `ancli-core.py`. Instead, add a new JSON object to `registry.json`.

**Required Schema:**
```json
"app_id": {
  "name": "Human Readable Name",
  "description": "Short description shown in WebUI",
  "author": "Vendor or project",
  "install_mode": "proot",
  "install_cmd": "npm install -g something",
  "update_cmd": "npm update -g something",
  "uninstall_cmd": "npm uninstall -g something",
  "env_vars": ["ANY_REQUIRED_API_KEY"], 
  "optional_env_vars": ["ANY_PROXY_BASE_URL"],
  "executable": "command_name_to_bind",
  "version_cmd": "something --version",
  "latest": { "source": "npm", "package": "something" },
  "version": "1.2.3"
}
```
*Note: If `env_vars` is provided, `ancli-core.py` will automatically prompt the user for them and inject them into the wrapper via `export KEY="VALUE"`.*

All entries must also expose the generic proxy trio (`HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`) in `optional_env_vars` or `env_vars`. Add provider keys only when the vendor documents them. For example, `codex-cli` uses `@openai/codex` with `OPENAI_API_KEY` and `OPENAI_BASE_URL`; do not copy those keys to unrelated apps. Keep the app id stable because it is persisted in `installed.json` and rendered by the WebUI.

**Prefer tarball / npm / pip installs over `apt` inside the container.** The rootfs ships with a half-configured `systemd` (its postinst cannot run under proot: "Failed to resolve group 'systemd-journal'"), so `dpkg --configure -a` aborts with a proot assertion, and `apt-get` also fails archive signature checks unless every call carries `-o Acquire::AllowInsecureRepositories=true -o Acquire::AllowUnauthenticated=true --allow-unauthenticated` (see `customize.sh`). A chain that relies on `apt-get … && npm …` therefore dies at the first step and silently falls through to whatever `||` alternative follows. Download the vendor tarball (`.tar.gz`, not `.tar.xz` — `xz` is absent) or install from npm/pip instead.

**Update detection fields (never omit them):**
- `version_cmd` — the **installed** version is read from the tool itself inside the container
  (ask the vendor's real binary; verified prefixes are auto-whitelisted via `_registry_exe_prefixes`).
  Do not gate the probe on the exit code: `grok --version` prints the version but exits 126 on
  device, and some tools print it on stderr — `_probe_installed_version` accepts any version found
  in the output (stdout first, then a stderr-merged retry).
- `latest` — where the **official latest** version comes from. Supported sources:
  `github` (`repo`), `pypi` (`package`), `npm` (`package`), `json` (`url` + `path`),
  `text` (`urls[]`, first version token wins), `static` (no public API — uses `version`),
  `none` (detection unavailable; the UI says so). `note` is free-text documentation for
  humans; no code reads it. An unknown `source` is reported as an error, never treated as
  `static`.
- `version` — declared fallback used when the network is down or the source is `static`.
- Rule: **never** treat a hand-written `version` as the official latest, and never write the
  registry/AnCLI version into `installed_version` — that silently disables update detection
  (see CHANGELOG: "WebUI could not detect official versions").

**Skills are a per-app property too.** Agent CLIs read `SKILL.md` bundles from their own directory
plus the shared `~/.agents/skills` (Claude Code excepted — `~/.claude/skills` is symlinked to the
shared root by `ancli skills`). When adding an app, survey its real paths instead of guessing:
`scripts/survey_skill_paths.py` (piped into the container) greps the binary's own string literals and
the bundled docs. Aider-style tools with no skill system use `CONVENTIONS.md` / `--read`.

## 7. Testing Constraints
- There is **no Android emulator** for this project — and an emulator could not verify this module anyway (it needs a rooted real device).
- Only use `adb` when a device is actually attached (`adb devices` lists one). Even then, stick to read-only inspection (`ls`, `cat`, `settings get`, `logcat`) unless the user explicitly asks you to flash/test.
- **NEVER** claim the module was flashed or verified when it was not, and never flash/uninstall on your own. Fall back to `sh -n` syntax checks, `python -m pytest tests/`, and static reasoning.
- Update detection has two runnable checks: `python -m pytest tests/` (offline unit suite) and `python scripts/smoke_update_check.py` (optional live-upstream check for registry latest sources). Device state comes from `ancli list --json` on the currently attached phone or tablet.
- **Hot-patching a running module must go through the container.** A `su -c` one-liner cannot write `/data/local/tmp/ancli/*` or `/data/adb/modules/ancli/*` on this device (denied even with SELinux permissive — the KernelSU `su` mount namespace is the restriction), while the proot child writes the same files fine. Use `scripts/device_deploy_helper.py`, piped into the container with `... python3 -`, to replace `bin/ancli-core.py`, both registry copies, `bin/ancli_env.sh`, runtime context, `module.prop`, `service.sh`, `webroot/index.html` and `webroot/assets/app-icons/`. Restore SELinux (`setenforce 1`) if you temporarily relaxed it.
- **KernelSU's `su -c` resolves only the *first* command via `/data/adb/ksu/bin`.** `echo $PATH` shows no ksu entry, yet `su -c 'dsh --version'` works while `su -c 'dsh --version; claude --version'` fails with "inaccessible or not found" for the second command. Verify tools one per `su -c` invocation (or use absolute paths); do not conclude a wrapper is missing from a chained command. The manager's WebUI exec bridge is unaffected.
- **Never probe a TUI agent by hand.** `su -c 'mimo --version'` ignores the flag, opens the interactive UI and blocks forever (looks like a hung device; it cost a real debugging round). Use `ancli list --json` / `ancli check`, which probe inside the container with `PROBE_TIMEOUT` and never inherit the terminal.
- **After flashing the module, run `ancli repair`.** The flash replaces the module directory with the ZIP contents, so `system/bin/<tool>` loses every wrapper `ancli install` had added (the tools keep working through `/data/adb/ksu/bin`, but dual-injection is incomplete until repair regenerates them). The same repair re-applies the dsh cookie patch. Also expect a harmless `cp: can't create …/bin/proot: Text file busy` in the flash log when a proot process is alive: the running binary is the same version and the reboot settles it.
- **The WebUI can be rendered and reviewed without a device.** `.webui-preview/build.js` stubs `window.ksu` with `list --json` / `status --json` payloads captured from the device and writes `preview-*.html`; screenshot them with `chrome --headless --virtual-time-budget=4000 --window-size=412,900 --screenshot=… file:///…`. Without `--virtual-time-budget` the shot stops mid-load (skeleton cards); the harness disables CSS transitions because virtual time does not advance them (see `.webui-preview/README.md`). After any `webroot/index.html` change: render locally, then deploy through `scripts/device_deploy_helper.py` and open the page in the manager.
- **平板/手机实机截图要记录尺寸。** 连接设备后可用 `adb -s <serial> exec-out screencap -p > .webui-preview/shot-device.png` 保存当前页面；至少检查窄手机宽度和横向/竖向平板布局。截图只用于本地验证，不要提交到仓库。
- **WebUI JSON calls must stay quiet and parseable.** `status --json` is dispatched *before* catalog and official-version work so diagnostics cannot break `JSON.parse`. Internal payload calls go through `ancli(cmd, true)` so the output panel shows user actions rather than raw JSON.

## 8. Network Proxy & VPN Constraints (CRITICAL)
- **VPN Bypass**: Android VPNs in TUN mode (e.g. Clash, v2rayNG) bypass Root (UID 0) traffic by default. If a Python or curl script runs as root and the proxy is not explicitly set, it will ignore the VPN and fail to connect to domains like `github.com`.
- **Proxy Override**: Always parse the Android system proxy via `dumpsys connectivity`. Extract the `HttpProxy` IP and port. `ancli_env.sh` caches the result for 30 s, keeps the last successful value in `.proxy_cache_last` (used when the probe comes back empty, instead of silently going direct), and exports `no_proxy=localhost,127.0.0.1,::1` so local model servers/MCP endpoints are never tunnelled.
- **DNS inheritance (2026-11-13)**: the host-side `ancli_env.sh` remains the source of truth for proxy and DNS discovery. It probes Android resolvers (properties, then `dumpsys connectivity` → `DnsAddresses`) and caches them in `/data/local/tmp/ancli/.dns_cache` (refreshed hourly); `_get_android_dns()` reads that cache first. A container may run `/system/bin/getprop` or `/system/bin/dumpsys` when the wrapper exposes them and SELinux permits it, but the core must not depend on that optional path. `service.sh` performs the boot-time probe with absolute paths, writes safe fallbacks, and retries asynchronously for up to 5 minutes. Never lead `resolv.conf` with 8.8.8.8 — it is blocked or poisoned on many networks.
- **Browser hand-off (xdg-open)**: Android's own binaries cannot execute inside the glibc guest — `/system/bin/am` fails with `cmd: inaccessible or not found` — so a container-side `xdg-open` that calls `am` looks successful (exit 0) while opening nothing. The container shim (`$ROOTFS/usr/local/bin/xdg-open`, also at `/usr/bin` + `/bin`, plus `sensible-browser`) therefore only appends URLs to `/data/local/tmp/ancli/.open_url`; the generated wrapper runs a watcher that drains that queue with `am start` on the host and prints the URL back to stderr if that fails. **The wrapper must export `BROWSER=/usr/local/bin/xdg-open`**: the npm `open` package (used by `dsh web`) prefers its own vendored freedesktop `xdg-open` over PATH and otherwise ends in `xdg-open: no method available for opening 'http://…'` (rc 3, verified on device). Re-check with `scripts/probe_open_package.py` after a dsh upgrade. The drain also prefers a real browser over the chooser (`am start -p <pkg>`, Chrome first): with no default browser set, `am start` raises "Open with", and a WebView-based pick (Via and friends) does not carry the auth cookie.
- **`dsh web` on Android needs `SameSite=Lax`** (patched by `patch_dsh_web_cookie()`, re-applied by `ancli repair` and after installing/updating dsh). dsh authenticates by setting an HttpOnly `SameSite=Strict` cookie on the token URL and redirecting to `/`; Chrome treats a navigation started by an external app — exactly what an Android Intent is — as cross-site, drops the Strict cookie on that redirect, and the UI answers "dsh web authentication required". Typing the URL or launching from a desktop browser works, which is why upstream does not see it. The patch edits the one packaged file that carries the attribute (`…/@deepseek-ai/dsh-client-connection/lib/index.js`) and is idempotent. If a future dsh release renders differently, `scripts/probe_dsh_web_page.py` replays the token/cookie exchange against a live server.
- **DO NOT hardcode 127.0.0.1**: Android users often run proxy software on other devices (e.g. PC at `192.168.1.x`) and set the Android proxy to that IP. Always use the IP dynamically returned by `dumpsys`.
- **Go Socket Binds**: Go binaries (like `agy`) crash when binding to `198.18.0.x` virtual IPs created by Clash TUN. Ensure `ancli_env.sh` pre-binds these to the loopback interface (`ip addr add 198.18.0.x/32 dev lo`).

## 9. PRoot Path Resolution & TUI Crashes
- **The `/sdcard` Trap**: Android's `/sdcard` is a complex chain of symlinks pointing to `/mnt/user/0/...` or `/storage/emulated/0`; some adb-root namespaces omit the alias entirely.
- **Node.js/Bun TUI Panics**: If you run Node.js-based tools (like `mimo`) inside PRoot while inside `/sdcard`, the tool will attempt to resolve its absolute physical path. If PRoot is missing `-b /mnt` or `-b /data` binds, the physical path resolution will fail, causing the Event Loop or TUI library to silently crash (you won't even see an error, the TUI simply won't launch).
- **The Solution**: Do not choose binds from `$PWD`. Generate wrappers with the complete common-tree list and include each path when it exists in the current namespace: `/sdcard`, `/storage`, `/mnt`, `/data`, `/apex`, `/linkerconfig`, and `/system`. This keeps symlink targets available without emitting a fatal-looking warning for an absent `/sdcard` alias.

## 10. Version & Release Checklist (CRITICAL)
Stable releases are consumed by Magisk/KernelSU/APatch managers through `update.json`. Beta releases are published as immutable GitHub tags for direct testing and intentionally leave the stable manifest unchanged. In both cases, the module source and ZIP name must agree:

| Where | Field | Must match |
| :--- | :--- | :--- |
| `src/module/module.prop` | `version` / `versionCode` | source of truth; `versionCode` must always increase |
| `update.json` | stable channel only: `version` / `versionCode` / `zipUrl` / `changelog` | stable `zipUrl` must point at the **exact** asset name of the stable Release; do not overwrite it for beta testing |
| GitHub Release asset | `<id>-<versionCode>-<version>.zip` | file name must equal the basename in `zipUrl` (case included) |

Use a three-part version with an unrestricted numeric patch segment. A beta tag adds the `-beta` suffix, for example `v1.2.621-beta`; `versionCode` remains a separate monotonically increasing integer for KernelSU and Magisk comparisons. `scripts/prepare_release.py` accepts only stable `vX.Y.Z` syntax, so beta releases use the manual procedure below.

### Stable release procedure

```bash
# 1) add an `## Unreleased — ...` section to CHANGELOG.md
# 2) synchronize module.prop, update.json, core, registry and the official asset name
python scripts/prepare_release.py vX.Y.Z N
# 3) build the flashable zip (module.prop must be at the ZIP root)
python scripts/build_module.py
unzip -l <id>-N-vX.Y.Z.zip | head
# 4) run local checks, review scope, then commit, tag and push
python scripts/extract_release_notes.py vX.Y.Z .release-notes.md
python -m pytest tests/ -q
git diff --check
git status --short
git add CHANGELOG.md src/module/module.prop src/ancli-core.py src/registry.json update.json scripts/deploy_remote_devices.py
git commit -m "release: vX.Y.Z (versionCode N)"
git tag -a vX.Y.Z -m "vX.Y.Z"
git push origin main && git push origin vX.Y.Z
# 5) wait for the KernelSU workflow and inspect the immutable Release asset
# 6) confirm the manager-visible stable manifest points to the new asset
curl -sL https://raw.githubusercontent.com/AHLLX/AnCLI/main/update.json
```

### Beta release procedure

```bash
# 1) add a dated `## vX.Y.Z-beta (YYYY-MM-DD)` section to CHANGELOG.md
# 2) update these beta source-of-truth files together; leave update.json unchanged
#    src/module/module.prop: version=vX.Y.Z-beta, versionCode=N
#    src/ancli-core.py: VERSION = "X.Y.Z-beta"
#    src/registry.json: top-level version = "X.Y.Z-beta"
#    scripts/deploy_remote_devices.py: ZIP_NAME = "ancli-N-vX.Y.Z-beta.zip"
# 3) build and run the same local checks as a stable release
python scripts/build_module.py
python -m pytest tests/ -q
git diff --check
git status --short
# 4) stage only reviewed files, then tag and push
git add CHANGELOG.md src/module/module.prop src/ancli-core.py src/registry.json scripts/deploy_remote_devices.py src/module/webroot
git commit -m "release: vX.Y.Z-beta (versionCode N)"
git tag -a vX.Y.Z-beta -m "vX.Y.Z-beta"
git push origin main && git push origin vX.Y.Z-beta
# 5) wait for the KernelSU workflow; the workflow builds the exact beta asset
gh run list --limit 3 --json databaseId,status,conclusion,headBranch,headSha,workflowName,createdAt
gh release view vX.Y.Z-beta --json tagName,url,isDraft,isPrerelease,assets
```

Release rules:

- **Never reuse or decrease `versionCode`**: managers compare it to decide there is an update.
- **Never edit or replace an immutable Release**: fix a bad build with a new tag and a higher `versionCode`.
- **Keep the KernelSU workflow pinned**: it performs the same ZIP validation as the official catalog and fails if the published Release is not immutable.
- **Release notes must be version-specific**: use `scripts/extract_release_notes.py`; do not publish the entire historical `CHANGELOG.md` as one Release body. The workflow accepts the beta tag because its section is already headed with the full `-beta` version.
- **Never ship a zip whose root is not the module root** (no extra folder around `module.prop`), and keep the module directory name equal to `id` in `module.prop`.
- **Upgrade must not lose user data**: `uninstall.sh` preserves `/data/local/tmp/ancli/rootfs` and `installed.json` (see section 4.5). Re-check that rule before every release that touches install/uninstall logic.
- **Pre-release checks you can actually run here**: `sh -n` on every `src/module/*.sh`, `python -m pytest tests/ -q`, `python -m json.tool src/registry.json`, `python scripts/build_module.py`, WebUI `node --check`, and a manual read of the diff for `/system` writes (must not exist — see section 3 dual-injection).
- **On a real device only** (when `adb devices -l` shows one, and only with the user's go-ahead): identify the current serial, hot-patch through the container helper when needed, then verify `ancli --version`, `ancli list --json`, and `ancli status --json`. Flashing, rebooting, uninstalling, or installing a new CLI requires explicit user scope; never claim those actions from a hot-patch or read-only probe.
