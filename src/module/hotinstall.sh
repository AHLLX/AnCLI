#!/system/bin/sh
# KernelSU executes this after swapping in the live module. Existing bridge and
# agent processes remain untouched; the normal wrapper starts a missing bridge.
MODDIR=${0%/*}
if [ ! -x /data/local/tmp/ancli/container-bootstrap.sh ] || [ ! -x /data/adb/ksu/bin/ancli ]; then
    echo '! AnCLI host entry is missing; check the installer log.'
    exit 1
fi
if ! /system/bin/sh /data/local/tmp/ancli/container-bootstrap.sh status --json >/dev/null; then
    echo '! AnCLI container status check failed; check host entry permissions.'
    exit 1
fi
echo '>> AnCLI host entry and WebUI files are ready; system overlay remains a boot-time operation.'
