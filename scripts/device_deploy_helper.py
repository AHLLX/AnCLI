"""Device-side deploy helper: run INSIDE the AnCLI container (python3 -).

Copies the freshly pushed files into the live AnCLI tree and reports what the
KernelSU mount namespace allows. Used only during development verification.
"""
import os
import shutil
import sys

ANCLI = "/data/local/tmp/ancli"
MOD = "/data/adb/modules/ancli"
STAGE = "/data/local/tmp"


def copy_replace(src, dst, mode):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.lexists(dst):
        os.remove(dst)
    shutil.copyfile(src, dst)
    os.chmod(dst, mode)


jobs = [
    (f"{STAGE}/ancli-core.py.new", f"{ANCLI}/bin/ancli-core.py", 0o755),
    (f"{STAGE}/ancli-registry.new", f"{ANCLI}/registry.json", 0o644),
    (f"{STAGE}/ancli-registry.new", f"{ANCLI}/bin/registry.json", 0o644),
    (f"{STAGE}/ancli-env.sh.new", f"{ANCLI}/bin/ancli_env.sh", 0o755),
    (f"{STAGE}/ancli-runtime-context.md.new", f"{ANCLI}/runtime-context/AGENTS.md", 0o644),
    (f"{STAGE}/container-profiles.json.new", f"{ANCLI}/container-profiles.json", 0o644),
    (f"{STAGE}/catalog-cache.json.new", f"{ANCLI}/catalog-cache.json", 0o644),
    (f"{STAGE}/container-profile-data.sh.new", f"{ANCLI}/container-profile-data.sh", 0o644),
    (f"{STAGE}/container-bootstrap.sh.new", f"{ANCLI}/container-bootstrap.sh", 0o755),
    (f"{STAGE}/android-probe.sh.new", f"{ANCLI}/android-probe.sh", 0o755),
    (f"{STAGE}/container-mirror.sh.new", f"{ANCLI}/container-mirror.sh", 0o755),
    (f"{STAGE}/apt-key-compat.sh.new", f"{ANCLI}/apt-key-compat.sh", 0o755),
    (f"{STAGE}/install-network.sh.new", f"{ANCLI}/install-network.sh", 0o755),
    (f"{STAGE}/install-transaction.sh.new", f"{ANCLI}/install-transaction.sh", 0o755),
    (f"{STAGE}/node-setup.sh.new", f"{ANCLI}/node-setup.sh", 0o755),
    (f"{STAGE}/task-stop.sh.new", f"{ANCLI}/task-stop.sh", 0o755),
    (f"{STAGE}/dsh-web-control.sh.new", f"{ANCLI}/dsh-web-control.sh", 0o755),
    (f"{STAGE}/bash-host-compat.new", f"{ANCLI}/bash-host-compat", 0o755),
    # Module copies: what a reflash would deploy, so the on-device module stays
    # consistent with the running core (overwritten by the next flash anyway).
    (f"{STAGE}/ancli-core.py.new", f"{MOD}/ancli/ancli-core.py", 0o755),
    (f"{STAGE}/ancli-registry.new", f"{MOD}/ancli/registry.json", 0o644),
    (f"{STAGE}/ancli-env.sh.new", f"{MOD}/ancli/ancli_env.sh", 0o755),
    (f"{STAGE}/ancli-env.sh.new", f"{MOD}/ancli_env.sh", 0o755),
    (f"{STAGE}/ancli-runtime-context.md.new", f"{MOD}/ancli/runtime-context/AGENTS.md", 0o644),
    (f"{STAGE}/container-profiles.json.new", f"{MOD}/ancli/container-profiles.json", 0o644),
    (f"{STAGE}/catalog-cache.json.new", f"{MOD}/ancli/catalog-cache.json", 0o644),
    (f"{STAGE}/container-profile-data.sh.new", f"{MOD}/container-profile-data.sh", 0o644),
    (f"{STAGE}/container-bootstrap.sh.new", f"{MOD}/container-bootstrap.sh", 0o755),
    (f"{STAGE}/android-probe.sh.new", f"{MOD}/android-probe.sh", 0o755),
    (f"{STAGE}/container-mirror.sh.new", f"{MOD}/container-mirror.sh", 0o755),
    (f"{STAGE}/apt-key-compat.sh.new", f"{MOD}/apt-key-compat.sh", 0o755),
    (f"{STAGE}/install-network.sh.new", f"{MOD}/install-network.sh", 0o755),
    (f"{STAGE}/install-transaction.sh.new", f"{MOD}/install-transaction.sh", 0o755),
    (f"{STAGE}/node-setup.sh.new", f"{MOD}/node-setup.sh", 0o755),
    (f"{STAGE}/task-stop.sh.new", f"{MOD}/task-stop.sh", 0o755),
    (f"{STAGE}/dsh-web-control.sh.new", f"{MOD}/dsh-web-control.sh", 0o755),
    (f"{STAGE}/bash-host-compat.new", f"{MOD}/ancli/bash-host-compat", 0o755),
    (f"{STAGE}/customize.sh.new", f"{MOD}/customize.sh", 0o755),
    (f"{STAGE}/uninstall.sh.new", f"{MOD}/uninstall.sh", 0o755),
    (f"{STAGE}/hotinstall.sh.new", f"{MOD}/hotinstall.sh", 0o755),
    (f"{STAGE}/hot-install-policy.sh.new", f"{MOD}/hot-install-policy.sh", 0o755),
    (f"{STAGE}/ancli-module.prop.new", f"{MOD}/module.prop", 0o644),
    (f"{STAGE}/service.sh.new", f"{MOD}/service.sh", 0o755),
    (f"{STAGE}/ancli-webui-logo.png.new", f"{MOD}/webroot/logo.png", 0o644),
    # Optional, explicitly staged migration of the user's existing default-cwd
    # memory. It is never created by the normal build/deploy path.
    (f"{STAGE}/ancli-sdcard-agents.md.new", "/sdcard/AGENTS.md", 0o660),
]

