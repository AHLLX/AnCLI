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
        _download_proxy="${https_proxy:-${http_proxy:-}}"
        if [ -n "$_download_proxy" ]; then
            ui_print "$(installer_text ">> 下载路由：curl 显式使用 HTTP 代理 $_download_proxy" ">> Download route: curl explicitly uses HTTP proxy $_download_proxy")"
            curl -f -L -A "curl/7.68.0" --proxy "$_download_proxy" --noproxy '' --connect-timeout 20 --max-time 600 --progress-bar -o "$_out" "$_url"
        else
            ui_print "$(installer_text '>> 下载路由：未配置 HTTP 代理，使用设备网络/VPN 路由' '>> Download route: no HTTP proxy is configured; using device/VPN routing')"
            curl -f -L -A "curl/7.68.0" --noproxy '*' --connect-timeout 20 --max-time 600 --progress-bar -o "$_out" "$_url"
        fi
        return $?
    fi
    for _bb in /data/adb/ksu/bin/busybox /data/adb/ap/bin/busybox /data/adb/magisk/busybox busybox; do
        if [ -x "$_bb" ]; then
            if [ -n "${https_proxy:-${http_proxy:-}}" ]; then
                ui_print "$(installer_text ">> 下载路由：BusyBox wget 启用 HTTP 代理 ${https_proxy:-${http_proxy:-}}" ">> Download route: BusyBox wget enabled HTTP proxy ${https_proxy:-${http_proxy:-}}")"
                "$_bb" wget -Y on -U "curl/7.68.0" -T 30 -O "$_out" "$_url"
            else
                ui_print "$(installer_text '>> 下载路由：未配置 HTTP 代理，使用设备网络/VPN 路由' '>> Download route: no HTTP proxy is configured; using device/VPN routing')"
                "$_bb" wget -Y off -U "curl/7.68.0" -T 30 -O "$_out" "$_url"
            fi
            return $?
        fi
    done
    return 127
}

HOST_ARCH=$(detect_host_arch)

if [ "$HOST_ARCH" = "x86_64" ]; then
    ROOTFS_ARCH="amd64"
    APT_REPO_TYPE="main"
else
    ROOTFS_ARCH="arm64"
    APT_REPO_TYPE="ports"
fi

# 从 module.prop 动态读取版本号用于刷入日志横幅，避免硬编码与发布元数据脱节
MODULE_VERSION="$(grep '^version=' "${MODPATH:-$(dirname "$0")}/module.prop" 2>/dev/null | cut -d= -f2)"
[ -n "$MODULE_VERSION" ] || MODULE_VERSION="unknown"

ui_print "============================================"
ui_print "  AnCLI Bootstrap Installer ${MODULE_VERSION}"
ui_print "============================================"
ui_print ""

# Language is the first installer choice. Create only the small input scratch
# directory before asking; the normal setup banner follows the selection.
mkdir -p "$ANCLI_DIR"
read_container_key() {
    _key_result=""
    _key_file="$ANCLI_DIR/.container-key.$$"
    : > "$_key_file" || return 1
    /system/bin/getevent -ql > "$_key_file" 2>/dev/null &
    _key_pid=$!
    _wait=0
    _timeout="${1:-20}"
    while [ "$_wait" -lt "$_timeout" ]; do
        _key_result=$(grep -m 1 -E 'KEY_(VOLUMEUP|VOLUMEDOWN).*DOWN' "$_key_file" 2>/dev/null)
        [ -n "$_key_result" ] && break
        kill -0 "$_key_pid" 2>/dev/null || break
        /system/bin/sleep 1
        _wait=$((_wait + 1))
    done
    kill "$_key_pid" 2>/dev/null || true
    wait "$_key_pid" 2>/dev/null || true
    rm -f "$_key_file"
    [ -n "$_key_result" ]
}
ui_print "Select installer language / 选择安装语言"
ui_print "Volume Up: 中文  |  Volume Down: English"
if [ -x /system/bin/getevent ] && read_container_key 8; then
    case "$_key_result" in *KEY_VOLUMEDOWN*) INSTALL_LANG="en" ;; *) INSTALL_LANG="zh" ;; esac
else
    INSTALL_LANG="zh"
    ui_print "No key detected; defaulting to Chinese / 无按键输入，默认中文"
fi
installer_text() { if [ "$INSTALL_LANG" = en ]; then printf '%s' "$2"; else printf '%s' "$1"; fi; }
installer_abort() {
    _abort_zh="$1"; _abort_en="$2"; shift 2
    if [ "$INSTALL_LANG" = en ]; then _abort_message=$(printf "$_abort_en" "$@"); else _abort_message=$(printf "$_abort_zh" "$@"); fi
    abort "$_abort_message"
}
if [ "$HOST_ARCH" = "unknown" ]; then
    installer_abort '不支持的 CPU 架构：%s' 'Unsupported CPU architecture: %s' "$(uname -m 2>/dev/null)"
fi
ui_print "--------------------------------------------"
ui_print "$(installer_text '安装语言：中文' 'Installer language: English')"
ui_print "--------------------------------------------"

# 1. 创建运行目录与共享内存目录
ui_print "$(installer_text '>> 正在准备目录...' '>> Preparing directories...')"
mkdir -p "$BIN_DIR" "$ANCLI_DIR/shm"
# 挂载到容器 /dev/shm 的目录设为 1777（粘滞位），确保普通 shell 用户（uid 2000）可读写
chmod 1777 "$ANCLI_DIR/shm" 2>/dev/null || true

