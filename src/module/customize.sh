#!/system/bin/sh
# ============================================================
# AnCLI 模块刷入安装钩子 (customize.sh)
# 触发时机：用户通过 Magisk / KernelSU / APatch 刷入模块 ZIP 时自动执行。
# 职责：准备运行目录、部署 PRoot、下载并解压 Ubuntu Base rootfs、
#       初始化 APT 基础工具链与 npm 路径、部署核心脚本与动态快捷入口。
# ============================================================

# 管理器框架自动注入环境变量与函数：$MODPATH, ui_print(), abort(), set_perm()
ANCLI_DIR="/data/local/tmp/ancli"
ROOTFS="${ANCLI_DIR}/rootfs"
BIN_DIR="${ANCLI_DIR}/bin"
UBUNTU_MIRROR="${ANCLI_MIRROR:-mirrors.tuna.tsinghua.edu.cn}"

# 0. 架构检测与下载辅助函数
detect_host_arch() {
    _mach=$(uname -m 2>/dev/null)
    case "$_mach" in
        aarch64|arm64) echo "arm64"; return 0 ;;
        x86_64|amd64) echo "x86_64"; return 0 ;;
    esac
    _abi=$(getprop ro.product.cpu.abi 2>/dev/null)
    case "$_abi" in
        arm64*|aarch64*) echo "arm64"; return 0 ;;
        x86_64*) echo "x86_64"; return 0 ;;
    esac
    echo "unknown"
    return 1
}

get_rootfs_arch() {
    _bash="$1/bin/bash"
    if [ ! -f "$_bash" ]; then
        echo "none"
        return 0
    fi
    _hex=$(od -An -tx1 -j 18 -N 2 "$_bash" 2>/dev/null | tr -d ' \r\n')
    case "$_hex" in
        b700|00b7|b7) echo "arm64"; return 0 ;;
        3e00|003e|3e) echo "x86_64"; return 0 ;;
        *) echo "unknown"; return 0 ;;
    esac
}

download_tarball() {
    _out="$1"
    _url="$2"
    if command -v curl >/dev/null 2>&1; then
        curl -f -L -A "curl/7.68.0" --connect-timeout 20 --max-time 600 --progress-bar -o "$_out" "$_url"
        return $?
    fi
    for _bb in /data/adb/ksu/bin/busybox /data/adb/ap/bin/busybox /data/adb/magisk/busybox busybox; do
        if [ -x "$_bb" ]; then
            "$_bb" wget -U "curl/7.68.0" -T 30 -O "$_out" "$_url"
            return $?
        fi
    done
    return 127
}

HOST_ARCH=$(detect_host_arch)
if [ "$HOST_ARCH" = "unknown" ]; then
    abort "Unsupported CPU architecture: $(uname -m 2>/dev/null)"
fi

if [ "$HOST_ARCH" = "x86_64" ]; then
    ROOTFS_ARCH="amd64"
    APT_REPO_TYPE="main"
else
    ROOTFS_ARCH="arm64"
    APT_REPO_TYPE="ports"
fi

UBUNTU_PATH_PRIMARY="ubuntu-cdimage/ubuntu-base/releases/24.04/release/ubuntu-base-24.04.5-base-${ROOTFS_ARCH}.tar.gz"
UBUNTU_PATH_FALLBACK="ubuntu-cdimage/ubuntu-base/releases/24.04/release/ubuntu-base-24.04.4-base-${ROOTFS_ARCH}.tar.gz"

# 从 module.prop 动态读取版本号用于刷入日志横幅，避免硬编码与发布元数据脱节
MODULE_VERSION="$(grep '^version=' "${MODPATH:-$(dirname "$0")}/module.prop" 2>/dev/null | cut -d= -f2)"
[ -n "$MODULE_VERSION" ] || MODULE_VERSION="unknown"

ui_print "============================================"
ui_print "  AnCLI Bootstrap Installer ${MODULE_VERSION}"
ui_print "============================================"
ui_print ""

# 1. 创建运行目录与共享内存目录
ui_print ">> Preparing directories..."
mkdir -p "$ROOTFS" "$BIN_DIR" "$ANCLI_DIR/shm"
# 挂载到容器 /dev/shm 的目录设为 1777（粘滞位），确保普通 shell 用户（uid 2000）可读写
chmod 1777 "$ANCLI_DIR/shm" 2>/dev/null || true

