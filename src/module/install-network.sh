#!/system/bin/sh
# Sourced by installer scripts. Android VPNs need not expose an HTTP proxy;
# never claim their absence means the device has no network.
ancli_install_network() {
    _ancli_mode="${1:-system}"
    _ancli_manual="${2:-}"
    _ancli_candidate=""
    _ancli_host=""
    _ancli_port=""
    _ancli_proxy=""
    case "$_ancli_mode" in
        direct)
            unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY
            echo ">> 网络：直连模式。"
            return 0 ;;
        manual) _ancli_candidate="$_ancli_manual" ;;
        *)
            _ancli_setting=$(settings get global http_proxy 2>/dev/null | head -n 1 | tr -d '\r')
            case "$_ancli_setting" in ''|null|:0|0.0.0.0:0) ;; *) _ancli_candidate="$_ancli_setting" ;; esac
            if [ -z "$_ancli_candidate" ]; then
                _ancli_info=$(dumpsys connectivity 2>/dev/null | grep -i 'HttpProxy:' | head -n 1)
                _ancli_host=$(printf '%s\n' "$_ancli_info" | sed -n 's/.*HttpProxy:[[:space:]]*\[\([^] ]*\)\].*/\1/p')
                _ancli_port=$(printf '%s\n' "$_ancli_info" | sed -n 's/.*HttpProxy:[[:space:]]*\[[^]]*\][[:space:]]*\([0-9][0-9]*\).*/\1/p')
                [ -n "$_ancli_host" ] && [ -n "$_ancli_port" ] && _ancli_candidate="$_ancli_host:$_ancli_port"
            fi
            [ -n "$_ancli_candidate" ] || _ancli_candidate="${https_proxy:-${http_proxy:-${HTTPS_PROXY:-${HTTP_PROXY:-}}}}"
            ;;
    esac
    if [ -z "$_ancli_candidate" ]; then
        if [ "$_ancli_mode" = manual ]; then
            unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY
            if [ "${ANCLI_SKIP_PROXY_CONNECTIVITY_TEST:-0}" = 1 ] || [ "${ANCLI_ALLOW_EMPTY_MANUAL_PROXY:-0}" = 1 ]; then
                return 0
            fi
            echo ">> 网络：手动代理未配置，无法使用代理。"
            return 1
        fi
        echo ">> 网络：未发现系统 HTTP 代理；使用设备当前网络（VPN 仍可能生效）。"
        return 0
    fi
    _ancli_scheme="http"
    case "$_ancli_candidate" in
        http://*) _ancli_scheme="http"; _ancli_address=${_ancli_candidate#http://} ;;
        https://*) _ancli_scheme="https"; _ancli_address=${_ancli_candidate#https://} ;;
        *) _ancli_address=$_ancli_candidate ;;
    esac
    _ancli_address=${_ancli_address%/}
    # Accept host:port only. Do not evaluate configuration or print credentials.
    case "$_ancli_address" in
        ''|*[!a-zA-Z0-9.:-]*|:*|*:|*:*:*) echo ">> 网络：代理地址不受支持，请在 WebUI 中检查配置。"; return 1 ;;
        *:*) ;;
        *) echo ">> 网络：代理地址无效。"; return 1 ;;
    esac
    _ancli_host=${_ancli_address%:*}; _ancli_port=${_ancli_address##*:}
    case "$_ancli_port" in ''|*[!0-9]*) echo ">> 网络：代理端口无效。"; return 1 ;; esac
    if [ -z "$_ancli_host" ] || [ "${#_ancli_port}" -gt 5 ] || [ "$_ancli_port" -lt 1 ] || [ "$_ancli_port" -gt 65535 ]; then
        echo ">> 网络：代理地址无效。"; return 1
    fi
    _ancli_proxy="${_ancli_scheme}://$_ancli_host:$_ancli_port"
    export http_proxy="$_ancli_proxy" https_proxy="$_ancli_proxy" HTTP_PROXY="$_ancli_proxy" HTTPS_PROXY="$_ancli_proxy"
    unset all_proxy ALL_PROXY
    export no_proxy="localhost,127.0.0.1,::1,0.0.0.0" NO_PROXY="localhost,127.0.0.1,::1,0.0.0.0"
    [ "${ANCLI_NETWORK_VERBOSE:-0}" = 1 ] && echo ">> HTTP 代理：$_ancli_proxy"
    if [ "${ANCLI_SKIP_PROXY_CONNECTIVITY_TEST:-0}" = 1 ]; then
        echo ">> 网络：已识别 HTTP 代理；快速识别模式未测试连通性。"
        return 0
    fi
    echo ">> 网络：已检测 HTTP 代理，正在测试连接..."
    if command -v curl >/dev/null 2>&1; then
        if curl -fsSI --proxy "$_ancli_proxy" --connect-timeout 4 --max-time "${ANCLI_PROXY_TEST_TIMEOUT:-8}" "${ANCLI_PROXY_TEST_URL:-https://mirrors.tuna.tsinghua.edu.cn/}" >/dev/null 2>&1; then
            echo ">> 网络：代理连接测试成功。"
        else
            echo ">> 网络：代理连接测试失败，将继续尝试下载；请检查代理监听和网络。"
        fi
    else
        echo ">> 网络：系统没有 curl，无法预检代理，将直接尝试下载。"
    fi
    return 0
}