# Installer support files must be restored after a user-selected clean reinstall.
deploy_installer_assets() {
# 1.1 保存容器规格与延迟安装器。即使用户选择稍后通过 WebUI 安装，
#     宿主侧 `ancli container status/install` 仍然必须可用。
if [ -f "$MODPATH/ancli/container-profiles.json" ]; then
    cp -f "$MODPATH/ancli/container-profiles.json" "$ANCLI_DIR/container-profiles.json"
    chmod 644 "$ANCLI_DIR/container-profiles.json" 2>/dev/null || true
fi
if [ -f "$MODPATH/container-profile-data.sh" ]; then
    cp -f "$MODPATH/container-profile-data.sh" "$ANCLI_DIR/container-profile-data.sh"
    chmod 644 "$ANCLI_DIR/container-profile-data.sh" 2>/dev/null || true
else
    installer_abort '缺少生成的容器规格清单' 'Generated container profile catalog is missing'
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
if [ -f "$MODPATH/module-update.sh" ]; then
    cp -f "$MODPATH/module-update.sh" "$ANCLI_DIR/module-update.sh" || installer_abort '无法部署模块更新检查器' 'Could not deploy module update checker'
    chmod 755 "$ANCLI_DIR/module-update.sh" 2>/dev/null || true
fi
if [ -f "$MODPATH/container-export.py" ]; then
    cp -f "$MODPATH/container-export.py" "$ANCLI_DIR/container-export.py" || installer_abort '无法部署容器导出工具' 'Could not deploy container export helper'
    chmod 755 "$ANCLI_DIR/container-export.py"
fi
for _install_helper in install-network.sh install-transaction.sh node-setup.sh task-stop.sh container-mirror.sh dsh-web-control.sh; do
    [ -f "$MODPATH/$_install_helper" ] || installer_abort '缺少安装辅助脚本：%s' 'Missing installer helper: %s' "$_install_helper"
    cp -f "$MODPATH/$_install_helper" "$ANCLI_DIR/$_install_helper" || installer_abort '无法部署安装辅助脚本' 'Could not deploy installer helper'
    chmod 755 "$ANCLI_DIR/$_install_helper" 2>/dev/null || true
done
. "$ANCLI_DIR/install-network.sh"
. "$ANCLI_DIR/install-transaction.sh"
ancli_cleanup_stale_rootfs || installer_abort '无法清理遗留的安装暂存目录' 'Could not clean up the abandoned installation staging directory'

if [ -f "$MODPATH/apt-key-compat.sh" ]; then
    cp -f "$MODPATH/apt-key-compat.sh" "$ANCLI_DIR/apt-key-compat.sh"
    chmod 755 "$ANCLI_DIR/apt-key-compat.sh" 2>/dev/null || true
fi
if [ -f "$MODPATH/android-probe.sh" ]; then
    chmod 755 "$MODPATH/android-probe.sh" 2>/dev/null || true
    cp -f "$MODPATH/android-probe.sh" "$ANCLI_DIR/android-probe.sh"
    chmod 755 "$ANCLI_DIR/android-probe.sh"
fi

}
deploy_installer_assets
. "$ANCLI_DIR/container-profile-data.sh" || installer_abort '无法载入容器规格清单' 'Could not load the container profile catalog'
. "$ANCLI_DIR/container-mirror.sh" || installer_abort '无法载入容器下载站点配置' 'Could not load the container mirror catalog'

# 1.2 容器尚未安装时始终提供选择，即使上次已经留下延后安装配置。
# 已安装的 rootfs 保持原配置，不通过刷入流程覆盖用户环境。
# 旧版本或未完成的 rootfs 必须由用户选择保留修补或彻底清理。
# The destructive path requires a second physical key confirmation.
ancli_clean_for_reinstall() {
    [ "$ANCLI_DIR" = "/data/local/tmp/ancli" ] && [ "$ROOTFS" = "$ANCLI_DIR/rootfs" ] || return 1
    [ -d "$ANCLI_DIR" ] && [ ! -L "$ANCLI_DIR" ] && [ ! -L "$ROOTFS" ] || return 1
    [ -r /proc/mounts ] || { ui_print "$(installer_text '>> 无法检查挂载，已取消清理。' '>> Cannot inspect mounts; cleanup cancelled.')"; return 1; }
    if awk -v p="$ANCLI_DIR" '$2 == p || index($2, p "/") == 1 { found=1 } END { exit !found }' /proc/mounts; then
        ui_print "$(installer_text '>> 容器目录仍有挂载，已取消清理；请先卸载挂载后重试。' '>> The container directory is still mounted; cleanup cancelled. Unmount it and retry.')"
        return 1
    fi
    if ps -A -o ARGS 2>/dev/null | grep -q '[p]root.*\/data\/local\/tmp\/ancli\/rootfs'; then
        ui_print "$(installer_text '>> 容器仍在运行，已取消清理；请关闭容器进程后重试。' '>> The container is still running; cleanup cancelled. Stop its processes and retry.')"
        return 1
    fi
    # Remove only wrappers referencing AnCLI's own directory.
    for _clean_bin in /data/adb/ksu/bin /data/adb/ap/bin; do
        [ -d "$_clean_bin" ] || continue
        grep -rl "$ANCLI_DIR" "$_clean_bin" 2>/dev/null | while IFS= read -r _clean_file; do
            rm -f "$_clean_file" 2>/dev/null || true
        done
    done
    rm -rf "$ANCLI_DIR" || return 1
    mkdir -p "$BIN_DIR" "$ANCLI_DIR/shm" || return 1
    chmod 1777 "$ANCLI_DIR/shm" 2>/dev/null || true
    deploy_installer_assets
    ui_print "$(installer_text '>> 旧容器和配置已清理，重新选择安装方式。' '>> Old container and configuration removed; choose an installation mode again.')"
}

ancli_discard_empty_placeholder || true
_repair_existing=false
_existing_reason=""
_existing_reason_en=""
if [ -d "$ROOTFS" ] && [ -n "$(ls -A "$ROOTFS" 2>/dev/null)" ]; then
    _old_prop=/data/adb/modules/ancli/module.prop
    _old_version=$(sed -n 's/^version=//p' "$_old_prop" 2>/dev/null | head -n 1)
    if [ ! -f "$ROOTFS/bin/bash" ] || [ ! -f "$ROOTFS/usr/bin/python3" ]; then
        _existing_reason="检测到未完成的容器"; _existing_reason_en="An incomplete container was detected"
    elif [ -z "$_old_version" ]; then
        _existing_reason="检测到版本未知的已有容器"; _existing_reason_en="A container with an unknown version was detected"
    elif [ "$_old_version" != "$MODULE_VERSION" ]; then
        _existing_reason="检测到旧版容器（$_old_version）"; _existing_reason_en="An older container version was detected ($_old_version)"
    fi