# 1.1 保存容器规格与延迟安装器。即使用户选择稍后通过 WebUI 安装，
#     宿主侧 `ancli container status/install` 仍然必须可用。
if [ -f "$MODPATH/ancli/container-profiles.json" ]; then
    cp -f "$MODPATH/ancli/container-profiles.json" "$ANCLI_DIR/container-profiles.json"
    chmod 644 "$ANCLI_DIR/container-profiles.json" 2>/dev/null || true
fi
if [ -f "$MODPATH/ancli/catalog-cache.json" ]; then
    cp -f "$MODPATH/ancli/catalog-cache.json" "$ANCLI_DIR/catalog-cache.json"
    chmod 644 "$ANCLI_DIR/catalog-cache.json" 2>/dev/null || true
fi
if [ -f "$MODPATH/container-bootstrap.sh" ]; then
    chmod 755 "$MODPATH/container-bootstrap.sh" 2>/dev/null || true
    cp -f "$MODPATH/container-bootstrap.sh" "$ANCLI_DIR/container-bootstrap.sh"
    chmod 755 "$ANCLI_DIR/container-bootstrap.sh"
fi
if [ -f "$MODPATH/android-probe.sh" ]; then
    chmod 755 "$MODPATH/android-probe.sh" 2>/dev/null || true
    cp -f "$MODPATH/android-probe.sh" "$ANCLI_DIR/android-probe.sh"
    chmod 755 "$ANCLI_DIR/android-probe.sh"
fi

