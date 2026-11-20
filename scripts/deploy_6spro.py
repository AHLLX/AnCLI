"""Deploy an AnCLI module ZIP to a single device via ADB and KernelSU."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def resolve_zip(zip_arg):
    if zip_arg:
        path = Path(zip_arg)
        if not path.is_absolute():
            path = ROOT / path
        if not path.is_file():
            raise SystemExit(f"[X] ZIP file not found: {path}")
        return path
    candidates = sorted(ROOT.glob("ancli-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise SystemExit("[X] No ancli-*.zip found in repo root; pass --zip or set ANCLI_ZIP")
    return candidates[0]


def run_adb(serial, cmd):
    full = ["adb", "-s", serial] + cmd
    return subprocess.run(full, capture_output=True, text=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Deploy AnCLI module ZIP to a single ADB device.")
    parser.add_argument("--serial", default=os.environ.get("ANCLI_DEVICE_SERIAL", ""),
                        help="ADB device serial or host:port (or set ANCLI_DEVICE_SERIAL)")
    parser.add_argument("--zip", dest="zip_path", default=os.environ.get("ANCLI_ZIP", ""),
                        help="Module ZIP path (default: newest ancli-*.zip in repo root or ANCLI_ZIP)")
    args = parser.parse_args(argv)

    serial = (args.serial or "").strip()
    if not serial:
        print("[X] Missing device serial: pass --serial or set ANCLI_DEVICE_SERIAL", file=sys.stderr)
        return 2

    zip_file = resolve_zip(args.zip_path)
    zip_name = zip_file.name

    print(f"=== Deploying {zip_name} to {serial} ===")
    if ":" in serial:
        subprocess.run(["adb", "connect", serial], capture_output=True)

    print("[1] Pushing ZIP to device...")
    res = run_adb(serial, ["push", str(zip_file), f"/data/local/tmp/{zip_name}"])
    print(res.stdout, res.stderr)
    run_adb(serial, ["push", str(zip_file), f"/sdcard/Download/{zip_name}"])

    print("[2] Running ksud module install...")
    res = run_adb(serial, ["shell", "su", "-c", f"/data/adb/ksu/bin/ksud module install /data/local/tmp/{zip_name}"])
    print(res.stdout)

    print("[3] Live syncing updated module files via container python3...")
    sync_code = """
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
            os.chmod(dst, 0o755 if dst.endswith((".py", ".sh", "bash-host-compat", "/ancli", "/bash")) else 0o644)
            print("OK synced:", dst)
        except Exception as e:
            print("FAIL sync:", dst, e)

guest_bash = "/data/local/tmp/ancli/rootfs/usr/local/bin/bash"
if not os.path.lexists(guest_bash):
    try:
        os.symlink("/bin/bash", guest_bash)
        print("OK created /usr/local/bin/bash symlink")
    except Exception as e:
        print("FAIL create bash symlink:", e)

print("SYNC COMPLETE")
"""

    proot_cmd = (
        "/data/local/tmp/ancli/bin/proot "
        "-r /data/local/tmp/ancli/rootfs "
        "-b /data/adb/modules_update/ancli "
        "-b /data/adb/modules/ancli "
        "-b /data/adb/ksu "
        "-b /data/local/tmp/ancli "
        "/usr/bin/python3 -"
    )
    p = subprocess.Popen(
        ["adb", "-s", serial, "shell", "su", "-c", proot_cmd],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    out, err = p.communicate(input=sync_code)
    print(out)
    if err:
        print("Stderr:", err)

    print("[4] Running ancli repair to regenerate all wrappers...")
    res = run_adb(serial, ["shell", "su", "-c", "/data/adb/ksu/bin/ancli repair"])
    print(res.stdout)

    print(f"[5] Verifying installation & state on {serial}...")
    res = run_adb(serial, ["shell", "su", "-c", "/data/adb/ksu/bin/ancli --version"])
    print("Version:", res.stdout.strip())

    res = run_adb(serial, ["shell", "su", "-c", "/data/adb/ksu/bin/ancli status --json"])
    print("Status:", res.stdout.strip())

    res = run_adb(serial, ["shell", "su", "-c", "/data/adb/ksu/bin/ancli check-module --channel=stable --json"])
    print("Check stable:", res.stdout.strip())

    res = run_adb(serial, ["shell", "su", "-c", "/data/adb/ksu/bin/ancli check-module --channel=beta --json"])
    print("Check beta:", res.stdout.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
