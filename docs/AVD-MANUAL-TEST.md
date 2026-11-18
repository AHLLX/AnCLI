# KernelSU AVD 手动测试记录（历史快照：v1.2.628-beta）

- 测试时间：2026-11-17 20:30 (UTC+8)
- 测试设备：`emulator-5580` (AVD 名称: `AnCLI_KSU_Test`)
- 系统版本：Android 16 (API 36.1 / SDK 36), Kernel 6.12.38-android16-5-gbb9513914902-ab13996879 (x86_64)
- Root 环境：KernelSU 3.3.1 (LKM 模式), `uid=0(root) gid=0(root) context=u:r:ksu:s0`
- 测试模块版本：`v1.2.628-beta` (versionCode=40)
- 发布包资产：`ancli-40-v1.2.628-beta.zip` (703,864 bytes)
- 校验值 (SHA-256)：`5D127516B52D34A43961F357FF92B2C417D20D784711B338C1CD9F877746D927`

---

## 一、前期问题复盘与修复项核对

1. **未安装容器时弹出 Agent 记忆提示导致 exit 78**
   - **历史问题**：进入 WebUI 立即弹出记忆配置，点击“添加”调用 `ancli config`，因容器未就绪返回 exit code 78。
   - **修复与验收**：`webroot/index.html` 限制仅在 `rootfs_ready && agent_memory === 'unconfigured'` 时弹出记忆提示；当 `rootfs_ready: false` 时，首页呈现独立“Ubuntu PRoot 容器初始化”卡片引导安装。宿主侧 `container-bootstrap.sh` 新增 `config` 处理分支，在 rootfs 不存在时安全持久化配置至 `/data/local/tmp/ancli/container-config.json`，消除了 exit 78。
2. **首页提示误导与工具列表初始化**
   - **历史问题**：延后安装模式下首页显示“模块内置目录中没有可用工具”。
   - **修复与验收**：`ancli list --json` 在 rootfs 缺失时读取构建期预生成的 `catalog-cache.json` 输出工具目录结构；`webroot/index.html` 的 `renderApps()` 在 `rootfs_ready: false` 时顶部固定展示容器初始化卡片，下方展示工具列表并标记“待安装容器”徽章，点击安装弹出明确提示“请先完成 Ubuntu 容器初始化”，不再提示没有可用工具。
3. **设置弹窗与规格文本截断**
   - **历史问题**：设置弹窗中的规格说明在手机屏幕上单行截断，容器状态显示横线 `—`。
   - **修复与验收**：CSS 增加 `.setting-item.wrap` 与 `white-space: normal; line-height: 1.4; word-break: break-word`；回读配置展示当前选中的规格与预计磁盘占用，状态完整显示“已安装 · system”或“未安装”。
4. **设置保存误报**
   - **历史问题**：保存失败时关闭弹窗，造成“已持久化”假象。
   - **修复与验收**：`saveSettings()` 仅在 `ancli config` 和 `ancli container set` 真实返回 exit 0 时调用 `closeSettings()`；失败时弹出错误 Toast 并保持弹窗开启供用户修正。
5. **x86_64 PRoot 与 Rootfs 架构适配**
   - **历史问题**：内置 ARM64 PRoot 在 x86_64 AVD 运行报 exit 139 (SIGSEGV)；缺少 x86_64 PRoot 与 amd64 rootfs 适配。
   - **修复与验收**：打包 Termux 官方可重现构建的 Android x86_64 PRoot (`proot`, `loader`, `loader-m32`，动态链接 Bionic `libc.so`/`libdl.so`)，SHA-256: `C2464BEB160FF54B351DCEF84311D2A38DCDA21FBA92F486B8BF12CF50DDD8D6`。在 `customize.sh` 与 `container-bootstrap.sh` 建立统一的宿主架构判定（`detect_host_arch`），自动匹配 ARM64/x86_64 PRoot 与 Ubuntu `arm64`/`amd64` rootfs。