# 1.2 新安装时使用音量键选择安装时机。按音量上键立即安装，音量下键
#     延后到 WebUI；无可用按键入口时安全回落到延迟安装。
if [ ! -f "$ANCLI_DIR/container-config.json" ]; then
    _container_mode="deferred"
    if [ -x /system/bin/getevent ]; then
        ui_print "选择容器安装方式：音量上=现在安装，音量下=进入 WebUI 后安装（默认延后）"
        _key_file="$ANCLI_DIR/.container-key.$$"
        _key_pid=""
        ( /system/bin/getevent -qlc 1 > "$_key_file" 2>/dev/null ) &
        _key_pid=$!
        _wait=0
        while [ "$_wait" -lt 20 ] && [ ! -s "$_key_file" ]; do
            /system/bin/sleep 1
            _wait=$((_wait + 1))
        done
        [ -n "$_key_pid" ] && kill "$_key_pid" 2>/dev/null || true
        _key_event=$(cat "$_key_file" 2>/dev/null)
        rm -f "$_key_file"
        case "$_key_event" in
            *KEY_VOLUMEUP*) _container_mode="installed" ;;
            *) _container_mode="deferred" ;;
        esac
    fi
    _container_profile="standard"
    if [ "$_container_mode" = "installed" ] && [ -x /system/bin/getevent ]; then
        ui_print "选择容器规格：音量上切换，音量下或电源键确认"
        _profile_index=2
        _profile_name="standard"
        _profile_wait=0
        ui_print "当前规格：standard，约 900 MB；Python、Git、Node.js、npm、Java 17、binutils。"
        ui_print "音量上切换，音量下或电源确认；20 秒无操作使用当前规格。"
        while [ "$_profile_wait" -lt 20 ]; do
            _profile_file="$ANCLI_DIR/.container-profile-key.$$"
            ( /system/bin/getevent -qlc 1 > "$_profile_file" 2>/dev/null ) &
            _profile_pid=$!
            _profile_tick=0
            while [ "$_profile_tick" -lt 1 ] && [ ! -s "$_profile_file" ]; do
                /system/bin/sleep 1
                _profile_tick=$((_profile_tick + 1))
            done
            [ -n "$_profile_pid" ] && kill "$_profile_pid" 2>/dev/null || true
            _profile_event=$(cat "$_profile_file" 2>/dev/null)
            rm -f "$_profile_file"
            case "$_profile_event" in
                *KEY_VOLUMEUP*)
                    _profile_index=$((_profile_index + 1))
                    [ "$_profile_index" -gt 4 ] && _profile_index=1
                    _profile_wait=0
                    ;;
                *KEY_VOLUMEDOWN*|*KEY_POWER*) break ;;
                *) _profile_wait=$((_profile_wait + 1)); continue ;;
            esac
            case "$_profile_index" in
                1) _profile_name="minimal"; _profile_summary="约 480 MB；Python、pip、Git、curl、证书。" ;;
                2) _profile_name="standard"; _profile_summary="约 900 MB；minimal 加 Node.js、npm、Java 17、binutils。" ;;
                3) _profile_name="developer"; _profile_summary="约 1120 MB；standard 加 zip、unzip 和编译工具。" ;;
                4) _profile_name="full"; _profile_summary="约 1480 MB；developer 加 Fcitx5 中文输入法组件。" ;;
            esac
            ui_print "当前规格：$_profile_name，$_profile_summary"
            ui_print "音量上切换，音量下或电源确认。"
            _profile_wait=$((_profile_wait + 1))
        done
        _container_profile="$_profile_name"
    fi
    case "$_container_profile" in
        minimal) _container_size=480 ;;
        standard) _container_size=900 ;;
        developer) _container_size=1120 ;;
        full) _container_size=1480 ;;
        *) _container_profile=standard; _container_size=900 ;;
    esac
    case "$_container_profile" in
        minimal) _profile_desc="Python、Git、curl、证书；适合轻量脚本和基础 CLI。"; _profile_contents="ca-certificates curl python3 python3-pip git" ;;
        standard) _profile_desc="最小环境加 Node.js/npm、Java 17、binutils；适合 Codex、AGY、Claude、OpenCode。"; _profile_contents="minimal + nodejs npm openjdk-17-jre-headless binutils" ;;
        developer) _profile_desc="标准环境加 zip/unzip 和 build-essential；适合 Android、源码编译和二进制分析。"; _profile_contents="standard + zip unzip build-essential" ;;
        full) _profile_desc="开发环境加 CJK 输入法组件；工具最全，占用空间最高。"; _profile_contents="developer + fcitx5 fcitx5-chinese-addons" ;;
    esac
    ui_print "规格说明：$_profile_desc"
    ui_print "包含：$_profile_contents；预计占用约 ${_container_size} MB"
    printf '{\n  "profile": "%s",\n  "install_mode": "%s",\n  "network_mode": "system",\n  "estimated_size_mb": %s\n}\n' "$_container_profile" "$_container_mode" "$_container_size" > "$ANCLI_DIR/container-config.json"
    chmod 600 "$ANCLI_DIR/container-config.json" 2>/dev/null || true
fi

_container_mode=$(sed -n 's/^[[:space:]]*"install_mode"[[:space:]]*:[[:space:]]*"\([^\"]*\)".*/\1/p' "$ANCLI_DIR/container-config.json" 2>/dev/null | head -n 1)
_container_profile=$(sed -n 's/^[[:space:]]*"profile"[[:space:]]*:[[:space:]]*"\([^\"]*\)".*/\1/p' "$ANCLI_DIR/container-config.json" 2>/dev/null | head -n 1)
[ -n "$_container_profile" ] || _container_profile="standard"

# 2. 从模块包部署静态编译的 PRoot 二进制
ui_print ">> Detected host architecture: $HOST_ARCH (container: $ROOTFS_ARCH)"
ui_print ">> Deploying PRoot for $HOST_ARCH..."
if [ "$HOST_ARCH" = "x86_64" ]; then
    if [ -f "$MODPATH/bin/x86_64/proot" ]; then
        cp "$MODPATH/bin/x86_64/proot" "$BIN_DIR/proot"
        cp "$MODPATH/bin/x86_64/loader"* "$BIN_DIR/" 2>/dev/null || true
    elif [ -f "$MODPATH/bin/proot" ]; then
        cp "$MODPATH/bin/proot" "$BIN_DIR/proot"
    fi
    chmod 755 "$BIN_DIR/proot" "$BIN_DIR/loader"* 2>/dev/null || true