fi
if [ -n "$_existing_reason" ]; then
    ui_print "--------------------------------"
    ui_print "$(installer_text "$_existing_reason" "$_existing_reason_en")"
    ui_print "$(installer_text '音量上：清理数据并重新选择规格' 'Volume Up: clean the container and choose a profile again')"
    ui_print "$(installer_text '音量下：保留数据并尝试修补继续' 'Volume Down: keep data and attempt a repair')"
    ui_print "$(installer_text '20 秒无操作：保留数据并停止安装' 'No input for 20 seconds: keep data and stop installation')"
    if [ -x /system/bin/getevent ] && read_container_key; then
        case "$_key_result" in
            *KEY_VOLUMEUP*)
                ui_print "--------------------------------"
                ui_print "$(installer_text '清理将删除容器、已安装工具、配置和密钥。' 'Cleanup deletes the container, installed tools, configuration, and keys.')"
                ui_print "$(installer_text '再次按音量上确认清理；音量下取消。' 'Press Volume Up again to confirm cleanup; Volume Down cancels.')"
                if read_container_key && case "$_key_result" in *KEY_VOLUMEUP*) true ;; *) false ;; esac; then
                    ancli_clean_for_reinstall || installer_abort '清理未完成，安装已停止；请检查上方原因。' 'Cleanup did not finish; installation stopped. See the reason above.'
                else
                    installer_abort '已取消清理，保留现有数据并停止安装。' 'Cleanup cancelled; existing data was kept and installation stopped.'
                fi
                ;;
            *KEY_VOLUMEDOWN*)
                [ -f "$ROOTFS/bin/bash" ] || installer_abort '容器缺少基础 shell，无法安全修补；请重新安装并选择清理，或手动备份恢复数据。' 'The container is missing its base shell and cannot be repaired safely. Reinstall and choose cleanup, or back up and restore it manually.'
                _repair_existing=true; ui_print "$(installer_text '>> 已选择保留数据并修补。' '>> Keep-data repair selected.')" ;;
            *) installer_abort '未选择处理方式，保留数据并停止安装。' 'No recovery option selected; data was kept and installation stopped.' ;;
        esac
    else
        installer_abort '未检测到选择，保留数据并停止安装。' 'No key selection detected; data was kept and installation stopped.'
    fi
    ui_print "--------------------------------"
fi

