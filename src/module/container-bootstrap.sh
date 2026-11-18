#!/system/bin/sh
# AnCLI 容器延迟安装器。
# 这个脚本运行在 Android 宿主，而不是 PRoot 内，因此即使 rootfs 尚未创建，
# `ancli container status/install/set` 和 `ancli config` 仍然可用。
# 真正的 Agent 依赖只在安装请求时进入 PRoot。

ANCLI_DIR="/data/local/tmp/ancli"
ROOTFS="$ANCLI_DIR/rootfs"
BIN_DIR="$ANCLI_DIR/bin"
CONFIG="$ANCLI_DIR/container-config.json"
PROFILE_FILE="$ANCLI_DIR/container-profiles.json"
MODULE_DIR="${MODDIR:-/data/adb/modules/ancli}"
if [ -d "/data/adb/modules_update/ancli" ]; then
    MODULE_DIR="/data/adb/modules_update/ancli"
fi
UBUNTU_MIRROR="${ANCLI_MIRROR:-mirrors.tuna.tsinghua.edu.cn}"

say() { if command -v ui_print >/dev/null 2>&1; then ui_print "$1"; else echo "$1"; fi; }

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

json_escape() {
    printf '%s' "${1:-}" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

url_decode() {
    printf '%s' "${1:-}" | awk '
BEGIN {
    for (i = 0; i < 256; i++) {
        h = sprintf("%02x", i);
        ord[h] = sprintf("%c", i);
        ord[toupper(h)] = sprintf("%c", i);
    }
}
{
    while (match($0, /%[0-9a-fA-F]{2}/)) {
        code = substr($0, RSTART + 1, 2);
        $0 = substr($0, 1, RSTART - 1) ord[code] substr($0, RSTART + 3);
    }
    printf "%s", $0;
}'
}

json_value() {
    key="$1"
    [ -f "$CONFIG" ] || return 0
    sed -n "s/^[[:space:]]*\"$key\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" "$CONFIG" 2>/dev/null | head -n 1
}

profile_name() {
    case "$1" in
        minimal) echo "最小运行环境" ;;
        standard) echo "标准 Agent 环境" ;;
        developer) echo "开发工具环境" ;;
        full) echo "完整开发环境" ;;
        *) echo "未知规格" ;;
    esac
}

profile_size() {
    case "$1" in
        minimal) echo 480 ;;
        standard) echo 900 ;;
        developer) echo 1120 ;;
        full) echo 1480 ;;
        *) echo 0 ;;
    esac
}

profile_packages() {
    case "$1" in
        minimal) echo "ca-certificates curl python3 python3-pip git" ;;
        standard) echo "ca-certificates curl python3 python3-pip git nodejs npm openjdk-17-jre-headless binutils" ;;
        developer) echo "ca-certificates curl python3 python3-pip git nodejs npm openjdk-17-jre-headless binutils zip unzip build-essential" ;;
        full) echo "ca-certificates curl python3 python3-pip git nodejs npm openjdk-17-jre-headless binutils zip unzip build-essential fcitx5 fcitx5-chinese-addons" ;;
        *) return 1 ;;
    esac
}

profile_description() {
    case "$1" in
        minimal) echo "Python、Git、curl、证书；适合轻量脚本和基础 CLI。" ;;
        standard) echo "最小环境加 Node.js/npm、Java 17、binutils；适合常用 Agent。" ;;
        developer) echo "标准环境加 zip/unzip 和 build-essential；适合 Android 与二进制分析。" ;;
        full) echo "开发环境加 CJK 输入法组件；工具最全，占用空间最高。" ;;
        *) echo "" ;;
    esac
}