else
    if [ -f "$MODPATH/bin/arm64/proot" ]; then
        cp "$MODPATH/bin/arm64/proot" "$BIN_DIR/proot"
    elif [ -f "$MODPATH/bin/proot" ]; then
        cp "$MODPATH/bin/proot" "$BIN_DIR/proot"
    fi
    chmod 755 "$BIN_DIR/proot"
fi
ui_print ">> PRoot deployed successfully."

# 3. 下载并解压 Ubuntu Base 根文件系统（若已存在则幂等跳过）。
#    用户选择 WebUI 延迟安装时，仅保留宿主入口，跳过这里和后面的 APT。
if [ -f "$ROOTFS/bin/bash" ]; then
    _existing_arch=$(get_rootfs_arch "$ROOTFS")
    if [ "$_existing_arch" != "none" ] && [ "$_existing_arch" != "$HOST_ARCH" ]; then
        abort "Existing rootfs architecture ($_existing_arch) does not match host architecture ($HOST_ARCH)! Refusing to overwrite."
    fi
fi

if [ "$_container_mode" != "installed" ]; then
    ui_print ">> Container installation deferred; configure it in WebUI."
else
if [ ! -f "$ROOTFS/bin/bash" ]; then
    ui_print ">> Downloading Ubuntu Base ($ROOTFS_ARCH)..."
    TAR_PATH="$ANCLI_DIR/ubuntu-base.tar.gz"
    success=0
    
    for _path in "$UBUNTU_PATH_PRIMARY" "$UBUNTU_PATH_FALLBACK"; do
        for mirror in \
            "https://${UBUNTU_MIRROR}/${_path}" \
            "https://mirrors.tuna.tsinghua.edu.cn/${_path}" \
            "https://mirrors.ustc.edu.cn/${_path}" \
            "https://cdimage.ubuntu.com/${_path}"; do
            ui_print ">> Trying mirror: $mirror"
            if download_tarball "$TAR_PATH" "$mirror" && [ -s "$TAR_PATH" ]; then
                success=1
                break 2
            fi
        done
    done
    
    if [ "$success" -ne 1 ]; then
        abort "Failed to download Ubuntu Base ($ROOTFS_ARCH) from all available mirrors"
    fi
    ui_print ">> Ubuntu Base downloaded successfully."

    ui_print ">> Extracting rootfs (this may take a minute)..."
    tar -xf "$TAR_PATH" -C "$ROOTFS" || abort "Failed to extract rootfs (corrupted download?)"
    rm -f "$TAR_PATH"

    # 初始化容器内 resolv.conf：优先写入宿主机 net.dns1/2，随后追加备用公共 DNS（去重且最多 3 条）
    rm -f "$ROOTFS/etc/resolv.conf"
    : > "$ROOTFS/etc/resolv.conf"
    _dns_seen=""
    _dns_count=0
    for _dns_val in "$(getprop net.dns1 2>/dev/null)" "$(getprop net.dns2 2>/dev/null)" 223.5.5.5 119.29.29.29 1.1.1.1 8.8.8.8; do
        case "$_dns_val" in
            ''|0.*|*[!0-9.]*) continue ;;
        esac
        case " $_dns_seen " in
            *" $_dns_val "*) continue ;;
        esac
        echo "nameserver $_dns_val" >> "$ROOTFS/etc/resolv.conf"
        _dns_seen="$_dns_seen $_dns_val"
        _dns_count=$((_dns_count + 1))
        [ "$_dns_count" -ge 3 ] && break
    done
    ui_print ">> Rootfs extracted and configured."
else
    ui_print ">> Rootfs already present, skipping download."
fi
fi

# 4. 通过 PRoot 引导或补齐容器内 APT 基础依赖（带幂等检查）
PROOT_CMD="$BIN_DIR/proot -r $ROOTFS -b /dev -b /proc -b /sys -w /root"