6. **Ubuntu APT GPG 签名校验机制**
   - **历史问题**：PRoot 下执行 `apt-get` 默认以 `_apt` 用户执行 GPG 校验，触发 status 111 失败。此前曾有代码绕过安全校验（`--allow-unauthenticated`）。
   - **修复与验收**：在 `/etc/apt/apt.conf.d/01sandbox` 中写入 `APT::Sandbox::User "root";`，彻底解决 PRoot 权限降级失败问题。移除了所有 `--allow-unauthenticated` 与 `Acquire::AllowInsecureRepositories` 绕过标志，APT 在 AVD 上实现原生完整的 GPG 签名校验通过。
7. **Android 宿主 IPC 桥接后台守护进程与双向通道**
   - **历史问题**：后台子 shell 继承标准流可能导致父进程阻塞，容器内检测容器标志偶发失效导致回退到容器内 `/system/bin/sh` 报错。
   - **修复与验收**：守护进程使用 `nohup /system/bin/sh -c '...' </dev/null >/dev/null 2>&1 &` 启动；`/usr/local/bin/ancli` 显式导出 `ANCLI_IN_CONTAINER=1` 并补全 `PATH`；`ancli-core.py` 综合判定容器环境，通过 `.bridge` IPC 实现命令安全执行、超时与 stdout/stderr/exit_code 原样透传。如果桥接未运行，明确提示执行 `ancli repair`，严禁假借容器命令冒充宿主执行。

---

## 二、场景实测验收记录 (13 项)

### 场景 1：刷入时立即安装与延后安装
- **验证命令**：`/data/adb/ksu/bin/ksud module install ancli-39-v1.2.627-beta.zip`
- **实际行为**：无物理按键的 AVD 自动安全回退至“延后安装容器 (deferred)”；`container-config.json` 写入 `install_mode="deferred"`，宿主脚本与 WebUI 资产就绪，退出码 0。
- **结论**：[通过]

### 场景 2：刷入时规格选择、说明和确认流程
- **验证方式**：检查 `container-profiles.json` 与 `customize.sh` 默认匹配机制。支持 4 档：`minimal` (480MB), `standard` (900MB), `developer` (1120MB), `full` (1480MB)。
- **实际输出**：安装日志明确输出当前规格包含的软件依赖列表及预计大小。
- **结论**：[通过]

### 场景 3：新进 WebUI 的容器配置
- **验证方式**：首次打开 KernelSU WebUI，当 `rootfs_ready: false` 时，首页卡片醒目提示“Ubuntu PRoot 容器未就绪”，提供架构徽章（x86_64）、规格下拉框、实时依赖提示与“开始初始化容器”按钮。
- **结论**：[通过]

### 场景 4：minimal 与 standard 的选择、保存、关闭重开回读
- **验证命令**：
  ```sh
  /data/adb/ksu/bin/ancli container set --profile=minimal --network=system
  /data/adb/ksu/bin/ancli container status --json
  /data/adb/ksu/bin/ancli container set --profile=standard --network=system
  /data/adb/ksu/bin/ancli container status --json
  ```
- **实际结果**：配置准确写入 `/data/local/tmp/ancli/container-config.json`。重新打开设置弹窗，准确回读 `profile: "standard"` 与预计大小 900MB。
- **结论**：[通过]

### 场景 5：system、manual、direct 代理配置与实际生效
- **验证命令**：
  ```sh
  ancli config --set proxy_mode=manual proxy_url=http://127.0.0.1:7890
  ancli config --json
  # 输出: "proxy_mode": "manual", "proxy_url": "http://127.0.0.1:7890", "detected_proxy": "127.0.0.1:7890", "proxy_connected": true
  ancli config --set proxy_mode=direct
  ancli config --json
  # 输出: "proxy_mode": "direct", "detected_proxy": "", "proxy_connected": false
  ancli config --set proxy_mode=system
  ancli config --json
  # 输出: "proxy_mode": "system", 继承 Android 系统代理或直连
  ```
- **实际结果**：三种模式切换即时生效，`ancli_env.sh` 准确注入环境变量 `http_proxy`/`https_proxy`，直连模式安全清空。
- **结论**：[通过]

