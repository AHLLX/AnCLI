#!/usr/bin/env python3
"""Package an exported AnCLI Ubuntu rootfs for offline import from WebUI."""

import argparse
import hashlib
import io
import json
import pathlib
import tarfile
import zipfile


def read_rootfs_facts(rootfs_tar: pathlib.Path) -> dict:
    with tarfile.open(rootfs_tar, "r:gz") as archive:
        members = archive.getmembers()
        names = {member.name.lstrip("./") for member in members}
        for member in members:
            path = pathlib.PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise SystemExit(f"unsafe path in rootfs tar: {member.name}")

        required = {"etc/os-release", "usr/bin/bash", "root/.ancli-config.json"}
        missing = sorted(required - names)
        if missing:
            raise SystemExit("rootfs tar is missing required files: " + ", ".join(missing))

        os_release = archive.extractfile(next(m for m in members if m.name.lstrip("./") == "etc/os-release"))
        if os_release is None:
            raise SystemExit("cannot read etc/os-release from rootfs tar")
        release_text = os_release.read().decode("utf-8", errors="replace")
        release = {}
        for line in release_text.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                release[key] = value.strip().strip('"')
        if release.get("ID") != "ubuntu" or release.get("VERSION_ID") != "24.04":
            raise SystemExit("expected Ubuntu 24.04 rootfs")

        config_member = next(m for m in members if m.name.lstrip("./") == "root/.ancli-config.json")
        config_file = archive.extractfile(config_member)
        if config_file is None:
            raise SystemExit("cannot read root/.ancli-config.json from rootfs tar")
        config = json.load(io.TextIOWrapper(config_file, encoding="utf-8"))
        if config.get("agent_memory", "unconfigured") != "unconfigured":
            raise SystemExit("rootfs has configured global Agent memory; refusing to package it")
        memory_paths = {
            "root/.agents/AGENTS.md",
            "root/.codex/AGENTS.md",
            "root/.gemini/AGENTS.md",
        }
        present = sorted(memory_paths & names)
        if present:
            raise SystemExit("global memory files exist in rootfs: " + ", ".join(present))

        return {"ubuntu_version": release["VERSION_ID"], "agent_memory": "unconfigured"}


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rootfs-tar", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    parser.add_argument("--source-module-version", required=True)
    parser.add_argument("--profile", choices=["full"], default="full")
    parser.add_argument("--installed-apps-count", type=int, default=0)
    args = parser.parse_args()

    if args.installed_apps_count != 0:
        raise SystemExit("this importer bundle is intentionally limited to a container with no Agent apps")
    if not args.rootfs_tar.is_file():
        raise SystemExit(f"rootfs archive not found: {args.rootfs_tar}")
    args.output.parent.mkdir(parents=True, exist_ok=True)

    facts = read_rootfs_facts(args.rootfs_tar)
    rootfs_sha = sha256_file(args.rootfs_tar)
    manifest = {
        "format": "ancli-container-import",
        "schema_version": 1,
        "source_module_version": args.source_module_version,
        "required_importer_version": "v1.2.654-beta",
        "architecture": "arm64",
        "ubuntu_version": facts["ubuntu_version"],
        "profile": args.profile,
        "installed_apps_count": 0,
        "agent_memory": "unconfigured",
        "rootfs_archive": "rootfs-arm64.tar.gz",
        "rootfs_sha256": rootfs_sha,
    }
    instructions = (
        "AnCLI Ubuntu 24.04 ARM64 Full Development Environment offline import package\n"
        "\n"
        "Contents: Ubuntu 24.04 arm64, full development environment profile.\n"
        "No Agent applications are installed. Global Agent memory is unconfigured.\n"
        "\n"
        "Copy this ZIP to /sdcard/Download on an Android device with AnCLI v1.2.654-beta or newer.\n"
        "In AnCLI WebUI, use Offline container import and enter the ZIP path.\n"
        "The import is arm64-only and requires an empty/uninstalled container.\n"
    )
    with zipfile.ZipFile(args.output, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as package:
        package.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        package.writestr("README.txt", instructions)
        package.write(args.rootfs_tar, arcname="rootfs-arm64.tar.gz")

    with zipfile.ZipFile(args.output, "r") as package:
        bad = package.testzip()
        if bad:
            raise SystemExit(f"ZIP verification failed at {bad}")
        zipped_manifest = json.loads(package.read("manifest.json"))
        if zipped_manifest != manifest:
            raise SystemExit("manifest verification failed")

    print(f"Wrote {args.output} ({args.output.stat().st_size} bytes)")
    print(f"Rootfs SHA-256: {rootfs_sha}")
    print(f"Format: {manifest['format']} v{manifest['schema_version']}; arm64; Ubuntu {facts['ubuntu_version']}; profile={args.profile}")


if __name__ == "__main__":
    main()
