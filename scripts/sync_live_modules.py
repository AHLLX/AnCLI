"""Sync staged modules_update/ancli files into the live module and ANCLI runtime dir."""
import argparse
import os
import subprocess
import sys


def parse_devices(device_args):
    raw_items = list(device_args or [])
    if not raw_items:
        env_devices = os.environ.get("ANCLI_DEVICES", "").strip()
        if env_devices:
            raw_items = [item.strip() for item in env_devices.split(",") if item.strip()]
        else:
            single = os.environ.get("ANCLI_DEVICE_SERIAL", "").strip()
            if single:
                raw_items = [single]
    devices = []
    for item in raw_items:
        if "=" in item:
            name, serial = item.split("=", 1)
            devices.append((name.strip() or serial.strip(), serial.strip()))
        else:
            devices.append((item.strip(), item.strip()))
    return [(n, s) for n, s in devices if s]


SYNC_CODE = """
import os, shutil

pairs = [
    ("/data/adb/modules_update/ancli/module.prop", "/data/adb/modules/ancli/module.prop"),
    ("/data/adb/modules_update/ancli/customize.sh", "/data/adb/modules/ancli/customize.sh"),
    ("/data/adb/modules_update/ancli/service.sh", "/data/adb/modules/ancli/service.sh"),
    ("/data/adb/modules_update/ancli/uninstall.sh", "/data/adb/modules/ancli/uninstall.sh"),
    ("/data/adb/modules_update/ancli/hotinstall.sh", "/data/adb/modules/ancli/hotinstall.sh"),
    ("/data/adb/modules_update/ancli/hot-install-policy.sh", "/data/adb/modules/ancli/hot-install-policy.sh"),
    ("/data/adb/modules_update/ancli/ancli_env.sh", "/data/adb/modules/ancli/ancli_env.sh"),
    ("/data/adb/modules_update/ancli/dsh-web-control.sh", "/data/adb/modules/ancli/dsh-web-control.sh"),
    ("/data/adb/modules_update/ancli/dsh-web-control.sh", "/data/local/tmp/ancli/dsh-web-control.sh"),
    ("/data/adb/modules_update/ancli/container-bootstrap.sh", "/data/adb/modules/ancli/container-bootstrap.sh"),
    ("/data/adb/modules_update/ancli/container-bootstrap.sh", "/data/local/tmp/ancli/container-bootstrap.sh"),
    ("/data/adb/modules_update/ancli/container-profile-data.sh", "/data/adb/modules/ancli/container-profile-data.sh"),
    ("/data/adb/modules_update/ancli/container-profile-data.sh", "/data/local/tmp/ancli/container-profile-data.sh"),
    ("/data/adb/modules_update/ancli/android-probe.sh", "/data/adb/modules/ancli/android-probe.sh"),
    ("/data/adb/modules_update/ancli/android-probe.sh", "/data/local/tmp/ancli/android-probe.sh"),
    ("/data/adb/modules_update/ancli/container-mirror.sh", "/data/adb/modules/ancli/container-mirror.sh"),
    ("/data/adb/modules_update/ancli/container-mirror.sh", "/data/local/tmp/ancli/container-mirror.sh"),
    ("/data/adb/modules_update/ancli/apt-key-compat.sh", "/data/adb/modules/ancli/apt-key-compat.sh"),
    ("/data/adb/modules_update/ancli/apt-key-compat.sh", "/data/local/tmp/ancli/apt-key-compat.sh"),
    ("/data/adb/modules_update/ancli/install-network.sh", "/data/adb/modules/ancli/install-network.sh"),
    ("/data/adb/modules_update/ancli/install-network.sh", "/data/local/tmp/ancli/install-network.sh"),
    ("/data/adb/modules_update/ancli/install-transaction.sh", "/data/adb/modules/ancli/install-transaction.sh"),
    ("/data/adb/modules_update/ancli/install-transaction.sh", "/data/local/tmp/ancli/install-transaction.sh"),
    ("/data/adb/modules_update/ancli/node-setup.sh", "/data/adb/modules/ancli/node-setup.sh"),
    ("/data/adb/modules_update/ancli/node-setup.sh", "/data/local/tmp/ancli/node-setup.sh"),
    ("/data/adb/modules_update/ancli/task-stop.sh", "/data/adb/modules/ancli/task-stop.sh"),
    ("/data/adb/modules_update/ancli/task-stop.sh", "/data/local/tmp/ancli/task-stop.sh"),
    ("/data/adb/modules_update/ancli/system/bin/ancli", "/data/adb/modules/ancli/system/bin/ancli"),
    ("/data/adb/modules_update/ancli/webroot/index.html", "/data/adb/modules/ancli/webroot/index.html"),
    ("/data/adb/modules_update/ancli/webroot/logo.png", "/data/adb/modules/ancli/webroot/logo.png"),
    ("/data/adb/modules_update/ancli/ancli/ancli-core.py", "/data/adb/modules/ancli/ancli/ancli-core.py"),
    ("/data/adb/modules_update/ancli/ancli/ancli-core.py", "/data/local/tmp/ancli/bin/ancli-core.py"),
    ("/data/adb/modules_update/ancli/ancli/registry.json", "/data/adb/modules/ancli/ancli/registry.json"),
    ("/data/adb/modules_update/ancli/ancli/registry.json", "/data/local/tmp/ancli/registry.json"),
    ("/data/adb/modules_update/ancli/ancli/registry.json", "/data/local/tmp/ancli/bin/registry.json"),
    ("/data/adb/modules_update/ancli/ancli/container-profiles.json", "/data/adb/modules/ancli/ancli/container-profiles.json"),
    ("/data/adb/modules_update/ancli/ancli/container-profiles.json", "/data/local/tmp/ancli/container-profiles.json"),
    ("/data/adb/modules_update/ancli/ancli/catalog-cache.json", "/data/adb/modules/ancli/ancli/catalog-cache.json"),
    ("/data/adb/modules_update/ancli/ancli/catalog-cache.json", "/data/local/tmp/ancli/catalog-cache.json"),
    ("/data/adb/modules_update/ancli/ancli/ancli_env.sh", "/data/adb/modules/ancli/ancli/ancli_env.sh"),
    ("/data/adb/modules_update/ancli/ancli/ancli_env.sh", "/data/local/tmp/ancli/bin/ancli_env.sh"),
    ("/data/adb/modules_update/ancli/ancli/bash-host-compat", "/data/adb/modules/ancli/ancli/bash-host-compat"),
    ("/data/adb/modules_update/ancli/ancli/bash-host-compat", "/data/local/tmp/ancli/bash-host-compat"),
    ("/data/adb/modules_update/ancli/ancli/runtime-context/AGENTS.md", "/data/adb/modules/ancli/ancli/runtime-context/AGENTS.md"),
    ("/data/adb/modules_update/ancli/ancli/runtime-context/AGENTS.md", "/data/local/tmp/ancli/runtime-context/AGENTS.md"),
]

for src, dst in pairs:
    if os.path.exists(src):
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if os.path.lexists(dst):
                os.remove(dst)
            shutil.copyfile(src, dst)
            os.chmod(dst, 0o755 if dst.endswith((".py", ".sh", "bash-host-compat", "/ancli")) else 0o644)
            print("OK synced:", dst)
        except Exception as e:
            print("FAIL sync:", dst, e)
    else:
        print("SKIP not found:", src)

print("ALL SYNC DONE")
"""