# APT 依赖脚本生成（幂等）：基础工具链 = python/git/node + Java 17 + binutils
# Java 17 供 jadx 等反编译工具；binutils 提供 strings/readelf/objdump；
# 并解锁 PEP 668（Ubuntu 24.04 默认拒绝 pip install）以允许直接 pip 装库。
case "$_container_profile" in
    minimal) _profile_packages="ca-certificates curl python3 python3-pip git" ;;
    standard) _profile_packages="ca-certificates curl python3 python3-pip git nodejs npm openjdk-17-jre-headless binutils" ;;
    developer) _profile_packages="ca-certificates curl python3 python3-pip git nodejs npm openjdk-17-jre-headless binutils zip unzip build-essential" ;;
    full) _profile_packages="ca-certificates curl python3 python3-pip git nodejs npm openjdk-17-jre-headless binutils zip unzip build-essential fcitx5 fcitx5-chinese-addons" ;;
    *) _container_profile="standard"; _profile_packages="ca-certificates curl python3 python3-pip git nodejs npm openjdk-17-jre-headless binutils" ;;
esac
write_setup_script() {
    cat > "$ROOTFS/root/setup.sh" << SETUP
#!/bin/bash
set -eu
export DEBIAN_FRONTEND=noninteractive
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export TMPDIR=/tmp

# PRoot 环境下配置 APT sandbox 为 root，实现安全规范的 GPG 签名校验
mkdir -p /etc/apt/apt.conf.d
echo 'APT::Sandbox::User "root";' > /etc/apt/apt.conf.d/01sandbox
apt-get update -y
apt-get install -y --no-install-recommends \\
    $_profile_packages
apt-get clean

# 移除 PEP 668 限制标记并配置清华 PyPI 镜像，允许在容器系统 Python 中直接 pip install
rm -f /usr/lib/python3.12/EXTERNALLY-MANAGED
printf '[global]\nindex-url = https://pypi.tuna.tsinghua.edu.cn/simple\n' > /etc/pip.conf
SETUP
    chmod 755 "$ROOTFS/root/setup.sh"
}

if [ "$_container_mode" = "installed" ] && ! $PROOT_CMD /usr/bin/python3 --version >/dev/null 2>&1; then
    ui_print ">> Bootstrapping APT dependencies (Python, Git, Node.js)..."

    # 宿主机侧预先替换 Ubuntu APT 源为国内镜像（使用 http 避免初期无 ca-certificates 时校验报错）
    if [ "$APT_REPO_TYPE" = "main" ]; then
        sed -i "s|http://archive.ubuntu.com/ubuntu|http://${UBUNTU_MIRROR}/ubuntu|g" \
            "$ROOTFS/etc/apt/sources.list.d/ubuntu.sources" 2>/dev/null || true
        sed -i "s|http://security.ubuntu.com/ubuntu|http://${UBUNTU_MIRROR}/ubuntu|g" \
            "$ROOTFS/etc/apt/sources.list.d/ubuntu.sources" 2>/dev/null || true
        sed -i "s|https://archive.ubuntu.com/ubuntu|http://${UBUNTU_MIRROR}/ubuntu|g" \
            "$ROOTFS/etc/apt/sources.list.d/ubuntu.sources" 2>/dev/null || true
        sed -i "s|https://security.ubuntu.com/ubuntu|http://${UBUNTU_MIRROR}/ubuntu|g" \
            "$ROOTFS/etc/apt/sources.list.d/ubuntu.sources" 2>/dev/null || true
        sed -i "s|http://archive.ubuntu.com/ubuntu|http://${UBUNTU_MIRROR}/ubuntu|g" \
            "$ROOTFS/etc/apt/sources.list" 2>/dev/null || true
        sed -i "s|http://security.ubuntu.com/ubuntu|http://${UBUNTU_MIRROR}/ubuntu|g" \
            "$ROOTFS/etc/apt/sources.list" 2>/dev/null || true
    else
        sed -i "s|http://ports.ubuntu.com/ubuntu-ports|http://${UBUNTU_MIRROR}/ubuntu-ports|g" \
            "$ROOTFS/etc/apt/sources.list.d/ubuntu.sources" 2>/dev/null || true
        sed -i "s|https://ports.ubuntu.com/ubuntu-ports|http://${UBUNTU_MIRROR}/ubuntu-ports|g" \
            "$ROOTFS/etc/apt/sources.list.d/ubuntu.sources" 2>/dev/null || true
        sed -i "s|http://ports.ubuntu.com/ubuntu-ports|http://${UBUNTU_MIRROR}/ubuntu-ports|g" \
            "$ROOTFS/etc/apt/sources.list" 2>/dev/null || true
    fi
    ui_print ">> APT mirror: ${UBUNTU_MIRROR} ($APT_REPO_TYPE)"

    # 按系统语言决定是否安装 Fcitx5 中文输入法（约 50MB，仅对中/日/韩系统启用）
    SYS_LANG=$(getprop persist.sys.locale 2>/dev/null || getprop ro.product.locale 2>/dev/null || echo "en-US")
    case "$SYS_LANG" in
        zh*|ja*|ko*)
            FCITX_PKGS="fcitx5 fcitx5-chinese-addons"
            ui_print ">> CJK locale detected ($SYS_LANG) — will install Fcitx5 input method."
            ;;
        *)
            FCITX_PKGS=""
            ui_print ">> Non-CJK locale ($SYS_LANG) — skipping Fcitx5."
            ;;
    esac

    write_setup_script
    $PROOT_CMD /root/setup.sh || abort "APT bootstrap failed"
    rm -f "$ROOTFS/root/setup.sh"
    ui_print ">> Dependencies installed successfully."
