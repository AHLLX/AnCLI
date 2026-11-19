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

# Profile names, descriptions and package sets are generated from the same
# canonical JSON catalog used by the installer and embedded WebUI catalog.
if [ -f "$ANCLI_DIR/container-profile-data.sh" ]; then
    . "$ANCLI_DIR/container-profile-data.sh"
elif [ -f "$MODULE_DIR/container-profile-data.sh" ]; then
    . "$MODULE_DIR/container-profile-data.sh"
fi

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
        _download_proxy="${https_proxy:-${http_proxy:-}}"
        if [ -n "$_download_proxy" ]; then
            say ">> 下载路由：curl 显式使用 HTTP 代理 $_download_proxy"
            curl -f -L -A "curl/7.68.0" --proxy "$_download_proxy" --noproxy '' --connect-timeout 20 --max-time 600 --progress-bar -o "$_out" "$_url"
        else
            say ">> 下载路由：未配置 HTTP 代理，使用设备网络/VPN 路由"
            curl -f -L -A "curl/7.68.0" --noproxy '*' --connect-timeout 20 --max-time 600 --progress-bar -o "$_out" "$_url"
        fi
        return $?
    fi
    for _bb in /data/adb/ksu/bin/busybox /data/adb/ap/bin/busybox /data/adb/magisk/busybox busybox; do
        if [ -x "$_bb" ]; then
            if [ -n "${https_proxy:-${http_proxy:-}}" ]; then
                say ">> 下载路由：BusyBox wget 启用 HTTP 代理 ${https_proxy:-${http_proxy:-}}"
                "$_bb" wget -Y on -U "curl/7.68.0" -T 30 -O "$_out" "$_url"
            else
                say ">> 下载路由：未配置 HTTP 代理，使用设备网络/VPN 路由"
                "$_bb" wget -Y off -U "curl/7.68.0" -T 30 -O "$_out" "$_url"
            fi
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
    ancli_profile_name_zh "$1" 2>/dev/null || echo "未知规格"
}

profile_size() {
    case "$1" in
        minimal) echo 0 ;;
        standard) echo 0 ;;
        developer) echo 0 ;;
        full) echo 0 ;;
        *) echo 0 ;;
    esac
}

profile_packages() {
    ancli_profile_packages "$1"
}

profile_description() {
    ancli_profile_description_zh "$1"
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
    _write_mirror="${8:-$(json_value mirror)}"
    case "$_write_mirror" in tuna|ustc|official) ;; *) _write_mirror=tuna ;; esac

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
  "mirror": "$_write_mirror",
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
    _status_mirror="$(json_value mirror)"; [ -n "$_status_mirror" ] || _status_mirror=tuna
    apply_network_mode "$network"

    ready=false
    rootfs_arch="none"
    if [ -f "$ROOTFS/bin/bash" ]; then
        rootfs_arch="$(get_rootfs_arch "$ROOTFS")"
        [ -f "$ROOTFS/usr/bin/python3" ] && ready=true
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

    printf '{"status":"ok","ancli_version":"%s","execution_backend":"proot-ubuntu","rootfs_ready":%s,"proot_deployed":%s,"host_arch":"%s","rootfs_arch":"%s","installed_count":0,"android_data":false,"agent_memory":"%s","profile":"%s","profile_name":"%s","profile_description":"%s","profile_packages":"%s","estimated_size_mb":%s,"install_mode":"%s","network_mode":"%s","proxy_url":"%s","mirror":"%s","config":"%s"}\n' \
        "$(json_escape "$version")" "$ready" "$proot_deployed" "$(json_escape "$host_arch")" "$(json_escape "$rootfs_arch")" \
        "$(json_escape "$agent_mem")" "$(json_escape "$profile")" "$(json_escape "$(profile_name "$profile")")" \
        "$(json_escape "$(profile_description "$profile")")" "$(json_escape "$(profile_packages "$profile")")" \
        "$(profile_size "$profile")" "$(json_escape "$mode")" "$(json_escape "$network")" "$(json_escape "$proxy_url")" \
        "$(json_escape "$_status_mirror")" "$(json_escape "$CONFIG")"
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