_defer_this_install=false
if [ ! -f "$ROOTFS/bin/bash" ]; then
    _config_file="$ANCLI_DIR/container-config.json"
    _config_exists=false
    [ -f "$_config_file" ] && _config_exists=true
    _container_mode="deferred"
    _container_profile="standard"
    if [ "$_config_exists" = true ]; then
        _saved_profile=$(sed -n 's/^[[:space:]]*"profile"[[:space:]]*:[[:space:]]*"\([^\"]*\)".*/\1/p' "$_config_file" | head -n 1)
        case "$_saved_profile" in minimal|standard|developer|full) _container_profile="$_saved_profile" ;; esac
        ui_print "$(installer_text '>> 发现已有容器配置，但容器尚未安装。' '>> Existing container settings found, but the container is not installed.')"
    fi
    while :; do
    _return_to_mode=false
    _container_mode="deferred"
    if [ -x /system/bin/getevent ]; then
        ui_print "$(installer_text '选择容器安装方式：音量上=现在安装；音量下=稍后在 WebUI 安装。' 'Container setup: Volume Up = install now; Volume Down = configure in WebUI later.')"
        ui_print "$(installer_text '20 秒无按键或按键不可用时，将保留配置并稍后安装。' 'No input for 20 seconds: keep configuration and defer installation.')"
        ui_print "$(installer_text '刷入时默认使用国内镜像；选择 WebUI 安装可选择下载站点并测速。' 'Flashing defaults to a China mirror; WebUI lets you choose and test download mirrors.')"
        if read_container_key; then
            case "$_key_result" in *KEY_VOLUMEUP*) _container_mode="installed" ;; esac
        else
            ui_print "$(installer_text '>> 未检测到音量按键，稍后在 WebUI 安装容器。' '>> No volume key detected; defer container setup to WebUI.')"
        fi
    else
        ui_print "$(installer_text '>> 无法读取音量按键，稍后在 WebUI 安装容器。' '>> Volume keys unavailable; defer container setup to WebUI.')"
    fi
    if [ "$_container_mode" = "installed" ]; then
        case "$_container_profile" in
            minimal) _profile_index=1 ;;
            standard) _profile_index=2 ;;
            developer) _profile_index=3 ;;
            full) _profile_index=4 ;;
            *) _profile_index=2 ;;
        esac
        ui_print ""
        ui_print "--------------------------------"
        ui_print "$(installer_text '选择 Ubuntu 容器规格' 'Choose Ubuntu container profile')"
        _profile_line=1
        for _profile_id in minimal standard developer full; do
            _profile_name_fn="ancli_profile_name_${INSTALL_LANG}"
            _profile_desc_fn="ancli_profile_description_${INSTALL_LANG}"
            _profile_label=$($_profile_name_fn "$_profile_id")
            _profile_desc=$($_profile_desc_fn "$_profile_id")
            _profile_display_packages=$(ancli_profile_display_packages "$_profile_id" "$INSTALL_LANG")
            ui_print "${_profile_line}. ${_profile_label}"
            ui_print "   ${_profile_desc}"
            ui_print "   $(installer_text '包含软件包：' 'Packages: ')${_profile_display_packages}"
            ui_print "--------------------------------"
            _profile_line=$((_profile_line + 1))
        done
        ui_print "$(installer_text '5 返回上一级：重新选择安装方式' '5 Back: return to container setup mode')"
        ui_print "$(installer_text '音量上：切换  |  音量下：确认安装' 'Volume Up: next  |  Volume Down: select')"
        ui_print "$(installer_text '20 秒未操作则延后安装' 'No input for 20 seconds: defer installation')"
        ui_print "--------------------------------"
        while :; do
            case "$_profile_index" in
                1) _profile_name="minimal" ;;
                2) _profile_name="standard" ;;
                3) _profile_name="developer" ;;
                4) _profile_name="full" ;;
                5) _profile_name="back" ;;
            esac
            if [ "$_profile_name" = back ]; then
                _profile_label=$(installer_text '返回上一级' 'Back')
            else
                _profile_name_fn="ancli_profile_name_${INSTALL_LANG}"
                _profile_label=$($_profile_name_fn "$_profile_name")
            fi
            ui_print "$(installer_text '> 已选' '> Selected') [$_profile_index/5] $_profile_label"
            if ! read_container_key; then
                _container_mode="deferred"
                ui_print "$(installer_text '>> 未确认容器规格，稍后在 WebUI 安装。' '>> No profile selected; defer installation to WebUI.')"
                break
            fi
            case "$_key_result" in
                *KEY_VOLUMEUP*) _profile_index=$((_profile_index + 1)); [ "$_profile_index" -gt 5 ] && _profile_index=1 ;;
                *KEY_VOLUMEDOWN*)
                    if [ "$_profile_name" = "back" ]; then
                        _return_to_mode=true
                        ui_print "$(installer_text '>> 返回容器安装方式选择。' '>> Returning to container setup mode.')"
                    else
                        _container_profile="$_profile_name"
                        ui_print "$(installer_text '>> 已确认：' '>> Selected: ')$_profile_label"
                    fi
                    ui_print "--------------------------------"; break ;;
                    *) _container_mode="deferred"; ui_print "$(installer_text '>> 已取消，稍后在 WebUI 安装。' '>> Cancelled; defer installation to WebUI.')"; break ;;
            esac
        done
    fi
    [ "$_return_to_mode" = true ] && continue
    break
    done
    [ "$_container_mode" = "deferred" ] && _defer_this_install=true
    _container_size=0
    if [ "$_config_exists" = true ]; then
        # Update only the three known values and retain proxy, language and other
        # existing configuration. Never overwrite an existing config on timeout.
        if [ "$_container_mode" = "installed" ]; then
            if grep -q '"profile"[[:space:]]*:' "$_config_file" && grep -q '"install_mode"[[:space:]]*:' "$_config_file"; then
                sed -e "s/\(\"profile\"[[:space:]]*:[[:space:]]*\"\)[^\"]*\"/\1$_container_profile\"/" \
                    -e 's/\("install_mode"[[:space:]]*:[[:space:]]*"\)[^"]*"/\1installed"/' \
                    -e "s/\(\"estimated_size_mb\"[[:space:]]*:[[:space:]]*\)[0-9][0-9]*/\1$_container_size/" \
                    "$_config_file" > "$_config_file.tmp.$$" || installer_abort '无法更新容器配置' 'Could not update container configuration'
                mv -f "$_config_file.tmp.$$" "$_config_file" || installer_abort '无法保存容器配置' 'Could not save container configuration'
                chmod 600 "$_config_file" 2>/dev/null || true
            else
                installer_abort '已有容器配置不完整；请在 WebUI 中配置，未修改原配置。' 'The existing container configuration is incomplete. Configure it in WebUI; the original was not changed.'
            fi
        fi
    else
        printf '{\n  "profile": "%s",\n  "install_mode": "%s",\n  "network_mode": "system",\n  "estimated_size_mb": %s\n}\n' "$_container_profile" "$_container_mode" "$_container_size" > "$_config_file"
        chmod 600 "$_config_file" 2>/dev/null || true
    fi
else
    if [ -f "$ROOTFS/usr/bin/python3" ]; then
    ui_print "$(installer_text '>> 已检测到现有容器，保留原有数据和配置。' '>> Existing container found; keeping its data and configuration.')"
    else
        ui_print "$(installer_text '>> 检测到未完成的旧 rootfs，将保留数据并继续修复。' '>> Incomplete rootfs found; keeping data and continuing with repair.')"
    fi
fi

_container_mode=$(sed -n 's/^[[:space:]]*"install_mode"[[:space:]]*:[[:space:]]*"\([^\"]*\)".*/\1/p' "$ANCLI_DIR/container-config.json" 2>/dev/null | head -n 1)
_container_profile=$(sed -n 's/^[[:space:]]*"profile"[[:space:]]*:[[:space:]]*"\([^\"]*\)".*/\1/p' "$ANCLI_DIR/container-config.json" 2>/dev/null | head -n 1)
[ "$_defer_this_install" = true ] && _container_mode="deferred"
[ "$_repair_existing" = true ] && _container_mode="installed"
[ -n "$_container_profile" ] || _container_profile="standard"

if [ "$_container_mode" = "installed" ]; then
    _network_mode=$(sed -n 's/^[[:space:]]*"network_mode"[[:space:]]*:[[:space:]]*"\([^\"]*\)".*/\1/p' "$ANCLI_DIR/container-config.json" 2>/dev/null | head -n 1)
    _proxy_url=$(sed -n 's/^[[:space:]]*"proxy_url"[[:space:]]*:[[:space:]]*"\([^\"]*\)".*/\1/p' "$ANCLI_DIR/container-config.json" 2>/dev/null | head -n 1)
    ancli_install_network "${_network_mode:-system}" "$_proxy_url" || installer_abort '代理配置无效，请先在 WebUI 中检查网络设置' 'Proxy configuration is invalid. Check the network settings in WebUI first.'
fi

