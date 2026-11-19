"""Device-side deploy helper: run INSIDE the AnCLI container (python3 -).

Copies the freshly pushed files into the live AnCLI tree and reports what the
KernelSU mount namespace allows. Used only during development verification.
"""
import os
import shutil
import sys

ANCLI = "/data/local/tmp/ancli"
STAGE = "/data/local/tmp"

jobs = [
    (f"{STAGE}/ancli-core.py.new", f"{ANCLI}/bin/ancli-core.py", 0o755),
    (f"{STAGE}/ancli-registry.new", f"{ANCLI}/registry.json", 0o644),
    (f"{STAGE}/ancli-registry.new", f"{ANCLI}/bin/registry.json", 0o644),
    (f"{STAGE}/ancli-env.sh.new", f"{ANCLI}/bin/ancli_env.sh", 0o755),
    (f"{STAGE}/ancli-runtime-context.md.new", f"{ANCLI}/runtime-context/AGENTS.md", 0o644),
    # Module copies: what a reflash would deploy, so the on-device module stays
    # consistent with the running core (overwritten by the next flash anyway).
    (f"{STAGE}/ancli-core.py.new", "/data/adb/modules/ancli/ancli/ancli-core.py", 0o755),
    (f"{STAGE}/ancli-registry.new", "/data/adb/modules/ancli/ancli/registry.json", 0o644),
    (f"{STAGE}/ancli-env.sh.new", "/data/adb/modules/ancli/ancli/ancli_env.sh", 0o755),
    (f"{STAGE}/ancli-runtime-context.md.new", "/data/adb/modules/ancli/ancli/runtime-context/AGENTS.md", 0o644),
    (f"{STAGE}/ancli-module.prop.new", "/data/adb/modules/ancli/module.prop", 0o644),
    # Optional, explicitly staged migration of the user's existing default-cwd
    # memory. It is never created by the normal build/deploy path.
    (f"{STAGE}/ancli-sdcard-agents.md.new", "/sdcard/AGENTS.md", 0o660),
    # Boot script: only takes effect on the next boot, but keep the module copy
    # consistent with the repository (a flash would deploy the same file).
    (f"{STAGE}/service.sh.new", "/data/adb/modules/ancli/service.sh", 0o755),
    # DSH WebUI process controller: its host-side PATH must not be assumed to
    # contain the KernelSU/APatch instant-access wrapper directories.
    (f"{STAGE}/dsh-web-control.sh.new", f"{ANCLI}/dsh-web-control.sh", 0o755),
]

for src, dst, mode in jobs:
    if not os.path.exists(src):
        print(f"SKIP  {dst}: {src} not staged")
        continue
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
        os.chmod(dst, mode)
        print(f"OK    {dst} ({os.path.getsize(dst)} bytes)")
    except Exception as e:
        print(f"FAIL  {dst}: {e}")

for stale in (".webui_last.log", ".webui_last.log.exit"):
    path = f"{ANCLI}/{stale}"
    if os.path.exists(path):
        try:
            os.remove(path)
            print(f"OK    removed {path}")
        except Exception as e:
            print(f"FAIL  remove {path}: {e}")

# Module directory: is it writable at all (mount-level read-only check)?
probe = "/data/adb/modules/ancli/.write_probe"
try:
    with open(probe, "w") as fh:
        fh.write("x")
    os.remove(probe)
    print("MODULE-DIR  writable")
except Exception as e:
    print(f"MODULE-DIR  read-only ({e})")

# Static launcher: carries the same default-cwd logic as the generated wrappers.
launcher = f"{STAGE}/ancli-launcher.new"
if os.path.exists(launcher):
    for dst in ("/data/adb/modules/ancli/system/bin/ancli",
                "/data/adb/ksu/bin/ancli",
                "/data/adb/ap/bin/ancli"):
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(launcher, dst)
            os.chmod(dst, 0o755)
            print(f"OK    {dst} ({os.path.getsize(dst)} bytes)")
        except Exception as e:
            print(f"SKIP  {dst}: {e}")

# Can the WebUI page be replaced from here?
webui_src = f"{STAGE}/ancli-webui.html.new"
webui_dst = "/data/adb/modules/ancli/webroot/index.html"
if os.path.exists(webui_src):
    try:
        shutil.copyfile(webui_src, webui_dst)
        print(f"OK    {webui_dst} replaced")
    except Exception as e:
        print(f"FAIL  {webui_dst}: {e}")

# 2026-11-16：WebUI 官方图标随页面一起热部署，失败时页面仍有字母徽章回退。
icons_src = f"{STAGE}/ancli-app-icons"
icons_dst = "/data/adb/modules/ancli/webroot/assets/app-icons"
if os.path.isdir(icons_src):
    try:
        # 2026-11-16：adb push 目录在目标不存在时会多套一层 app-icons/；
        # 兼容平铺目录和这个嵌套目录，确保新图标不会只留在临时目录里。
        nested = os.path.join(icons_src, "app-icons")
        source_root = nested if os.path.isdir(nested) else icons_src
        for root, _dirs, files in os.walk(source_root):
            for name in files:
                src = os.path.join(root, name)
                rel = os.path.relpath(src, source_root)
                dst = os.path.join(icons_dst, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(src, dst)
                os.chmod(dst, 0o644)
                print(f"OK    {dst} ({os.path.getsize(dst)} bytes)")
    except Exception as e:
        print(f"FAIL  {icons_dst}: {e}")

sys.exit(0)
