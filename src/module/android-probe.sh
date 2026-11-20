#!/system/bin/sh
# 一次性读取 Android 宿主事实，供 Agent 和 `ancli agent-init` 使用。
# 只读取系统状态，不输出账号、cookie、token 或设备标识。

json_escape_stream() {
    (cat; printf '__ANCLI_EOF__') | LC_ALL=C awk -v BINMODE=3 '
    BEGIN {
        for (i = 1; i < 32; i++) {
            ch = sprintf("%c", i)
            if (i == 8) rep[ch] = "\\" "b"
            else if (i == 9) rep[ch] = "\\" "t"
            else if (i == 10) rep[ch] = "\\" "n"
            else if (i == 12) rep[ch] = "\\" "f"
            else if (i == 13) rep[ch] = "\\" "r"
            else rep[ch] = "\\" sprintf("u%04x", i)
        }
    }
    function esc(s,    n, i, c, r) {
        r = ""
        n = length(s)
        for (i = 1; i <= n; i++) {
            c = substr(s, i, 1)
            if (c == "\\") r = r "\\\\"
            else if (c == "\"") r = r "\\\""
            else if (c in rep) r = r rep[c]
            else r = r c
        }
        return r
    }
    NR == 1 { prev = $0; next }
    {
        printf "%s\\n", esc(prev)
        prev = $0
    }
    END {
        prev = substr(prev, 1, length(prev) - 13)
        printf "%s", esc(prev)
    }'
}

json_escape() {
    printf '%s' "${1:-}" | json_escape_stream
}

prop() { /system/bin/getprop "$1" 2>/dev/null | tr -d '\r\n'; }

action="${1:-probe}"
case "$action" in
    probe|--json) ;;
    list|stat|read|shell|exec) ;;
    *) echo "用法：ancli android probe|list|stat|read|shell <命令或路径>" >&2; exit 2 ;;
esac

# 宿主桥接入口：命令在 Android /system/bin/sh 中执行，stdout/stderr
# 原样返回给 Agent。模块不重复实现 Android 权限策略。
if [ "$action" = "shell" ] || [ "$action" = "exec" ]; then
    shift
    [ "$#" -gt 0 ] || { echo "用法：ancli android shell <宿主命令>" >&2; exit 2; }
    exec /system/bin/sh -c "$*"
fi

# list/stat/read 是便捷的结构化入口；需要完整宿主能力时使用上面的 shell
# 桥接。模块只负责桥接，不增加路径白名单和确认层。
if [ "$action" = "list" ] || [ "$action" = "stat" ] || [ "$action" = "read" ]; then
    shift
    json_flag=0
    target=""
    for _arg in "$@"; do
        case "$_arg" in
            --json) json_flag=1 ;;
            -*) echo '{"status":"error","error":"unsupported option"}'; exit 2 ;;
            *)
                if [ -z "$target" ]; then
                    target="$_arg"
                else
                    echo '{"status":"error","error":"unexpected extra argument"}'; exit 2
                fi
                ;;
        esac
    done
    [ -n "$target" ] || { echo '{"status":"error","error":"target path is required"}'; exit 2; }
    case "$target" in /*) ;; *) echo '{"status":"error","error":"absolute path is required"}'; exit 2 ;; esac
    if [ "$action" = "list" ]; then
        [ -d "$target" ] || { echo '{"status":"error","error":"directory not found"}'; exit 1; }
        if [ "$json_flag" -eq 1 ]; then
            first=true; printf '{"status":"ok","path":"%s","entries":[' "$(json_escape "$target")"
            for entry in "$target"/*; do
                [ -e "$entry" ] || [ -L "$entry" ] || continue
                [ "$first" = true ] || printf ','
                first=false
                printf '"%s"' "$(json_escape "${entry##*/}")"
            done
            printf ']}\n'
        else
            ls -la "$target"
        fi
        exit 0
    fi
    if [ "$action" = "stat" ]; then
        [ -e "$target" ] || { echo '{"status":"error","error":"path not found"}'; exit 1; }
        size="$(/system/bin/stat -c '%s' "$target" 2>/dev/null || echo 0)"
        mode="$(/system/bin/stat -c '%A' "$target" 2>/dev/null || echo unknown)"
        type="file"; [ -d "$target" ] && type="directory"
        printf '{"status":"ok","path":"%s","type":"%s","size":%s,"mode":"%s"}\n' "$(json_escape "$target")" "$type" "$size" "$(json_escape "$mode")"
        exit 0
    fi
    [ -f "$target" ] || { echo '{"status":"error","error":"regular file not found"}'; exit 1; }
    bytes="$(/system/bin/stat -c '%s' "$target" 2>/dev/null || echo 0)"
    printf '{"status":"ok","path":"%s","bytes":%s,"content":"' "$(json_escape "$target")" "$bytes"
    cat "$target" 2>/dev/null | json_escape_stream
    printf '"}\n'
    exit 0
fi

release="$(prop ro.build.version.release)"
sdk="$(prop ro.build.version.sdk)"
abi="$(prop ro.product.cpu.abilist)"
model="$(prop ro.product.model)"
kernel="$(uname -srmo 2>/dev/null | tr -d '\r\n')"
rootfs_free="$(/system/bin/df -k /data 2>/dev/null | tail -n 1 | awk '{print $4}' | tr -d '\r\n')"
shared_free="$(/system/bin/df -k /sdcard 2>/dev/null | tail -n 1 | awk '{print $4}' | tr -d '\r\n')"
proxy="$(/system/bin/settings get global http_proxy 2>/dev/null | tr -d '\r\n')"
dns1="$(prop net.dns1)"
dns2="$(prop net.dns2)"
data_channel=false
/system/bin/mount 2>/dev/null | grep -q ' /mnt/ancli-data ' && data_channel=true

checked_at="$( { date -Iseconds 2>/dev/null || date +%Y-%m-%dT%H:%M:%S%z 2>/dev/null || true; } | tr -d '\r\n')"
printf '{"checked_at":"%s","android":{"release":"%s","sdk":"%s","model":"%s","abi":"%s","kernel":"%s"},"storage_kb":{"data_free":"%s","shared_free":"%s"},"network":{"http_proxy":"%s","dns":["%s","%s"]},"paths":{"data":%s,"sdcard":%s,"storage":%s,"mnt":%s,"android_data_channel":%s}}\n' \
    "$(json_escape "$checked_at")" \
    "$(json_escape "$release")" "$(json_escape "$sdk")" "$(json_escape "$model")" "$(json_escape "$abi")" "$(json_escape "$kernel")" \
    "$(json_escape "$rootfs_free")" "$(json_escape "$shared_free")" "$(json_escape "$proxy")" "$(json_escape "$dns1")" "$(json_escape "$dns2")" \
    "$([ -d /data ] && echo true || echo false)" "$([ -d /sdcard ] && echo true || echo false)" "$([ -d /storage ] && echo true || echo false)" "$([ -d /mnt ] && echo true || echo false)" "$data_channel"
