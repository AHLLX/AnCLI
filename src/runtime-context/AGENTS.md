<!-- ancli:runtime-context v1 -->
<!-- template maintained 2026-11-19 -->
# AnCLI Runtime Context

这份文件由 AnCLI 写入容器全局路径 `/root/.agents/AGENTS.md`。Codex CLI 通过 `/root/.codex/AGENTS.md` 读取同一份内容，Antigravity 通过 `/root/.gemini/AGENTS.md` 或 `/root/.gemini/GEMINI.md` 读取同一份内容；这些入口均由 AnCLI 建立为指向规范文件的软链接。它记录运行环境、路径和操作边界，属于设备运行时上下文，不是项目开发规则文件。旧版手机记忆只能作为历史参考，不能覆盖当前命令和路径探测结果。不要把 API key、cookie、token 或其他密钥写入这里。

## 当前环境

- AnCLI 版本：`{{ANCLI_VERSION}}`
- 配置的默认工作目录：`{{DEFAULT_CWD}}`
- 裸 `/` 或不可映射目录的 TUI 回退目录：`/root/.ancli-work`
- Ubuntu/PRoot 根目录（宿主路径）：`{{ROOTFS}}`
- AnCLI 数据目录：`{{ANCLI_DIR}}`
- Android 真实 `/data` 通道：`{{ANDROID_DATA_MNT}}`（可用性用 `ancli status --json` 查看）
- 共享 skills 根目录：`{{SHARED_SKILL_DIR}}`
- 当前已安装工具：`{{INSTALLED_APPS}}`
- 已保存的配置变量名（不含值）：`{{CONFIGURED_ENV}}`

Codex 的全局规则入口是 `/root/.codex/AGENTS.md`；AGY 的全局规则入口可能是 `/root/.gemini/AGENTS.md` 或 `/root/.gemini/GEMINI.md`。AnCLI 将这些入口都链接到本文件；规范源仍是 `/root/.agents/AGENTS.md`。如果某个 Agent 报告没有加载记忆，先检查它自己的官方发现路径和 `readlink -f` 结果。进入具体仓库后，仓库根目录到当前目录之间的项目级 `AGENTS.md` 仍按工具规则优先于全局入口。

在 Android PRoot 中，Codex 默认命令沙箱的 bubblewrap 挂载隔离可能无法建立；安装 bubblewrap 包并不保证修复 PRoot 的挂载兼容问题。需要执行命令时，可在 Codex 输入框运行 `/permissions` 并由用户手动选择 `Full Access` 后重试。该模式关闭 Codex 自身命令沙箱，只在用户信任的工作区使用；AnCLI 不自动更改权限。

<!-- ancli:first-run:pending -->
## 首次环境确认（按需执行）

在用户进行系统开发、模块修改或排障任务时，先调用宿主侧 `ancli android probe --json` 一次性读取 Android 版本、架构、存储、网络和挂载事实，再按需调用当前容器内的 `ancli doctor --json` 与 `ancli status --json` 复核环境状态。日常通用问答或普通指令无需主动发起并发系统全量探测。
若需固化环境探测结果，可检查挂载与常用宿主命令后执行 `ancli agent-init --finalize`，由 AnCLI 将探测结果记录并删除本节。以后以实测内容为准。