apply_network_mode() {
    network="$1"
    case "$network" in
        direct)
            unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY
            ;;
        manual)
            proxy_url=""
            if [ -f "$CONFIG" ]; then
                proxy_url=$(json_value proxy_url)
            fi
            if [ -z "$proxy_url" ] && [ -f "$ROOTFS/root/.ancli-config.json" ]; then
                proxy_url=$(sed -n 's/^[[:space:]]*"proxy_url"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$ROOTFS/root/.ancli-config.json" 2>/dev/null | head -n 1)
            fi
            if [ -n "$proxy_url" ]; then
                export http_proxy="$proxy_url" https_proxy="$proxy_url" HTTP_PROXY="$proxy_url" HTTPS_PROXY="$proxy_url" ALL_PROXY="$proxy_url"
            else
                say "[WARN] manual 网络模式未配置 proxy_url，将按当前环境直连"
            fi
            ;;
        system|*)
            if [ -f "$ANCLI_DIR/bin/ancli_env.sh" ]; then
                host_path="$PATH"
                . "$ANCLI_DIR/bin/ancli_env.sh" 2>/dev/null || true
                PATH="$host_path"
                export PATH
            fi
            ;;
    esac
}

valid_profile() { case "$1" in minimal|standard|developer|full) return 0 ;; *) return 1 ;; esac; }

write_config() {
    profile="$1"
    install_mode="$2"
    network_mode="${3:-system}"
    proxy_url="${4:-$(json_value proxy_url)}"
    lang="${5:-$(json_value lang)}"
    [ -n "$lang" ] || lang="zh"
    default_cwd="${6:-$(json_value default_cwd)}"
    agent_mem="${7:-$(json_value agent_memory)}"
    [ -n "$agent_mem" ] || agent_mem="unconfigured"

    tmp="$CONFIG.tmp.$$"
    cat > "$tmp" <<EOF
{
  "profile": "$profile",
  "install_mode": "$install_mode",
  "network_mode": "$network_mode",
  "proxy_url": "$proxy_url",
  "lang": "$lang",
  "default_cwd": "$default_cwd",
  "agent_memory": "$agent_mem",
  "estimated_size_mb": $(profile_size "$profile")
}
EOF
    mv -f "$tmp" "$CONFIG" || return 1
    chmod 600 "$CONFIG" 2>/dev/null || true
}

ensure_proot_deployed() {
    host_arch=$(detect_host_arch)
    [ "$host_arch" != "unknown" ] || return 1
    mkdir -p "$BIN_DIR" || return 1

    needs_deploy=0
    if [ ! -f "$BIN_DIR/proot" ]; then
        needs_deploy=1
    else
        cur_proot_hex=$(od -An -tx1 -j 18 -N 2 "$BIN_DIR/proot" 2>/dev/null | tr -d ' \r\n')
        case "$host_arch" in
            x86_64) [ "$cur_proot_hex" = "3e00" ] || [ "$cur_proot_hex" = "3e" ] || needs_deploy=1 ;;
            arm64) [ "$cur_proot_hex" = "b700" ] || [ "$cur_proot_hex" = "b7" ] || needs_deploy=1 ;;
        esac
    fi

    if [ "$needs_deploy" -eq 1 ]; then
        say ">> 部署 $host_arch PRoot 执行引擎..."
        if [ "$host_arch" = "x86_64" ]; then
            if [ -f "$MODULE_DIR/bin/x86_64/proot" ]; then
                cp -f "$MODULE_DIR/bin/x86_64/proot" "$BIN_DIR/proot" 2>/dev/null || true
                cp -f "$MODULE_DIR/bin/x86_64/loader"* "$BIN_DIR/" 2>/dev/null || true
            elif [ -f "$MODULE_DIR/bin/proot" ]; then
                cp -f "$MODULE_DIR/bin/proot" "$BIN_DIR/proot" 2>/dev/null || true
            fi
            chmod 755 "$BIN_DIR/proot" "$BIN_DIR/loader"* 2>/dev/null || true
        else
            if [ -f "$MODULE_DIR/bin/arm64/proot" ]; then
                cp -f "$MODULE_DIR/bin/arm64/proot" "$BIN_DIR/proot" 2>/dev/null || true
            elif [ -f "$MODULE_DIR/bin/proot" ]; then
                cp -f "$MODULE_DIR/bin/proot" "$BIN_DIR/proot" 2>/dev/null || true
            fi
            chmod 755 "$BIN_DIR/proot" 2>/dev/null || true
        fi
    fi
    [ -x "$BIN_DIR/proot" ]
}