# 2. 从模块包部署静态编译的 PRoot 二进制
ui_print "$(installer_text ">> 已检测主机架构：$HOST_ARCH（容器：$ROOTFS_ARCH）" ">> Detected host architecture: $HOST_ARCH (container: $ROOTFS_ARCH)")"
ui_print "$(installer_text ">> 正在部署 $HOST_ARCH 版 PRoot..." ">> Deploying PRoot for $HOST_ARCH...")"
install_runtime_file() {
    _src="$1"; _dst="$2"; _tmp="${_dst}.new.$$"
    cp -f "$_src" "$_tmp" && chmod 755 "$_tmp" && mv -f "$_tmp" "$_dst" || {
        rm -f "$_tmp"
        installer_abort '无法部署运行文件：%s' 'Could not deploy runtime file: %s' "$_dst"
    }
}
if [ "$HOST_ARCH" = "x86_64" ]; then
    if [ -f "$MODPATH/bin/x86_64/proot" ]; then
        install_runtime_file "$MODPATH/bin/x86_64/proot" "$BIN_DIR/proot"
        for _loader in "$MODPATH"/bin/x86_64/loader*; do
            [ -f "$_loader" ] || continue
            install_runtime_file "$_loader" "$BIN_DIR/${_loader##*/}"
        done
    elif [ -f "$MODPATH/bin/proot" ]; then
        install_runtime_file "$MODPATH/bin/proot" "$BIN_DIR/proot"
    else
        installer_abort '缺少 x86_64 PRoot' 'Missing x86_64 PRoot binary'
    fi
else
    if [ -f "$MODPATH/bin/arm64/proot" ]; then
        install_runtime_file "$MODPATH/bin/arm64/proot" "$BIN_DIR/proot"
    elif [ -f "$MODPATH/bin/proot" ]; then
        install_runtime_file "$MODPATH/bin/proot" "$BIN_DIR/proot"
    else
        installer_abort '缺少 arm64 PRoot' 'Missing arm64 PRoot binary'
    fi
fi
ui_print "$(installer_text '>> PRoot 部署成功。' '>> PRoot deployed successfully.')"

# A fresh install is built in a marked sibling directory. Only that directory
# is removed on failure; an existing user rootfs is never deleted.
if [ "$_container_mode" = "installed" ] && [ ! -f "$ROOTFS/bin/bash" ]; then
    ancli_begin_rootfs || installer_abort '无法创建容器安装临时目录（已有数据已保留）' 'Could not create the container staging directory; existing data was kept.'
fi
ancli_install_abort() { ancli_rollback_rootfs; abort "$1"; }

# 3. 下载并解压 Ubuntu Base 根文件系统（若已存在则幂等跳过）。
#    用户选择 WebUI 延迟安装时，仅保留宿主入口，跳过这里和后面的 APT。
if [ -f "$ROOTFS/bin/bash" ]; then
    _existing_arch=$(get_rootfs_arch "$ROOTFS")
    if [ "$_existing_arch" != "none" ] && [ "$_existing_arch" != "$HOST_ARCH" ]; then
        installer_abort '现有 rootfs 架构（%s）与主机架构（%s）不匹配；拒绝覆盖。' 'Existing rootfs architecture (%s) does not match host architecture (%s); refusing to overwrite.' "$_existing_arch" "$HOST_ARCH"
    fi
fi

_cfg_mirror=$(sed -n 's/^[[:space:]]*"mirror"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$ANCLI_DIR/container-config.json" 2>/dev/null | head -n 1)
_pref_mirror=""
_custom_mirror_host=""
if [ -n "${ANCLI_MIRROR:-}" ]; then
    case "$ANCLI_MIRROR" in
        tuna|mirrors.tuna.tsinghua.edu.cn) _pref_mirror="tuna" ;;
        ustc|mirrors.ustc.edu.cn) _pref_mirror="ustc" ;;
        official|cdimage.ubuntu.com) _pref_mirror="official" ;;
        *) _custom_mirror_host="$ANCLI_MIRROR"; _pref_mirror="tuna" ;;
    esac
else
    case "$_cfg_mirror" in
        tuna|ustc|official) _pref_mirror="$_cfg_mirror" ;;
        *) _pref_mirror="tuna" ;;
    esac
fi
_selected_apt_mirror="$_pref_mirror"

if [ "$_container_mode" != "installed" ]; then
ui_print "$(installer_text '>> 容器安装已延后；请在 WebUI 中配置。' '>> Container installation deferred; configure it in WebUI.')"
else
verify_downloaded_tarball() {
    _vdt_file="$1"
    _vdt_url="$2"
    _vdt_sums="${_vdt_file}.SHA256SUMS"
    _vdt_name="${_vdt_url##*/}"
    _vdt_sums_url="${_vdt_url%/*}/SHA256SUMS"
    download_tarball "$_vdt_sums" "$_vdt_sums_url" && [ -s "$_vdt_sums" ] || { rm -f "$_vdt_sums" "$_vdt_file"; return 1; }
    _vdt_expected=$(awk -v f="$_vdt_name" '$2 == f || $2 == ("*" f) {print tolower($1); exit}' "$_vdt_sums")
    _vdt_actual=$(sha256sum "$_vdt_file" 2>/dev/null | awk '{print tolower($1)}')
    rm -f "$_vdt_sums"
    if [ -n "$_vdt_expected" ] && [ "$_vdt_expected" = "$_vdt_actual" ]; then
        return 0
    fi
    rm -f "$_vdt_file"
    return 1
}