<!-- ancli:first-run:end -->
工具通过宿主 wrapper 进入单层 Ubuntu PRoot。应用 wrapper 会加载 `ancli_env.sh` 继承 Android 系统代理并将宿主机 DNS 写入 `/data/local/tmp/ancli/.dns_cache`（供开机、安装或 `ancli repair` 刷新容器 `/etc/resolv.conf`），设置 `TMPDIR=/tmp`、`TERM`、`LANG=C.UTF-8` 和 `BROWSER=/usr/local/bin/xdg-open`（主入口 `ancli` 同样加载环境桥接，但不单独注入 `BROWSER`；`dsh web` 后台进程同样显式注入 `BROWSER=/usr/local/bin/xdg-open`），并按宿主机存在性动态绑定 `/sdcard`、`/storage`、`/mnt`、`/data` 等 Android 路径。
- **容器内 ancli 入口**：容器已内置 `/usr/local/bin/ancli`（并在 `/usr/bin/ancli`、`/bin/ancli` 提供软链接），可直接在容器内无前缀调用 `ancli status --json`、`ancli doctor`、`ancli agent-init --finalize`、`ancli shell [命令...]`；宿主机侧执行 `ancli shell` 可直接进入容器 Ubuntu 24.04 交互式登录 Bash（`/bin/bash -l`），带参数时则在容器内执行对应命令后退出。
- **容器内 PATH 注入与优先级**：应用 wrapper 与容器 `/etc/environment` 设定为 `PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/root/.local/bin:/data/adb/ksu/bin:/data/adb/ap/bin:/system/bin`（`ancli_env.sh` 还会附加 `:/system/xbin`）。Ubuntu GNU 工具与 `/root/.local/bin` 优先；Root 动态工具目录次之；`/system/bin` 仅作兜底。若当前设备的 `/apex`、`/linkerconfig` 与 SELinux 策略兼容，容器内可能可直接调用 `getprop` 等 Android 原生只读检测命令；实际可用性须先复核，不可用时请改用 `ancli android probe --json` 或 `ancli android shell` 宿主桥接。
- **PRoot 命令执行规范与沙箱约束**：Android 内核限制下，PRoot 基于 ptrace 用户态虚拟化，不支持 Linux User Namespace / Seccomp 沙箱隔离层（调用此类沙箱如 `clone(CLONE_NEWUSER...)` 会直接报错 `invalid argument`）。在容器内执行工具命令时，禁止启用沙箱（Sandbox）模式，须使用直通/非沙箱模式。
- **宿主 bash 兼容入口（Dual-Injection）**：AGY 的远程命令执行器可能在进入容器前先调用宿主 `bash -c`；AnCLI 会在 `/data/adb/modules/ancli/system/bin/bash`（Path A）以及 `/data/adb/ksu/bin/bash`、`/data/adb/ap/bin/bash`（Path B）提供仅转发 POSIX 命令到 `/system/bin/sh` 的兼容入口，宿主外部进程默认命中。它不是 GNU Bash，也不是第二套运行后端；需要 Bash 专有语法时仍应在容器内使用 `/bin/bash`。

## 设备观察（版本和路径可能变化）

以下内容是跨设备的工作提示，不是能力保证；开始任务时仍要用 `command -v`、`pwd` 和实际命令结果复核。设备探测结果优先于本文件。

