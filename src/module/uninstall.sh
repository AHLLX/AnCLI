#!/system/bin/sh
# ============================================================
# AnCLI 模块卸载脚本 (uninstall.sh)
# 触发时机：
#   1. 用户在 Magisk / KernelSU / APatch 管理器中卸载模块时自动执行；
#   2. 或在 WebUI / 终端中触发彻底完全卸载 (uninstall.sh --purge)。
# ============================================================

ANCLI_DIR="/data/local/tmp/ancli"

# 1. 终止所有正在运行的 Agent 与 PRoot 进程
_ancli_agent_re='(/data/adb/(ksu|ap)/bin|/usr/local/bin)/(aider|mimo|agy|dsh|claude|opencode|grok|codex)'
/system/bin/toybox pkill -KILL -f "$_ancli_agent_re" 2>/dev/null || true
pkill -9 -f "proot.*${ANCLI_DIR}/rootfs" 2>/dev/null || \
    pkill -9 -f "proot.*ancli" 2>/dev/null || true

# 2. 管理器卸载与 WebUI 卸载均彻底清理 AnCLI 用户资产。
# 仅清理本模块的目录和标记，不匹配删除同目录中的 ZIP 等其他文件。
echo "[*] 正在解除数据通道挂载..."
umount -l /mnt/ancli-data 2>/dev/null || true
# 目录可能仍是挂载点；绝不递归删除该路径，避免误删宿主 /data。
rmdir /mnt/ancli-data 2>/dev/null || true
rm -f /data/local/tmp/ancli_force_purge 2>/dev/null || true
# Old WebUI uninstall task records live outside ANCLI_DIR. Keep the current
# record until the WebUI has observed the exit status; the runner removes it.
for _log in /data/local/tmp/.ancli-uninstall-*.log /data/local/tmp/.ancli-uninstall-*.log.exit /data/local/tmp/.ancli-uninstall-*.log.meta; do
    [ -f "$_log" ] || continue
    case "$_log" in "${ANCLI_UNINSTALL_LOG:-/nonexistent}"|"${ANCLI_UNINSTALL_LOG:-/nonexistent}.exit"|"${ANCLI_UNINSTALL_LOG:-/nonexistent}.meta") continue ;; esac
    rm -f "$_log" 2>/dev/null || true
done
if ! rm -rf "$ANCLI_DIR"; then
    echo "[X] 无法清理 AnCLI 容器和数据：$ANCLI_DIR" >&2
    exit 1
fi

# 3. 清理 KernelSU / APatch 动态 bin 目录中由 AnCLI 注入的包装器与辅助脚本
echo "[*] 正在清理宿主注入脚本..."
for BIN_DIR in /data/adb/ksu/bin /data/adb/ap/bin; do
    if [ -d "$BIN_DIR" ]; then
        grep -rl "$ANCLI_DIR" "$BIN_DIR" 2>/dev/null | while IFS= read -r f; do
            rm -f "$f" 2>/dev/null || true
        done
        if [ -f "$BIN_DIR/bash" ] && head -n 8 "$BIN_DIR/bash" 2>/dev/null \
            | grep -qE 'AnCLI AGY host bash compatibility|AnCLI proot shim'; then
            rm -f "$BIN_DIR/bash" 2>/dev/null || true
        fi
    fi
done

# 4. 标记模块供管理器移除。重复卸载或目录已消失时仍保持幂等。
MOD_DIR="/data/adb/modules/ancli"
if [ -d "$MOD_DIR" ]; then
    echo "[*] 正在标记并清理模块本体 ($MOD_DIR)..."
    touch "$MOD_DIR/remove" 2>/dev/null || true
    rm -rf "$MOD_DIR/system" "$MOD_DIR/ancli" "$MOD_DIR/bin" "$MOD_DIR/shm" 2>/dev/null || true
fi
echo "[OK] AnCLI 容器与数据已清理；模块目录由管理器在重启后移除。"