for src, dst, mode in jobs:
    if not os.path.exists(src):
        print(f"SKIP  {dst}: {src} not staged")
        continue
    try:
        copy_replace(src, dst, mode)
        print(f"OK    {dst} ({os.path.getsize(dst)} bytes)")
    except Exception as e:
        print(f"FAIL  {dst}: {e}")

# Conditional bash-host-compat deployment: never overwrite a user-installed bash.
bash_compat_src = f"{STAGE}/bash-host-compat.new"
if os.path.exists(bash_compat_src):
    for dst in (f"{MOD}/system/bin/bash", "/data/adb/ksu/bin/bash", "/data/adb/ap/bin/bash"):
        parent = os.path.dirname(dst)
        if not os.path.isdir(parent) and dst != f"{MOD}/system/bin/bash":
            print(f"SKIP  {dst}: parent dir absent")
            continue
        replace = not os.path.lexists(dst)
        if not replace:
            try:
                with open(dst, "r", encoding="utf-8", errors="replace") as fh:
                    head = fh.read(512)
                replace = ("AnCLI AGY host bash compatibility" in head or "AnCLI proot shim" in head)
            except OSError:
                replace = False
        if not replace:
            print(f"KEEP  {dst}: existing non-AnCLI bash preserved")
            continue
        try:
            copy_replace(bash_compat_src, dst, 0o755)
            print(f"OK    {dst} ({os.path.getsize(dst)} bytes)")
        except Exception as e:
            print(f"SKIP  {dst}: {e}")

for stale in (".webui_last.log", ".webui_last.log.exit"):
    path = f"{ANCLI}/{stale}"
    if os.path.exists(path):
        try:
            os.remove(path)
            print(f"OK    removed {path}")
        except Exception as e:
            print(f"FAIL  remove {path}: {e}")

# Module directory: is it writable at all (mount-level read-only check)?
probe = f"{MOD}/.write_probe"
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
    for dst in (f"{MOD}/system/bin/ancli",
                "/data/adb/ksu/bin/ancli",
                "/data/adb/ap/bin/ancli"):
        try:
            copy_replace(launcher, dst, 0o755)
            print(f"OK    {dst} ({os.path.getsize(dst)} bytes)")
        except Exception as e:
            print(f"SKIP  {dst}: {e}")

# Can the WebUI page be replaced from here?
webui_src = f"{STAGE}/ancli-webui.html.new"
webui_dst = f"{MOD}/webroot/index.html"
if os.path.exists(webui_src):
    try:
        copy_replace(webui_src, webui_dst, 0o644)
        print(f"OK    {webui_dst} replaced")
    except Exception as e:
        print(f"FAIL  {webui_dst}: {e}")

# 2026-11-16：WebUI 官方图标随页面一起热部署，失败时页面仍有字母徽章回退。
icons_src = f"{STAGE}/ancli-app-icons"
icons_dst = f"{MOD}/webroot/assets/app-icons"
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
                copy_replace(src, dst, 0o644)
                print(f"OK    {dst} ({os.path.getsize(dst)} bytes)")
    except Exception as e:
        print(f"FAIL  {icons_dst}: {e}")

sys.exit(0)
