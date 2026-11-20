"""Deploy AnCLI build to specified devices via ADB and KernelSU."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


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


def run_adb(device_serial, cmd):
    full_cmd = ["adb", "-s", device_serial] + cmd
    res = subprocess.run(full_cmd, capture_output=True, text=True)
    return res.returncode, res.stdout, res.stderr


def deploy(name, serial, zip_path):
    zip_name = zip_path.name
    print(f"\n==========================================")
    print(f"Deploying {zip_name} to {name} ({serial})...")
    print(f"==========================================")

    # 1. Connect (network ADB targets only)
    if ":" in serial:
        subprocess.run(["adb", "connect", serial], capture_output=True)

    # 2. Push ZIP
    print(f"[*] Pushing {zip_name}...")
    rc, out, err = run_adb(serial, ["push", str(zip_path), f"/data/local/tmp/{zip_name}"])
    if rc != 0:
        print(f"[FAIL] Push zip: {err}")
        return False
    print("[OK] Zip pushed.")

    # 3. ksud module install
    print("[*] Running ksud module install...")
    rc, out, err = run_adb(serial, ["shell", "su", "-c", f"/data/adb/ksu/bin/ksud module install /data/local/tmp/{zip_name}"])
    print(out)
    if "Module installed successfully" not in out and "Installation complete" not in out:
        print(f"[WARN] Install output: {err}")

    # 4. Stage live hot-deploy files
    print("[*] Staging live hot-patch files...")
    stages = [
        ("src/ancli-core.py", "/data/local/tmp/ancli-core.py.new"),
        ("src/registry.json", "/data/local/tmp/ancli-registry.new"),
        ("src/container-profiles.json", "/data/local/tmp/container-profiles.json.new"),
        ("src/bash-host-compat", "/data/local/tmp/bash-host-compat.new"),
        ("src/module/ancli_env.sh", "/data/local/tmp/ancli-env.sh.new"),
        ("src/runtime-context/AGENTS.md", "/data/local/tmp/ancli-runtime-context.md.new"),
        ("src/module/module.prop", "/data/local/tmp/ancli-module.prop.new"),
        ("src/module/service.sh", "/data/local/tmp/service.sh.new"),
        ("src/module/customize.sh", "/data/local/tmp/customize.sh.new"),
        ("src/module/uninstall.sh", "/data/local/tmp/uninstall.sh.new"),
        ("src/module/hotinstall.sh", "/data/local/tmp/hotinstall.sh.new"),
        ("src/module/hot-install-policy.sh", "/data/local/tmp/hot-install-policy.sh.new"),
        ("src/module/dsh-web-control.sh", "/data/local/tmp/dsh-web-control.sh.new"),
        ("src/module/container-bootstrap.sh", "/data/local/tmp/container-bootstrap.sh.new"),
        ("src/module/container-profile-data.sh", "/data/local/tmp/container-profile-data.sh.new"),
        ("src/module/android-probe.sh", "/data/local/tmp/android-probe.sh.new"),
        ("src/module/container-mirror.sh", "/data/local/tmp/container-mirror.sh.new"),
        ("src/module/apt-key-compat.sh", "/data/local/tmp/apt-key-compat.sh.new"),
        ("src/module/install-network.sh", "/data/local/tmp/install-network.sh.new"),
        ("src/module/install-transaction.sh", "/data/local/tmp/install-transaction.sh.new"),
        ("src/module/node-setup.sh", "/data/local/tmp/node-setup.sh.new"),
        ("src/module/task-stop.sh", "/data/local/tmp/task-stop.sh.new"),
        ("src/module/ancli/catalog-cache.json", "/data/local/tmp/catalog-cache.json.new"),
        ("src/module/system/bin/ancli", "/data/local/tmp/ancli-launcher.new"),
        ("src/module/webroot/index.html", "/data/local/tmp/ancli-webui.html.new"),
        ("src/module/webroot/logo.png", "/data/local/tmp/ancli-webui-logo.png.new"),
    ]
    for rel_local, remote_f in stages:
        local_f = ROOT / rel_local
        if local_f.is_file():
            run_adb(serial, ["push", str(local_f), remote_f])
    icons_dir = ROOT / "src/module/webroot/assets/app-icons"
    if icons_dir.is_dir():
        run_adb(serial, ["push", str(icons_dir) + "/.", "/data/local/tmp/ancli-app-icons"])

    # 5. Run device_deploy_helper inside container
    print("[*] Executing device_deploy_helper via container python3...")
    helper_code = (ROOT / "scripts/device_deploy_helper.py").read_text(encoding="utf-8")

    proot_cmd = (
        "/data/local/tmp/ancli/bin/proot "
        "-r /data/local/tmp/ancli/rootfs "
        "-b /data/local/tmp "
        "-b /data/adb/modules/ancli "
        "-b /data/adb/ksu "
        "-b /data/adb/ap "
        "/usr/bin/env PATH=/usr/local/bin:/usr/bin:/bin python3 -"
    )
    p = subprocess.Popen(
        ["adb", "-s", serial, "shell", "su", "-c", proot_cmd],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    h_out, h_err = p.communicate(input=helper_code)
    print(h_out)
    if h_err:
        print(f"Helper stderr: {h_err}")

    # 6. Run ancli repair to ensure guest bash symlink and dual injection
    print(f"[*] Running ancli repair on {name}...")
    run_adb(serial, ["shell", "su", "-c", "/data/adb/ksu/bin/ancli repair"])

    # 7. Clean up old webui log files
    print(f"[*] Cleaning up stale WebUI temporary logs on {name}...")
    run_adb(serial, ["shell", "su", "-c", "rm -f /data/local/tmp/ancli/.webui_*.log*"])

    # 8. Verify with status
    print(f"[*] Verifying on {name}...")
    rc, out, err = run_adb(serial, ["shell", "su", "-c", "/data/adb/ksu/bin/ancli status --json"])
    print(f"Status: {out.strip()}")

    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description="Deploy AnCLI ZIP and live hot-patch files to target ADB devices.")
    parser.add_argument("--device", action="append", default=[],
                        help="Target device serial or name=serial (repeatable; or set ANCLI_DEVICES / ANCLI_DEVICE_SERIAL)")
    parser.add_argument("--zip", dest="zip_path", default=os.environ.get("ANCLI_ZIP", ""),
                        help="Module ZIP path (default: newest ancli-*.zip in repo root or ANCLI_ZIP)")
    args = parser.parse_args(argv)

    devices = parse_devices(args.device)
    if not devices:
        print("[X] No devices specified: pass --device or set ANCLI_DEVICES / ANCLI_DEVICE_SERIAL", file=sys.stderr)
        return 2

    zip_path = resolve_zip(args.zip_path)
    ok = True
    for name, serial in devices:
        ok = deploy(name, serial, zip_path) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