install_container() (
    host_arch=$(detect_host_arch)
    if [ "$host_arch" = "unknown" ]; then
        say "[X] 不支持的宿主 CPU 架构：$(uname -m 2>/dev/null)"
        return 1
    fi

    profile="$(json_value profile)"
    [ -n "$profile" ] || profile="standard"
    _selected_mirror="$(json_value mirror)"; [ -n "$_selected_mirror" ] || _selected_mirror=tuna
    for arg in "$@"; do
        case "$arg" in --profile=*) profile="${arg#*=}" ;; --mirror=*) _selected_mirror="${arg#*=}" ;; *) say "[X] 未知安装参数：$arg"; return 2 ;; esac
    done
    valid_profile "$profile" || { say "[X] 无效容器规格：$profile"; return 2; }
    network="$(json_value network_mode)"
    [ -n "$network" ] || network="system"
    proxy_url="$(json_value proxy_url)"
    [ -f "$ANCLI_DIR/install-network.sh" ] && [ -f "$ANCLI_DIR/install-transaction.sh" ] || { say "[X] 容器安装辅助脚本缺失，请更新模块"; return 1; }
    . "$ANCLI_DIR/install-network.sh"
    . "$ANCLI_DIR/install-transaction.sh"
    [ -f "$ANCLI_DIR/container-mirror.sh" ] || { say "[X] 容器下载站点配置缺失"; return 1; }
    . "$ANCLI_DIR/container-mirror.sh"
    ancli_mirror_valid "$_selected_mirror" || { say "[X] 无效下载站点"; return 2; }
    ( write_config "$(json_value profile)" "$(json_value install_mode)" "$network" "$proxy_url" "$(json_value lang)" "$(json_value default_cwd)" "$(json_value agent_memory)" "$_selected_mirror" ) || return 1
    say ">> 容器下载站点：$_selected_mirror"
    ancli_install_network "$network" "$proxy_url" || return 1

    mkdir -p "$BIN_DIR" "$ANCLI_DIR/shm" || return 1
    chmod 1777 "$ANCLI_DIR/shm" 2>/dev/null || true

    ensure_proot_deployed || { say "[X] PRoot 部署失败，无法安装容器"; return 1; }

    if [ "$host_arch" = "x86_64" ]; then
        rootfs_arch="amd64"
        apt_repo_type="main"
    else
        rootfs_arch="arm64"
        apt_repo_type="ports"
    fi

    if [ ! -f "$ROOTFS/bin/bash" ]; then
        if ancli_resume_rootfs; then
            trap 'ancli_preserve_rootfs' EXIT
        else
            ancli_begin_rootfs || return 1
            trap 'ancli_preserve_rootfs' EXIT
        fi
    fi

    if [ -f "$ROOTFS/bin/bash" ]; then
        existing_arch="$(get_rootfs_arch "$ROOTFS")"
        if [ "$existing_arch" != "none" ] && [ "$existing_arch" != "$host_arch" ]; then
            say "[X] 现有 rootfs 架构为 $existing_arch，与宿主架构 $host_arch 不匹配！拒绝覆盖解压。"
            return 1
        fi
        say ">> 现有 Ubuntu Base ($existing_arch) 架构核对正确，跳过基础镜像下载"
    else
        say ">> 检测到宿主架构：$host_arch，将部署 Ubuntu Base ($rootfs_arch)..."
        tar_path="${ANCLI_ROOTFS_ARCHIVE:-$ANCLI_DIR/ubuntu-base.tar.gz}"
        success=0
        for _version in 24.04.5 24.04.4; do
            _mirror_url=$(ancli_mirror_rootfs_url "$_selected_mirror" "$_version" "$rootfs_arch") || return 1
            say ">> 正在下载基础系统：$_mirror_url"
            if download_tarball "$tar_path" "$_mirror_url" && [ -s "$tar_path" ]; then success=1; break; fi
        done
        [ "$success" -eq 1 ] || { say "[X] 所选下载站点下载失败，请检查网络或切换站点"; return 1; }

        say ">> 解压 Ubuntu 根文件系统..."
        tar -xf "$tar_path" -C "$ROOTFS" || { say "[X] rootfs 解压失败"; return 1; }
        rm -f "$tar_path"

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

    _apt_url=$(ancli_mirror_apt_url "$_selected_mirror" "$rootfs_arch") || return 1
    ancli_mirror_configure_apt "$ROOTFS" "$_apt_url" || return 1
    say ">> Ubuntu 软件包源：$_apt_url"

    packages="$(profile_packages "$profile")"
    setup="$ROOTFS/root/ancli-container-setup.sh"
    proxy_exports=""
    printf 'profile=%s\nphase=apt\n' "$profile" > "$ROOTFS/.ancli-install-checkpoint"
    if [ -n "${http_proxy:-}" ]; then
        proxy_exports="export http_proxy='${http_proxy:-}' https_proxy='${https_proxy:-${http_proxy:-}}' HTTP_PROXY='${HTTP_PROXY:-${http_proxy:-}}' HTTPS_PROXY='${HTTPS_PROXY:-${https_proxy:-${http_proxy:-}}}'"
    fi
    cat > "$setup" <<EOF
