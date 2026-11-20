#!/system/bin/sh
# Ubuntu container mirrors only. Agent registries and vendor URLs are separate.
ancli_mirror_valid() { case "$1" in tuna|ustc|official) return 0 ;; *) return 1 ;; esac; }
ancli_mirror_rootfs_url() {
    _mirror_id="$1"; _mirror_version="$2"; _mirror_arch="$3"
    ancli_mirror_valid "$_mirror_id" || return 1
    case "$_mirror_version" in 24.04.5|24.04.4) ;; *) return 1 ;; esac
    case "$_mirror_arch" in arm64|amd64) ;; *) return 1 ;; esac
    case "$_mirror_id" in
        tuna) _mirror_root=https://mirrors.tuna.tsinghua.edu.cn/ubuntu-cdimage ;;
        ustc) _mirror_root=https://mirrors.ustc.edu.cn/ubuntu-cdimage ;;
        official) _mirror_root=https://cdimage.ubuntu.com ;;
    esac
    printf '%s/ubuntu-base/releases/24.04/release/ubuntu-base-%s-base-%s.tar.gz\n' "$_mirror_root" "$_mirror_version" "$_mirror_arch"
}
ancli_mirror_apt_url() {
    _mirror_id="$1"; _mirror_arch="$2"
    ancli_mirror_valid "$_mirror_id" || return 1
    case "$_mirror_arch" in arm64) _repo=ubuntu-ports ;; amd64) _repo=ubuntu ;; *) return 1 ;; esac
    case "$_mirror_id" in
        tuna) printf 'http://mirrors.tuna.tsinghua.edu.cn/%s\n' "$_repo" ;;
        ustc) printf 'http://mirrors.ustc.edu.cn/%s\n' "$_repo" ;;
        official) if [ "$_mirror_arch" = arm64 ]; then echo 'http://ports.ubuntu.com/ubuntu-ports'; else echo 'http://archive.ubuntu.com/ubuntu'; fi ;;
    esac
}
ancli_mirror_configure_apt() {
    _mirror_rootfs="$1"; _mirror_apt="$2"
    _sources="$_mirror_rootfs/etc/apt/sources.list.d/ubuntu.sources"
    if [ -f "$_sources" ] && [ ! -L "$_sources" ]; then
        [ -f "$_sources.ancli-backup" ] || cp -p "$_sources" "$_sources.ancli-backup" || return 1
        sed -i "s|^URIs:.*|URIs: $_mirror_apt|" "$_sources" || return 1
    else
        echo '[X] Ubuntu source configuration not found; existing data preserved' >&2
        return 1
    fi
}
ancli_mirror_speed_test() (
    _speed_mirror="$1"; _speed_arch="$2"
    ancli_mirror_valid "$_speed_mirror" || return 1
    case "$_speed_arch" in arm64|amd64) ;; *) return 1 ;; esac
    _speed_limit="${ANCLI_SPEED_BYTES:-1048576}"
    case "$_speed_limit" in ''|0|*[!0-9]*) _speed_limit=1048576 ;; esac
    _speed_limit=$(printf '%s' "$_speed_limit" | sed 's/^0*//')
    [ -n "$_speed_limit" ] && [ "$_speed_limit" -gt 0 ] 2>/dev/null || _speed_limit=1048576
    _speed_url="https://speed.cloudflare.com/__down?bytes=$_speed_limit"
    _speed_file=$(mktemp "$ANCLI_DIR/.mirror-speed.XXXXXX") || return 1
    _speed_exit="$_speed_file.exit"
    trap 'rm -f "$_speed_file" "$_speed_file.cap" "$_speed_file.part" "$_speed_exit"' EXIT HUP INT TERM
    _speed_start=$(awk '{print $1}' /proc/uptime)
    if command -v curl >/dev/null 2>&1; then
        if [ -n "${https_proxy:-${http_proxy:-}}" ]; then
            echo "ANCLI_SPEED_ROUTE=proxy:${https_proxy:-${http_proxy:-}}"
            ( timeout 120 curl -fLsS --proxy "${https_proxy:-${http_proxy:-}}" --noproxy '' --connect-timeout 8 --max-time 110 "$_speed_url" -o "$_speed_file" 2>/dev/null; echo "$?" > "$_speed_exit" ) &
        else
            echo 'ANCLI_SPEED_ROUTE=android-network'
            ( timeout 120 curl -fLsS --noproxy '*' --connect-timeout 8 --max-time 110 "$_speed_url" -o "$_speed_file" 2>/dev/null; echo "$?" > "$_speed_exit" ) &
        fi
        _speed_pid=$!
    else
        _speed_bb=''
        for _candidate in /data/adb/ksu/bin/busybox /data/adb/ap/bin/busybox /data/adb/magisk/busybox; do
            [ -x "$_candidate" ] && { _speed_bb="$_candidate"; break; }
        done
        [ -n "$_speed_bb" ] || { echo 'ANCLI_SPEED_ERROR=no_downloader'; return 1; }
        if [ -n "${https_proxy:-${http_proxy:-}}" ]; then
            echo "ANCLI_SPEED_ROUTE=proxy:${https_proxy:-${http_proxy:-}}"
            ( timeout 120 "$_speed_bb" wget -Y on -q -T 8 -O "$_speed_file" "$_speed_url" 2>/dev/null; echo "$?" > "$_speed_exit" ) &
        else
            echo 'ANCLI_SPEED_ROUTE=android-network'
            ( timeout 120 "$_speed_bb" wget -Y off -q -T 8 -O "$_speed_file" "$_speed_url" 2>/dev/null; echo "$?" > "$_speed_exit" ) &
        fi
        _speed_pid=$!
    fi
    # Report real bytes from the Cloudflare sample so the WebUI can render live progress.
    while [ ! -f "$_speed_exit" ]; do
        _speed_bytes=$(wc -c < "$_speed_file" 2>/dev/null | tr -d ' ')
        case "$_speed_bytes" in ''|*[!0-9]*) _speed_bytes=0 ;; esac
        [ "$_speed_bytes" -gt "$_speed_limit" ] && _speed_bytes=$_speed_limit
        printf 'ANCLI_SPEED_PROGRESS=%s/%s\n' "$_speed_bytes" "$_speed_limit"
        sleep 0.5 2>/dev/null || sleep 1
    done
    wait "$_speed_pid" 2>/dev/null || true
    _speed_rc=$(cat "$_speed_exit" 2>/dev/null || echo 1)
    _speed_bytes=$(wc -c < "$_speed_file" 2>/dev/null | tr -d ' ')
    case "$_speed_bytes" in ''|*[!0-9]*) _speed_bytes=0 ;; esac
    if [ "$_speed_bytes" -gt "$_speed_limit" ]; then
        head -c "$_speed_limit" "$_speed_file" > "$_speed_file.cap" && mv -f "$_speed_file.cap" "$_speed_file" || return 1
        _speed_bytes=$_speed_limit
    fi
    _speed_end=$(awk '{print $1}' /proc/uptime)
    _speed_bytes=$(wc -c < "$_speed_file" | tr -d ' ')
    [ "$_speed_bytes" -gt "$_speed_limit" ] && _speed_bytes=$_speed_limit
    printf 'ANCLI_SPEED_PROGRESS=%s/%s\n' "$_speed_bytes" "$_speed_limit"
    if [ "$_speed_rc" -ne 0 ] || [ "$_speed_bytes" -ne "$_speed_limit" ]; then
        echo 'ANCLI_SPEED_ERROR=download_failed'; return 1
    fi
    _speed_result=$(awk -v a="$_speed_start" -v b="$_speed_end" -v n="$_speed_bytes" 'BEGIN { d=b-a; if (d<0.01) d=0.01; printf "%.2f %d", d, n/d }')
    printf 'ANCLI_SPEED_SOURCE=cloudflare\nANCLI_SPEED_MIRROR=%s\nANCLI_SPEED_BYTES=%s\nANCLI_SPEED_SECONDS=%s\nANCLI_SPEED_BPS=%s\n' "$_speed_mirror" "$_speed_bytes" "${_speed_result% *}" "${_speed_result##* }"
)