- Android 版本、内核和架构会随设备变化；容器常见为 Ubuntu 24.04 arm64（亦支持 x86_64 宿主与容器，具体以实际探测为准；部分工具如 `dsh` 仅支持 `arm64`）。先用宿主侧 `ancli android probe --json`，再用 `ancli doctor --json` 复核，不要把模板中的版本当成当前事实。`/root` 是容器 HOME，通常不是项目仓库，先进入目标项目再工作。
- `/data` 通常比共享存储更适合缓存和编译；`/sdcard` 及其底层挂载在不同 Android 版本上性能不同。临时文件、构建缓存和大文件优先放 `/data/local/tmp/build_cache` 或其他 `/data` 路径，最终用户文件再放共享存储。
- 启动前先确认工具是否存在。按所选容器规格预装不同组件：`minimal` 及以上提供 `bash`、`sh`、`apt`、`curl`、`ca-certificates`、`python3`、`pip`、`git` 和 `Node.js/npm`（容器引导会准备 Node 24）；`standard`（默认规格）及以上额外提供 `zip`、`unzip`；`developer` 及以上额外提供 `build-essential`、`binutils`（`strings`、`readelf`、`objdump`、`nm` 等）、`java`（OpenJDK 17 JRE）与 `adb`；`full` 额外提供 `jq`、`rg`（`ripgrep`）、`less`、`file`、`procps`、`dnsutils`、`ssh`、`tmux`、`vim-tiny` 与 `fcitx5`。`protoc`、`wget`、`fd`、`gh`、Docker、其他编辑器等默认**未预装**，不要假定存在。宿主侧的 `bash` 兼容入口只支持 POSIX 命令转发，不要把它当作容器内 GNU Bash。
- Python 包按当前 registry 和项目约束安装；优先使用 venv 或 AnCLI 的安装入口，不要假设 PEP 668、镜像或 apt 参数在每台设备都相同。大型分析依赖优先放 venv。
- 部分 aarch64 PRoot 上 `bash -c 'test -x <file>'` 可能因 `faccessat2` 映射问题误报，`sh` 的 `test -x` 也可能误报。因此不要把 `test -x` 当作可执行性证明，改用 `command -v`、Python `os.access(..., os.X_OK)`、直接调用程序，或显式 `java -cp`。从压缩包解出的启动文件需要先 `chmod +x`。
- APK 静态分析的建议容器内工作目录是 `/workspace/apk`（解包）和 `/workspace/out`（报告/源码）；`/workspace` 不是预建目录或宿主机绑定挂载，使用前按需 `mkdir -p` 创建。`agy`、`grok` 等注册表工具在当前设备均走 PRoot；不要假设它们能作为 Android 原生 ELF 直接执行。
- Android bionic 命令默认由宿主 wrapper 调用。若容器的 `PATH` 明确包含 `/system/bin` 且权限允许，`getprop`、`pm`、`dumpsys` 或 `cmd` 可能也能在容器内读取状态；先用 `command -v` 和实际命令结果复核。浏览器跳转仍由宿主桥接处理；不要在记忆文件中写入具体代理地址、账号、模型端点、cookie 或 token。

## 当前 Android 宿主探测规则

- 首次运行先执行 `ancli android probe --json`，再执行 `ancli doctor --json`，根据实际结果判断路径和命令能力。不要把旧设备记忆或模板中的能力描述当成当前事实。
- 当前 wrapper 明确绑定 `/system`、`/data`、`/sdcard`、`/storage`、`/mnt`、`/apex`、`/linkerconfig` 和 `/data/adb`。`/vendor` 可能只是 rootfs 空目录，`/product` 可能未绑定。使用前先用 `stat`、`mount` 或 `ancli doctor --json` 复核。
- 如果 `PATH` 中加入 `/system/bin` 后 `getprop`、`pm`、`dumpsys` 或 `cmd` 能运行，可以在容器内调用它们读取 Android 状态。调用失败时通过宿主桥接命令处理，不要循环猜测路径。
- 修改 Android 宿主文件时先用 `ancli android shell` 读取目标、权限和 inode，再执行用户明确要求的宿主命令。不要把 `/vendor` 的 rootfs 空壳写入当成真机修改。
- 宿主便捷读取可使用 `ancli android probe [--json]`、`ancli android list [--json] <路径>`（或 `<路径> --json`）、`ancli android stat <路径>` 和 `ancli android read <路径>`；需要完整 Android 能力时使用 `ancli android shell <宿主命令>`。容器内调用会通过 `/data/local/tmp/ancli/.bridge` IPC 投递到宿主 `/system/bin/sh`，属于**非交互式批处理（无交互 stdin，单次超时 60 秒）**并原样返回 stdout/stderr 与退出码。不要把容器内路径和宿主路径混淆；实际权限由 Android 身份、SELinux 和目标命令决定。

## APK 与 Android 工具链