# Inspect connected NetworkAgentInfo records, not VPN providers, requests or
# NOT_VPN capabilities. Absence does not prove that app traffic bypasses VPN.
ancli_detect_vpn() {
    _vpn_dump=$(dumpsys connectivity 2>/dev/null) || { echo 'ANCLI_VPN=unknown'; return 0; }
    [ -n "$_vpn_dump" ] || { echo 'ANCLI_VPN=unknown'; return 0; }
    if printf '%s\n' "$_vpn_dump" | grep -E 'NetworkAgentInfo.*(ni\{VPN CONNECTED|Transports: (VPN([|[:space:]]|$)|[A-Z_]+\|VPN([|[:space:]]|$)))' >/dev/null; then
        echo 'ANCLI_VPN=connected'
    elif printf '%s\n' "$_vpn_dump" | grep 'NetworkAgentInfo' >/dev/null; then
        echo 'ANCLI_VPN=not_detected'
    else
        echo 'ANCLI_VPN=unknown'
    fi
}

# A successful HTTPS request verifies reachability for this execution context,
# not whether all traffic uses a VPN or which upstream proxy handled it.
ancli_probe_route() {
    _route_target="${ANCLI_ROUTE_TEST_URL:-https://mirrors.tuna.tsinghua.edu.cn/}"
    if command -v curl >/dev/null 2>&1; then
        if curl -fsSI --noproxy '*' --connect-timeout 3 --max-time 5 "$_route_target" >/dev/null 2>&1; then echo 'ANCLI_ROUTE=reachable'; else echo 'ANCLI_ROUTE=failed'; fi
    elif [ -x "$ANCLI_DIR/bin/proot" ] && [ -f "$ANCLI_DIR/rootfs/usr/bin/curl" ]; then
        if "$ANCLI_DIR/bin/proot" -r "$ANCLI_DIR/rootfs" -b /dev -b /proc -b /sys /usr/bin/env PATH=/usr/bin:/bin /usr/bin/curl -fsSI --noproxy '*' --connect-timeout 3 --max-time 5 "$_route_target" >/dev/null 2>&1; then echo 'ANCLI_ROUTE=reachable'; else echo 'ANCLI_ROUTE=failed'; fi
    else
        echo 'ANCLI_ROUTE=unknown'
    fi
}

# Probe in parallel with a strict deadline; ICMP reachability cannot establish
# proxy identity and blocked echo requests do not establish disconnection.
ancli_probe_ping_sites() (
    if ! command -v ping >/dev/null 2>&1 || ! command -v timeout >/dev/null 2>&1; then
        echo 'ANCLI_PING=unavailable'
        return 0
    fi
    _ping_dir=$(mktemp -d "$ANCLI_DIR/.netprobe.XXXXXX") || { echo 'ANCLI_PING=unavailable'; return 0; }
    trap 'rm -f "$_ping_dir/baidu" "$_ping_dir/qq" "$_ping_dir/google" "$_ping_dir/cloudflare"; rmdir "$_ping_dir" 2>/dev/null || true' EXIT HUP INT TERM
    _ping_site() {
        if timeout 4 ping -c 1 -W 2 "$2" >/dev/null 2>&1; then echo ok > "$_ping_dir/$1"; else echo failed > "$_ping_dir/$1"; fi
    }
    _ping_site baidu www.baidu.com &
    _ping_site qq www.qq.com &
    _ping_site google www.google.com &
    _ping_site cloudflare www.cloudflare.com &
    wait
    for _ping_name in baidu qq google cloudflare; do
        _ping_value=$(cat "$_ping_dir/$_ping_name" 2>/dev/null || echo unknown)
        printf 'ANCLI_PING_%s=%s\n' "$_ping_name" "$_ping_value"
    done
)