elif [ "$_container_mode" = "installed" ]; then
    ui_print ">> Dependencies already installed, skipping APT."
    # 模块覆盖升级路径：若旧版 rootfs 缺失新版本追加的 Java 17 或 binutils，则幂等补装
    if ! $PROOT_CMD /usr/bin/java -version >/dev/null 2>&1 || \
       ! $PROOT_CMD /usr/bin/strings --version >/dev/null 2>&1; then
        ui_print ">> Backfilling base toolchain (Java 17 / binutils / pip unlock)..."
        write_setup_script
        if $PROOT_CMD /root/setup.sh; then
            ui_print ">> Base toolchain backfill complete."
        else
            ui_print ">> [WARN] Backfill failed — run 'ancli repair' or reinstall the module."
        fi
        rm -f "$ROOTFS/root/setup.sh"
    fi
fi

# 4b. 确保 /usr/local 目录结构存在（Ubuntu Base 24.04 默认不带 /usr/local/lib/node_modules，npm -g 依赖它）
ui_print ">> Ensuring npm global install paths..."
mkdir -p "$ROOTFS/usr/local/bin" \
         "$ROOTFS/usr/local/sbin" \
         "$ROOTFS/usr/local/lib/node_modules" \
         "$ROOTFS/usr/local/share" \
         "$ROOTFS/root/.npm/_logs"
chmod 755 "$ROOTFS/usr/local/bin" \
          "$ROOTFS/usr/local/sbin" \
          "$ROOTFS/usr/local/lib" \
          "$ROOTFS/usr/local/lib/node_modules"
ui_print ">> npm paths ready."

# 5. 部署 AnCLI 核心脚本、环境桥接脚本、宿主机 bash 兼容模板与运行时上下文模板
ui_print ">> Deploying AnCLI Core..."
cp "$MODPATH/ancli/ancli-core.py" "$BIN_DIR/ancli-core.py"
cp "$MODPATH/ancli/ancli_env.sh" "$BIN_DIR/ancli_env.sh"
chmod 755 "$BIN_DIR/ancli-core.py" "$BIN_DIR/ancli_env.sh"

# 部署 AGY 宿主机 POSIX `bash -c` 兼容模板（Dual-Injection Path A：重启后通过 systemless overlay 生效）
if [ -f "$MODPATH/ancli/bash-host-compat" ]; then
    cp "$MODPATH/ancli/bash-host-compat" "$ANCLI_DIR/bash-host-compat"
    chmod 755 "$ANCLI_DIR/bash-host-compat"
    mkdir -p "$MODPATH/system/bin"
    cp -f "$MODPATH/ancli/bash-host-compat" "$MODPATH/system/bin/bash"
    chmod 755 "$MODPATH/system/bin/bash"
fi

# 部署容器内部直达 `ancli` 入口，支持在 PRoot 容器内直接调用 ancli 命令
if [ -d "$ROOTFS/usr/local/bin" ]; then
    cat <<'EOF' > "$ROOTFS/usr/local/bin/ancli"
