import subprocess

devices = [
    ("k80p", "192.168.1.174:42111"),
    ("6spro", "192.168.1.207:45165"),
]

code = """
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
    ("/data/adb/modules_update/ancli/ancli/runtime-context/AGENTS.md", "/data/local/tmp/ancli/runtime-context/AGENTS.md"),
]

for src, dst in pairs:
    if os.path.exists(src):
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if os.path.exists(dst):
                os.remove(dst)
            shutil.copyfile(src, dst)
            print("OK synced:", dst)
        except Exception as e:
            print("FAIL sync:", dst, e)
    else:
        print("SKIP not found:", src)

print("ALL SYNC DONE")
"""

for name, serial in devices:
    print(f"=== Syncing live module on {name} ({serial}) ===")
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
    out, err = p.communicate(input=code)
    print("Stdout:", out)
    if err:
        print("Stderr:", err)
