#!/system/bin/sh
# Module update checks and downloads must work even when Ubuntu rootfs is absent.

MODULE_DIR=/data/adb/modules/ancli
[ -f /data/adb/modules_update/ancli/module.prop ] && MODULE_DIR=/data/adb/modules_update/ancli
ANCLI_DIR=/data/local/tmp/ancli
UPDATE_DIR="$ANCLI_DIR/module-updates"

json_error() {
    printf '{"status":"error","error":"%s"}\n' "$1"
}

json_value() {
    _key="$1"
    printf '%s\n' "$2" | grep -oE '"'"$_key"'"[[:space:]]*:[[:space:]]*"[^"\\]*"' | head -n 1 | sed 's/^[^:]*:[[:space:]]*"//; s/"$//'
}

numeric_value() {
    _key="$1"
    printf '%s\n' "$2" | grep -oE '"'"$_key"'"[[:space:]]*:[[:space:]]*[0-9]+' | head -n 1 | sed 's/.*:[[:space:]]*//'
}

check_update() {
    _channel=stable
    [ "$1" = beta ] && _channel=beta
    _current_version=$(sed -n 's/^version=//p' "$MODULE_DIR/module.prop" 2>/dev/null | head -n 1 | tr -d '\r')
    _current_code=$(sed -n 's/^versionCode=//p' "$MODULE_DIR/module.prop" 2>/dev/null | head -n 1 | tr -cd '0-9')
    [ -n "$_current_version" ] && [ -n "$_current_code" ] || { json_error '无法读取当前模块版本'; return 0; }

    if [ "$_channel" = stable ]; then
        _payload=$(curl -fsSL -A AnCLI --connect-timeout 15 --max-time 25 'https://raw.githubusercontent.com/AHLLX/AnCLI/main/update.json' 2>/dev/null) || {
            json_error '正式版更新信息获取失败，请检查网络或代理设置'; return 0;
        }
        _latest_version=$(json_value version "$_payload")
        _latest_code=$(numeric_value versionCode "$_payload")
        _zip_url=$(json_value zipUrl "$_payload")
        _html_url="https://github.com/AHLLX/AnCLI/releases/tag/$_latest_version"
    else
        _payload=$(curl -fsSL -A AnCLI --connect-timeout 15 --max-time 25 'https://api.github.com/repos/AHLLX/AnCLI/releases?per_page=1' 2>/dev/null) || {
            json_error 'Beta Release 信息获取失败，请检查网络或代理设置'; return 0;
        }
        _latest_version=$(json_value tag_name "$_payload")
        _html_url=$(json_value html_url "$_payload")
        _zip_url=$(printf '%s\n' "$_payload" | grep -oE '"browser_download_url"[[:space:]]*:[[:space:]]*"https://github.com/AHLLX/AnCLI/releases/download/[^" ]*/ancli-[0-9]+-[^" ]+\.zip"' | head -n 1 | sed 's/^[^:]*:[[:space:]]*"//; s/"$//')
        _asset_name=${_zip_url##*/}
        _latest_code=$(printf '%s' "$_asset_name" | sed -n 's/^ancli-\([0-9][0-9]*\)-.*/\1/p')
    fi

    printf '%s\n' "$_latest_version" | grep -Eq '^v[0-9]+\.[0-9]+\.[0-9]+(-beta)?$' || { json_error 'Release 版本号无效'; return 0; }
    case "$_latest_code" in ''|*[!0-9]*) json_error 'Release 缺少有效 versionCode'; return 0 ;; esac
    printf '%s\n' "$_zip_url" | grep -Eq '^https://github\.com/AHLLX/AnCLI/releases/download/v[0-9]+\.[0-9]+\.[0-9]+(-beta)?/ancli-[0-9]+-v[0-9]+\.[0-9]+\.[0-9]+(-beta)?\.zip$' || { json_error 'Release 中没有可信的模块 ZIP'; return 0; }

    _has_update=false
    if [ "$_latest_code" -gt "$_current_code" ] 2>/dev/null; then _has_update=true
    elif [ "$_latest_code" -eq "$_current_code" ] 2>/dev/null && [ "$_latest_version" != "$_current_version" ]; then _has_update=true; fi
    printf '{"status":"ok","channel":"%s","current_version":"%s","current_version_code":%s,"has_update":%s,"latest_version":"%s","latest_version_code":%s,"zip_url":"%s","changelog":"%s","published_at":"","html_url":"%s"}\n' \
        "$_channel" "$_current_version" "$_current_code" "$_has_update" "$_latest_version" "$_latest_code" "$_zip_url" "$_latest_version" "$_html_url"
}

