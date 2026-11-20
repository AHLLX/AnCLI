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
    (printf '%s' "${1:-}"; printf '__ANCLI_EOF__') | LC_ALL=C awk -v BINMODE=3 '
    BEGIN {
        for (i = 1; i < 32; i++) {
            ch = sprintf("%c", i)
            if (i == 8) rep[ch] = "\\" "b"
            else if (i == 9) rep[ch] = "\\" "t"
            else if (i == 10) rep[ch] = "\\" "n"
            else if (i == 12) rep[ch] = "\\" "f"
            else if (i == 13) rep[ch] = "\\" "r"
            else rep[ch] = "\\" sprintf("u%04x", i)
        }
    }
    function esc(s,    n, i, c, r) {
        r = ""
        n = length(s)
        for (i = 1; i <= n; i++) {
            c = substr(s, i, 1)
            if (c == "\\") r = r "\\\\"
            else if (c == "\"") r = r "\\\""
            else if (c in rep) r = r rep[c]
            else r = r c
        }
        return r
    }
    NR == 1 { prev = $0; next }
    {
        printf "%s\\n", esc(prev)
        prev = $0
    }
    END {
        prev = substr(prev, 1, length(prev) - 13)
        printf "%s", esc(prev)
    }'
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

_json_read_file_key() {
    _jrf_file="$1"
    _jrf_key="$2"
    [ -n "$_jrf_key" ] && [ -f "$_jrf_file" ] || return 1
    LC_ALL=C awk -v target="$_jrf_key" '
    BEGIN {
        for (i = 0; i < 10; i++) hex[sprintf("%d", i)] = i
        hex["a"] = 10; hex["b"] = 11; hex["c"] = 12; hex["d"] = 13; hex["e"] = 14; hex["f"] = 15
        hex["A"] = 10; hex["B"] = 11; hex["C"] = 12; hex["D"] = 13; hex["E"] = 14; hex["F"] = 15
        bom = sprintf("%c%c%c", 239, 187, 191)
        buf = ""
    }
    {
        if (NR == 1) {
            if (substr($0, 1, 3) == bom) $0 = substr($0, 4)
            buf = $0
        } else {
            buf = buf "\n" $0
        }
    }
    function skip_ws(b, p, n,    c) {
        while (p <= n) {
            c = substr(b, p, 1)
            if (c == " " || c == "\t" || c == "\r" || c == "\n") p++
            else break
        }
        return p
    }
    function read_str(b, p, n,    c, r, h1, h2, h3, h4, cp) {
        p++
        r = ""
        while (p <= n) {
            c = substr(b, p, 1)
            if (c == "\"") {
                _rs_next = p + 1
                _rs_val = r
                return 1
            }
            if (c == "\\") {
                p++
                if (p > n) return 0
                c = substr(b, p, 1)
                if (c == "\"" || c == "\\" || c == "/") r = r c
                else if (c == "b") r = r "\b"
                else if (c == "f") r = r "\f"
                else if (c == "n") r = r "\n"
                else if (c == "r") r = r "\r"
                else if (c == "t") r = r "\t"
                else if (c == "u" && p + 4 <= n) {
                    h1 = substr(b, p + 1, 1); h2 = substr(b, p + 2, 1)
                    h3 = substr(b, p + 3, 1); h4 = substr(b, p + 4, 1)
                    if ((h1 in hex) && (h2 in hex) && (h3 in hex) && (h4 in hex)) {
                        cp = hex[h1] * 4096 + hex[h2] * 256 + hex[h3] * 16 + hex[h4]
                        if (cp >= 1 && cp < 128) r = r sprintf("%c", cp)
                        else if (cp >= 128 && cp < 2048) r = r sprintf("%c%c", 192 + int(cp / 64), 128 + (cp % 64))
                        else if (cp >= 2048) r = r sprintf("%c%c%c", 224 + int(cp / 4096), 128 + (int(cp / 64) % 64), 128 + (cp % 64))
                        p += 4
                    } else {
                        r = r "\\u"
                    }
                } else {
                    r = r "\\" c
                }
                p++
                continue
            }
            r = r c
            p++
        }
        return 0
    }
    END {
        n = length(buf)
        p = skip_ws(buf, 1, n)
        if (p > n || substr(buf, p, 1) != "{") exit 1
        depth = 0
        expect_key = 0
        while (p <= n) {
            p = skip_ws(buf, p, n)
            if (p > n) break
            c = substr(buf, p, 1)
            if (c == "{") {
                depth++
                if (depth == 1) expect_key = 1
                p++
            } else if (c == "}") {
                depth--
                if (depth <= 0) break
                expect_key = 0
                p++
            } else if (c == "[") {
                depth++
                expect_key = 0
                p++
            } else if (c == "]") {
                depth--
                expect_key = 0
                p++
            } else if (c == ",") {
                if (depth == 1) expect_key = 1
                p++
            } else if (c == "\"") {
                if (!read_str(buf, p, n)) exit 1
                str_val = _rs_val
                p = _rs_next
                if (depth == 1 && expect_key) {
                    p = skip_ws(buf, p, n)
                    if (p <= n && substr(buf, p, 1) == ":") {
                        p++
                        p = skip_ws(buf, p, n)
                        if (p <= n && substr(buf, p, 1) == "\"") {
                            if (!read_str(buf, p, n)) exit 1
                            if (str_val == target) {
                                printf "%s\n", _rs_val
                                exit 0
                            }
                            p = _rs_next
                            expect_key = 0
                        } else {
                            if (str_val == target) {
                                if (substr(buf, p, 4) == "null") {
                                    printf "\n"
                                    exit 0
                                }
                                val = ""
                                while (p <= n) {
                                    vc = substr(buf, p, 1)
                                    if (vc == "," || vc == "}" || vc == "]" || vc == " " || vc == "\t" || vc == "\r" || vc == "\n") break
                                    val = val vc
                                    p++
                                }
                                if (val != "" && substr(val, 1, 1) != "{" && substr(val, 1, 1) != "[") {
                                    printf "%s\n", val
                                    exit 0
                                }
                                exit 1
                            }
                            expect_key = 0
                        }
                    } else {
                        expect_key = 0
                    }
                }
            } else {
                expect_key = 0
                p++
            }
        }
        exit 1
    }' "$_jrf_file" 2>/dev/null
}

json_value() {
    [ -f "$CONFIG" ] || return 0
    _json_read_file_key "$CONFIG" "$1" || true
}

profile_name() {
    ancli_profile_name_zh "$1" 2>/dev/null || echo "未知规格"
}

profile_size() { echo 0; }

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
            unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY npm_config_proxy npm_config_https_proxy NPM_CONFIG_PROXY NPM_CONFIG_HTTPS_PROXY
            ;;
        manual)
            proxy_url=""
            if [ -f "$CONFIG" ]; then
                proxy_url=$(json_value proxy_url)
            fi
            if [ -z "$proxy_url" ] && [ -f "$ROOTFS/root/.ancli-config.json" ]; then
                proxy_url=$(_json_read_file_key "$ROOTFS/root/.ancli-config.json" "proxy_url" || true)
            fi
            if [ -n "$proxy_url" ]; then
                export http_proxy="$proxy_url" https_proxy="$proxy_url" all_proxy="$proxy_url" HTTP_PROXY="$proxy_url" HTTPS_PROXY="$proxy_url" ALL_PROXY="$proxy_url"
                export no_proxy="localhost,127.0.0.1,::1,0.0.0.0" NO_PROXY="localhost,127.0.0.1,::1,0.0.0.0"
            else
                say "[WARN] manual 网络模式未配置 proxy_url，将按当前环境直连" >&2
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