#!/bin/bash
set -eu
export DEBIAN_FRONTEND=noninteractive
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export TMPDIR=/tmp
$proxy_exports
mkdir -p /etc/apt/apt.conf.d
if [ -n "\${http_proxy:-}" ]; then
    printf 'Acquire::http::Proxy "%s";\n' "\$http_proxy" > /etc/apt/apt.conf.d/99ancli-proxy
    printf 'Acquire::https::Proxy "%s";\n' "\${https_proxy:-\$http_proxy}" >> /etc/apt/apt.conf.d/99ancli-proxy
fi
echo 'APT::Sandbox::User "root";' > /etc/apt/apt.conf.d/01sandbox
printf 'profile=%s\\nphase=apt-update\\n' '$profile' > /.ancli-install-checkpoint
apt-get update -y
printf 'profile=%s\\nphase=apt-install\\n' '$profile' > /.ancli-install-checkpoint
apt-get install -y --no-install-recommends $packages
apt-get clean
rm -f /etc/apt/apt.conf.d/99ancli-proxy
printf 'profile=%s\\nphase=node-setup\\n' '$profile' > /.ancli-install-checkpoint
/bin/sh /data/local/tmp/ancli/node-setup.sh
rm -f /usr/lib/python3.12/EXTERNALLY-MANAGED /usr/lib/python3.11/EXTERNALLY-MANAGED
mkdir -p /usr/local/bin /usr/local/sbin /usr/local/lib/node_modules /usr/local/share /root/.npm/_logs /root/.ancli-work
chmod 755 /usr/local/bin /usr/local/sbin /usr/local/lib /usr/local/lib/node_modules
EOF
    chmod 755 "$setup"
    proot="$BIN_DIR/proot -r $ROOTFS -b /dev -b /proc -b /sys -b $ANCLI_DIR -b /data/adb -w /root"
    say ">> 安装容器规格：$(profile_name "$profile")"
    say ">> 规格说明：$(profile_description "$profile")"
    say ">> 软件包：$packages"
    say ">> 说明：切换较小规格不会卸载已有软件包。"
    _installed_packages=""
    _missing_packages=""
    if [ -x "$ROOTFS/usr/bin/dpkg-query" ] || [ -x "$ROOTFS/bin/dpkg-query" ]; then
        for _pkg in $packages; do
            _pkg_state=$($proot /usr/bin/dpkg-query -W '-f=${db:Status-Abbrev}' "$_pkg" 2>/dev/null || true)
            case "$_pkg_state" in
                ii*) _installed_packages="$_installed_packages $_pkg" ;;
                *) _missing_packages="$_missing_packages $_pkg" ;;
            esac
        done
        if [ -n "$_installed_packages" ]; then
            say ">> 检查点状态：已安装目标软件包：${_installed_packages# }"
        else
            say ">> 检查点状态：已安装目标软件包：无"
        fi
        if [ -n "$_missing_packages" ]; then
            say ">> 检查点状态：待补齐软件包：${_missing_packages# }"
        else
            say ">> 检查点状态：待补齐软件包：无"
        fi
        say ">> 安装失败会保留已安装内容；再次执行同一规格会由 APT 跳过已完成项并补齐缺失项。"
    else
        say ">> 检查点状态：基础系统尚未完成，Ubuntu Base 与 APT 状态会保留供下次续装。"
    fi
    if [ -n "${https_proxy:-${http_proxy:-}}" ]; then
        say ">> APT、Node.js 与基础镜像下载已配置为显式使用代理：${https_proxy:-${http_proxy:-}}"
    else
        say ">> 未配置 HTTP 代理；下载使用 Android 网络路由（可能包含 VPN 或透明代理）。"
    fi

    if [ -f "$ANCLI_DIR/apt-key-compat.sh" ]; then
        sh "$ANCLI_DIR/apt-key-compat.sh" "$ROOTFS" || { say "[X] APT 密钥环兼容设置失败"; return 1; }
    else
        say "[X] APT 密钥环兼容脚本缺失；请更新模块"
        return 1
    fi
    if ! $proot /root/ancli-container-setup.sh; then
        say "[X] 容器依赖安装失败；已安装软件包和安装检查点均保留，可修复网络后再次执行 ancli container install 续装"
        rm -f "$setup"
        return 1
    fi
    rm -f "$setup"
    rm -f "$ROOTFS/.ancli-install-checkpoint"
    sh "$ANCLI_DIR/apt-key-compat.sh" "$ROOTFS" || return 1

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

    if [ -n "${ANCLI_ROOTFS_STAGE:-}" ]; then
        ancli_commit_rootfs || { say "[X] 无法提交新容器；已有数据未被覆盖"; return 1; }
    fi
    write_config "$profile" "installed" "$network" "$proxy_url" "$lang" "$default_cwd" "$agent_mem" "$_selected_mirror"
    say "[OK] 容器安装完成。可以运行 ancli repair 初始化 Agent 入口。"
    return 0
)

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
        _set_mirror="$(json_value mirror)"; [ -n "$_set_mirror" ] || _set_mirror=tuna
        for arg in "$@"; do
            case "$arg" in
                --profile=*) profile="${arg#*=}" ;;
                --network=*) network="${arg#*=}" ;;
                --mirror=*) _set_mirror="${arg#*=}" ;;
                --proxy-url=*) proxy_url="$(url_decode "${arg#*=}")" ;;
            esac
        done
        valid_profile "$profile" || { say "[X] profile 必须是 minimal、standard、developer 或 full"; exit 2; }
        case "$network" in system|manual|direct) ;; *) say "[X] network 必须是 system、manual 或 direct"; exit 2 ;; esac
        case "$_set_mirror" in tuna|ustc|official) ;; *) say "[X] 无效下载站点"; exit 2 ;; esac
        write_config "$profile" "$(json_value install_mode)" "$network" "$proxy_url" "$(json_value lang)" "$(json_value default_cwd)" "$(json_value agent_memory)" "$_set_mirror" || exit 1
        status_json
        ;;
    speed-test)
        shift
        _speed_choice=tuna; _speed_network=system; _speed_proxy=''
        for arg in "$@"; do
            case "$arg" in
                --mirror=*) _speed_choice="${arg#*=}" ;;
                --network=*) _speed_network="${arg#*=}" ;;
                --proxy-url=*) _speed_proxy="$(url_decode "${arg#*=}")" ;;
                *) say "[X] Unknown speed test option"; exit 2 ;;
            esac
        done
        case "$_speed_network" in system|manual|direct) ;; *) exit 2 ;; esac
        [ -f "$ANCLI_DIR/container-mirror.sh" ] && [ -f "$ANCLI_DIR/install-network.sh" ] || exit 1
        . "$ANCLI_DIR/container-mirror.sh"
        . "$ANCLI_DIR/install-network.sh"
        ancli_mirror_valid "$_speed_choice" || exit 2
        unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY
        ANCLI_PROXY_TEST_TIMEOUT=4 ancli_install_network "$_speed_network" "$_speed_proxy" || exit 1
        case "$(detect_host_arch)" in arm64) _speed_arch=arm64 ;; x86_64) _speed_arch=amd64 ;; *) exit 1 ;; esac
        ancli_mirror_speed_test "$_speed_choice" "$_speed_arch"
        ;;
    mirror-ping)
        [ -f "$ANCLI_DIR/container-mirror.sh" ] || exit 1
        . "$ANCLI_DIR/container-mirror.sh"
        case "$(detect_host_arch)" in arm64) _ping_official=ports.ubuntu.com ;; x86_64) _ping_official=archive.ubuntu.com ;; *) _ping_official=ports.ubuntu.com ;; esac
        _mirror_ping_one() {
            _name=$1 _host=$2 _result=timeout
            if command -v ping >/dev/null 2>&1 && command -v timeout >/dev/null 2>&1; then
                _ping_output=$(timeout 4 ping -c 1 -W 2 "$_host" 2>&1)
                if [ "$?" -eq 0 ]; then
                    _result=$(printf '%s\n' "$_ping_output" | sed -n 's/.*time[=<]\([0-9.][0-9.]*\)[[:space:]]*ms.*/\1/p' | head -n 1)
                    [ -n "$_result" ] || _result=ok
                fi
            else _result=unavailable; fi
            printf 'ANCLI_MIRROR_PING_%s=%s\n' "$_name" "$_result"
        }
        _mirror_ping_one tuna mirrors.tuna.tsinghua.edu.cn & _p1=$!
        _mirror_ping_one ustc mirrors.ustc.edu.cn & _p2=$!
        _mirror_ping_one official "$_ping_official" & _p3=$!
        wait "$_p1"; wait "$_p2"; wait "$_p3"
        ;;
    vpn-status)
        [ -f "$ANCLI_DIR/install-network.sh" ] || exit 1
        . "$ANCLI_DIR/install-network.sh"
        ancli_detect_vpn
        ;;
    proxy-status)
        shift
        _proxy_status_mode="system"
        _proxy_status_proxy=""
        for arg in "$@"; do
            case "$arg" in
                --network=*) _proxy_status_mode="${arg#*=}" ;;
                --proxy-url=*) _proxy_status_proxy="$(url_decode "${arg#*=}")" ;;
                *) say "[X] Unknown proxy status option"; exit 2 ;;
            esac
        done
        case "$_proxy_status_mode" in system|manual|direct) ;; *) say "[X] Invalid network mode"; exit 2 ;; esac
        [ -f "$ANCLI_DIR/install-network.sh" ] || { say "[X] Network helper missing"; exit 1; }
        . "$ANCLI_DIR/install-network.sh"
        export ANCLI_NETWORK_VERBOSE=1 ANCLI_SKIP_PROXY_CONNECTIVITY_TEST=1
        ancli_detect_vpn
        ancli_install_network "$_proxy_status_mode" "$_proxy_status_proxy" || exit $?
        if [ -n "${_ancli_host:-}" ] && [ -n "${_ancli_port:-}" ]; then
            printf 'ANCLI_PROXY_URL=%s:%s\n' "$_ancli_host" "$_ancli_port"
        else
            echo 'ANCLI_PROXY_URL='
        fi
        printf 'ANCLI_PROXY_MODE=%s\n' "$_proxy_status_mode"
        ;;
    network-test)
        shift
        _test_mode="system"
        _test_proxy=""
        for arg in "$@"; do
            case "$arg" in
                --network=*) _test_mode="${arg#*=}" ;;
                --proxy-url=*) _test_proxy="$(url_decode "${arg#*=}")" ;;
                *) say "[X] Unknown network test option"; exit 2 ;;
            esac
        done
        case "$_test_mode" in system|manual|direct) ;; *) say "[X] Invalid network mode"; exit 2 ;; esac
        [ -f "$ANCLI_DIR/install-network.sh" ] || { say "[X] Network helper missing"; exit 1; }
        unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY
        . "$ANCLI_DIR/install-network.sh"
        export ANCLI_NETWORK_VERBOSE=1 ANCLI_PROXY_TEST_TIMEOUT=5
        ancli_detect_vpn
        ancli_probe_ping_sites
        ancli_probe_route
        ancli_install_network "$_test_mode" "$_test_proxy" || exit $?
        if [ "$_test_mode" = system ]; then
            if [ -n "${_ancli_candidate:-}" ] && [ -n "${_ancli_host:-}" ] && [ -n "${_ancli_port:-}" ]; then
                printf '%s %s %s\n' "$_ancli_host" "$_ancli_port" "$(date +%s)" > "$ANCLI_DIR/.proxy_cache" &&
                    cp -f "$ANCLI_DIR/.proxy_cache" "$ANCLI_DIR/.proxy_cache_last" || true
            else
                rm -f "$ANCLI_DIR/.proxy_cache" "$ANCLI_DIR/.proxy_cache_last"
            fi
        fi
        ;;
    install|apply)
        shift
        install_container "$@"
        ;;
    *)
        echo "用法：ancli container status [--json] | ancli container set --profile=standard --network=system | ancli container install" >&2
        exit 2
        ;;
esac