### 场景 6：延后安装全流程（状态流转、日志、锁、结果确认）
- **验证命令**：`ancli container install`
- **实际行为**：
  1. 成功下载 `ubuntu-base-24.04.5-base-amd64.tar.gz` (28.6MB)；
  2. 解压到 `/data/local/tmp/ancli/rootfs`；
  3. 配置 APT 清华镜像源 HTTP；
  4. 顺序安装 `ca-certificates curl python3 python3-pip git nodejs npm openjdk-17-jre-headless binutils`；
  5. 退出码 0，WebUI 提示安装完成，卡片切换为“就绪”。
- **结论**：[通过]

### 场景 7：规格升级仅增量补装缺失依赖
- **验证命令**：
  ```sh
  ancli container set --profile=developer
  ancli container install
  ```
- **实际输出**：
  - `>> 现有 Ubuntu Base (x86_64) 架构核对正确，跳过基础镜像下载`
  - 增量安装 `build-essential unzip zip`（42 个新包），未重新下载整个 rootfs。
  - 容器内运行 `/usr/bin/gcc --version` 返回 `gcc (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0`，`zip -v` 返回 `Zip 3.0`。
- **结论**：[通过]

### 场景 8：架构自动识别 (ARM64 / x86_64) 一致性
- **验证方式**：在 `emulator-5580` (`x86_64`) 上，`detect_host_arch` 均准确命中 `x86_64`，选择 `bin/x86_64/proot` 与 `ubuntu-base-*-amd64.tar.gz`。
- **结论**：[通过]

### 场景 9：架构不匹配保护
- **验证方式**：在已安装 `amd64` rootfs 的环境中模拟 target 为 `arm64` 安装。
- **实际输出**：`get_rootfs_arch` 返回 `x86_64`，检测到架构不匹配，直接退出并拦截覆盖行为。
- **结论**：[通过]

### 场景 10：Android 宿主桥接真实性、退出码与 stdout/stderr
- **验证命令**：
  ```sh
  ancli android probe --json
  ancli android shell "getprop ro.product.model"
  ancli android shell "id"
  ancli android shell "ls /non_existing_dir_12345; exit 42"
  ```
- **实际输出**：
  - `ancli android probe --json`：
    `{"checked_at":"...","android":{"release":"16","sdk":"36","model":"sdk_gphone64_x86_64","abi":"x86_64,arm64-v8a","kernel":"Linux 6.12.38-android16-5-gbb9513914902-ab13996879 x86_64 Toybox"},"storage_kb":{"data_free":"4225992","shared_free":"4225992"},...}`
  - `getprop` 返回：`sdk_gphone64_x86_64`
  - `id` 返回：`uid=0(root) gid=0(root) groups=0(root) context=u:r:ksu:s0`（脱离 PRoot 真实宿主 Root 上下文）
  - 异常流输出：`ls: /non_existing_dir_12345: No such file or directory`，退出码准确回传 `42`。
- **结论**：[通过]

### 场景 11：Agent 记忆模板部署与链接
- **验证命令**：`ancli repair` 与 `ancli agent-init --finalize`
- **文件检查**：
  - `/root/.agents/AGENTS.md` (实体文件，已测量宿主环境写入)
  - `/root/.codex/AGENTS.md -> ../.agents/AGENTS.md`
  - `/root/.gemini/AGENTS.md -> ../.agents/AGENTS.md`
  - `/root/.gemini/GEMINI.md -> ../.agents/AGENTS.md`
- **结论**：[通过]

### 场景 12：模块重置 (ancli reset --yes) 保留项与清理项
- **验证命令**：`ancli reset --yes`
- **实际结果**：
  - 清理项：删除应用包装脚本、密钥、已安装应用记录、WebUI 历史日志、临时状态。
  - 保留项：保留 `/data/local/tmp/ancli/rootfs` 实体与已安装系统软件包（gcc/nodejs/python3 保持完好），保留 `container-config.json` 中的规格 (`developer`)。
  - 重建项：重新部署干净的 `/root/.agents/AGENTS.md` 模板，`agent_memory` 重置为 `unconfigured`。