if [ ! -f "$ROOTFS/bin/bash" ]; then
    ui_print "$(installer_text ">> 正在下载 Ubuntu Base（$ROOTFS_ARCH）..." ">> Downloading Ubuntu Base ($ROOTFS_ARCH)...")"
    TAR_PATH="${ANCLI_ROOTFS_ARCHIVE:-$ANCLI_DIR/ubuntu-base.tar.gz}"
    success=0

    for _ver in 24.04.5 24.04.4; do
        if [ -n "$_custom_mirror_host" ]; then
            mirror="https://${_custom_mirror_host}/ubuntu-cdimage/ubuntu-base/releases/24.04/release/ubuntu-base-${_ver}-base-${ROOTFS_ARCH}.tar.gz"
            ui_print "$(installer_text ">> 正在尝试镜像：$mirror" ">> Trying mirror: $mirror")"
            if download_tarball "$TAR_PATH" "$mirror" && [ -s "$TAR_PATH" ] && verify_downloaded_tarball "$TAR_PATH" "$mirror"; then
                success=1
                break
            fi
        fi
        _tried_mirrors=""
        for _m_id in "$_pref_mirror" tuna ustc official; do
            case " $_tried_mirrors " in *" $_m_id "*) continue ;; esac
            _tried_mirrors="$_tried_mirrors $_m_id"
            mirror=$(ancli_mirror_rootfs_url "$_m_id" "$_ver" "$ROOTFS_ARCH") || continue
            ui_print "$(installer_text ">> 正在尝试镜像：$mirror" ">> Trying mirror: $mirror")"
            if download_tarball "$TAR_PATH" "$mirror" && [ -s "$TAR_PATH" ] && verify_downloaded_tarball "$TAR_PATH" "$mirror"; then
                _selected_apt_mirror="$_m_id"
                _custom_mirror_host=""
                success=1
                break 2
            fi
        done
    done
    
    if [ "$success" -ne 1 ]; then
        ancli_install_abort "$(installer_text "所有镜像均无法下载 Ubuntu Base（$ROOTFS_ARCH）" "Failed to download Ubuntu Base ($ROOTFS_ARCH) from all available mirrors")"
    fi
    ui_print "$(installer_text '>> Ubuntu Base 下载完成。' '>> Ubuntu Base downloaded successfully.')"

    ui_print "$(installer_text '>> 正在解压 rootfs（可能需要一些时间）...' '>> Extracting rootfs (this may take a minute)...')"
    tar -xf "$TAR_PATH" -C "$ROOTFS" || ancli_install_abort "$(installer_text 'rootfs 解压失败，下载文件可能已损坏' 'Failed to extract rootfs; the downloaded archive may be corrupted')"
    rm -f "$TAR_PATH"

    # 初始化容器内 resolv.conf：优先写入宿主机 net.dns1/2 与 dumpsys DnsAddresses，随后追加备用公共 DNS（去重且最多 3 条）
    rm -f "$ROOTFS/etc/resolv.conf"
    : > "$ROOTFS/etc/resolv.conf"
    _dns_net=$(/system/bin/dumpsys connectivity 2>/dev/null | grep -o 'DnsAddresses: \[[^]]*\]' | head -n 1 \
        | tr ',' '\n' | grep -oE '[0-9]{1,3}(\.[0-9]{1,3}){3}' | head -n 3)
    _dns_seen=""
    _dns_count=0
    for _dns_val in "$(getprop net.dns1 2>/dev/null)" "$(getprop net.dns2 2>/dev/null)" $_dns_net 223.5.5.5 119.29.29.29 1.1.1.1 8.8.8.8; do
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
    ui_print "$(installer_text '>> Rootfs 已解压并完成配置。' '>> Rootfs extracted and configured.')"
else
    ui_print "$(installer_text '>> Rootfs 已存在，跳过下载。' '>> Rootfs already present; skipping download.')"
fi
fi

# Fix ARM64 PRoot access checks before APT reads its signed keyring.
if [ "$_container_mode" = "installed" ] && [ -f "$ROOTFS/bin/bash" ]; then
    sh "$ANCLI_DIR/apt-key-compat.sh" "$ROOTFS" || ancli_install_abort "$(installer_text 'APT 密钥环兼容性配置失败' 'APT keyring compatibility setup failed')"
fi

# 4. 通过 PRoot 引导或补齐容器内 APT 基础依赖（带幂等检查）
_ancli_dir_clean="${ANCLI_DIR%/}"
_rootfs_clean="${ROOTFS%/}"
[ -n "$_ancli_dir_clean" ] && [ -n "$_rootfs_clean" ] || installer_abort '无效的容器目录配置' 'Invalid container directory configuration'
mkdir -p "$_rootfs_clean/root" 2>/dev/null || true
_proot_binds=""
_proot_seen=""
for _pb in /dev /proc /sys "$_ancli_dir_clean"; do
    _pb="${_pb%/}"
    [ -n "$_pb" ] || continue
    [ -e "$_pb" ] || [ -d "$_pb" ] || continue
    case " $_proot_seen " in *" $_pb "*) continue ;; esac
    _proot_seen="$_proot_seen $_pb"
    _proot_binds="$_proot_binds -b $_pb"
done
export PROOT_NO_SECCOMP=1
PROOT_CMD="$BIN_DIR/proot -r $_rootfs_clean$_proot_binds -w /root"

# APT 依赖脚本生成（幂等）；软件包清单与安装菜单、WebUI 共用 container-profiles.json。
# Java 17 供 jadx 等反编译工具；binutils 提供 strings/readelf/objdump；
# 并解锁 PEP 668（Ubuntu 24.04 默认拒绝 pip install）以允许直接 pip 装库。
_profile_packages=$(ancli_profile_packages "$_container_profile") || installer_abort '无效的容器规格：%s' 'Invalid container profile: %s' "$_container_profile"
write_setup_script() {
    case "$_selected_apt_mirror" in
        ustc) _pip_url="https://pypi.mirrors.ustc.edu.cn/simple" ;;
        official) _pip_url="https://pypi.org/simple" ;;
        *) _pip_url="https://pypi.tuna.tsinghua.edu.cn/simple" ;;
    esac
    _setup_proxy_exports=""
    if [ -n "${http_proxy:-}" ]; then
        _setup_proxy_exports="export http_proxy='${http_proxy}' https_proxy='${https_proxy:-${http_proxy}}' HTTP_PROXY='${HTTP_PROXY:-${http_proxy}}' HTTPS_PROXY='${HTTPS_PROXY:-${https_proxy:-${http_proxy}}}'"
    fi
    cat > "$ROOTFS/root/setup.sh" << SETUP