normalize_proxy_url() {
    _val=$(printf '%s' "${1:-}" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')
    if [ -z "$_val" ]; then
        return 0
    fi
    _clean=$(printf '%s' "$_val" | LC_ALL=C tr -d '\001-\037\177')
    if [ "$_clean" != "$_val" ]; then
        printf '%s\n' "代理地址不能包含认证信息或控制字符"
        return 1
    fi
    case "$_val" in
        *[@?#\;\'\"\\]*|*[[:space:]]*)
            printf '%s\n' "代理地址不能包含认证信息或控制字符"
            return 1
            ;;
    esac
    case "$_val" in
        *://*) ;;
        *) _val="http://$_val" ;;
    esac
    case "$_val" in
        http://*) _scheme="http"; _rest="${_val#http://}" ;;
        https://*) _scheme="https"; _rest="${_val#https://}" ;;
        *)
            printf '%s\n' "代理地址格式错误，请输入 IP:端口 或 http://IP:端口"
            return 1
            ;;
    esac
    _rest="${_rest%/}"
    case "$_rest" in
        */*)
            printf '%s\n' "代理地址格式错误，请输入 IP:端口 或 http://IP:端口"
            return 1
            ;;
    esac
    case "$_rest" in
        \[*\]:*)
            _host="${_rest%%\]:*}]"
            _port="${_rest##*\]:}"
            if [ "$_host" = "[]" ]; then
                printf '%s\n' "代理地址格式错误，请输入 IP:端口 或 http://IP:端口"
                return 1
            fi
            ;;
        *:*)
            _before="${_rest%:*}"
            case "$_before" in
                *:*)
                    printf '%s\n' "代理地址格式错误，请输入 IP:端口 或 http://IP:端口"
                    return 1
                    ;;
            esac
            _host="$_before"
            _port="${_rest##*:}"
            ;;
        *)
            printf '%s\n' "代理端口必须在 1 到 65535 之间"
            return 1
            ;;
    esac
    if [ -z "$_host" ]; then
        printf '%s\n' "代理地址格式错误，请输入 IP:端口 或 http://IP:端口"
        return 1
    fi
    case "$_port" in
        ''|*[!0-9]*)
            printf '%s\n' "代理端口必须在 1 到 65535 之间"
            return 1
            ;;
    esac
    if [ "${#_port}" -gt 5 ] || [ "$_port" -lt 1 ] || [ "$_port" -gt 65535 ]; then
        printf '%s\n' "代理端口必须在 1 到 65535 之间"
        return 1
    fi
    printf '%s://%s:%s\n' "$_scheme" "$_host" "$_port"
    return 0
}

validate_default_cwd() {
    _cwd=$(printf '%s' "${1:-}" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')
    if [ -z "$_cwd" ]; then
        return 0
    fi
    _cwd_clean=$(printf '%s' "$_cwd" | LC_ALL=C tr -d '\001-\037\177')
    if [ "$_cwd_clean" != "$_cwd" ]; then
        printf '%s\n' "默认目录包含非法字符"
        return 1
    fi
    case "$_cwd" in
        /*) ;;
        *)
            printf '%s\n' "默认目录必须是绝对路径（例如 /sdcard 或 /sdcard/projects）"
            return 1
            ;;
    esac
    case "/$_cwd/" in
        */./*|*/../*)
            printf '%s\n' "默认目录不能包含 . 或 .."
            return 1
            ;;
    esac
    case "$_cwd" in
        /dev|/dev/*|/proc|/proc/*|/sys|/sys/*|/sdcard|/sdcard/*|/storage|/storage/*|/mnt|/mnt/*|/data|/data/*|/apex|/apex/*|/system|/system/*|/linkerconfig|/linkerconfig/*)
            printf '%s\n' "$_cwd"
            return 0
            ;;
    esac
    if [ -d "$ROOTFS$_cwd" ]; then
        printf '%s\n' "$_cwd"
        return 0
    fi
    printf '%s\n' "容器里看不到这个目录：$_cwd（先创建它，或检查拼写）"
    return 1
}

_strip_proxy_hostport() {
    _raw=$(printf '%s' "${1:-}" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')
    [ -n "$_raw" ] || return 1
    case "$_raw" in
        http://*) _raw="${_raw#http://}" ;;
        https://*) _raw="${_raw#https://}" ;;
        *://*) _raw="${_raw#*://}" ;;
    esac
    _raw="${_raw%%/*}"
    case "$_raw" in
        \[*\]:*)
            _hp_h="${_raw%%\]:*}]"
            _hp_p="${_raw##*\]:}"
            ;;
        *:*)
            _hp_h="${_raw%:*}"
            _hp_p="${_raw##*:}"
            ;;
        *) return 1 ;;
    esac
    case "$_hp_p" in ''|*[!0-9]*) return 1 ;; esac
    if [ -z "$_hp_h" ] || [ "${#_hp_p}" -gt 5 ] || [ "$_hp_p" -lt 1 ] || [ "$_hp_p" -gt 65535 ]; then
        return 1
    fi
    printf '%s:%s\n' "$_hp_h" "$_hp_p"
    return 0
}

get_detected_proxy() {
    _gp_mode="${1:-system}"
    _gp_url="${2:-}"
    case "$_gp_mode" in
        direct)
            return 0
            ;;
        manual)
            _strip_proxy_hostport "$_gp_url" 2>/dev/null || true
            return 0
            ;;
    esac
    _gp_now=$(date +%s 2>/dev/null || echo 0)
    case "$_gp_now" in ''|*[!0-9]*) _gp_now=0 ;; esac
    for _gp_cache in "$ANCLI_DIR/.proxy_cache" "$ANCLI_DIR/.proxy_cache_last"; do
        if [ -f "$_gp_cache" ]; then
            read -r _gp_h _gp_p _gp_ts < "$_gp_cache" 2>/dev/null || continue
            case "$_gp_p" in ''|*[!0-9]*) continue ;; esac
            if [ -n "$_gp_h" ] && [ "${#_gp_p}" -le 5 ] && [ "$_gp_p" -ge 1 ] && [ "$_gp_p" -le 65535 ]; then
                case "$_gp_ts" in ''|*[!0-9]*) _gp_ts=0 ;; esac
                if [ "$_gp_now" -gt 0 ] && [ "$_gp_ts" -gt 0 ] && [ $((_gp_now - _gp_ts)) -gt 3600 ]; then
                    continue
                fi
                printf '%s:%s\n' "$_gp_h" "$_gp_p"
                return 0
            fi
        fi
    done
    for _gp_env in "${http_proxy:-}" "${all_proxy:-}" "${HTTP_PROXY:-}" "${ALL_PROXY:-}"; do
        if [ -n "$_gp_env" ]; then
            if _gp_res=$(_strip_proxy_hostport "$_gp_env" 2>/dev/null); then
                printf '%s\n' "$_gp_res"
                return 0
            fi
        fi
    done
    return 0
}

read_cfg_key() {
    _rck_key="$1"
    _rck_fallback="${2:-}"
    _rck_file="$ROOTFS/root/.ancli-config.json"
    if [ -f "$_rck_file" ] && _rck_val=$(_json_read_file_key "$_rck_file" "$_rck_key"); then
        printf '%s\n' "$_rck_val"
        return 0
    fi
    if [ -f "$CONFIG" ] && _rck_val=$(_json_read_file_key "$CONFIG" "$_rck_key"); then
        printf '%s\n' "$_rck_val"
        return 0
    fi
    printf '%s\n' "$_rck_fallback"
}

extract_profiles_json() {
    _ep_file=""
    for _ep_cand in "$PROFILE_FILE" "$ANCLI_DIR/bin/container-profiles.json" "$MODULE_DIR/ancli/container-profiles.json" "$MODULE_DIR/container-profiles.json"; do
        if [ -f "$_ep_cand" ]; then
            _ep_file="$_ep_cand"
            break
        fi
    done
    if [ -n "$_ep_file" ]; then
        _ep_out=$(LC_ALL=C awk '
        BEGIN { started=0; depth=0; in_str=0; esc=0; out="" }
        {
            line = $0
            if (!started) {
                if (match(line, /"profiles"[[:space:]]*:[[:space:]]*\{/)) {
                    line = substr(line, RSTART + RLENGTH - 1)
                    started = 1
                } else {
                    next
                }
            }
            n = length(line)
            for (i = 1; i <= n; i++) {
                c = substr(line, i, 1)
                out = out c
                if (in_str) {
                    if (esc) { esc = 0 }
                    else if (c == "\\") { esc = 1 }
                    else if (c == "\"") { in_str = 0 }
                } else {
                    if (c == "\"") { in_str = 1 }
                    else if (c == "{") { depth++ }
                    else if (c == "}") {
                        depth--
                        if (depth == 0) {
                            print out
                            exit 0
                        }
                    }
                }
            }
            out = out " "
        }' "$_ep_file" 2>/dev/null | tr '\n' ' ' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')
        case "$_ep_out" in
            \{*\})
                printf '%s\n' "$_ep_out"
                return 0
                ;;
        esac
    fi
    if command -v ancli_profile_packages >/dev/null 2>&1; then
        _ep_json="{"
        _ep_first=1
        for _ep_id in minimal standard developer full; do
            _ep_name=$(ancli_profile_name_zh "$_ep_id" 2>/dev/null || echo "$_ep_id")
            _ep_name_en=$(ancli_profile_name_en "$_ep_id" 2>/dev/null || echo "$_ep_id")
            _ep_desc=$(ancli_profile_description_zh "$_ep_id" 2>/dev/null || echo "")
            _ep_desc_en=$(ancli_profile_description_en "$_ep_id" 2>/dev/null || echo "")
            _ep_pkgs=$(ancli_profile_packages "$_ep_id" 2>/dev/null || echo "")
            _ep_pkg_json="["
            _ep_pf=1
            for _ep_p in $_ep_pkgs; do
                [ "$_ep_pf" -eq 1 ] || _ep_pkg_json="$_ep_pkg_json,"
                _ep_pf=0
                _ep_pkg_json="${_ep_pkg_json}\"$(json_escape "$_ep_p")\""
            done
            _ep_pkg_json="${_ep_pkg_json}]"
            [ "$_ep_first" -eq 1 ] || _ep_json="$_ep_json,"
            _ep_first=0
            _ep_json="${_ep_json}\"$_ep_id\":{\"name\":\"$(json_escape "$_ep_name")\",\"name_en\":\"$(json_escape "$_ep_name_en")\",\"description\":\"$(json_escape "$_ep_desc")\",\"description_en\":\"$(json_escape "$_ep_desc_en")\",\"estimated_size_mb\":0,\"packages\":$_ep_pkg_json}"
        done
        _ep_json="${_ep_json}}"
        printf '%s\n' "$_ep_json"
        return 0
    fi
    echo "{}"
}

write_config() {
    profile="${1:-$(json_value profile)}"
    [ -n "$profile" ] || profile="standard"
    install_mode="${2:-$(json_value install_mode)}"
    [ -n "$install_mode" ] || install_mode="deferred"
    network_mode="${3:-$(json_value network_mode)}"
    [ -n "$network_mode" ] || network_mode="system"
    proxy_url="${4-$(json_value proxy_url)}"
    lang="${5:-$(json_value lang)}"
    [ -n "$lang" ] || lang="zh"
    default_cwd="${6-$(json_value default_cwd)}"
    agent_mem="${7:-$(json_value agent_memory)}"
    [ -n "$agent_mem" ] || agent_mem="unconfigured"
    _write_mirror="${8:-$(json_value mirror)}"
    case "$_write_mirror" in tuna|ustc|official) ;; *) _write_mirror=tuna ;; esac
    _write_channel="${9:-$(json_value update_channel)}"
    case "$_write_channel" in stable|beta) ;; *) _write_channel="stable" ;; esac

    mkdir -p "$ANCLI_DIR" 2>/dev/null || true
    tmp="$CONFIG.tmp.$$"
    cat > "$tmp" <<EOF
{
  "profile": "$(json_escape "$profile")",
  "install_mode": "$(json_escape "$install_mode")",
  "network_mode": "$(json_escape "$network_mode")",
  "proxy_url": "$(json_escape "$proxy_url")",
  "lang": "$(json_escape "$lang")",
  "default_cwd": "$(json_escape "$default_cwd")",
  "agent_memory": "$(json_escape "$agent_mem")",
  "update_channel": "$(json_escape "$_write_channel")",
  "mirror": "$(json_escape "$_write_mirror")",
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
    version_code=63
    if [ -f "$MODULE_DIR/module.prop" ]; then
        version=$(sed -n 's/^version=//p' "$MODULE_DIR/module.prop" 2>/dev/null | head -n 1 | tr -d '\r')
        [ -n "$version" ] || version="unknown"
        _vc=$(sed -n 's/^versionCode=//p' "$MODULE_DIR/module.prop" 2>/dev/null | head -n 1 | tr -d '\r')
        case "$_vc" in ''|*[!0-9]*) ;; *) version_code="$_vc" ;; esac
    fi

    android_data=false
    grep -qs ' /mnt/ancli-data ' /proc/mounts 2>/dev/null && android_data=true

    host_bash_compat=false
    if [ -f "$MODULE_DIR/system/bin/bash" ] || [ -f /data/adb/ksu/bin/bash ] || [ -f /data/adb/ap/bin/bash ]; then
        host_bash_compat=true
    fi

    # 容器未安装时，agent_memory 保持 not_ready，避免 WebUI 抢占弹出配置弹窗
    agent_mem="not_ready"
    if [ "$ready" = true ]; then
        agent_mem="$(read_cfg_key agent_memory "unconfigured")"
        [ -n "$agent_mem" ] || agent_mem="unconfigured"
    fi

    _global_proxy_mode="$(read_cfg_key proxy_mode "$network")"
    [ -n "$_global_proxy_mode" ] || _global_proxy_mode="$network"
    _global_proxy_url="$(read_cfg_key proxy_url "$proxy_url")"
    detected_proxy="$(get_detected_proxy "$_global_proxy_mode" "$_global_proxy_url")"
    proxy_connected=false
    [ -n "$detected_proxy" ] && proxy_connected=true

    _pname="$(profile_name "$profile")"
    _pdesc="$(profile_description "$profile")"
    _ppkgs="$(profile_packages "$profile")"
    _psize="$(profile_size "$profile")"
    _cfg_obj=$(printf '{"profile":"%s","install_mode":"%s","network_mode":"%s","proxy_url":"%s","mirror":"%s","profile_name":"%s","description":"%s","estimated_size_mb":%s,"host_arch":"%s","rootfs_arch":"%s","rootfs_ready":%s,"proot_deployed":%s}' \
        "$(json_escape "$profile")" "$(json_escape "$mode")" "$(json_escape "$network")" \
        "$(json_escape "$proxy_url")" "$(json_escape "$_status_mirror")" "$(json_escape "$_pname")" \
        "$(json_escape "$_pdesc")" "$_psize" "$(json_escape "$host_arch")" "$(json_escape "$rootfs_arch")" \
        "$ready" "$proot_deployed")
    _profiles_json="$(extract_profiles_json)"

    printf '{"status":"ok","ancli_version":"%s","version_code":%s,"execution_backend":"proot-ubuntu","rootfs_ready":%s,"proot_deployed":%s,"host_arch":"%s","rootfs_arch":"%s","installed_count":0,"android_data":%s,"host_bash_compat":%s,"proxy_mode":"%s","proxy_url":"%s","detected_proxy":"%s","proxy_connected":%s,"agent_memory":"%s","profile":"%s","profile_name":"%s","profile_description":"%s","profile_packages":"%s","estimated_size_mb":%s,"install_mode":"%s","network_mode":"%s","mirror":"%s","config_path":"%s","config":%s,"profiles":%s,"container":{"config":%s,"profiles":%s}}\n' \
        "$(json_escape "$version")" "$version_code" "$ready" "$proot_deployed" "$(json_escape "$host_arch")" "$(json_escape "$rootfs_arch")" \
        "$android_data" "$host_bash_compat" "$(json_escape "$_global_proxy_mode")" "$(json_escape "$_global_proxy_url")" \
        "$(json_escape "$detected_proxy")" "$proxy_connected" "$(json_escape "$agent_mem")" \
        "$(json_escape "$profile")" "$(json_escape "$_pname")" "$(json_escape "$_pdesc")" "$(json_escape "$_ppkgs")" \
        "$_psize" "$(json_escape "$mode")" "$(json_escape "$network")" "$(json_escape "$_status_mirror")" \
        "$(json_escape "$CONFIG")" "$_cfg_obj" "$_profiles_json" "$_cfg_obj" "$_profiles_json"
}

list_json() {
    version="unknown"
    if [ -f "$MODULE_DIR/module.prop" ]; then
        version=$(sed -n 's/^version=//p' "$MODULE_DIR/module.prop" 2>/dev/null | head -n 1 | tr -d '\r')
        [ -n "$version" ] || version="unknown"
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
        version=$(sed -n 's/^version=//p' "$MODULE_DIR/module.prop" 2>/dev/null | head -n 1 | tr -d '\r')
        [ -n "$version" ] || version="unknown"
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
    _cfg_json_mode=0
    _cfg_has_set=0
    for _ca in "$@"; do
        case "$_ca" in
            --json) _cfg_json_mode=1 ;;
            --set|--set=*|*=*) _cfg_has_set=1 ;;
        esac
    done

    profile="$(json_value profile)"; [ -n "$profile" ] || profile="standard"
    network="$(read_cfg_key proxy_mode "$(json_value network_mode)")"; [ -n "$network" ] || network="system"
    proxy_url="$(read_cfg_key proxy_url "$(json_value proxy_url)")"
    lang="$(read_cfg_key lang "$(json_value lang)")"; [ -n "$lang" ] || lang="zh"
    default_cwd="$(read_cfg_key default_cwd "$(json_value default_cwd)")"
    agent_mem="$(read_cfg_key agent_memory "$(json_value agent_memory)")"; [ -n "$agent_mem" ] || agent_mem="unconfigured"
    update_channel="$(read_cfg_key update_channel "$(json_value update_channel)")"
    case "$update_channel" in stable|beta) ;; *) update_channel="stable" ;; esac

    if [ "$_cfg_has_set" -eq 0 ] && [ "$_cfg_json_mode" -eq 0 ]; then
        detected_proxy="$(get_detected_proxy "$network" "$proxy_url")"
        effective_cwd="${default_cwd:-/root/.ancli-work}"
        echo "默认工作目录: $effective_cwd"
        echo "lang: $lang"
        echo "proxy_mode: $network"
        echo "proxy_url: $proxy_url"
        echo "detected_proxy: $detected_proxy"
        echo "agent_memory: $agent_mem"
        echo "update_channel: $update_channel"
        echo "container_profile: $profile"
        return 0
    fi

    _cfg_errors=""
    if [ "$_cfg_has_set" -eq 1 ]; then
        for arg in "$@"; do
            case "$arg" in
                --json|--set) continue ;;
                --set=*) arg="${arg#*=}" ;;
            esac
            case "$arg" in
                *=*)
                    _k="${arg%%=*}"
                    _v="$(url_decode "${arg#*=}")"
                    ;;
                *)
                    _cfg_errors="${_cfg_errors}未知参数：$arg
"
                    continue
                    ;;
            esac
            case "$_k" in
                lang)
                    _v_low=$(printf '%s' "$_v" | tr 'A-Z' 'a-z')
                    case "$_v_low" in
                        zh|en) lang="$_v_low" ;;
                        *) _cfg_errors="${_cfg_errors}不支持的语言：$_v（可选：zh, en）
" ;;
                    esac
                    ;;
                proxy_mode)
                    _v_low=$(printf '%s' "$_v" | tr 'A-Z' 'a-z')
                    case "$_v_low" in
                        system|manual|direct)
                            network="$_v_low"
                            if [ "$network" = "direct" ]; then
                                rm -f "$ANCLI_DIR/.proxy_cache" "$ANCLI_DIR/.proxy_cache_last" 2>/dev/null || true
                            fi
                            ;;
                        *) _cfg_errors="${_cfg_errors}代理模式必须是 system、manual 或 direct
" ;;
                    esac
                    ;;
                proxy_url)
                    if _norm_p=$(normalize_proxy_url "$_v"); then
                        proxy_url="$_norm_p"
                    else
                        _cfg_errors="${_cfg_errors}${_norm_p}
"
                    fi
                    ;;
                default_cwd)
                    if _valid_cwd=$(validate_default_cwd "$_v"); then
                        default_cwd="$_valid_cwd"
                    else
                        _cfg_errors="${_cfg_errors}${_valid_cwd}
"
                    fi
                    ;;
                agent_memory)
                    _v_low=$(printf '%s' "$_v" | tr 'A-Z' 'a-z')
                    case "$_v_low" in
                        1|true|enabled|yes|on|add) agent_mem="enabled" ;;
                        0|false|disabled|no|off|none|skip) agent_mem="disabled" ;;
                        *) _cfg_errors="${_cfg_errors}agent_memory 必须是 enabled (添加) 或 disabled (不添加)
" ;;
                    esac
                    ;;
                update_channel)
                    _v_low=$(printf '%s' "$_v" | tr 'A-Z' 'a-z')
                    case "$_v_low" in
                        stable|beta) update_channel="$_v_low" ;;
                        *) _cfg_errors="${_cfg_errors}更新通道必须是 stable (正式主线) 或 beta (Beta 预览)
" ;;
                    esac
                    ;;
                container_profile)
                    if valid_profile "$_v"; then
                        profile="$_v"
                    else
                        _cfg_errors="${_cfg_errors}无效容器规格：$_v
"
                    fi
                    ;;
                *)
                    _cfg_errors="${_cfg_errors}未知配置项：$_k（支持 default_cwd, lang, proxy_mode, proxy_url, agent_memory, update_channel, container_profile）
"
                    ;;
            esac
        done

        write_config "$profile" "$(json_value install_mode)" "$network" "$proxy_url" "$lang" "$default_cwd" "$agent_mem" "$(json_value mirror)" "$update_channel" || return 1
        # 若 rootfs 已存在，则同步写回容器 /root/.ancli-config.json
        if [ -d "$ROOTFS/root" ]; then
            cat > "$ROOTFS/root/.ancli-config.json.tmp" <<EOF
{
  "lang": "$(json_escape "$lang")",
  "proxy_mode": "$(json_escape "$network")",
  "proxy_url": "$(json_escape "$proxy_url")",
  "default_cwd": "$(json_escape "$default_cwd")",
  "agent_memory": "$(json_escape "$agent_mem")",
  "update_channel": "$(json_escape "$update_channel")"
}
EOF
            mv -f "$ROOTFS/root/.ancli-config.json.tmp" "$ROOTFS/root/.ancli-config.json" 2>/dev/null || true
            chmod 644 "$ROOTFS/root/.ancli-config.json" 2>/dev/null || true
        fi
    fi

    effective_cwd="${default_cwd:-/root/.ancli-work}"
    apply_network_mode "$network"
    detected_proxy="$(get_detected_proxy "$network" "$proxy_url")"
    proxy_connected=false
    [ -n "$detected_proxy" ] && proxy_connected=true

    if [ "$_cfg_json_mode" -eq 1 ]; then
        printf '{"default_cwd":"%s","effective_cwd":"%s","lang":"%s","proxy_mode":"%s","proxy_url":"%s","agent_memory":"%s","update_channel":"%s","container_profile":"%s","detected_proxy":"%s","proxy_connected":%s,"supported_keys":["default_cwd","lang","proxy_mode","proxy_url","agent_memory","update_channel","container_profile"]}\n' \
            "$(json_escape "$default_cwd")" "$(json_escape "$effective_cwd")" "$(json_escape "$lang")" \
            "$(json_escape "$network")" "$(json_escape "$proxy_url")" "$(json_escape "$agent_mem")" \
            "$(json_escape "$update_channel")" "$(json_escape "$profile")" "$(json_escape "$detected_proxy")" \
            "$proxy_connected"
        if [ -n "$_cfg_errors" ]; then
            printf '%s' "$_cfg_errors" | while IFS= read -r _err_line; do
                [ -n "$_err_line" ] && printf '[X] %s\n' "$_err_line" >&2
            done
            return 1
        fi
        return 0
    fi

    if [ -n "$_cfg_errors" ]; then
        printf '%s' "$_cfg_errors" | while IFS= read -r _err_line; do
            [ -n "$_err_line" ] && printf '[X] %s\n' "$_err_line" >&2
        done
        return 1
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
    ( write_config "$profile" "$(json_value install_mode)" "$network" "$proxy_url" "$(json_value lang)" "$(json_value default_cwd)" "$(json_value agent_memory)" "$_selected_mirror" "$(json_value update_channel)" ) || return 1
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
        _dns_net=$(/system/bin/dumpsys connectivity 2>/dev/null | grep -o 'DnsAddresses: \[[^]]*\]' | head -n 1 \
            | tr ',' '\n' | grep -oE '[0-9]{1,3}(\.[0-9]{1,3}){3}' | head -n 3)
        _dns_seen=""
        for dns in "$(getprop net.dns1 2>/dev/null)" "$(getprop net.dns2 2>/dev/null)" $_dns_net 223.5.5.5 119.29.29.29 1.1.1.1 8.8.8.8; do
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

    case "$_selected_mirror" in
        ustc) _pip_url="https://pypi.mirrors.ustc.edu.cn/simple" ;;
        official) _pip_url="https://pypi.org/simple" ;;
        *) _pip_url="https://pypi.tuna.tsinghua.edu.cn/simple" ;;
    esac

    packages="$(profile_packages "$profile")"
    _ancli_dir_clean="${ANCLI_DIR%/}"
    _rootfs_clean="${ROOTFS%/}"
    [ -n "$_ancli_dir_clean" ] && [ -n "$_rootfs_clean" ] || { say "[X] 无效的容器目录配置"; return 1; }
    mkdir -p "$_rootfs_clean/root" || return 1
    setup="$_rootfs_clean/root/ancli-container-setup.sh"
    proxy_exports=""
    printf 'profile=%s\nphase=apt\n' "$profile" > "$ROOTFS/.ancli-install-checkpoint"
    if [ -n "${http_proxy:-}" ]; then
        proxy_exports="export http_proxy='${http_proxy:-}' https_proxy='${https_proxy:-${http_proxy:-}}' HTTP_PROXY='${HTTP_PROXY:-${http_proxy:-}}' HTTPS_PROXY='${HTTPS_PROXY:-${https_proxy:-${http_proxy:-}}}'"
    fi
    if ! cat > "$setup" <<EOF; then
#!/bin/bash
set -eu
export DEBIAN_FRONTEND=noninteractive
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export TMPDIR=/tmp
$proxy_exports
mkdir -p /usr/sbin
printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d
chmod 755 /usr/sbin/policy-rc.d
mkdir -p /etc/apt/apt.conf.d
if [ -n "\${http_proxy:-}" ]; then
    printf 'Acquire::http::Proxy "%s";\n' "\$http_proxy" > /etc/apt/apt.conf.d/99ancli-proxy
    printf 'Acquire::https::Proxy "%s";\n' "\${https_proxy:-\$http_proxy}" >> /etc/apt/apt.conf.d/99ancli-proxy
fi
printf 'APT::Sandbox::User "root";\n' > /etc/apt/apt.conf.d/01sandbox
printf 'DPkg::Use-Pty "0";\nDPkg::Options {\n    "--force-confdef";\n    "--force-confold";\n};\n' > /etc/apt/apt.conf.d/02ancli-dpkg
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
printf 'profile=%s\\nphase=apt-update\\n' '$profile' > /.ancli-install-checkpoint
apt-get update -y
printf 'profile=%s\\nphase=apt-install\\n' '$profile' > /.ancli-install-checkpoint
if ! apt-get install -y --no-install-recommends $packages; then
    if command -v dpkg >/dev/null 2>&1 && dpkg --audit 2>/dev/null | grep -qE 'systemd|udev'; then
        ancli_harden_systemd_dpkg
        apt-get install -y --no-install-recommends $packages
    else
        exit 1
    fi
fi
ancli_harden_systemd_dpkg
apt-get clean
rm -f /etc/apt/apt.conf.d/99ancli-proxy
printf 'profile=%s\\nphase=node-setup\\n' '$profile' > /.ancli-install-checkpoint
/bin/sh /data/local/tmp/ancli/node-setup.sh
rm -f /usr/lib/python3*/EXTERNALLY-MANAGED
if [ ! -f /etc/pip.conf ] || grep -q '# AnCLI managed' /etc/pip.conf 2>/dev/null || [ ! -s /etc/pip.conf ]; then
    printf '# AnCLI managed\n[global]\nindex-url = %s\n' '$_pip_url' > /etc/pip.conf
fi
mkdir -p /usr/local/bin /usr/local/sbin /usr/local/lib/node_modules /usr/local/share /root/.npm/_logs /root/.ancli-work
chmod 755 /usr/local/bin /usr/local/sbin /usr/local/lib /usr/local/lib/node_modules
EOF
        echo '[X] Failed to write container setup script' >&2
        return 1
    fi
    chmod 755 "$setup" || return 1
    _proot_binds=""
    _proot_seen=""
    for _pb in /dev /proc /sys "$_ancli_dir_clean" /data/adb; do
        _pb="${_pb%/}"
        [ -n "$_pb" ] || continue
        [ -e "$_pb" ] || [ -d "$_pb" ] || continue
        case " $_proot_seen " in *" $_pb "*) continue ;; esac
        _proot_seen="$_proot_seen $_pb"
        _proot_binds="$_proot_binds -b $_pb"
    done
    export PROOT_NO_SECCOMP=1
    proot="$BIN_DIR/proot -r $_rootfs_clean$_proot_binds -w /root"
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
    update_channel="$(json_value update_channel)"
    case "$update_channel" in stable|beta) ;; *) update_channel="stable" ;; esac
    cat > "$ROOTFS/root/.ancli-config.json" <<EOF
{
  "lang": "$(json_escape "$lang")",
  "proxy_mode": "$(json_escape "$network")",
  "proxy_url": "$(json_escape "$proxy_url")",
  "default_cwd": "$(json_escape "$default_cwd")",
  "agent_memory": "$(json_escape "$agent_mem")",
  "update_channel": "$(json_escape "$update_channel")"
}
EOF
    chmod 644 "$ROOTFS/root/.ancli-config.json" 2>/dev/null || true

    if [ -n "${ANCLI_ROOTFS_STAGE:-}" ]; then
        ancli_commit_rootfs || { say "[X] 无法提交新容器；已有数据未被覆盖"; return 1; }
    fi
    write_config "$profile" "installed" "$network" "$proxy_url" "$lang" "$default_cwd" "$agent_mem" "$_selected_mirror" "$update_channel"
    say "[OK] 容器安装完成。可以运行 ancli repair 初始化 Agent 入口。"
    return 0
)