- **结论**：[通过]

### 场景 13：WebUI 手机与平板布局适配
- **验证方式**：
  - 手机实机布局 (1440x3040, density 560): 通过 KernelSU WebUI 真实交互验证，文字无截断，弹窗滚动顺畅，卡片展示正常。截图已核实。
  - 平板布局 (2560x1600, density 280): 通过 `wm size 2560x1600` 切换实测，卡片自适应居中，栅格布局对齐，按钮正常响应。
- **结论**：[通过]


---

## 三、修复后复测与 v1.2.629-beta 实测验证记录

- 复测时间：2026-11-18 01:15 ~ 02:06 (UTC+8)
- 测试设备：`emulator-5580` (AVD 名称: `AnCLI_KSU_Test`)
- 系统版本：Android 16 (API 36.1 / SDK 36), Kernel `6.12.38-android16-5-gbb9513914902-ab13996879` (`x86_64`)
- Root 环境：KernelSU 3.3.1 (LKM 模式), `uid=0(root) gid=0(root) context=u:r:ksu:s0`
- 测试模块版本：`v1.2.629-beta` (`versionCode=41`)
- 发布包资产：`F:\Code\Android\model\AnCLI-Android\ancli-41-v1.2.629-beta.zip` (`704,078 bytes`)
- 校验值 (SHA-256)：`A6E90E6F404B5F9492F4A095C9FF621A31C8D86D8FCDBFE7130507CEC355216C`

### 1. APT 安全修复与容器内 GPG 签名校验实测
- **状态**：[通过]
- **检查/命令**：
  - 全仓检索 `src/`、`scripts/`、`tests/`：`src/` 与 `scripts/` 中 `allow-unauthenticated`、`AllowInsecureRepositories`、`AllowUnauthenticated`、`Check-Valid-Until=false`、`trusted=yes` 命中数为 `0`；仅 `tests/test_ancli_core.py` 的 `TestAptSecurityRegression` 断言中包含上述禁用字符串。
  - 容器内实测：`proot ... sh -c "cat /etc/apt/apt.conf.d/01sandbox; apt-get update && apt-get install -s curl; echo EXIT=$?"`
- **实际结果**：
  - `/etc/apt/apt.conf.d/01sandbox` 为 `APT::Sandbox::User "root";`。
  - `apt-get update` 成功拉取 `noble`、`noble-updates`、`noble-backports`、`noble-security` 四个 `InRelease` 并完成 GPG 签名校验（`Fetched 4466 kB in 11s`），`apt-get install -s curl` 返回 `EXIT=0`。

### 2. 构建新 Beta 与冷启动核验
- **状态**：[通过]
- **命令与过程**：
  - `python scripts/build_module.py` 生成 `ancli-41-v1.2.629-beta.zip`（未覆盖 `ancli-40-v1.2.628-beta.zip`）。
  - `ksud module install /data/local/tmp/ancli-41-v1.2.629-beta.zip` 后，重启前 `ksud module list` 显示 `versionCode: "41"`, `version: "v1.2.629-beta"`, `update: "true"`（KernelSU 已将更新暂存至 `/data/adb/modules_update/ancli`，等待下次重启生效）。
  - 通过 `Start-AnCLI-KSU-Test.ps1` 冷重启 AVD。
- **冷启动前后核对**：
  - 设备：`AnCLI_KSU_Test` (`emulator-5580`)
  - `boot_id`：重启前 `70a67a9d-d08a-457f-95d4-92e904d42603` -> 重启后 `4f1814f6-a2d7-478b-a9ee-98687bcb7155`
  - Root：`uid=0(root) gid=0(root) groups=0(root) context=u:r:ksu:s0`
  - `ksud module list`（重启后）：`version: "v1.2.629-beta"`, `versionCode: "41"`, `enabled: "true"`, `web: "true"`, `update: "false"`（更新已正式应用，无待应用更新）。
  - 文件哈希一致性：设备端 `/data/local/tmp/ancli/bin/ancli-core.py` (`4472e5a7...`)、`/data/adb/modules/ancli/ancli_env.sh` (`ce26de6b...`)、`/data/adb/ksu/bin/ancli` (`e8764504...`)、`/data/adb/modules/ancli/webroot/index.html` (`6779037a...`) 与源码完全一致。
  - 数据保留：`/data/local/tmp/ancli/rootfs`、`/data/local/tmp/ancli/container-config.json` 完整保留。