#!/bin/bash
set -eu
export DEBIAN_FRONTEND=noninteractive
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export TMPDIR=/tmp
$_setup_proxy_exports

# 禁止 invoke-rc.d 在 PRoot 容器内拉起系统服务
mkdir -p /usr/sbin
printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d
chmod 755 /usr/sbin/policy-rc.d

# PRoot 环境下配置 APT sandbox 为 root 与非交互 dpkg 选项，实现安全规范的 GPG 签名校验
mkdir -p /etc/apt/apt.conf.d
if [ -n "\${http_proxy:-}" ]; then
    printf 'Acquire::http::Proxy "%s";\n' "\$http_proxy" > /etc/apt/apt.conf.d/99ancli-proxy
    printf 'Acquire::https::Proxy "%s";\n' "\${https_proxy:-\$http_proxy}" >> /etc/apt/apt.conf.d/99ancli-proxy
fi
printf 'APT::Sandbox::User "root";\n' > /etc/apt/apt.conf.d/01sandbox
printf 'DPkg::Use-Pty "0";\nDPkg::Options {\n    "--force-confdef";\n    "--force-confold";\n};\n' > /etc/apt/apt.conf.d/02ancli-dpkg

# 清理 PRoot 下无法执行的 systemd/udev postinst，避免半配置状态或 triggers 触发后续 apt/dpkg 崩溃
ancli_harden_systemd_dpkg() {
    for _pi in /var/lib/dpkg/info/systemd*.postinst /var/lib/dpkg/info/udev.postinst; do
        [ -f "\$_pi" ] || continue
        if grep -qE 'systemd-sysusers|systemd-tmpfiles|systemctl|udevadm' "\$_pi" 2>/dev/null; then
            printf '#!/bin/sh\n# AnCLI PRoot stub: systemd/udev postinst cannot run under PRoot\nexit 0\n' > "\$_pi"
            chmod 755 "\$_pi"
        fi
    done
    if command -v dpkg >/dev/null 2>&1; then
        dpkg --configure --pending >/dev/null 2>&1 || true
    fi
}

ancli_harden_systemd_dpkg
apt-get update -y
if ! apt-get install -y --no-install-recommends \\
    $_profile_packages; then
    if command -v dpkg >/dev/null 2>&1 && dpkg --audit 2>/dev/null | grep -qE 'systemd|udev'; then
        ancli_harden_systemd_dpkg
        apt-get install -y --no-install-recommends \\
            $_profile_packages
    else
        exit 1
    fi
fi
ancli_harden_systemd_dpkg
apt-get clean
rm -f /etc/apt/apt.conf.d/99ancli-proxy
/bin/sh /data/local/tmp/ancli/node-setup.sh

# 移除 PEP 668 限制标记并配置镜像对应的 PyPI 源，允许在容器系统 Python 中直接 pip install
rm -f /usr/lib/python3*/EXTERNALLY-MANAGED
if [ ! -f /etc/pip.conf ] || grep -q '# AnCLI managed' /etc/pip.conf 2>/dev/null || [ ! -s /etc/pip.conf ]; then
    printf '# AnCLI managed\n[global]\nindex-url = %s\n' '$_pip_url' > /etc/pip.conf
fi
SETUP
    chmod 755 "$ROOTFS/root/setup.sh"
}

if [ "$_container_mode" = "installed" ] && ! $PROOT_CMD /usr/bin/python3 --version >/dev/null 2>&1; then
    _profile_name_fn="ancli_profile_name_${INSTALL_LANG}"
    _profile_install_name=$($_profile_name_fn "$_container_profile")
    ui_print "$(installer_text ">> 正在安装容器规格“$_profile_install_name”所需的软件包..." ">> Installing packages for container profile '$_profile_install_name'...")"

    # 宿主机侧预先替换 Ubuntu APT 源（复用 container-mirror.sh，自动备份 ubuntu.sources）
    if [ -n "${_custom_mirror_host:-}" ]; then
        if [ "$APT_REPO_TYPE" = "main" ]; then
            _apt_url="http://${_custom_mirror_host}/ubuntu"
        else
            _apt_url="http://${_custom_mirror_host}/ubuntu-ports"
        fi
    else
        _apt_url=$(ancli_mirror_apt_url "$_selected_apt_mirror" "$ROOTFS_ARCH") || \
            ancli_install_abort "$(installer_text '无法解析 APT 镜像地址' 'Could not resolve APT mirror URL')"
    fi
    if [ -f "$ROOTFS/etc/apt/sources.list.d/ubuntu.sources" ]; then
        ancli_mirror_configure_apt "$ROOTFS" "$_apt_url" || \
            ancli_install_abort "$(installer_text '无法写入 APT 源配置' 'Could not configure APT mirror')"
    elif [ -f "$ROOTFS/etc/apt/sources.list" ]; then
        sed -i "s|https\?://[^/]*\.ubuntu\.com/ubuntu\(-ports\)\?|$_apt_url|g" "$ROOTFS/etc/apt/sources.list" 2>/dev/null || true
    fi
    ui_print "$(installer_text ">> APT 镜像：$_apt_url（$APT_REPO_TYPE）" ">> APT mirror: $_apt_url ($APT_REPO_TYPE)")"

    write_setup_script
    $PROOT_CMD /root/setup.sh || ancli_install_abort "$(installer_text 'APT 引导安装失败' 'APT bootstrap failed')"
    sh "$ANCLI_DIR/apt-key-compat.sh" "$ROOTFS" || ancli_install_abort "$(installer_text 'APT 密钥环兼容性配置失败' 'APT keyring compatibility setup failed')"
    rm -f "$ROOTFS/root/setup.sh"
    ui_print "$(installer_text '>> 依赖安装成功。' '>> Dependencies installed successfully.')"
