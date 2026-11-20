#!/system/bin/sh
# Sourced by installers; only directories created and marked by this invocation
# may ever be recursively removed. Existing rootfs trees are never removed.
# Host wrappers may have created an empty work directory while the container
# was being inspected. Remove only the known empty placeholder shapes, never
# user files.
ancli_discard_empty_placeholder() {
    [ -d "$ROOTFS" ] && [ ! -L "$ROOTFS" ] || return 1
    [ "$(ls -A "$ROOTFS" 2>/dev/null)" = "root" ] || return 1
    [ -d "$ROOTFS/root" ] && [ ! -L "$ROOTFS/root" ] || return 1
    _ancli_placeholder_contents=$(ls -A "$ROOTFS/root" 2>/dev/null) || return 1
    case "$_ancli_placeholder_contents" in
        "") ;;
        .ancli-work)
            [ -d "$ROOTFS/root/.ancli-work" ] && [ ! -L "$ROOTFS/root/.ancli-work" ] || return 1
            [ -z "$(ls -A "$ROOTFS/root/.ancli-work" 2>/dev/null)" ] || return 1
            rmdir "$ROOTFS/root/.ancli-work" || return 1
            ;;
        *) return 1 ;;
    esac
    rmdir "$ROOTFS/root" && rmdir "$ROOTFS"
}

ancli_begin_rootfs() {
    ANCLI_ROOTFS_TARGET="$ROOTFS"
    ANCLI_ROOTFS_STAGE=""
    ANCLI_ROOTFS_ARCHIVE=""
    if [ -e "$ROOTFS" ]; then
        ancli_discard_empty_placeholder || true
    fi
    if [ -e "$ROOTFS" ] || [ -L "$ROOTFS" ]; then
        if [ -d "$ROOTFS" ] && [ ! -L "$ROOTFS" ] && rmdir "$ROOTFS" 2>/dev/null; then :
        else echo ">> 现有不完整容器目录已保留；请先检查或备份后处理。"; return 1; fi
    fi
    ANCLI_ROOTFS_STAGE="$ANCLI_DIR/.rootfs-install-$$"
    ANCLI_ROOTFS_ARCHIVE="$ANCLI_DIR/.ubuntu-base-install-$$.tar.gz"
    [ ! -e "$ANCLI_ROOTFS_STAGE" ] && [ ! -L "$ANCLI_ROOTFS_STAGE" ] || return 1
    mkdir "$ANCLI_ROOTFS_STAGE" || return 1
    printf '%s\n' 'ancli-rootfs-transaction-v1' > "$ANCLI_ROOTFS_STAGE/.ancli-transaction" || { rmdir "$ANCLI_ROOTFS_STAGE" 2>/dev/null || true; return 1; }
    if [ -n "${ANCLI_TASK_ID:-}" ]; then
        case "$ANCLI_TASK_ID" in *[!0-9_]*|_*) return 1 ;; esac
        printf "%s\n" "$ANCLI_TASK_ID" > "$ANCLI_ROOTFS_STAGE/.ancli-task" || return 1
    fi
    ROOTFS="$ANCLI_ROOTFS_STAGE"
    return 0
}

# Reuse an abandoned, AnCLI-marked WebUI installation checkpoint. Never resume
# a tree owned by a live installer PID and never accept an unmarked directory.
ancli_resume_rootfs() {
    if [ -e "$ROOTFS" ] || [ -L "$ROOTFS" ]; then return 1; fi
    for _resume_stage in "$ANCLI_DIR"/.rootfs-install-*; do
        [ -d "$_resume_stage" ] && [ ! -L "$_resume_stage" ] || continue
        [ "$(cat "$_resume_stage/.ancli-transaction" 2>/dev/null)" = 'ancli-rootfs-transaction-v1' ] || continue
        _resume_pid=${_resume_stage##*/.rootfs-install-}
        case "$_resume_pid" in ''|*[!0-9]*) continue ;; esac
        kill -0 "$_resume_pid" 2>/dev/null && continue
        ANCLI_ROOTFS_TARGET="$ROOTFS"
        ANCLI_ROOTFS_STAGE="$_resume_stage"
        ANCLI_ROOTFS_ARCHIVE="$ANCLI_DIR/.ubuntu-base-install-$_resume_pid.tar.gz"
        ROOTFS="$ANCLI_ROOTFS_STAGE"
        if [ -n "${ANCLI_TASK_ID:-}" ]; then
            case "$ANCLI_TASK_ID" in *[!0-9_]*|_*) return 1 ;; esac
            printf '%s\n' "$ANCLI_TASK_ID" > "$ROOTFS/.ancli-task" || return 1
        fi
        echo ">> 已找到上次未完成的容器检查点，将从现有文件继续；已安装的软件包不会删除。"
        return 0
    done
    return 1
}

