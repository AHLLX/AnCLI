"""Cut a release: fold the CHANGELOG's Unreleased sections into one section and
move every version number together.

Usage:
    python scripts/prepare_release.py                # re-sync to the current version (no CHANGELOG fold)
    python scripts/prepare_release.py v1.2.610       # multi-digit patch version, versionCode = current + 1
    python scripts/prepare_release.py v1.2.610 22    # new version with an explicit versionCode
    python scripts/prepare_release.py v1.2.651-beta 63

Why this script derives instead of carrying literals: the v1.2.4 cut left the
core's VERSION at 1.2.3 (the manager said v1.2.4 while `ancli --version` said
1.2.3), because the version lived in five places and only three were updated by
hand. Here every copy is written from one source:

    src/module/module.prop     version / versionCode   (managers read this)
    update.json                version / versionCode / KernelSU asset zipUrl (stable releases only)
    src/ancli-core.py          VERSION / VERSION_CODE  (`ancli --version`, --json payloads)
    src/registry.json          top-level "version"     (docs/bookkeeping; no code reads it)
    CHANGELOG.md               ## vX.Y.Z section
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PROP = ROOT / "src" / "module" / "module.prop"
CORE = ROOT / "src" / "ancli-core.py"
REGISTRY = ROOT / "src" / "registry.json"
MANIFEST = ROOT / "update.json"
CHANGELOG = ROOT / "CHANGELOG.md"

VERSION_RE = re.compile(r"v\d+\.\d+\.\d+(?:-(?:alpha|beta|rc|test)(?:\.\d+)?)?")
PRERELEASE_RE = re.compile(r"-(?:alpha|beta|rc|test)(?:\.\d+)?$")


def read(path):
    return path.read_text(encoding="utf-8")


def write(path, text):
    path.write_text(text, encoding="utf-8", newline="\n")


def sync_release(version_arg=None, code_arg=None, update_manifest=False, root=ROOT):
    prop_path = root / "src" / "module" / "module.prop"
    core_path = root / "src" / "ancli-core.py"
    registry_path = root / "src" / "registry.json"
    manifest_path = root / "update.json"
    changelog_path = root / "CHANGELOG.md"

    prop = read(prop_path)
    module_id = re.search(r"^id=(.+)$", prop, re.M).group(1).strip()
    current_version = re.search(r"^version=(.+)$", prop, re.M).group(1).strip()
    current_code = int(re.search(r"^versionCode=(\d+)$", prop, re.M).group(1))

    target_version = version_arg.strip() if version_arg else current_version
    if code_arg is not None:
        target_code = int(code_arg)
    elif target_version != current_version:
        target_code = current_code + 1          # versionCode must always increase
    else:
        target_code = current_code

    if not VERSION_RE.fullmatch(target_version):
        raise SystemExit(
            f"version must look like v1.2.601 or v1.2.651-beta, got {target_version!r}"
        )
    if target_version != current_version and target_code <= current_code:
        raise SystemExit(
            f"versionCode must increase: current {current_code}, requested {target_code}"
        )

    is_prerelease = bool(PRERELEASE_RE.search(target_version)) or ("-" in target_version)

    # 1) CHANGELOG: fold "## Unreleased — …" headings into one release section.
    #    Skipped (not an error) when there is nothing unreleased to fold, so a plain
    #    re-sync run stays usable.
    text = read(changelog_path)
    count = len(re.findall(r"^## Unreleased — ", text, re.M))
    if count:
        text = re.sub(r"^## Unreleased — (.+)$", r"### \1", text, flags=re.M)
        first = text.index("### ")
        text = text[:first] + f"## {target_version}\n\n" + text[first:]
        write(changelog_path, text)
        print(f"CHANGELOG: folded {count} Unreleased section(s) into ## {target_version}")
    elif f"## {target_version}" not in text:
        print(f"CHANGELOG: [warn] no Unreleased section and no '## {target_version}' heading — "
              f"add release notes before publishing")
    else:
        print(f"CHANGELOG: ## {target_version} already present; left untouched")

    # 2) module.prop
    prop = re.sub(r"^version=.*$", f"version={target_version}", prop, flags=re.M)
    prop = re.sub(r"^versionCode=.*$", f"versionCode={target_code}", prop, flags=re.M)
    write(prop_path, prop)
    print("module.prop:", [line for line in prop.splitlines()
                           if line.startswith(("version", "versionCode"))])

    # 3) update.json (the official action names assets as <id>-<versionCode>-<version>.zip)
    asset_name = f"{module_id}-{target_code}-{target_version}.zip"
    if is_prerelease and not update_manifest:
        print(f"[skip] update.json: kept on stable release for prerelease {target_version}")
    else:
        manifest = json.loads(read(manifest_path))
        manifest["version"] = target_version
        manifest["versionCode"] = target_code
        manifest["zipUrl"] = (f"https://github.com/AHLLX/AnCLI/releases/download/"
                              f"{target_version}/{asset_name}")
        write(manifest_path, json.dumps(manifest, indent=2) + "\n")
        print("update.json:", {k: manifest[k] for k in ("version", "versionCode", "zipUrl")})

    # 4) src/ancli-core.py VERSION & VERSION_CODE
    core = read(core_path)
    core, n_ver = re.subn(
        r'^VERSION = ".*"$', f'VERSION = "{target_version.lstrip("v")}"', core, flags=re.M
    )
    if n_ver != 1:
        raise SystemExit(f"expected exactly one VERSION = \"…\" line in {core_path.name}, found {n_ver}")
    core, n_code = re.subn(
        r'^VERSION_CODE = \d+$', f'VERSION_CODE = {target_code}', core, flags=re.M
    )
    if n_code != 1:
        raise SystemExit(f"expected exactly one VERSION_CODE = <int> line in {core_path.name}, found {n_code}")
    write(core_path, core)
    print(f"ancli-core.py: VERSION = \"{target_version.lstrip('v')}\", VERSION_CODE = {target_code}")

    # 5) src/registry.json top-level version (bookkeeping — keep it from drifting).
    registry_text = read(registry_path)
    registry_text, n = re.subn(r'^(\s*"version"\s*:\s*")[^"]*(")',
                               rf"\g<1>{target_version.lstrip('v')}\g<2>",
                               registry_text, count=1, flags=re.M)
    if n != 1:
        raise SystemExit(f"could not find the top-level version in {registry_path.name}")
    write(registry_path, registry_text)
    print(f"registry.json: version = {target_version.lstrip('v')}")

    print(f"\n[OK] {target_version} (versionCode {target_code}) is consistent across release files.")
    print(f"     KernelSU asset: {asset_name}")
    print("     Next: python scripts/build_module.py, commit, tag, push; the KernelSU workflow publishes the immutable release.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Sync release versions across AnCLI files.")
    parser.add_argument("version", nargs="?", default=None,
                        help="Target version tag (e.g. v1.2.651-beta or v1.2.652)")
    parser.add_argument("code", nargs="?", type=int, default=None,
                        help="Explicit integer versionCode")
    parser.add_argument("--update-manifest", action="store_true",
                        help="Also update update.json even when version is a prerelease")
    args = parser.parse_args(argv)
    sync_release(args.version, args.code, update_manifest=args.update_manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
