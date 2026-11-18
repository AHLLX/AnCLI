#!/system/bin/sh
# System overlays remain mounted until reboot; compare both file trees.
ancli_hot_tree_compatible() (
    _hot_live="$1"
    _hot_new="$2"
    [ ! -L "$_hot_live" ] && [ ! -L "$_hot_new" ] || return 1
    [ -d "$_hot_live" ] || return 0
    [ -d "$_hot_new" ] || return 1
    for _hot_direction in old new; do
        if [ "$_hot_direction" = old ]; then _hot_from="$_hot_live"; _hot_to="$_hot_new"; else _hot_from="$_hot_new"; _hot_to="$_hot_live"; fi
        find "$_hot_from" \( -type f -o -type l \) -print | while IFS= read -r _hot_file; do
            _hot_rel=${_hot_file#"$_hot_from"/}
            _hot_other="$_hot_to/$_hot_rel"
            if [ -L "$_hot_file" ]; then
                [ -L "$_hot_other" ] && [ "$(readlink "$_hot_file")" = "$(readlink "$_hot_other")" ] || exit 1
            else
                [ -f "$_hot_other" ] && [ ! -L "$_hot_other" ] && cmp -s "$_hot_file" "$_hot_other" || exit 1
                if [ -x "$_hot_file" ]; then [ -x "$_hot_other" ] || exit 1; else [ ! -x "$_hot_other" ] || exit 1; fi
            fi
        done || return 1
    done
)
ancli_request_hot_install() {
    if [ "${KSU_HAS_HOT_INSTALL:-false}" != true ]; then
        ui_print ">> 当前管理器不支持热安装，请重启手机以激活模块。"
        return 0
    fi
    if ! ancli_hot_tree_compatible /data/adb/modules/ancli/system "$MODPATH/system"; then
        ui_print ">> 系统覆盖文件有变更，请重启手机以激活模块。"
        return 0
    fi
    if [ ! -f "$MODPATH/hotinstall.sh" ] || ! chmod 755 "$MODPATH/hotinstall.sh"; then
        ui_print ">> 热安装脚本不可用，请重启手机。"
        return 0
    fi
    export MODULE_HOT_INSTALL_REQUEST=true
    export MODULE_HOT_RUN_SCRIPT=hotinstall.sh
    ui_print ">> 已申请 KernelSU 热安装，请以管理器最终结果为准。"
}