status_json() {
    profile="$(json_value profile)"
    [ -n "$profile" ] || profile="standard"
    mode="$(json_value install_mode)"
    [ -n "$mode" ] || mode="deferred"
    network="$(json_value network_mode)"
    [ -n "$network" ] || network="system"
    proxy_url="$(json_value proxy_url)"
    apply_network_mode "$network"

    ready=false
    rootfs_arch="none"
    if [ -f "$ROOTFS/bin/bash" ]; then
        ready=true
        rootfs_arch="$(get_rootfs_arch "$ROOTFS")"
    fi
    host_arch="$(detect_host_arch)"
    proot_deployed=false
    [ -f "$BIN_DIR/proot" ] && proot_deployed=true

    version="unknown"
    if [ -f "$MODULE_DIR/module.prop" ]; then
        version=$(sed -n 's/^version=//p' "$MODULE_DIR/module.prop" 2>/dev/null | head -n 1)
    fi

    # 容器未安装时，agent_memory 保持 not_ready，避免 WebUI 抢占弹出配置弹窗
    agent_mem="not_ready"
    if [ "$ready" = true ]; then
        if [ -f "$ROOTFS/root/.ancli-config.json" ]; then
            agent_mem=$(sed -n 's/^[[:space:]]*"agent_memory"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$ROOTFS/root/.ancli-config.json" 2>/dev/null | head -n 1)
        fi
        [ -n "$agent_mem" ] || agent_mem="unconfigured"
    fi

    printf '{"status":"ok","ancli_version":"%s","execution_backend":"proot-ubuntu","rootfs_ready":%s,"proot_deployed":%s,"host_arch":"%s","rootfs_arch":"%s","installed_count":0,"android_data":false,"agent_memory":"%s","profile":"%s","profile_name":"%s","profile_description":"%s","profile_packages":"%s","estimated_size_mb":%s,"install_mode":"%s","network_mode":"%s","proxy_url":"%s","config":"%s"}\n' \
        "$(json_escape "$version")" "$ready" "$proot_deployed" "$(json_escape "$host_arch")" "$(json_escape "$rootfs_arch")" \
        "$(json_escape "$agent_mem")" "$(json_escape "$profile")" "$(json_escape "$(profile_name "$profile")")" \
        "$(json_escape "$(profile_description "$profile")")" "$(json_escape "$(profile_packages "$profile")")" \
        "$(profile_size "$profile")" "$(json_escape "$mode")" "$(json_escape "$network")" "$(json_escape "$proxy_url")" \
        "$(json_escape "$CONFIG")"
}

list_json() {
    version="unknown"
    if [ -f "$MODULE_DIR/module.prop" ]; then
        version=$(sed -n 's/^version=//p' "$MODULE_DIR/module.prop" 2>/dev/null | head -n 1)
    fi
    for f in "$ANCLI_DIR/catalog-cache.json" "$MODULE_DIR/ancli/catalog-cache.json"; do
        if [ -f "$f" ]; then
            apps_json=$(cat "$f" 2>/dev/null)
            if [ -n "$apps_json" ]; then
                printf '{"ancli_version":"%s","apps":%s,"check_ttl":21600,"last_check":0,"check_failed":0}\n' "$(json_escape "$version")" "$apps_json"
                return 0
            fi
        fi
    done
    printf '{"ancli_version":"%s","apps":[],"check_ttl":21600,"last_check":0,"check_failed":0}\n' "$(json_escape "$version")"
}

doctor_json() {
    version="unknown"
    if [ -f "$MODULE_DIR/module.prop" ]; then
        version=$(sed -n 's/^version=//p' "$MODULE_DIR/module.prop" 2>/dev/null | head -n 1)
    fi
    ready=false
    [ -f "$ROOTFS/bin/bash" ] && ready=true
    proot_deployed=false
    [ -f "$BIN_DIR/proot" ] && proot_deployed=true

    printf '{"ancli_version":"%s","execution_backend":"proot-ubuntu","rootfs_ready":%s,"proot_deployed":%s,"host_arch":"%s","container_bootstrap_ready":true,"paths":{"rootfs":"%s","ancli_dir":"%s","data_channel":"/mnt/ancli-data"}}\n' \
        "$(json_escape "$version")" "$ready" "$proot_deployed" "$(json_escape "$(detect_host_arch)")" \
        "$(json_escape "$ROOTFS")" "$(json_escape "$ANCLI_DIR")"
}

config_handler() {
    if [ $# -eq 0 ]; then
        profile="$(json_value profile)"; [ -n "$profile" ] || profile="standard"
        network="$(json_value network_mode)"; [ -n "$network" ] || network="system"
        proxy_url="$(json_value proxy_url)"
        lang="$(json_value lang)"; [ -n "$lang" ] || lang="zh"
        default_cwd="$(json_value default_cwd)"
        agent_mem="$(json_value agent_memory)"; [ -n "$agent_mem" ] || agent_mem="unconfigured"
        echo "container_profile: $profile"
        echo "network_mode: $network"
        echo "proxy_url: $proxy_url"
        echo "lang: $lang"
        echo "default_cwd: $default_cwd"
        echo "agent_memory: $agent_mem"
        return 0
    fi

    if [ "${1:-}" = "--json" ]; then
        profile="$(json_value profile)"; [ -n "$profile" ] || profile="standard"
        network="$(json_value network_mode)"; [ -n "$network" ] || network="system"
        proxy_url="$(json_value proxy_url)"
        lang="$(json_value lang)"; [ -n "$lang" ] || lang="zh"
        default_cwd="$(json_value default_cwd)"
        agent_mem="$(json_value agent_memory)"; [ -n "$agent_mem" ] || agent_mem="unconfigured"
        printf '{"default_cwd":"%s","effective_cwd":"/root","lang":"%s","proxy_mode":"%s","proxy_url":"%s","agent_memory":"%s","container_profile":"%s"}\n' \
            "$(json_escape "$default_cwd")" "$(json_escape "$lang")" "$(json_escape "$network")" \
            "$(json_escape "$proxy_url")" "$(json_escape "$agent_mem")" "$(json_escape "$profile")"
        return 0
    fi

    profile="$(json_value profile)"; [ -n "$profile" ] || profile="standard"
    network="$(json_value network_mode)"; [ -n "$network" ] || network="system"
    proxy_url="$(json_value proxy_url)"
    lang="$(json_value lang)"; [ -n "$lang" ] || lang="zh"
    default_cwd="$(json_value default_cwd)"
    agent_mem="$(json_value agent_memory)"; [ -n "$agent_mem" ] || agent_mem="unconfigured"

    for arg in "$@"; do
        case "$arg" in
            --set) continue ;;
            --set=*) arg="${arg#*=}" ;;
        esac
        case "$arg" in
            lang=*) lang="$(url_decode "${arg#*=}")" ;;
            proxy_mode=*) network="$(url_decode "${arg#*=}")" ;;
            proxy_url=*) proxy_url="$(url_decode "${arg#*=}")" ;;
            default_cwd=*) default_cwd="$(url_decode "${arg#*=}")" ;;
            agent_memory=*) agent_mem="$(url_decode "${arg#*=}")" ;;
            container_profile=*) profile="$(url_decode "${arg#*=}")" ;;
        esac
    done

    write_config "$profile" "$(json_value install_mode)" "$network" "$proxy_url" "$lang" "$default_cwd" "$agent_mem" || return 1
    # 若 rootfs 已存在，则同步写回容器 /root/.ancli-config.json
    if [ -d "$ROOTFS/root" ]; then
        cat > "$ROOTFS/root/.ancli-config.json.tmp" <<EOF
{
  "lang": "$lang",
  "proxy_mode": "$network",
  "proxy_url": "$proxy_url",
  "default_cwd": "$default_cwd",
  "agent_memory": "$agent_mem"
}
EOF
        mv -f "$ROOTFS/root/.ancli-config.json.tmp" "$ROOTFS/root/.ancli-config.json" 2>/dev/null || true
    fi
    echo "[OK] Configuration saved"
    return 0
}

