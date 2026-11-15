import subprocess
import sys
import os

SERIAL = "192.168.1.207:36657"
ZIP_NAME = "ancli-25-v1.2.613-beta.zip"

def run_adb(cmd):
    full = ["adb", "-s", SERIAL] + cmd
    return subprocess.run(full, capture_output=True, text=True)

print("=== Connecting to 6spro ===")
subprocess.run(["adb", "connect", SERIAL], capture_output=True)

print("[1] Pushing ZIP to device...")
res = run_adb(["push", ZIP_NAME, f"/data/local/tmp/{ZIP_NAME}"])
print(res.stdout, res.stderr)
run_adb(["push", ZIP_NAME, f"/sdcard/Download/{ZIP_NAME}"])

print("[2] Running ksud module install...")
res = run_adb(["shell", "su", "-c", f"/data/adb/ksu/bin/ksud module install /data/local/tmp/{ZIP_NAME}"])
print(res.stdout)

print("[3] Live syncing updated module files via container python3...")
sync_code = """
import os, shutil

pairs = [
    ("/data/adb/modules_update/ancli/module.prop", "/data/adb/modules/ancli/module.prop"),
    ("/data/adb/modules_update/ancli/webroot/index.html", "/data/adb/modules/ancli/webroot/index.html"),
    ("/data/adb/modules_update/ancli/webroot/logo.png", "/data/adb/modules/ancli/webroot/logo.png"),
    ("/data/adb/modules_update/ancli/ancli/ancli-core.py", "/data/adb/modules/ancli/ancli/ancli-core.py"),
    ("/data/adb/modules_update/ancli/ancli/ancli-core.py", "/data/local/tmp/ancli/bin/ancli-core.py"),
    ("/data/adb/modules_update/ancli/ancli/registry.json", "/data/adb/modules/ancli/ancli/registry.json"),
    ("/data/adb/modules_update/ancli/ancli/registry.json", "/data/local/tmp/ancli/registry.json"),
    ("/data/adb/modules_update/ancli/ancli/ancli_env.sh", "/data/adb/modules/ancli/ancli/ancli_env.sh"),
    ("/data/adb/modules_update/ancli/ancli/ancli_env.sh", "/data/local/tmp/ancli/bin/ancli_env.sh"),
    ("/data/adb/modules_update/ancli/ancli/bash-host-compat", "/data/adb/modules/ancli/bash-host-compat"),
    ("/data/adb/modules_update/ancli/ancli/bash-host-compat", "/data/adb/ksu/bin/bash"),
    ("/data/adb/modules_update/ancli/ancli/bash-host-compat", "/data/adb/modules/ancli/system/bin/bash"),
    ("/data/adb/modules_update/ancli/ancli/runtime-context/AGENTS.md", "/data/local/tmp/ancli/runtime-context/AGENTS.md"),
]

for src, dst in pairs:
    if os.path.exists(src):
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if os.path.exists(dst):
                os.remove(dst)
            shutil.copyfile(src, dst)
            os.chmod(dst, 0o755 if "bin" in dst or "bash" in dst or "py" in dst or "sh" in dst else 0o644)
            print("OK synced:", dst)
        except Exception as e:
            print("FAIL sync:", dst, e)

# 确保容器内的 /usr/local/bin/bash 软链接存在
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
    ["adb", "-s", SERIAL, "shell", "su", "-c", proot_cmd],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
)
out, err = p.communicate(input=sync_code)
print(out)
if err:
    print("Stderr:", err)

print("[4] Running ancli repair to regenerate all wrappers...")
res = run_adb(["shell", "su", "-c", "/data/adb/ksu/bin/ancli repair"])
print(res.stdout)

print("[5] Verifying installation & state on 6spro...")
res = run_adb(["shell", "su", "-c", "/data/adb/ksu/bin/ancli --version"])
print("Version:", res.stdout.strip())

res = run_adb(["shell", "su", "-c", "/data/adb/ksu/bin/ancli status --json"])
print("Status:", res.stdout.strip())

res = run_adb(["shell", "su", "-c", "/data/adb/ksu/bin/ancli check-module --channel=stable --json"])
print("Check stable:", res.stdout.strip())

res = run_adb(["shell", "su", "-c", "/data/adb/ksu/bin/ancli check-module --channel=beta --json"])
print("Check beta:", res.stdout.strip())
