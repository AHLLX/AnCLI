"""Build the flashable module ZIP without requiring `zip` (Windows-friendly).

Equivalent to build.sh: sync the generated sources into src/module/ancli/,
then archive src/module/ with the module root at the ZIP root. Entries use the
executable mode expected by Android root managers.

Usage: python scripts/build_module.py [output-name.zip]
"""
import os
import sys
import json
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE_DIR = os.path.join(ROOT, "src", "module")
SYNC = [
    ("src/ancli-core.py", "ancli/ancli-core.py"),
    ("src/registry.json", "ancli/registry.json"),
    ("src/container-profiles.json", "ancli/container-profiles.json"),
    ("src/module/ancli_env.sh", "ancli/ancli_env.sh"),
    ("src/bash-host-compat", "ancli/bash-host-compat"),
    ("src/runtime-context/AGENTS.md", "ancli/runtime-context/AGENTS.md"),
]
SKIP_DIRS = {".git", "__MACOSX", "__pycache__"}
SKIP_FILES = {".DS_Store"}


def module_metadata():
    values = {}
    with open(os.path.join(MODULE_DIR, "module.prop"), encoding="utf-8") as fh:
        for line in fh:
            if "=" in line:
                key, value = line.rstrip("\n").split("=", 1)
                values[key] = value.strip()
    for key in ("id", "version", "versionCode"):
        if not values.get(key):
            raise SystemExit(f"module.prop has no {key}=")
    return values


def main():
    metadata = module_metadata()
    version = metadata["version"]
    output = (sys.argv[1] if len(sys.argv) > 1 else
              f"{metadata['id']}-{metadata['versionCode']}-{version}.zip")
    output_path = os.path.join(ROOT, output)

    print("> Syncing source files...")
    for source, target in SYNC:
        src = os.path.join(ROOT, source)
        dst = os.path.join(MODULE_DIR, target)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(src, "rb") as fh:
            data = fh.read()
        with open(dst, "wb") as fh:
            fh.write(data)
        print(f"    {source} -> src/module/{target} ({len(data)} bytes)")

    print("> Generating offline catalog-cache.json...")
    with open(os.path.join(ROOT, "src", "registry.json"), "r", encoding="utf-8") as f:
        reg = json.load(f)
    apps = []
    for aid, app in (reg.get("apps") or {}).items():
        apps.append({
            "id": aid,
            "name": app.get("name", aid),
            "description": app.get("description", ""),
            "executable": app.get("executable", aid),
            "installed": False,
            "active": False,
            "installed_version": None,
            "version_verified": False,
            "cloud_version": app.get("version", "unknown"),
            "cloud_source": "registry",
            "cloud_checked": False,
            "cloud_error": None,
            "update_available": False,
            "update_cmd": app.get("update_cmd", ""),
            "launch": app.get("launch") if isinstance(app.get("launch"), dict) else None,
            "required_env_vars": app.get("env_vars", []),
            "optional_env_vars": app.get("optional_env_vars", []),
            "configured_keys": []
        })
    cat_path = os.path.join(MODULE_DIR, "ancli", "catalog-cache.json")
    with open(cat_path, "w", encoding="utf-8") as f:
        json.dump(apps, f, ensure_ascii=False, indent=2)
    print(f"    Generated {cat_path} with {len(apps)} apps")

    print("> Building module ZIP...")
    entries = []
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for base, dirs, files in os.walk(MODULE_DIR):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in sorted(files):
                if name in SKIP_FILES or name.endswith(".pyc"):
                    continue
                full = os.path.join(base, name)
                rel = os.path.relpath(full, MODULE_DIR).replace(os.sep, "/")
                info = zipfile.ZipInfo(rel)
                # Root managers unpack module entries with executable mode.
                info.external_attr = (0o100755 << 16)
                info.compress_type = zipfile.ZIP_DEFLATED
                with open(full, "rb") as fh:
                    zf.writestr(info, fh.read())
                entries.append(rel)

    print(f"> Wrote {output} ({os.path.getsize(output_path)} bytes, {len(entries)} entries)")
    if "module.prop" not in entries:
        raise SystemExit("module.prop must sit at the ZIP root — refusing this build")
    for rel in sorted(entries):
        print(f"    {rel}")


if __name__ == "__main__":
    main()