install_container() {
    host_arch=$(detect_host_arch)
    if [ "$host_arch" = "unknown" ]; then
        say "[X] 不支持的宿主 CPU 架构：$(uname -m 2>/dev/null)"
        return 1
    fi

    profile="$(json_value profile)"
    [ -n "$profile" ] || profile="standard"
    valid_profile "$profile" || { say "[X] 无效容器规格：$profile"; return 2; }
    network="$(json_value network_mode)"
    [ -n "$network" ] || network="system"
    proxy_url="$(json_value proxy_url)"
    apply_network_mode "$network"

    mkdir -p "$ROOTFS" "$BIN_DIR" "$ANCLI_DIR/shm" || return 1
    chmod 1777 "$ANCLI_DIR/shm" 2>/dev/null || true

    ensure_proot_deployed || { say "[X] PRoot 部署失败，无法安装容器"; return 1; }

    if [ "$host_arch" = "x86_64" ]; then
        rootfs_arch="amd64"
        apt_repo_type="main"
    else
        rootfs_arch="arm64"
        apt_repo_type="ports"
    fi

    primary_path="ubuntu-cdimage/ubuntu-base/releases/24.04/release/ubuntu-base-24.04.5-base-${rootfs_arch}.tar.gz"
    fallback_path="ubuntu-cdimage/ubuntu-base/releases/24.04/release/ubuntu-base-24.04.4-base-${rootfs_arch}.tar.gz"

    if [ -f "$ROOTFS/bin/bash" ]; then
        existing_arch="$(get_rootfs_arch "$ROOTFS")"
        if [ "$existing_arch" != "none" ] && [ "$existing_arch" != "$host_arch" ]; then
            say "[X] 现有 rootfs 架构为 $existing_arch，与宿主架构 $host_arch 不匹配！拒绝覆盖解压。"
            return 1
        fi
        say ">> 现有 Ubuntu Base ($existing_arch) 架构核对正确，跳过基础镜像下载"
    else
        say ">> 检测到宿主架构：$host_arch，将部署 Ubuntu Base ($rootfs_arch)..."
        tar_path="$ANCLI_DIR/ubuntu-base.tar.gz"
        success=0
        for _p in "$primary_path" "$fallback_path"; do
            for mirror in \
                "https://${UBUNTU_MIRROR}/${_p}" \
                "https://mirrors.tuna.tsinghua.edu.cn/${_p}" \
                "https://mirrors.ustc.edu.cn/${_p}" \
                "https://cdimage.ubuntu.com/${_p}"; do
                say ">> 正在下载基础系统：$mirror"
                if download_tarball "$tar_path" "$mirror" && [ -s "$tar_path" ]; then
                    success=1
                    break 2
                fi
            done
        done
        [ "$success" -eq 1 ] || { say "[X] 所有 Ubuntu 镜像均下载失败，请检查网络或配置代理"; return 1; }

        say ">> 解压 Ubuntu 根文件系统..."
        tar -xf "$tar_path" -C "$ROOTFS" || { say "[X] rootfs 解压失败"; return 1; }
        rm -f "$tar_path"

        # 配置 APT 软件源（使用 http 避免初期缺少 ca-certificates 时校验报错）
        if [ "$apt_repo_type" = "main" ]; then
            sed -i "s|http://archive.ubuntu.com/ubuntu|http://${UBUNTU_MIRROR}/ubuntu|g" "$ROOTFS/etc/apt/sources.list.d/ubuntu.sources" 2>/dev/null || true
            sed -i "s|http://security.ubuntu.com/ubuntu|http://${UBUNTU_MIRROR}/ubuntu|g" "$ROOTFS/etc/apt/sources.list.d/ubuntu.sources" 2>/dev/null || true
            sed -i "s|https://archive.ubuntu.com/ubuntu|http://${UBUNTU_MIRROR}/ubuntu|g" "$ROOTFS/etc/apt/sources.list.d/ubuntu.sources" 2>/dev/null || true
            sed -i "s|https://security.ubuntu.com/ubuntu|http://${UBUNTU_MIRROR}/ubuntu|g" "$ROOTFS/etc/apt/sources.list.d/ubuntu.sources" 2>/dev/null || true
            sed -i "s|http://archive.ubuntu.com/ubuntu|http://${UBUNTU_MIRROR}/ubuntu|g" "$ROOTFS/etc/apt/sources.list" 2>/dev/null || true
            sed -i "s|http://security.ubuntu.com/ubuntu|http://${UBUNTU_MIRROR}/ubuntu|g" "$ROOTFS/etc/apt/sources.list" 2>/dev/null || true
        else
            sed -i "s|http://ports.ubuntu.com/ubuntu-ports|http://${UBUNTU_MIRROR}/ubuntu-ports|g" "$ROOTFS/etc/apt/sources.list" 2>/dev/null || true
            sed -i "s|http://ports.ubuntu.com/ubuntu-ports|http://${UBUNTU_MIRROR}/ubuntu-ports|g" "$ROOTFS/etc/apt/sources.list.d/ubuntu.sources" 2>/dev/null || true
            sed -i "s|https://ports.ubuntu.com/ubuntu-ports|http://${UBUNTU_MIRROR}/ubuntu-ports|g" "$ROOTFS/etc/apt/sources.list.d/ubuntu.sources" 2>/dev/null || true
        fi

        # 配置容器 resolv.conf
        rm -f "$ROOTFS/etc/resolv.conf"
        : > "$ROOTFS/etc/resolv.conf"
        _dns_seen=""
        for dns in "$(getprop net.dns1 2>/dev/null)" "$(getprop net.dns2 2>/dev/null)" 223.5.5.5 119.29.29.29 1.1.1.1 8.8.8.8; do
            case "$dns" in ''|0.*|*[!0-9.]*) continue ;; esac
            case " $_dns_seen " in *" $dns "*) continue ;; esac
            echo "nameserver $dns" >> "$ROOTFS/etc/resolv.conf"
            _dns_seen="$_dns_seen $dns"
            [ "$(wc -l < "$ROOTFS/etc/resolv.conf")" -ge 3 ] && break
        done
    fi

    packages="$(profile_packages "$profile")"
    setup="$ROOTFS/root/ancli-container-setup.sh"
    proxy_exports=""
    if [ -n "${http_proxy:-}" ]; then
        proxy_exports="export http_proxy=\"${http_proxy:-}\" https_proxy=\"${https_proxy:-}\" HTTP_PROXY=\"${HTTP_PROXY:-}\" HTTPS_PROXY=\"${HTTPS_PROXY:-}\" all_proxy=\"${all_proxy:-}\" ALL_PROXY=\"${ALL_PROXY:-}\""
    fi
    cat > "$setup" <<EOF