elif [ "$_container_mode" = "installed" ]; then
    ui_print "$(installer_text '>> 依赖已安装，跳过 APT。' '>> Dependencies already installed; skipping APT.')"
    # 模块覆盖升级路径：若旧版 rootfs 缺失新版本追加的 Java 17 或 binutils，则幂等补装
    if [ "$_repair_existing" = true ] || ! $PROOT_CMD /usr/bin/python3 --version >/dev/null 2>&1; then
        ui_print "$(installer_text '>> 正在补齐所选规格的软件包和 Node.js...' '>> Repairing the selected profile packages and Node.js...')"
        write_setup_script
        if $PROOT_CMD /root/setup.sh; then
        sh "$ANCLI_DIR/apt-key-compat.sh" "$ROOTFS" || ancli_install_abort "$(installer_text 'APT 密钥环兼容性配置失败' 'APT keyring compatibility setup failed')"
        ui_print "$(installer_text '>> 基础工具链补齐完成。' '>> Base toolchain repair complete.')"
        else
        ui_print "$(installer_text ">> [警告] 补齐失败；请运行 'ancli repair' 或重新安装模块。" ">> [WARN] Repair failed; run 'ancli repair' or reinstall the module.")"
        fi
        rm -f "$ROOTFS/root/setup.sh"
    fi
fi

if [ -n "${ANCLI_ROOTFS_STAGE:-}" ]; then
    ancli_commit_rootfs || ancli_install_abort "$(installer_text '无法提交容器安装；已有数据未被覆盖' 'Could not commit the container installation; existing data was not overwritten')"
fi

# 4b. 确保 /usr/local 目录结构存在（Ubuntu Base 24.04 默认不带 /usr/local/lib/node_modules，npm -g 依赖它）
if [ "$_container_mode" = "installed" ] || [ -f "$ROOTFS/bin/bash" ]; then
ui_print "$(installer_text '>> 正在设置 npm 全局安装路径...' '>> Ensuring npm global install paths...')"
mkdir -p "$ROOTFS/usr/local/bin" \
         "$ROOTFS/usr/local/sbin" \
         "$ROOTFS/usr/local/lib/node_modules" \
         "$ROOTFS/usr/local/share" \
         "$ROOTFS/root/.npm/_logs" \
         "$ROOTFS/root/.ancli-work"
chmod 755 "$ROOTFS/usr/local/bin" \
          "$ROOTFS/usr/local/sbin" \
          "$ROOTFS/usr/local/lib" \
          "$ROOTFS/usr/local/lib/node_modules" \
          "$ROOTFS/root/.ancli-work"
ui_print "$(installer_text '>> npm 路径已就绪。' '>> npm paths ready.')"
fi

# 5. 部署 AnCLI 核心脚本、环境桥接脚本、宿主机 bash 兼容模板与运行时上下文模板
ui_print "$(installer_text '>> 正在部署 AnCLI 核心...' '>> Deploying AnCLI Core...')"
cp -f "$MODPATH/ancli/ancli-core.py" "$BIN_DIR/ancli-core.py"
cp -f "$MODPATH/ancli/ancli_env.sh" "$BIN_DIR/ancli_env.sh"
chmod 755 "$BIN_DIR/ancli-core.py" "$BIN_DIR/ancli_env.sh"

# 不再向 $MODPATH/system/bin/bash 或 /data/adb/{ksu,ap}/bin 写入假 bash 或包装脚本；
# `ancli` 及已安装工具通过 $MODPATH/system/bin/ 在重启后由管理器 systemless 挂载生效，
# 也可在容器内或通过 /data/local/tmp/ancli/bin/ 直接调用。
rm -f "$MODPATH/system/bin/bash" "$ANCLI_DIR/bash-host-compat" 2>/dev/null || true

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
    ln -sf /bin/bash "$ROOTFS/usr/local/bin/bash" 2>/dev/null || true
fi

# 部署不可变运行时上下文模板（仅作基准模板，实际 /root/.agents/AGENTS.md 由 ancli agent-init/repair 生成）
if [ -f "$MODPATH/ancli/runtime-context/AGENTS.md" ]; then
    mkdir -p "$ANCLI_DIR/runtime-context"
    cp -f "$MODPATH/ancli/runtime-context/AGENTS.md" "$ANCLI_DIR/runtime-context/AGENTS.md"
    chmod 644 "$ANCLI_DIR/runtime-context/AGENTS.md"
fi

# 同时部署 registry.json 到 $BIN_DIR 和 $ANCLI_DIR，兼容新旧核心版本的离线首启读取路径
cp -f "$MODPATH/ancli/registry.json" "$BIN_DIR/registry.json" 2>/dev/null || true
cp -f "$MODPATH/ancli/registry.json" "$ANCLI_DIR/registry.json" 2>/dev/null || true


# 6. 复制主入口到 $BIN_DIR/ancli，供 WebUI 与免重启直接调用；
#    $MODPATH/system/bin/ancli 将在设备重启后由系统无系统挂载至 /system/bin/ancli。
cp -f "$MODPATH/system/bin/ancli" "$BIN_DIR/ancli" 2>/dev/null || true
chmod 755 "$BIN_DIR/ancli" 2>/dev/null || true
ui_print "$(installer_text ">> 提示：模块与新装工具的全局 /system/bin 入口将在重启后生效。" ">> Note: Global /system/bin entrypoints take effect after reboot.")"


# Request hot install only through the official KernelSU capability flag.
if [ -f "$MODPATH/hot-install-policy.sh" ]; then
    . "$MODPATH/hot-install-policy.sh"
    ancli_request_hot_install
fi


ui_print ""
ui_print "=========================================="
ui_print "$(installer_text '  安装完成！' '  Installation complete!')"
ui_print "$(installer_text "  在任意 root 终端输入 'ancli' 启动。" "  Type 'ancli' in any root shell to start.")"
ui_print "=========================================="
