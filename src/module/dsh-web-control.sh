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
        pidfile="$expected_log.pid"
        rm -f "$pidfile" "$pidfile.tmp" "$expected_log.exit"
        runner='pidfile=$1
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
# Do not use the normal dsh wrapper here: its parent-terminal watchdog is
# designed for interactive commands and will kill a WebUI backend when this
# short-lived launcher returns. Start the guest binary directly in a detached,
# session-tracked PRoot process instead.
export PROOT_NO_SECCOMP=1
exec /data/local/tmp/ancli/bin/proot -r /data/local/tmp/ancli/rootfs -b /dev -b /proc -b /sys -b /data/local/tmp/ancli -b /sdcard -b /storage -b /mnt -b /data -b /apex -b /linkerconfig -b /system -b /data/local/tmp/ancli/hosts:/etc/hosts -b /data/adb -b /data/local/tmp/ancli/shm:/dev/shm -w /root/.ancli-work /usr/bin/env ANCLI_IN_CONTAINER=1 HOME=/root SHELL=/bin/bash PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/system/bin TERM=xterm-256color BROWSER=/usr/local/bin/xdg-open /usr/local/bin/dsh web --no-open'
        # Pass the PID file as a positional parameter so its path is not
        # interpolated into the shell source executed by setsid.
        nohup /system/bin/setsid /system/bin/sh -c "$runner" dsh-web "$pidfile" > "$expected_log" 2>&1 </dev/null &
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
        /system/bin/sh "$ANCLI_DIR/task-stop.sh" "$id" || exit $?
        rm -f "$ANCLI_DIR/.webui_${id}.log.pid" "$CURRENT" "$LOG_POINTER"
        echo 'DSH backend session stopped'
        ;;
    *)
        echo 'Usage: dsh-web-control.sh start <task_id> <log_path> | stop'
        exit 2
        ;;
esac
