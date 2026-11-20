#!/system/bin/sh
# Manage the DSH WebUI as one verified process session. The WebUI must stop
# this session through task-stop.sh; never signal processes by a broad name.
set -u

ANCLI_DIR=/data/local/tmp/ancli
CURRENT="$ANCLI_DIR/.dsh-web.task"
LOG_POINTER="$ANCLI_DIR/.dsh-web.logfile"

valid_id() {
    case "$1" in
        *[!0-9_]*|_*|*_*_*) return 1 ;;
        *_*) return 0 ;;
        *) return 1 ;;
    esac
}

session_alive() {
    _id=$1
    valid_id "$_id" || return 1
    _pidfile="$ANCLI_DIR/.webui_${_id}.log.pid"
    [ -f "$_pidfile" ] && [ ! -L "$_pidfile" ] || return 1
    read -r _pid _start _boot < "$_pidfile" || return 1
    case "$_pid:$_start" in *[!0-9:]*) return 1 ;; esac
    [ -n "$_pid" ] && [ -n "$_start" ] && [ "$_pid" -gt 1 ] 2>/dev/null || return 1
    [ "$(cat /proc/sys/kernel/random/boot_id 2>/dev/null)" = "$_boot" ] || return 1
    _stat=$(cat "/proc/$_pid/stat" 2>/dev/null) || return 1
    _rest=${_stat##*) }
    set -- $_rest
    [ "${3:-}" = "$_pid" ] && [ "${4:-}" = "$_pid" ] || return 1
    shift 19
    [ "${1:-}" = "$_start" ]
}