#!/bin/bash
set -eu
export DEBIAN_FRONTEND=noninteractive
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export TMPDIR=/tmp
$proxy_exports
mkdir -p /etc/apt/apt.conf.d
echo 'APT::Sandbox::User "root";' > /etc/apt/apt.conf.d/01sandbox
apt-get update -y
apt-get install -y --no-install-recommends $packages
apt-get clean
rm -f /usr/lib/python3.12/EXTERNALLY-MANAGED /usr/lib/python3.11/EXTERNALLY-MANAGED
mkdir -p /usr/local/bin /usr/local/sbin /usr/local/lib/node_modules /usr/local/share /root/.npm/_logs /root/.ancli-work
chmod 755 /usr/local/bin /usr/local/sbin /usr/local/lib /usr/local/lib/node_modules
EOF
    chmod 755 "$setup"
    proot="$BIN_DIR/proot -r $ROOTFS -b /dev -b /proc -b /sys -b $ANCLI_DIR -b /data/adb -w /root"
    say ">> 安装容器规格：$(profile_name "$profile")（约 $(profile_size "$profile") MB）"
    say ">> 规格说明：$(profile_description "$profile")"
    say ">> 软件包：$packages"

    if ! $proot /root/ancli-container-setup.sh; then
        say "[X] 容器依赖安装失败；配置已保留，可修复后再次执行 ancli container install"
        rm -f "$setup"
        return 1
    fi
    rm -f "$setup"

    mkdir -p "$ROOTFS/usr/local/bin" "$ROOTFS/usr/local/sbin" "$ROOTFS/usr/local/lib/node_modules" "$ROOTFS/root/.npm/_logs"
    # 部署容器内原生 ancli 入口与 /usr/local/bin/bash
    cat << 'EOF' > "$ROOTFS/usr/local/bin/ancli"