# A failed package/download step keeps the marked stage so a later install can
# continue idempotently. Explicit task-stop cleanup still removes its own stage.
ancli_preserve_rootfs() {
    if [ -n "${ANCLI_ROOTFS_STAGE:-}" ] && [ ! -L "$ANCLI_ROOTFS_STAGE" ] &&
       [ "$(cat "$ANCLI_ROOTFS_STAGE/.ancli-transaction" 2>/dev/null)" = 'ancli-rootfs-transaction-v1' ]; then
        echo ">> 安装未完成；检查点已保留在 $ANCLI_ROOTFS_STAGE，已安装的软件包不会删除。再次执行 ancli container install 可继续补齐。"
        ROOTFS="${ANCLI_ROOTFS_TARGET:-$ROOTFS}"
        ANCLI_ROOTFS_STAGE=""
        ANCLI_ROOTFS_ARCHIVE=""
    fi
}

ancli_rollback_rootfs() {
    if [ -n "${ANCLI_ROOTFS_STAGE:-}" ] && [ ! -L "$ANCLI_ROOTFS_STAGE" ] &&
       [ "$(cat "$ANCLI_ROOTFS_STAGE/.ancli-transaction" 2>/dev/null)" = 'ancli-rootfs-transaction-v1' ]; then
        case "$ANCLI_ROOTFS_STAGE" in "$ANCLI_DIR"/.rootfs-install-*) rm -rf "$ANCLI_ROOTFS_STAGE" ;; esac
    fi
    if [ -n "${ANCLI_ROOTFS_ARCHIVE:-}" ]; then
        case "$ANCLI_ROOTFS_ARCHIVE" in "$ANCLI_DIR"/.ubuntu-base-install-*.tar.gz) rm -f "$ANCLI_ROOTFS_ARCHIVE" ;; esac
    fi
    ROOTFS="${ANCLI_ROOTFS_TARGET:-$ROOTFS}"
    ANCLI_ROOTFS_STAGE=""
    ANCLI_ROOTFS_ARCHIVE=""
}

ancli_commit_rootfs() {
    [ -n "${ANCLI_ROOTFS_STAGE:-}" ] || return 0
    [ ! -e "$ANCLI_ROOTFS_TARGET" ] && [ ! -L "$ANCLI_ROOTFS_TARGET" ] || return 1
    [ ! -L "$ANCLI_ROOTFS_STAGE" ] && [ "$(cat "$ANCLI_ROOTFS_STAGE/.ancli-transaction" 2>/dev/null)" = 'ancli-rootfs-transaction-v1' ] || return 1
    mv "$ANCLI_ROOTFS_STAGE" "$ANCLI_ROOTFS_TARGET" || return 1
    ROOTFS="$ANCLI_ROOTFS_TARGET"
    rm -f "$ROOTFS/.ancli-transaction" "$ROOTFS/.ancli-task"
    ANCLI_ROOTFS_STAGE=""
    ANCLI_ROOTFS_ARCHIVE=""
    return 0
}

# Remove only abandoned AnCLI-marked transactions; never touch a live process.
ancli_cleanup_stale_rootfs() {
    for _ancli_stage in "$ANCLI_DIR"/.rootfs-install-*; do
        [ -d "$_ancli_stage" ] && [ ! -L "$_ancli_stage" ] || continue
        [ "$(cat "$_ancli_stage/.ancli-transaction" 2>/dev/null)" = 'ancli-rootfs-transaction-v1' ] || continue
        _ancli_pid=${_ancli_stage##*/.rootfs-install-}
        case "$_ancli_pid" in ''|*[!0-9]*) continue ;; esac
        kill -0 "$_ancli_pid" 2>/dev/null && continue
        rm -rf "$_ancli_stage" || return 1
        rm -f "$ANCLI_DIR/.ubuntu-base-install-${_ancli_pid}.tar.gz"
    done
}

# Remove only a marked staging tree owned by the confirmed stopped task.
ancli_cleanup_stopped_task() {
    _cleanup_task="$1"
    case "$_cleanup_task" in ''|*[!0-9_]*|_*) return 1 ;; esac
    for _cleanup_stage in "$ANCLI_DIR"/.rootfs-install-*; do
        [ -d "$_cleanup_stage" ] && [ ! -L "$_cleanup_stage" ] || continue
        [ "$(cat "$_cleanup_stage/.ancli-transaction" 2>/dev/null)" = 'ancli-rootfs-transaction-v1' ] || continue
        [ "$(cat "$_cleanup_stage/.ancli-task" 2>/dev/null)" = "$_cleanup_task" ] || continue
        _cleanup_pid=${_cleanup_stage##*/.rootfs-install-}
        case "$_cleanup_pid" in ''|*[!0-9]*) continue ;; esac
        rm -rf "$_cleanup_stage" || return 1
        rm -f "$ANCLI_DIR/.ubuntu-base-install-${_cleanup_pid}.tar.gz"
    done
}