case "${1:-}" in
    start)
        id=${2:-}
        log=${3:-}
        valid_id "$id" || { echo 'Invalid DSH task id'; exit 2; }
        expected_log="$ANCLI_DIR/.webui_${id}.log"
        [ "$log" = "$expected_log" ] || { echo 'Invalid DSH log path'; exit 2; }
        mkdir -p "$ANCLI_DIR" || exit 1
        if [ -f "$CURRENT" ]; then
            old_id=$(cat "$CURRENT" 2>/dev/null)
            if session_alive "$old_id"; then
                echo 'A managed DSH backend is already running'; exit 1
            fi
            valid_id "$old_id" && rm -f "$ANCLI_DIR/.webui_${old_id}.log.pid"
            rm -f "$CURRENT" "$LOG_POINTER"
        fi
        [ -x /system/bin/setsid ] || { echo 'setsid is unavailable; DSH backend not started'; exit 1; }
        _dsh_cwd=""
        for _dsh_cfg in "$ANCLI_DIR/rootfs/root/.ancli-config.json" "$ANCLI_DIR/container-config.json"; do
            if [ -f "$_dsh_cfg" ]; then
                _dsh_cwd=$(LC_ALL=C awk -v target="default_cwd" '
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
                }' "$_dsh_cfg" 2>/dev/null) || _dsh_cwd=""
                [ -n "$_dsh_cwd" ] && break
            fi
        done
        while [ "${_dsh_cwd%/}" != "$_dsh_cwd" ]; do
            _dsh_cwd=${_dsh_cwd%/}
        done
        proot_cwd="/root/.ancli-work"
        case "$_dsh_cwd" in
            ""|/|/dev|/dev/*|/proc|/proc/*|/sys|/sys/*|/apex|/apex/*|/linkerconfig|/linkerconfig/*|/system|/system/*)
                ;;
            /sdcard|/sdcard/*|/storage|/storage/*|/mnt|/mnt/*|/data|/data/*)
                [ -d "$_dsh_cwd" ] && proot_cwd="$_dsh_cwd"
                ;;
            /*)
                [ -d "$ANCLI_DIR/rootfs$_dsh_cwd" ] && proot_cwd="$_dsh_cwd"
                ;;
        esac
        : "${proot_cwd:=/root/.ancli-work}"
        pidfile="$expected_log.pid"
        rm -f "$pidfile" "$pidfile.tmp" "$expected_log.exit"
        runner='pidfile=$1
proot_cwd=${2:-/root/.ancli-work}
while [ "${proot_cwd%/}" != "$proot_cwd" ]; do
    proot_cwd=${proot_cwd%/}
done
[ -n "$proot_cwd" ] || proot_cwd=/root/.ancli-work
stat=$(cat /proc/$$/stat 2>/dev/null) || exit 1
rest=${stat##*) }
set -- $rest
shift 19
start=${1:-}
boot=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null)
[ -n "$start" ] && [ -n "$boot" ] || exit 1
umask 077
printf "%s %s %s\n" "$$" "$start" "$boot" > "$pidfile.tmp" 2>/dev/null
mv -f "$pidfile.tmp" "$pidfile" 2>/dev/null || exit 1
umask 022
# Do not use the normal dsh wrapper here: its parent-terminal watchdog is
# designed for interactive commands and will kill a WebUI backend when this
# short-lived launcher returns. Start the guest binary directly in a detached,
# session-tracked PRoot process instead.
. /data/local/tmp/ancli/bin/ancli_env.sh 2>/dev/null || true
if [ -f /data/local/tmp/ancli/secrets/dsh.env ]; then
    set -a
    . /data/local/tmp/ancli/secrets/dsh.env 2>/dev/null || true
    set +a
fi
unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY npm_config_proxy npm_config_https_proxy NPM_CONFIG_PROXY NPM_CONFIG_HTTPS_PROXY
SHM_BIND=""
if mkdir -p /data/local/tmp/ancli/shm 2>/dev/null; then
    chmod 1777 /data/local/tmp/ancli/shm 2>/dev/null || true
    SHM_BIND="-b /data/local/tmp/ancli/shm:/dev/shm"
fi
mkdir -p /data/local/tmp/ancli/rootfs/root/.ancli-work "/data/local/tmp/ancli/rootfs$proot_cwd" 2>/dev/null || true
if [ "$(id -u 2>/dev/null)" = "0" ] && ! grep -qs " /mnt/ancli-data " /proc/mounts; then
    mkdir -p /mnt/ancli-data 2>/dev/null || true
    mount --bind /data /mnt/ancli-data 2>/dev/null || true
fi
ANCLI_BIND_ARGS=""
for _b in /dev /proc /sys /data/local/tmp/ancli /sdcard /storage /mnt /data /apex /linkerconfig /system /data/adb; do
    while [ "${_b%/}" != "$_b" ]; do
        _b=${_b%/}
    done
    [ -n "$_b" ] || continue
    case " $ANCLI_BIND_ARGS " in
        *" -b $_b "*) continue ;;
    esac
    if [ -e "$_b" ] || [ -d "$_b" ]; then
        ANCLI_BIND_ARGS="$ANCLI_BIND_ARGS -b $_b"
    fi
done
HOSTS_BIND=""
[ -f /data/local/tmp/ancli/hosts ] && HOSTS_BIND="-b /data/local/tmp/ancli/hosts:/etc/hosts"
export PROOT_NO_SECCOMP=1
exec /data/local/tmp/ancli/bin/proot -r /data/local/tmp/ancli/rootfs $ANCLI_BIND_ARGS $HOSTS_BIND $SHM_BIND -w "$proot_cwd" /usr/bin/env ANCLI_IN_CONTAINER=1 HOME=/root PWD="$proot_cwd" SHELL=/bin/bash PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/root/.local/bin:/data/adb/ksu/bin:/data/adb/ap/bin:/system/bin TERM=xterm-256color BROWSER=/usr/local/bin/xdg-open /usr/local/bin/dsh web --no-open </dev/null'
        # Pass the PID file and normalized cwd as positional parameters so their
        # values are never interpolated into the shell source executed by setsid.
        nohup /system/bin/setsid /system/bin/sh -c "$runner" dsh-web "$pidfile" "$proot_cwd" > "$expected_log" 2>&1 </dev/null &
        sleep 1
        if ! session_alive "$id"; then
            rm -f "$pidfile" "$pidfile.tmp"
            echo 'DSH process session did not initialize safely'
            exit 1
        fi
        printf '%s\n' "$id" > "$CURRENT.tmp" && mv -f "$CURRENT.tmp" "$CURRENT" || exit 1
        printf '%s\n' "$expected_log" > "$LOG_POINTER.tmp" && mv -f "$LOG_POINTER.tmp" "$LOG_POINTER" || exit 1
        echo 'DSH backend session started'
        ;;
    stop)
        [ -f "$CURRENT" ] || { echo 'No managed DSH backend is recorded'; exit 1; }
        id=$(cat "$CURRENT" 2>/dev/null)
        valid_id "$id" || { echo 'Invalid DSH task record'; exit 1; }
        session_alive "$id" || { echo 'Recorded DSH session is not running in this boot'; exit 1; }
        /system/bin/sh "$ANCLI_DIR/task-stop.sh" "$id" --quiet || exit $?
        rm -f "$ANCLI_DIR/.webui_${id}.log.pid" "$CURRENT" "$LOG_POINTER"
        echo 'DSH backend session stopped'
        ;;
    *)
        echo 'Usage: dsh-web-control.sh start <task_id> <log_path> | stop'
        exit 2
        ;;
esac