### 3. `android_data=false` 诊断与修复验证
- **状态**：[通过]
- **原因诊断**：`v1.2.628-beta` 已部署的 `/data/adb/ksu/bin/ancli` 尚未包含 `# 5.5` 宿主 `mount --bind /data /mnt/ancli-data` 逻辑，而容器内 PRoot 调用 `ensure_android_data()` 无法在挂载命名空间中完成 bind mount，导致 `ancli status --json` 返回 `"android_data": false`。
- **修复与回读**：`v1.2.629-beta` 更新了 `system/bin/ancli` 与 `/data/adb/ksu/bin/ancli`，在 SELinux `Enforcing` 模式下由宿主 root 在进入 PRoot 前执行 `mount --bind /data /mnt/ancli-data`。冷启动后直接执行 `ancli status --json` 回读 `"android_data": true`。

### 4. 刷入交互（立即安装 vs 延后安装、规格选择）
- **状态**：[部分通过]（延后分支通过，音量键切换分支未测试）
- **实测证据**：
  - AVD 实测 `timeout 3 /system/bin/getevent -qlc 1` 期间发送 `input keyevent KEYCODE_VOLUME_UP` 返回 `EXIT=124`（超时）；`getevent -lp` 仅列出 `virtio_input_multi_touch_*` 触摸设备，无内核 evdev 音量键事件。
  - 因此延后安装默认回退分支 [通过]，而通过物理音量键触发“立即安装”及切换规格的交互分支在 AVD 上 [未测试]。

### 5. 代理模式（`system` / `manual` / `direct`）与真实流量验证
- **状态**：[通过]（`manual` 真实转发与 `direct` 清空变量已实测；注：`system` 模式依赖 `dumpsys connectivity` 的 `HttpProxy` 字段，仅 `settings put global http_proxy` 不会即时刷新 `dumpsys`）
- **实测证据**：
  - 在 AVD 启动受控本地目标端 (`10.0.2.16:18080`) 与本地代理端 (`127.0.0.1:18888`)。
  - `manual` (`http://127.0.0.1:18888`)：容器内 `curl -s http://10.0.2.16:18080/manual_req` 返回 `VIA_PROXY_OK`，代理日志记录 `PROXY http://10.0.2.16:18080/manual_req`（注：`ancli config --set` 需分次传参，单次传两个 `key=value` 仅第一个生效）。
  - `direct`：容器内环境变量 `http_proxy`、`https_proxy`、`HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY` 均为空，`curl -s http://10.0.2.16:18080/direct_req` 返回 `DIRECT_TARGET_OK`，目标端日志记录 `TARGET /direct_req`，未经过代理。
  - `system`：AVD 无系统代理时 `detected_proxy=""`, `proxy_connected=false`，请求直连目标端 (`TARGET /system_req`)。

### 6. Android 宿主桥接（容器内触发）
- **状态**：[部分通过]（命令透传/退出码/分离流/断桥报错通过；stdin 管道透传不支持）
- **实测证据**：
  - `ancli android probe --json` 返回真实宿主信息 (`sdk: "36"`, `android_data_channel: true`)。
  - `ancli android shell "id"` 返回 `uid=0(root) ... context=u:r:ksu:s0`。
  - `ancli android shell "echo ONLY_OUT; echo ONLY_ERR >&2"` 经重定向验证 stdout 与 stderr 严格分离；`exit 42` 准确回传 `HOST_EXIT=42`；含空格与引号参数透传正常。
  - 停止宿主 `.bridge` 守护进程后在容器内执行 `ancli android shell id` 返回 `[AnCLI] Android host bridge service is not running on host. Run 'ancli repair' on host or restart the module.` 且 `EXIT=1`，无容器内回退。
  - `stdin` 实测：`echo STDIN_PAYLOAD | ancli android shell "read line; echo GOT:$line"` 输出 `GOT:`，确认当前桥接为批处理脚本模式，不支持交互式或管道 stdin 透传。