#!/bin/sh
export ANCLI_IN_CONTAINER=1
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"
exec python3 /data/local/tmp/ancli/bin/ancli-core.py "$@"
EOF
    chmod 755 "$ROOTFS/usr/local/bin/ancli"
    for link_dir in /usr/bin /bin; do
        [ -d "$ROOTFS$link_dir" ] && ln -sf /usr/local/bin/ancli "$ROOTFS$link_dir/ancli" 2>/dev/null || true
    done
fi

# 部署不可变运行时上下文模板（仅作基准模板，实际 /root/.agents/AGENTS.md 由 ancli agent-init/repair 生成）
if [ -f "$MODPATH/ancli/runtime-context/AGENTS.md" ]; then
    mkdir -p "$ANCLI_DIR/runtime-context"
    cp "$MODPATH/ancli/runtime-context/AGENTS.md" "$ANCLI_DIR/runtime-context/AGENTS.md"
    chmod 644 "$ANCLI_DIR/runtime-context/AGENTS.md"
fi

# 同时部署 registry.json 到 $BIN_DIR 和 $ANCLI_DIR，兼容新旧核心版本的离线首启读取路径
cp "$MODPATH/ancli/registry.json" "$BIN_DIR/registry.json" 2>/dev/null || true
cp "$MODPATH/ancli/registry.json" "$ANCLI_DIR/registry.json" 2>/dev/null || true


# 6. 向 KernelSU / APatch 动态路径注入即时生效入口（无需重启即可使用）：
#    由于 SELinux 仅允许开机后覆写动态 bin 中已存在的文件，刷入阶段必须预先创建
#    主命令 `ancli`、AGY host bash 兼容脚本以及全部注册表工具的占位包装器。
REGISTRY_FILE="$MODPATH/ancli/registry.json"

for INSTANT_BIN in /data/adb/ksu/bin /data/adb/ap/bin; do
    [ -d "$INSTANT_BIN" ] || continue

    # 部署带 AnCLI 标记的 host bash 兼容脚本，不覆盖用户自己提供的真实 bash 二进制
    if [ -f "$MODPATH/ancli/bash-host-compat" ]; then
        host_bash="$INSTANT_BIN/bash"
        replace_host_bash=0
        if [ ! -e "$host_bash" ]; then
            replace_host_bash=1
        elif head -n 8 "$host_bash" 2>/dev/null | grep -q 'AnCLI AGY host bash compatibility\|AnCLI proot shim'; then
            replace_host_bash=1
        fi
        if [ "$replace_host_bash" -eq 1 ]; then
            cp -f "$MODPATH/ancli/bash-host-compat" "$host_bash"
            chmod 755 "$host_bash"
            ui_print ">> AGY host bash compatibility: $host_bash"
        fi
    fi

    # 注入主命令 ancli
    cp "$MODPATH/system/bin/ancli" "$INSTANT_BIN/ancli"
    chmod 755 "$INSTANT_BIN/ancli"
    ui_print ">> Instant access: $INSTANT_BIN/ancli"

    # 为注册表中的每个工具预置占位入口（后续由 ancli install / ancli repair 生成真实包装器）
    if [ -f "$REGISTRY_FILE" ]; then
        executables=$(grep '"executable"' "$REGISTRY_FILE" | sed 's/.*"executable"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/')
        for exe in $executables; do
            placeholder="$INSTANT_BIN/$exe"
            if [ ! -f "$placeholder" ]; then
                {
                    printf '#!/system/bin/sh\n'
                    printf 'target="/data/local/tmp/ancli/bin/%s"\n' "$exe"
                    printf 'if [ ! -x "$target" ]; then\n'
                    printf '    echo "[AnCLI] %s is not set up yet. Run: ancli repair" >&2\n' "$exe"
                    printf '    exit 127\n'
                    printf 'fi\n'
                    printf 'exec sh "$target" "$@"\n'
                } > "$placeholder"
                chmod 755 "$placeholder"
                ui_print ">> Pre-seeded placeholder: $INSTANT_BIN/$exe"
            fi
        done
    fi
done


ui_print ""
ui_print "=========================================="
ui_print "  Installation complete!"
ui_print "  Type 'ancli' in any root shell to start."
ui_print "=========================================="