- 解包放容器内建议目录 `/workspace/apk/<名称>`，报告和反编译源码放 `/workspace/out/<名称>`（非预建目录，需先 `mkdir -p`）；报告头部记录实际工具版本。
- 设备架构通常是 aarch64，但先用 `uname -m` 复核。Google SDK 中的 x86_64 `aapt2`、`d8`、`zipalign` 等不能直接执行；**严禁在正在运行的 `dsh web` 或交互式 TUI 等活跃 PRoot 会话内直接执行 `apt-get install` 或 `dpkg --configure`**（`dpkg` 的 `postinst` / triggers 及半配置 `systemd` 极易触发 PRoot `compare_paths2: length2 > 0`（`SIGABRT` / signal 6）断言异常，导致 `exit 143` 并连带杀掉整个 `dsh web` 会话）。需要原生工具或补充库时，改用 `apt-get download <pkg>` 后通过 `dpkg-deb -x <deb> <隔离目录>` 解包到 `/data/local/tmp/build_cache`（或 `/workspace`），或改用纯 Java 的 `d8.jar`、`apksigner.jar`、`ecj.jar`、官方预编译 tarball、npm/pip 隔离安装；`android.jar` 与 `aapt2` 版本不兼容时，优先按项目记录选择较旧的 API stub。
- PRoot 下不要把 `dpkg --configure -a` 当成修复手段；半配置的 `systemd` 等系统服务 `postinst` 会触发 PRoot 断言崩溃。安装依赖遵循 AnCLI registry 的命令和安全参数，额外 deb 一律通过 `apt-get download` + `dpkg-deb -x` 放入 `/data/local/tmp/build_cache` 的隔离目录验证使用。
- jadx、androguard、protobuf、ELF 符号和 DEX 对比按需安装/调用；在 PRoot 中类似 `./tools/jadx/bin/jadx` 的包装启动脚本可能因 `faccessat2` 或路径检查失败，此时应直接回退为 `java -cp ...` 显式调用 jar 主类。调用前先 `command -v` 或 `test -f` 检查 jar/二进制是否存在，再把版本、输入路径和输出路径写进报告。

## dsh 与交互工具

- `dsh web` 使用命令输出的完整带 token URL；只想打印地址时使用 `dsh web --no-open`。token URL 属于当前会话入口，不要把完整地址写进 `AGENTS.md`、skill、脚本或提交记录，也不要把不带 token 的 `127.0.0.1:3080` 当作已认证地址。
- 从 `/` 或不可映射目录启动交互式 Node/TUI 工具时，AnCLI wrapper 会优先使用 `default_cwd`；未配置时使用小型 `/root/.ancli-work`，避免扫描包含大量 Codex 缓存和会话的 `/root`。进入项目目录后再运行时，wrapper 保留当前目录，不会改成默认目录。
- 任何工具的配置、认证和环境变量都只记录变量名和文件位置，不复制值；需要变更时用 `ancli config` 或工具自己的配置命令。

## 工作指导