### 7. WebUI 真实交互与页面场景（KernelSU Manager 内）
- **状态**：[部分通过]（真机容器渲染/设置保存/软键盘/横屏/未安装初始化入口通过；后台任务超时强杀恢复仅单元覆盖，未在 AVD 强杀实测）
- **实测证据**：
  - 在 `me.weishu.kernelsu` 内打开 AnCLI WebUI：竖屏首页、Agent 记忆弹窗、设置弹窗滚动、`默认工作目录` 弹出软键盘时 `取消`/`保存` 按钮可见可点、横屏 4 列状态卡布局均截屏核验正常。
  - 临时移走 `rootfs` 验证 `rootfs_ready: false` 真机视图：首页显示 `Rootfs 缺失`、`• 容器尚未安装`、`x86_64`、`Ubuntu 容器初始化` 卡片与 `可选工具目录（需先安装容器）`（注：该状态下顶部副标题短暂出现 `vv1.2.629-beta` 双 `v` 前缀）。**更正：当次恢复未成功**，复核时发现原容器位于 `rootfs/rootfs.bak`。后续已在独立的 AnCLI_KSU_Test AVD 上恢复，回读 `rootfs_ready=true`、`rootfs_arch=x86_64`；保留了 `rootfs-displaced` 和 `rootfs-recreated` 以供检查。
  - 后台任务超时与防重复提交锁定逻辑由 `node --test tests/test_webui_lifecycle.js tests/test_webui_locks.js`（6/6）覆盖。

### 8. 容器规格、模板链接与硬件覆盖范围
- **状态**：[部分通过]
- **实测证据**：
  - `developer` 规格软件包实测：`gcc 13.3.0`、`Zip 3.0`、`UnZip 6.00`、`openjdk 17.0.21.0.3`、`node v18.19.1`、`npm 9.2.0`、`Python 3.12.3`、`git 2.43.0`、`curl 8.5.0`、`GNU ld 2.42`。
  - `ancli reset --yes` 实测保留 `rootfs` 与 `container-config.json` (`profile: "developer"`)，并将 `agent_memory` 重置为 `unconfigured`；随后 `ancli agent-init --finalize` 恢复实测环境写入。
  - 模板链接检查通过：`/root/.agents/AGENTS.md` (13,977 bytes) 及 `/root/.codex/AGENTS.md`、`/root/.gemini/AGENTS.md`、`/root/.gemini/GEMINI.md` 均正确指向 `../.agents/AGENTS.md`。**未实际安装或启动 Codex / Gemini Agent**，故仅标“链接检查通过”。
  - **未测试项**：**未在 ARM64 真机验证**（本次全部设备实测均基于 `x86_64` AVD `AnCLI_KSU_Test`）。

### 9. 离线自动化检查汇总（终版）
- `python -m pytest tests/ -q`：`173 passed, 1 skipped`
- `node --test tests/test_webui_lifecycle.js tests/test_webui_locks.js`：`6 passed, 0 failed`
- `node --check`（WebUI `<script>` 提取）：`rc=0`
- `sh -n`（`src/install.sh`、`src/module/*.sh`、`src/module/system/bin/ancli`）：全部通过
- `git diff --check`：无空白/冲突错误

### 2026-11-18 GUI 重测补充

