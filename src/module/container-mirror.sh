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
    _speed_url=$(ancli_mirror_rootfs_url "$_speed_mirror" 24.04.5 "$_speed_arch") || return 2
    _speed_file=$(mktemp "$ANCLI_DIR/.mirror-speed.XXXXXX") || return 1
    trap 'rm -f "$_speed_file"' EXIT HUP INT TERM
    _speed_start=$(awk '{print $1}' /proc/uptime)
    if command -v curl >/dev/null 2>&1; then
        timeout 14 curl -fLsS --connect-timeout 4 --max-time 12 "$_speed_url" 2>/dev/null | head -c 1048576 > "$_speed_file"
    else
        _speed_bb=''
        for _candidate in /data/adb/ksu/bin/busybox /data/adb/ap/bin/busybox /data/adb/magisk/busybox; do
            [ -x "$_candidate" ] && { _speed_bb="$_candidate"; break; }
        done
        [ -n "$_speed_bb" ] || { echo 'ANCLI_SPEED_ERROR=no_downloader'; return 1; }
        timeout 14 "$_speed_bb" wget -q -T 10 -O - "$_speed_url" 2>/dev/null | head -c 1048576 > "$_speed_file"
    fi
    _speed_end=$(awk '{print $1}' /proc/uptime)
    _speed_bytes=$(wc -c < "$_speed_file" | tr -d ' ')
    _speed_magic=$(od -An -tx1 -N2 "$_speed_file" 2>/dev/null | tr -d ' \r\n')
    if [ "$_speed_magic" != 1f8b ] || [ "$_speed_bytes" -lt 65536 ]; then
        echo 'ANCLI_SPEED_ERROR=download_failed'; return 1
    fi
    _speed_result=$(awk -v a="$_speed_start" -v b="$_speed_end" -v n="$_speed_bytes" 'BEGIN { d=b-a; if (d<0.01) d=0.01; printf "%.2f %d", d, n/d }')
    printf 'ANCLI_SPEED_MIRROR=%s\nANCLI_SPEED_BYTES=%s\nANCLI_SPEED_SECONDS=%s\nANCLI_SPEED_BPS=%s\n' "$_speed_mirror" "$_speed_bytes" "${_speed_result% *}" "${_speed_result##* }"
)