1. 普通日常对话与代码任务无需每次执行全套探测；仅当任务涉及 Android 宿主开发、模块修改或系统排障时，优先调用 `ancli android probe --json` 一次性读取宿主事实，再按需复核 `ancli doctor --json` 与 `ancli status --json`，确认当前目录、Android 宿主事实、rootfs、应用数据通道和工具状态。
2. 把 `/root` 当作容器私有 HOME；把 `/sdcard`、`/storage/emulated/0` 当作手机共享存储；把 `/mnt/ancli-data/data/<包名>` 当作跨 App 读取真实 `/data` 的入口。
3. 从 `/` 或不可映射目录启动工具时使用上面的默认工作目录；要修改它，运行 `ancli config --set default_cwd=/绝对路径`。留空会恢复内置回退目录 `/root/.ancli-work`。
4. 使用 `ancli config <app_id>` 或管理器 WebUI 配置 API key、代理和 endpoint。不要把密钥写入 `AGENTS.md`、skills、wrapper、shell 历史或提交内容。
5. 查看官方版本时运行 `ancli check`；只读脚本或 WebUI 使用 `ancli list --json`。`installed_version` 必须来自工具自己的 `version_cmd`，不能手写成 AnCLI 版本。
6. 共享 Agent skills 放在 `{{SHARED_SKILL_DIR}}/<name>/SKILL.md`，运行 `ancli skills` 修复共享目录和各工具链接。多步骤流程写成 skill；稳定事实和约束留在本文件或项目自己的 `AGENTS.md`。
7. AnCLI 只负责把 Agent 桥接到 Android 宿主，不替 Agent 决定目标路径或操作类型。涉及模块 wrapper、`/system/bin` 或 `/data/adb` 时，先确认当前任务目标，再执行对应宿主命令并回读结果；AnCLI 自身安装、升级或修复后运行 `ancli repair`。
8. `dsh web` 使用命令输出的带 token URL；需要只打印地址时使用 `dsh web --no-open`。不要把没有 token 的 `127.0.0.1:3080` 书签地址当作已认证入口。
9. 报告验证结果时区分源码检查、容器内运行和真实 Android 设备验证；没有看到设备证据时，不要声称已经刷机、重启或完成长时间稳定性验证。
10. `/data/data`、`/etc`、`/usr`、`/system` 和 `/data/adb` 是高影响路径。用户明确要求时可以通过 `ancli android shell` 操作；先确认目标、权限、回滚方式并在操作后回读状态，不要自行改 SELinux、持久化挂载或杀掉其他进程。
11. Agent 和 Python、Node、git、curl 等工具运行在 Ubuntu PRoot 内；Android 的 `su`、`am` 和修改型系统操作属于宿主能力。`pm`、`dumpsys`、`getprop`、`cmd` 等读取命令可能通过 `/system/bin` 暴露到容器，但必须先探测其可用性，不要把容器内的同名路径自动当成宿主能力。
12. 容器可以通过绑定路径读写手机文件。共享存储使用 `/sdcard`，跨 App 的真实数据使用 `/mnt/ancli-data/data/<包名>`。涉及删除或批量修改时先列出目标并保存 dry-run 结果。
13. WebUI 的设置窗口与 `ancli config` 共用全部 7 个全局配置键：`default_cwd`、`lang`、`proxy_mode`、`proxy_url`、`agent_memory`、`update_channel` 与 `container_profile`（前 6 项保存在 `/root/.ancli-config.json`，`container_profile` 对应 `/data/local/tmp/ancli/container-config.json` 的 `profile`）；修改全局配置优先使用这两个入口，不要另建配置文件。主题只属于 WebUI 本地偏好。
14. `dsh` 在 AnCLI registry 中声明 `proxy_mode: "direct"`，其 wrapper 会在加载 `ancli_env.sh` 后强制清空代理与 `npm_config_*proxy` 变量；其他应用 wrapper 通过 `ancli_env.sh` 读取 `/root/.ancli-config.json` 的 `proxy_mode`（`system` / `manual` / `direct`）与 `proxy_url`，而 `ancli` 主入口与容器安装器还会按 `/data/local/tmp/ancli/container-config.json` 的 `network_mode` 覆盖代理设置（修改全局代理建议同步使用 WebUI 或 `ancli config --set proxy_mode=...` 与 `ancli container set --network=...`）。开始下载或更新前先确认实际网络路径。
15. 严禁在容器内对宿主 AnCLI 目录运行全局递归遍历扫描（如 `find /data/local/tmp/ancli`）。宿主该目录包含容器 rootfs 实体镜像，扫描其嵌套的 proc/sys 等虚拟节点会触发 PRoot 底层路径断言异常（`readlink_proc: comparison == compare_paths`）导致进程闪退（Terminated 143）。代码与工具检索请限定在目标项目或 `/root` 内。

## 常用命令

```sh
ancli status --json
ancli doctor --json
ancli list --json
ancli check
ancli config --json
ancli config --set default_cwd=/sdcard/projects
ancli config --set proxy_mode=system
ancli config --set proxy_url=http://host:port
ancli container status --json
ancli android probe --json
ancli android shell <命令>
ancli shell
ancli shell -c "<命令>"
ancli agent-init
ancli agent-init --finalize
ancli skills
ancli repair
```

## 文件边界

- 工具安装和版本来源由 `registry.json` 定义；优先修改 registry 数据，不要为单个工具复制核心分支。
- wrapper 必须保持完整 Android bind、TTY 恢复、代理/DNS 继承和 `/mnt/ancli-data` 通道。
- `--json` stdout 必须只有 JSON；诊断写 stderr。
- 模块管理器卸载和 WebUI 彻底卸载都会删除 rootfs、安装状态、密钥和配置；需要保留时先备份。覆盖更新不应删除现有容器。
