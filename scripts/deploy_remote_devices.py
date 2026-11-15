"""Deploy AnCLI build to specified devices via ADB and KernelSU."""
import os
import subprocess
import sys

DEVICES = [
    ("k80p", "192.168.1.174:42111"),
    ("6spro", "192.168.1.207:45165"),
]

ZIP_NAME = "ancli-25-v1.2.613-beta.zip"

def run_adb(device_serial, cmd):
    full_cmd = ["adb", "-s", device_serial] + cmd
    res = subprocess.run(full_cmd, capture_output=True, text=True)
    return res.returncode, res.stdout, res.stderr

def deploy(name, serial):
    print(f"\n==========================================")
    print(f"Deploying to {name} ({serial})...")
    print(f"==========================================")

    # 1. Connect
    subprocess.run(["adb", "connect", serial], capture_output=True)

    # 2. Push ZIP
    print(f"[*] Pushing {ZIP_NAME}...")
    rc, out, err = run_adb(serial, ["push", ZIP_NAME, f"/data/local/tmp/{ZIP_NAME}"])
    if rc != 0:
        print(f"[FAIL] Push zip: {err}")
        return False
    print(f"[OK] Zip pushed.")

    # 3. ksud module install
    print(f"[*] Running ksud module install...")
    rc, out, err = run_adb(serial, ["shell", "su", "-c", f"/data/adb/ksu/bin/ksud module install /data/local/tmp/{ZIP_NAME}"])
    print(out)
    if "Module installed successfully" not in out and "Installation complete" not in out:
        print(f"[WARN] Install output: {err}")

    # 4. Stage live hot-deploy files
    print(f"[*] Staging live hot-patch files...")
    stages = [
        ("src/ancli-core.py", "/data/local/tmp/ancli-core.py.new"),
        ("src/registry.json", "/data/local/tmp/ancli-registry.new"),
        ("src/module/ancli_env.sh", "/data/local/tmp/ancli-env.sh.new"),
        ("src/runtime-context/AGENTS.md", "/data/local/tmp/ancli-runtime-context.md.new"),
        ("src/module/module.prop", "/data/local/tmp/ancli-module.prop.new"),
        ("src/module/system/bin/ancli", "/data/local/tmp/ancli-launcher.new"),
        ("src/module/webroot/index.html", "/data/local/tmp/ancli-webui.html.new"),
    ]
    for local_f, remote_f in stages:
        run_adb(serial, ["push", local_f, remote_f])

    # 5. Run device_deploy_helper inside container
    print(f"[*] Executing device_deploy_helper via container python3...")
    with open("scripts/device_deploy_helper.py", "r", encoding="utf-8") as f:
        helper_code = f.read()

    proot_cmd = (
        "/data/local/tmp/ancli/bin/proot "
        "-r /data/local/tmp/ancli/rootfs "
        "-b /data/local/tmp "
        "-b /data/adb/modules/ancli "
        "-b /data/adb/ksu "
        "-b /data/adb/ap "
        "python3 -"
    )
    p = subprocess.Popen(
        ["adb", "-s", serial, "shell", "su", "-c", proot_cmd],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    h_out, h_err = p.communicate(input=helper_code)
    print(h_out)
    if h_err:
        print(f"Helper stderr: {h_err}")

    # 6. Verify with ancli check-module and status
    print(f"[*] Verifying on {name}...")
    rc, out, err = run_adb(serial, ["shell", "su", "-c", "/data/local/tmp/ancli/bin/proot -r /data/local/tmp/ancli/rootfs python3 /data/local/tmp/ancli/bin/ancli-core.py check-module --json"])
    print(f"Check module: {out.strip()}")

    rc, out, err = run_adb(serial, ["shell", "su", "-c", "/data/local/tmp/ancli/bin/proot -r /data/local/tmp/ancli/rootfs python3 /data/local/tmp/ancli/bin/ancli-core.py status --json"])
    print(f"Status: {out.strip()}")

    return True

if __name__ == "__main__":
    for name, serial in DEVICES:
        deploy(name, serial)