download_update() {
    _url= _version= _code=
    _channel=stable
    for _arg in "$@"; do
        case "$_arg" in
            --url=*) _url=${_arg#*=} ;;
            --version=*) _version=${_arg#*=} ;;
            --code=*) _code=${_arg#*=} ;;
            --channel=beta) _channel=beta ;;
            --channel=stable) _channel=stable ;;
            *) json_error '更新下载参数无效'; return 1 ;;
        esac
    done
    if [ -z "$_url" ]; then
        _check_file="$ANCLI_DIR/.module-update-check-$$.json"
        check_update "$_channel" > "$_check_file"
        _check_payload=$(cat "$_check_file" 2>/dev/null); rm -f "$_check_file"
        [ "$(json_value status "$_check_payload")" = ok ] || { json_error '模块版本检查失败，请先重试检查更新'; return 1; }
        _url=$(json_value zip_url "$_check_payload")
        _version=$(json_value latest_version "$_check_payload")
        _code=$(numeric_value latest_version_code "$_check_payload")
    fi
    printf '%s\n' "$_url" | grep -Eq '^https://github\.com/AHLLX/AnCLI/releases/download/v[0-9]+\.[0-9]+\.[0-9]+(-beta)?/ancli-[0-9]+-v[0-9]+\.[0-9]+\.[0-9]+(-beta)?\.zip$' || { json_error '更新地址不可信'; return 1; }
    printf '%s\n' "$_version" | grep -Eq '^v[0-9]+\.[0-9]+\.[0-9]+(-beta)?$' || { json_error '更新版本号无效'; return 1; }
    case "$_code" in ''|*[!0-9]*) json_error '更新 versionCode 无效'; return 1 ;; esac
    _name=${_url##*/}
    case "$_name" in ancli-*-*.zip) ;; *) json_error '更新文件名无效'; return 1 ;; esac
    mkdir -p "$UPDATE_DIR" || { json_error '无法创建模块更新目录'; return 1; }
    _target="$UPDATE_DIR/$_name"
    _partial="$UPDATE_DIR/.$_name.$$.part"
    rm -f "$_partial"
    echo "[i] 正在下载 $_name …"
    if ! curl -fLsS -L -A AnCLI --connect-timeout 20 --max-time 600 --progress-bar -o "$_partial" "$_url"; then
        rm -f "$_partial"; json_error '模块 ZIP 下载失败'; return 1
    fi
    if ! unzip -tqq "$_partial" >/dev/null 2>&1; then
        rm -f "$_partial"; json_error '下载文件不是有效 ZIP'; return 1
    fi
    _prop=$(unzip -p "$_partial" module.prop 2>/dev/null) || {
        rm -f "$_partial"; json_error '模块 ZIP 根目录缺少 module.prop'; return 1;
    }
    _found_version=$(printf '%s\n' "$_prop" | sed -n 's/^version=//p' | head -n 1 | tr -d '\r')
    _found_code=$(printf '%s\n' "$_prop" | sed -n 's/^versionCode=//p' | head -n 1 | tr -cd '0-9')
    if [ "$_found_version" != "$_version" ] || [ "$_found_code" != "$_code" ]; then
        rm -f "$_partial"; json_error 'ZIP 版本与 Release 信息不匹配'; return 1
    fi
    if [ -e "$_target" ]; then
        _old_hash=$(sha256sum "$_target" 2>/dev/null | awk '{print $1}')
        _new_hash=$(sha256sum "$_partial" 2>/dev/null | awk '{print $1}')
        if [ -z "$_old_hash" ] || [ "$_old_hash" != "$_new_hash" ]; then
            rm -f "$_partial"; json_error '更新目录已有同名不同内容的 ZIP'; return 1
        fi
        rm -f "$_partial"
    else
        mv "$_partial" "$_target" || { rm -f "$_partial"; json_error '无法保存已验证的更新包'; return 1; }
    fi
    _bytes=$(wc -c < "$_target" | tr -d ' ')
    printf 'ANCLI_DOWNLOAD_PROGRESS %s %s 100\n' "$_bytes" "$_bytes"
    printf 'ANCLI_MODULE_DOWNLOAD_READY {"path":"%s","version":"%s","version_code":%s,"bytes":%s}\n' \
        "$_target" "$_version" "$_code" "$_bytes"
    echo "[OK] 更新 ZIP 已校验：$_target"
}

case "${1:-}" in
    check) check_update "${2:-stable}" ;;
    download) shift; download_update "$@" ;;
    *) json_error '用法：module-update.sh check [stable|beta] | download --url=... --version=... --code=...' ;;
esac
