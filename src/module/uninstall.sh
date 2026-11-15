#!/system/bin/sh
# ============================================================
# AnCLI 模块卸载脚本 (uninstall.sh)
# 触发时机：
#   1. 用户在 Magisk / KernelSU / APatch 管理器中卸载模块时自动执行；
#   2. 或在 WebUI / 终端中触发彻底完全卸载 (uninstall.sh --purge)。
# ============================================================

ANCLI_DIR="/data/local/tmp/ancli"

# 1. 终止所有正在运行的 Agent 与 PRoot 进程
_ancli_agent_re='(/data/adb/(ksu|ap)/bin|/usr/local/bin)/(aider|mimo|agy|dsh|claude|opencode|grok)'
/system/bin/toybox pkill -KILL -f "$_ancli_agent_re" 2>/dev/null || true
pkill -9 -f "proot.*${ANCLI_DIR}/rootfs" 2>/dev/null || \
    pkill -9 -f "proot.*ancli" 2>/dev/null || true

# 2. 判断是否彻底清除容器与模块资产：
#    管理器卸载默认保留用户资产（KEEP_DATA=1）；
#    当存在标记文件 /data/local/tmp/ancli_force_purge 或传入 --purge/-f 参数时彻底清空。
KEEP_DATA=1
PURGE_MARKER="/data/local/tmp/ancli_force_purge"
if [ -f "$PURGE_MARKER" ] || [ "${1:-}" = "--purge" ] || [ "${1:-}" = "-f" ] || [ "${1:-}" = "--all" ]; then
    KEEP_DATA=0
    rm -f "$PURGE_MARKER" 2>/dev/null || true
fi

if [ "$KEEP_DATA" -eq 1 ]; then
    # 默认安全保留模式（仅升级或非强制卸载）：保留 rootfs、installed.json、secrets/ 等用户资产
    rm -rf "$ANCLI_DIR/bin"
    rm -f "$ANCLI_DIR/bash-host-compat"
else
    # 彻底完全清理模式：抹除真实数据通道挂载与整个容器根文件系统
    echo "[*] 正在解除数据通道挂载..."
    umount -l /mnt/ancli-data 2>/dev/null || true
    rm -rf /mnt/ancli-data 2>/dev/null || true

    echo "[*] 正在彻底删除容器根目录与所有数据 (/data/local/tmp/ancli)..."
    rm -rf "$ANCLI_DIR" 2>/dev/null || true
    rm -f /data/local/tmp/ancli* 2>/dev/null || true
fi

# 3. 清理 KernelSU / APatch 动态 bin 目录中由 AnCLI 注入的包装器与辅助脚本
echo "[*] 正在清理宿主注入脚本..."
for BIN_DIR in /data/adb/ksu/bin /data/adb/ap/bin; do
    if [ -d "$BIN_DIR" ]; then
        grep -rl "$ANCLI_DIR" "$BIN_DIR" 2>/dev/null | while IFS= read -r f; do
            rm -f "$f" 2>/dev/null || true
        done
        rm -f "$BIN_DIR/ancli" 2>/dev/null || true
        rm -f "$BIN_DIR/xdg-open" 2>/dev/null || true
        if [ -f "$BIN_DIR/bash" ] && head -n 8 "$BIN_DIR/bash" 2>/dev/null \
            | grep -q 'AnCLI AGY host bash compatibility'; then
            rm -f "$BIN_DIR/bash" 2>/dev/null || true
        fi
    fi
done

# 4. 如果是彻底清除模式，标记卸载并清理模块本体
if [ "$KEEP_DATA" -eq 0 ]; then
    MOD_DIR="/data/adb/modules/ancli"
    if [ -d "$MOD_DIR" ]; then
        echo "[*] 正在标记并清理模块本体 (/data/adb/modules/ancli)..."
        touch "$MOD_DIR/remove" 2>/dev/null || true
        rm -rf "$MOD_DIR/system" "$MOD_DIR/ancli" "$MOD_DIR/bin" "$MOD_DIR/shm" 2>/dev/null || true
    fi
    echo "[OK] AnCLI 模块与全部容器应用已彻底清理完毕！请重启手机以应用系统变更。"
fi