import_container_bundle() (
    _bundle=""
    for arg in "$@"; do
        case "$arg" in
            --bundle=*) _bundle=$(url_decode "${arg#*=}") ;;
            *) say "[X] 未知离线导入参数：$arg"; return 2 ;;
        esac
    done
    case "$_bundle" in
        /sdcard/*|/storage/emulated/0/*|/data/local/tmp/*) ;;
        *) say "[X] 离线包路径必须位于共享存储或 /data/local/tmp"; return 2 ;;
    esac
    [ -f "$_bundle" ] || { say "[X] 找不到离线容器包：$_bundle"; return 1; }
    _host_arch=$(detect_host_arch)
    [ "$_host_arch" = arm64 ] || { say "[X] 当前仅支持 arm64 设备导入此容器包"; return 1; }
    command -v unzip >/dev/null 2>&1 || { say "[X] Android 系统缺少 unzip，无法读取离线包"; return 1; }
    command -v sha256sum >/dev/null 2>&1 || { say "[X] Android 系统缺少 sha256sum，无法验证离线包"; return 1; }
    [ -f "$ANCLI_DIR/install-transaction.sh" ] && [ -f "$ANCLI_DIR/container-mirror.sh" ] || { say "[X] 容器安装辅助文件缺失，请更新模块"; return 1; }
    . "$ANCLI_DIR/install-transaction.sh"
    . "$ANCLI_DIR/container-mirror.sh"

    _import_tmp="$ANCLI_DIR/.offline-import-$$"
    _import_manifest="$_import_tmp/manifest.json"
    _import_tar="$ANCLI_DIR/.offline-rootfs-$$.tar.gz"
    [ ! -e "$_import_tmp" ] && [ ! -e "$_import_tar" ] || { say "[X] 离线导入暂存路径已存在"; return 1; }
    mkdir -m 700 "$_import_tmp" || return 1
    trap 'ancli_rollback_rootfs; rm -rf "$_import_tmp"; rm -f "$_import_tar"' EXIT

    unzip -p "$_bundle" manifest.json > "$_import_manifest" || { say "[X] 无法读取包内清单"; return 1; }
    grep -q '"format"[[:space:]]*:[[:space:]]*"ancli-container-import"' "$_import_manifest" || { say "[X] 不是 AnCLI 离线容器包"; return 1; }
    grep -q '"schema_version"[[:space:]]*:[[:space:]]*1' "$_import_manifest" || { say "[X] 不支持此离线包版本"; return 1; }
    grep -q '"architecture"[[:space:]]*:[[:space:]]*"arm64"' "$_import_manifest" || { say "[X] 离线包架构不是 arm64"; return 1; }
    _profile=$(sed -n 's/.*"profile"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$_import_manifest" | head -n 1)
    valid_profile "$_profile" || { say "[X] 离线包容器规格无效"; return 1; }
    _archive_name=$(sed -n 's/.*"rootfs_archive"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$_import_manifest" | head -n 1)
    case "$_archive_name" in rootfs-arm64.tar.gz|rootfs.tar.gz) ;; *) say "[X] 离线包 rootfs 文件名无效"; return 1 ;; esac
    _agent_memory=$(sed -n 's/.*"agent_memory"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$_import_manifest" | head -n 1)
    case "$_agent_memory" in enabled|disabled|unconfigured) ;; *) say "[X] 离线包记忆状态无效"; return 1 ;; esac
    _import_sha=$(sed -n 's/.*"rootfs_sha256"[[:space:]]*:[[:space:]]*"\([0-9a-f]*\)".*/\1/p' "$_import_manifest" | head -n 1)
    case "$_import_sha" in *[!0-9a-f]*|'') say "[X] 离线包 SHA-256 清单无效"; return 1 ;; esac
    [ "${#_import_sha}" -eq 64 ] || { say "[X] 离线包 SHA-256 长度无效"; return 1; }

    say ">> 正在读取并校验 Ubuntu 24.04 arm64 容器包..."
    unzip -p "$_bundle" "$_archive_name" > "$_import_tar" || { say "[X] 无法读取 rootfs 数据"; return 1; }
    [ -s "$_import_tar" ] || { say "[X] 离线包中的 rootfs 为空"; return 1; }
    _actual_sha=$(sha256sum "$_import_tar" | awk '{print $1}')
    [ "$_actual_sha" = "$_import_sha" ] || { say "[X] rootfs SHA-256 校验失败，文件可能损坏"; return 1; }
    tar -tzf "$_import_tar" > "$_import_tmp/entries" || { say "[X] rootfs 压缩包无法读取"; return 1; }
    if awk '{ path=$0; marker=index(path," -> "); if (marker) path=substr(path,1,marker-1); if (substr(path,1,1)=="/") bad=1; n=split(path,p,"/"); for (i=1;i<=n;i++) if (p[i]=="..") bad=1 } END { exit bad ? 0 : 1 }' "$_import_tmp/entries"; then
        say "[X] rootfs 包含越界路径，拒绝解压"
        return 1
    fi
    _source_version=$(sed -n 's/.*"source_module_version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$_import_manifest" | head -n 1)
    say ">> 离线容器：Ubuntu 24.04 arm64，规格 $_profile，Agent 全局记忆状态 $_agent_memory。"

    ancli_begin_rootfs || { say ">> 现有容器或未完成检查点已保留；请先处理后再导入。"; return 1; }
    tar -xzf "$_import_tar" -C "$ROOTFS" || { say "[X] rootfs 解压失败"; return 1; }
    [ -x "$ROOTFS/usr/bin/bash" ] || [ -x "$ROOTFS/bin/bash" ] || { say "[X] 离线 rootfs 缺少 bash"; return 1; }
    [ "$(get_rootfs_arch "$ROOTFS")" = arm64 ] || { say "[X] 解压后的 rootfs 架构核对失败"; return 1; }
    grep -q '^ID=ubuntu$' "$ROOTFS/etc/os-release" 2>/dev/null || { say "[X] rootfs 不是 Ubuntu 系统"; return 1; }
    grep -q '^VERSION_ID="24\.04"$' "$ROOTFS/etc/os-release" 2>/dev/null || { say "[X] 离线 rootfs 不是 Ubuntu 24.04"; return 1; }

    network=$(json_value network_mode); [ -n "$network" ] || network=system
    proxy_url=$(json_value proxy_url)
    _selected_mirror=$(json_value mirror); [ -n "$_selected_mirror" ] || _selected_mirror=tuna
    ancli_mirror_valid "$_selected_mirror" || _selected_mirror=tuna
    _apt_url=$(ancli_mirror_apt_url "$_selected_mirror" arm64) || return 1
    ancli_mirror_configure_apt "$ROOTFS" "$_apt_url" || { say "[X] 无法设置导入后的 Ubuntu 软件源"; return 1; }
    rm -f "$ROOTFS/etc/resolv.conf"
    : > "$ROOTFS/etc/resolv.conf"
    _dns_net=$(/system/bin/dumpsys connectivity 2>/dev/null | grep -o 'DnsAddresses: \[[^]]*\]' | head -n 1 | tr ',' '\n' | grep -oE '[0-9]{1,3}(\.[0-9]{1,3}){3}' | head -n 3)
    _dns_seen=""
    for dns in "$(getprop net.dns1 2>/dev/null)" "$(getprop net.dns2 2>/dev/null)" $_dns_net 223.5.5.5 119.29.29.29 1.1.1.1 8.8.8.8; do
        case "$dns" in ''|0.*|*[!0-9.]*) continue ;; esac
        case " $_dns_seen " in *" $dns "*) continue ;; esac
        echo "nameserver $dns" >> "$ROOTFS/etc/resolv.conf"
        _dns_seen="$_dns_seen $dns"
        [ "$(wc -l < "$ROOTFS/etc/resolv.conf")" -ge 3 ] && break
    done
    sh "$ANCLI_DIR/apt-key-compat.sh" "$ROOTFS" || { say "[X] APT 密钥环兼容设置失败"; return 1; }
    mkdir -p "$ROOTFS/usr/local/bin" "$ROOTFS/usr/local/sbin" "$ROOTFS/usr/local/lib/node_modules" "$ROOTFS/root/.npm/_logs" || return 1
    cat > "$ROOTFS/usr/local/bin/ancli" <<'EOF'