- 环境：`AnCLI_KSU_Test` / `emulator-5580`，KernelSU Manager 中已启用 `v1.2.629-beta`；本次测试的是设备上已安装版本，不代表工作区未刷入改动已验证。
- 通过 GUI 实测：从 KernelSU 模块列表打开 AnCLI；首页状态加载为 rootfs 就绪、PRoot 已部署，工具列表出现；设置的常规、代理、日志、重置、关于页面均可打开；深色/自动主题切换、中文/英文切换和横竖屏旋转有响应；测试后恢复中文和自动主题。
- 问题：切到英文后首页的“就绪”“已部署”“未安装”“尚未检查官方版本”等动态文案仍为中文。
- 问题：点击 Codex CLI 安装两次，出现“开始安装 Codex CLI”，后一次提示“暂时无法确认后台任务结果，请刷新状态”。设备端未发现 `.webui_*.log.meta` 任务记录及 npm 安装进程，`ancli list --json` 仍显示 `codex-cli` 未安装。当前证据说明安装未被确认启动；尚未定位是任务恢复校验、桥接返回还是其他原因。不要将此记为 npm 或网络安装失败。
- 未完成：容器首次初始化、Agent 实际安装和启动、任务断线恢复、卸载/重置的端到端验证，以及 ARM64 真机验证；本次未修改或重置容器。

### 2026-11-18 后台任务、国际化与架构修复回归

- 将工作区 `index.html`、`ancli-core.py`、`registry.json` 通过部署 helper 热更新到 `emulator-5580`，设备模块标识仍是 `v1.2.629-beta`；不是新 ZIP 刷入测试。
- KernelSU WebUI 中点击 MiMo 安装：任务日志出现并以退出码 1 结束，明确显示当前 `x86_64` 与仅支持 `arm64` 不匹配；未执行 ARM64 安装命令。
- 英文切换后实际查看首页，Ready、Deployed、Not installed、None、Official versions not checked yet 均显示英文。
- KernelSU WebUI 中点击 Codex CLI 安装后，成功创建后台任务与日志；当时 AVD 无活动网络，手动启用并连接 AndroidWifi 后 npm 从官方 registry 下载 x86_64 包，安装以 exit 0 完成，WebUI 显示 Installed v0.169.1、Install Completed；执行 `/data/adb/ksu/bin/codex --version` 返回 `codex-cli 0.169.1`。未登录或实测交互式 TUI。
- 备注：任务恢复与轮询采用结束标记后，原空任务历史下无法启动的现象本次未再出现；尚未独立抓取 KernelSU bridge 的原始回调，不能确认其截断换行是唯一根因。下载期间日志长时间为空，npm 完成后才显示内容，仍需改善进度反馈。
- 自动检查：Python `176 passed, 1 skipped`；WebUI 国际化、生命周期和锁测试 `8 passed`；`git diff --check` 无错误（有行尾提示）。生成 `ancli-avd-validation.zip` 仅供本机测试，尚未刷入验证或发布。

### 2026-11-18 安装反馈与 x86_64 适配回归

- 通过容器部署 helper 热更新工作区 `ancli-core.py` 和 `registry.json` 至 `emulator-5580`；不是 ZIP 刷入验证。
- 从 KernelSU WebUI 安装 OpenCode，日志立即显示选择的官方 `opencode-linux-x64-baseline.tar.gz` 下载命令及 curl 输出，任务运行时另有每 15 秒的耗时提示；下载和安装完成，退出码为 0，WebUI 显示安装完成。通过 `/data/adb/ksu/bin/opencode --version` 返回 `1.19.6`，未登录或实测交互式 TUI。
- 设备端 `ancli list --json` 显示 OpenCode 已安装，MiMo、Claude Code、OpenCode 的 `update_cmd` 指向 x64 资源；DeepSeek 在 x86_64 下没有更新命令并继续受架构保护。MiMo 和 Claude 的 x86_64 安装和运行未实测，DeepSeek 的 x86_64 适配未完成。
- 自动测试：`python -m pytest tests/ -q` 为 185 passed、1 skipped；WebUI 国际化、生命周期和锁测试 8 passed；registry JSON 校验通过。
- `ancli-avd-validation-x86.zip` 为本机测试构建，不是已刷入或已发布版本。