def main(argv=None):
    parser = argparse.ArgumentParser(description="Sync staged modules_update/ancli files on target ADB devices.")
    parser.add_argument("--device", action="append", default=[],
                        help="Target device serial or name=serial (repeatable; or set ANCLI_DEVICES / ANCLI_DEVICE_SERIAL)")
    args = parser.parse_args(argv)

    devices = parse_devices(args.device)
    if not devices:
        print("[X] No devices specified: pass --device or set ANCLI_DEVICES / ANCLI_DEVICE_SERIAL", file=sys.stderr)
        return 2

    for name, serial in devices:
        print(f"=== Syncing live module on {name} ({serial}) ===")
        if ":" in serial:
            subprocess.run(["adb", "connect", serial], capture_output=True)
        proot_cmd = (
            "/data/local/tmp/ancli/bin/proot "
            "-r /data/local/tmp/ancli/rootfs "
            "-b /data/adb/modules_update/ancli "
            "-b /data/adb/modules/ancli "
            "-b /data/local/tmp/ancli "
            "/usr/bin/python3 -"
        )
        p = subprocess.Popen(
            ["adb", "-s", serial, "shell", "su", "-c", proot_cmd],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        out, err = p.communicate(input=SYNC_CODE)
        print("Stdout:", out)
        if err:
            print("Stderr:", err)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