#!/bin/sh
export ANCLI_IN_CONTAINER=1
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"
exec python3 /data/local/tmp/ancli/bin/ancli-core.py "$@"
EOF
    chmod 755 "$ROOTFS/usr/local/bin/ancli"
    ln -sf /usr/local/bin/ancli "$ROOTFS/usr/bin/ancli" 2>/dev/null || true
    ln -sf /usr/local/bin/ancli "$ROOTFS/bin/ancli" 2>/dev/null || true
    ln -sf /bin/bash "$ROOTFS/usr/local/bin/bash" 2>/dev/null || true

    lang=$(json_value lang); [ -n "$lang" ] || lang=zh
    default_cwd=$(json_value default_cwd)
    agent_mem=$_agent_memory
    [ -n "$agent_mem" ] || agent_mem=unconfigured
    update_channel=$(json_value update_channel)
    case "$update_channel" in stable|beta) ;; *) update_channel=stable ;; esac
    cat > "$ROOTFS/root/.ancli-config.json" <<EOF
{
  "lang": "$(json_escape "$lang")",
  "proxy_mode": "$(json_escape "$network")",
  "proxy_url": "$(json_escape "$proxy_url")",
  "default_cwd": "$(json_escape "$default_cwd")",
  "agent_memory": "$(json_escape "$agent_mem")",
  "update_channel": "$(json_escape "$update_channel")"
}
EOF
    chmod 644 "$ROOTFS/root/.ancli-config.json" 2>/dev/null || true
    ancli_commit_rootfs || { say "[X] 无法提交离线容器；现有数据未被覆盖"; return 1; }
    write_config "$_profile" installed "$network" "$proxy_url" "$lang" "$default_cwd" "$agent_mem" "$_selected_mirror" "$update_channel" || return 1
    rm -rf "$_import_tmp"
    rm -f "$_import_tar"
    trap - EXIT
    say ">> 来源版本：$_source_version"
    say "[OK] Ubuntu 24.04 arm64 容器已离线导入。"
    return 0
)

