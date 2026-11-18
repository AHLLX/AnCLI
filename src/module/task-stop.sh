#!/system/bin/sh
# Stop one verified AnCLI WebUI session, never a process matched by name.
set -u
ANCLI_DIR=/data/local/tmp/ancli
id=${1:-}
case "$id" in ''|*[!0-9_]*|_*|*_*_*) echo 'Invalid task id'; exit 2 ;; esac
case "$id" in *_*) ;; *) echo 'Invalid task id'; exit 2 ;; esac
log="$ANCLI_DIR/.webui_${id}.log"
[ -f "$log.exit" ] && { echo 'Task already finished'; exit 0; }
[ -f "$log.pid" ] && [ ! -L "$log.pid" ] || { echo 'Cannot verify task identity'; exit 3; }
read -r pid start boot < "$log.pid" || exit 3
case "$pid:$start" in *[!0-9:]*) echo 'Invalid task identity'; exit 3 ;; esac
[ -n "$pid" ] && [ -n "$start" ] && [ "$pid" -gt 1 ] 2>/dev/null || exit 3
[ "$(cat /proc/sys/kernel/random/boot_id 2>/dev/null)" = "$boot" ] || { echo 'Task belongs to another boot'; exit 3; }
stat=$(cat "/proc/$pid/stat" 2>/dev/null) || { echo 'Task process disappeared; refresh status'; exit 3; }
rest=${stat##*) }
set -- $rest
[ "$3" = "$pid" ] && [ "$4" = "$pid" ] || { echo 'Task is not in its own session'; exit 3; }
shift 19
[ "${1:-}" = "$start" ] || { echo 'Task identity changed'; exit 3; }
# kill the distinct session process group, not the manager or other tasks.
/system/bin/kill -TERM -- "-$pid" 2>/dev/null || { echo 'Unable to signal task'; exit 3; }
# A process in a new session keeps the group number reserved until it exits.
group_alive() {
    for statfile in /proc/[0-9]*/stat; do
        stat=$(cat "$statfile" 2>/dev/null) || continue
        rest=${stat##*) }
        set -- $rest
        [ "${3:-}" = "$pid" ] && [ "${4:-}" = "$pid" ] && [ "${1:-}" != Z ] && return 0
    done
    return 1
}
n=0
while group_alive && [ "$n" -lt 5 ]; do sleep 1; n=$((n+1)); done
if group_alive; then
    /system/bin/kill -KILL -- "-$pid" 2>/dev/null || true
    sleep 1
fi
if group_alive; then echo 'Task still running; no cleanup performed'; exit 3; fi
. "$ANCLI_DIR/install-transaction.sh"
ancli_cleanup_stopped_task "$id" || { echo 'Task stopped but cleanup failed'; exit 3; }
[ -f "$log.exit" ] || { printf '143\n' > "$log.exit.tmp" && mv -f "$log.exit.tmp" "$log.exit"; }
echo 'Task stopped; owned temporary installation data cleaned. Existing container data preserved.'