#!/bin/sh
export ANCLI_IN_CONTAINER=1
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"
exec python3 /data/local/tmp/ancli/bin/ancli-core.py "$@"
EOF
    chmod 755 "$ROOTFS/usr/local/bin/ancli"
    ln -sf /usr/local/bin/ancli "$ROOTFS/usr/bin/ancli" 2>/dev/null || true
    ln -sf /usr/local/bin/ancli "$ROOTFS/bin/ancli" 2>/dev/null || true
    ln -sf /bin/bash "$ROOTFS/usr/local/bin/bash" 2>/dev/null || true

    # 将 host 配置写入容器 /root/.ancli-config.json
    lang="$(json_value lang)"; [ -n "$lang" ] || lang="zh"
    default_cwd="$(json_value default_cwd)"
    agent_mem="$(json_value agent_memory)"; [ -n "$agent_mem" ] || agent_mem="unconfigured"
    cat > "$ROOTFS/root/.ancli-config.json" <<EOF
{
  "lang": "$lang",
  "proxy_mode": "$network",
  "proxy_url": "$proxy_url",
  "default_cwd": "$default_cwd",
  "agent_memory": "$agent_mem"
}
EOF
    chmod 644 "$ROOTFS/root/.ancli-config.json" 2>/dev/null || true

    write_config "$profile" "installed" "$network" "$proxy_url" "$lang" "$default_cwd" "$agent_mem"
    say "[OK] 容器安装完成。可以运行 ancli repair 初始化 Agent 入口。"
    return 0
}

action="${1:-status}"
case "$action" in
    status|--json)
        status_json
        ;;
    list)
        list_json
        ;;
    doctor)
        doctor_json
        ;;
    config)
        shift
        config_handler "$@"
        ;;
    set)
        profile="$(json_value profile)"; [ -n "$profile" ] || profile="standard"
        network="$(json_value network_mode)"; [ -n "$network" ] || network="system"
        proxy_url="$(json_value proxy_url)"
        for arg in "$@"; do
            case "$arg" in
                --profile=*) profile="${arg#*=}" ;;
                --network=*) network="${arg#*=}" ;;
                --proxy-url=*) proxy_url="${arg#*=}" ;;
            esac
        done
        valid_profile "$profile" || { say "[X] profile 必须是 minimal、standard、developer 或 full"; exit 2; }
        case "$network" in system|manual|direct) ;; *) say "[X] network 必须是 system、manual 或 direct"; exit 2 ;; esac
        write_config "$profile" "$(json_value install_mode)" "$network" "$proxy_url" || exit 1
        status_json
        ;;
    install|apply)
        install_container
        ;;
    *)
        echo "用法：ancli container status [--json] | ancli container set --profile=standard --network=system | ancli container install" >&2
        exit 2
        ;;
esac