active_proot_pids() {
    for _proc_comm in /proc/[0-9]*/comm; do
        [ -r "$_proc_comm" ] || continue
        IFS= read -r _proc_name < "$_proc_comm" || continue
        [ "$_proc_name" = proot ] || continue
        _proc_pid=${_proc_comm#/proc/}
        _proc_pid=${_proc_pid%/comm}
        printf '%s ' "$_proc_pid"
    done
}

export_container_bundle() (
    _host_arch=$(detect_host_arch)
    [ "$_host_arch" = arm64 ] || { say "[X] 当前仅支持导出 arm64 容器"; return 1; }
    [ -d "$ROOTFS" ] && [ ! -L "$ROOTFS" ] || { say "[X] 当前没有可导出的 Ubuntu 容器"; return 1; }
    [ -x "$ROOTFS/usr/bin/bash" ] || [ -x "$ROOTFS/bin/bash" ] || { say "[X] 容器尚未安装完成，不能导出"; return 1; }
    [ ! -e "$ROOTFS/.ancli-install-checkpoint" ] || { say "[X] 容器安装尚未完成，请先完成或处理检查点"; return 1; }
    [ -f "$ANCLI_DIR/container-export.py" ] || { say "[X] 导出工具缺失，请更新模块"; return 1; }
    command -v tar >/dev/null 2>&1 || { say "[X] 系统缺少 tar，无法导出"; return 1; }
    _active_pids=$(active_proot_pids)
    if [ -n "$_active_pids" ]; then
        say ">> 正在等待容器内的短时管理命令结束..."
        _wait=0
        while [ "$_wait" -lt 10 ]; do
            sleep 1
            _active_pids=$(active_proot_pids)
            [ -n "$_active_pids" ] || break
            _wait=$((_wait + 1))
        done
    fi
    if [ -n "$_active_pids" ]; then
        say "[X] 容器仍有 PRoot 进程在运行（PID: $_active_pids），请退出 Agent 和终端任务后重试。"
        return 1
    fi
    mkdir -p /sdcard/Download || { say "[X] 无法访问 Download 目录"; return 1; }
    _profile=$(json_value profile); [ -n "$_profile" ] || _profile=standard
    valid_profile "$_profile" || _profile=standard
    _agent_memory=$(json_value agent_memory); [ -n "$_agent_memory" ] || _agent_memory=unconfigured
    case "$_agent_memory" in enabled|disabled|unconfigured) ;; *) _agent_memory=unconfigured ;; esac
    _version=$(sed -n 's/^version=//p' "$MODULE_DIR/module.prop" 2>/dev/null | head -n 1)
    [ -n "$_version" ] || _version=unknown
    _stamp=$(date +%Y%m%d-%H%M%S)
    case "$_profile" in
        full) _profile_label=Full-Development-Environment ;;
        developer) _profile_label=Developer-Environment ;;
        standard) _profile_label=Standard-Agent-Environment ;;
        minimal) _profile_label=Minimal-Agent-Environment ;;
    esac
    _name="AnCLI-Ubuntu-24.04-${_host_arch}-${_profile_label}-${_stamp}.zip"
    _out="/sdcard/Download/$_name"
    _tmp_out="/sdcard/Download/.$_name.tmp"
    _tar="$ANCLI_DIR/.container-export-$$.tar.gz"
    [ ! -e "$_out" ] && [ ! -e "$_tmp_out" ] && [ ! -e "$_tar" ] || { say "[X] 导出暂存文件已存在，请稍后重试"; return 1; }
    trap 'rm -f "$_tar" "$_tmp_out"' EXIT
    say ">> 正在压缩 Ubuntu 容器文件..."
    tar -czf "$_tar" -C "$ROOTFS" . || { say "[X] 容器压缩失败"; return 1; }
    say ">> 正在生成完整开发环境 ZIP 并写入 Download..."
    export PROOT_NO_SECCOMP=1
    "$BIN_DIR/proot" -r "$ROOTFS" -b /dev -b /proc -b /sys -b "$ANCLI_DIR" -b /sdcard -w /root \
        /usr/bin/python3 /data/local/tmp/ancli/container-export.py \
        --rootfs-tar="/data/local/tmp/ancli/${_tar##*/}" --output="/sdcard/Download/.$_name.tmp" \
        --architecture="$_host_arch" --profile="$_profile" --agent-memory="$_agent_memory" --module-version="$_version" || {
        say "[X] ZIP 打包失败；临时文件将清理"; return 1;
    }
    mv "$_tmp_out" "$_out" || { say "[X] 无法将 ZIP 保存到 Download"; return 1; }
    rm -f "$_tar"
    trap - EXIT
    say ">> 已导出：/sdcard/Download/$_name"
    say "[OK] 完整开发环境容器导出完成。ZIP 包含当前 Ubuntu rootfs、已安装软件和容器内配置数据。"
)

remove_current_container() {
    _remove_yes=0
    for arg in "$@"; do
        case "$arg" in
            --yes) _remove_yes=1 ;;
            *) say "[X] 未知删除参数：$arg"; return 2 ;;
        esac
    done
    [ "$_remove_yes" -eq 1 ] || { say "[X] 删除容器需要显式确认：ancli container remove --yes"; return 2; }
    [ "$ANCLI_DIR" = "/data/local/tmp/ancli" ] && [ "$ROOTFS" = "/data/local/tmp/ancli/rootfs" ] || {
        say "[X] 容器目录校验失败，拒绝删除"; return 1;
    }
    [ ! -L "$ANCLI_DIR" ] && [ ! -L "$ROOTFS" ] || { say "[X] 检测到符号链接，拒绝删除容器目录"; return 1; }

    # A live PRoot process may still be reading or writing the container.
    _active_pids=$(active_proot_pids)
    if [ -n "$_active_pids" ]; then
        say "[X] 容器仍有 PRoot 进程在运行。请先退出 Agent 和终端任务，再删除容器。"
        return 1
    fi

    [ -f "$ANCLI_DIR/install-transaction.sh" ] && . "$ANCLI_DIR/install-transaction.sh"
    ancli_cleanup_stale_rootfs 2>/dev/null || { say "[X] 清理未完成安装检查点失败，容器数据未删除"; return 1; }
    if [ -e "$ROOTFS" ]; then
        [ -d "$ROOTFS" ] && [ ! -L "$ROOTFS" ] || { say "[X] rootfs 不是安全目录，拒绝删除"; return 1; }
        say ">> 正在删除 Ubuntu 容器文件与其中的应用、密钥和数据..."
        rm -rf "$ROOTFS" || { say "[X] 删除 Ubuntu 容器失败"; return 1; }
    fi
    profile=$(json_value profile); [ -n "$profile" ] || profile=standard
    network=$(json_value network_mode); [ -n "$network" ] || network=system
    proxy_url=$(json_value proxy_url)
    lang=$(json_value lang); [ -n "$lang" ] || lang=zh
    mirror=$(json_value mirror); [ -n "$mirror" ] || mirror=tuna
    update_channel=$(json_value update_channel)
    write_config "$profile" deferred "$network" "$proxy_url" "$lang" "" unconfigured "$mirror" "$update_channel" || {
        say "[X] 容器已删除，但安装状态配置未能重置"; return 1;
    }
    say "[OK] 当前 Ubuntu 容器和未完成安装检查点已删除。AnCLI 模块及代理、语言、下载站点设置已保留。"
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
        shift
        profile="$(json_value profile)"; [ -n "$profile" ] || profile="standard"
        network="$(json_value network_mode)"; [ -n "$network" ] || network="system"
        proxy_url="$(json_value proxy_url)"
        _set_mirror="$(json_value mirror)"; [ -n "$_set_mirror" ] || _set_mirror=tuna
        _set_proxy_seen=0
        for arg in "$@"; do
            case "$arg" in
                --json|--set) continue ;;
                --set=*) _kv="${arg#*=}" ;;
                --profile=*) profile="$(url_decode "${arg#*=}")"; continue ;;
                --network=*) network="$(url_decode "${arg#*=}")"; continue ;;
                --mirror=*) _set_mirror="$(url_decode "${arg#*=}")"; continue ;;
                --proxy-url=*) proxy_url="$(url_decode "${arg#*=}")"; _set_proxy_seen=1; continue ;;
                *=*) _kv="$arg" ;;
                *) say "[X] 未知参数：$arg" >&2; exit 2 ;;
            esac
            case "$_kv" in
                profile=*|container_profile=*) profile="$(url_decode "${_kv#*=}")" ;;
                network=*|network_mode=*) network="$(url_decode "${_kv#*=}")" ;;
                mirror=*) _set_mirror="$(url_decode "${_kv#*=}")" ;;
                proxy_url=*) proxy_url="$(url_decode "${_kv#*=}")"; _set_proxy_seen=1 ;;
                *) say "[X] 未知参数：$arg" >&2; exit 2 ;;
            esac
        done
        valid_profile "$profile" || { say "[X] profile 必须是 minimal、standard、developer 或 full" >&2; exit 2; }
        case "$network" in system|manual|direct) ;; *) say "[X] network 必须是 system、manual 或 direct" >&2; exit 2 ;; esac
        case "$_set_mirror" in tuna|ustc|official) ;; *) say "[X] 无效下载站点" >&2; exit 2 ;; esac
        if [ "$_set_proxy_seen" -eq 1 ]; then
            if ! _norm_set_proxy=$(normalize_proxy_url "$proxy_url"); then
                say "[X] $_norm_set_proxy" >&2
                exit 2
            fi
            proxy_url="$_norm_set_proxy"
        fi
        write_config "$profile" "$(json_value install_mode)" "$network" "$proxy_url" "$(json_value lang)" "$(json_value default_cwd)" "$(json_value agent_memory)" "$_set_mirror" "$(json_value update_channel)" || exit 1
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
    import)
        shift
        import_container_bundle "$@"
        ;;
    export)
        shift
        [ "$#" -eq 0 ] || { say "[X] 导出命令不接受参数"; exit 2; }
        export_container_bundle
        ;;
    remove)
        shift
        remove_current_container "$@"
        ;;
    *)
        echo "用法：ancli container status [--json] | ancli container set --profile=standard --network=system | ancli container install | ancli container export | ancli container import --bundle=/sdcard/Download/ancli-container.zip | ancli container remove --yes" >&2
        exit 2
        ;;
esac
