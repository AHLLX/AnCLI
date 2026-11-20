#!/usr/bin/env python3
"""Wrap a compressed Ubuntu rootfs in an AnCLI offline-import ZIP."""

import argparse
import hashlib
import json
import pathlib
import zipfile


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rootfs-tar", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    parser.add_argument("--architecture", required=True, choices=["arm64"])
    parser.add_argument("--profile", required=True, choices=["minimal", "standard", "developer", "full"])
    parser.add_argument("--agent-memory", required=True, choices=["enabled", "disabled", "unconfigured"])
    parser.add_argument("--module-version", required=True)
    args = parser.parse_args()

    if not args.rootfs_tar.is_file() or args.rootfs_tar.stat().st_size == 0:
        parser.error("rootfs archive is missing or empty")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload_name = "rootfs-arm64.tar.gz"
    manifest = {
        "format": "ancli-container-import",
        "schema_version": 1,
        "source_module_version": args.module_version,
        "required_importer_version": "v1.2.655-beta",
        "architecture": args.architecture,
        "ubuntu_version": "24.04",
        "profile": args.profile,
        "installed_apps_count": None,
        "agent_memory": args.agent_memory,
        "rootfs_archive": payload_name,
        "rootfs_sha256": sha256(args.rootfs_tar),
    }
    readme = (
        "AnCLI Ubuntu 24.04 arm64 container backup\n\n"
        f"Container profile: {args.profile}.\n"
        "This archive contains the complete current rootfs, including installed packages, "
        "Agent applications, user configuration, and secrets stored inside the container.\n"
        f"Global Agent memory setting: {args.agent_memory}.\n\n"
        "Keep this backup private. Import it on an arm64 device with AnCLI v1.2.655-beta or newer.\n"
        "Copy the ZIP into Android Download, then import it from AnCLI Settings > Reset > Container transfer.\n"
        "Import requires an uninstalled/empty container and replaces its complete rootfs.\n"
    )
    with zipfile.ZipFile(args.output, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as package:
        package.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        package.writestr("README.txt", readme)
        package.write(args.rootfs_tar, arcname=payload_name)
    with zipfile.ZipFile(args.output) as package:
        bad = package.testzip()
        if bad:
            raise SystemExit(f"ZIP verification failed at {bad}")
        if json.loads(package.read("manifest.json")) != manifest:
            raise SystemExit("manifest verification failed")
    print(f"[OK] Created {args.output} ({args.output.stat().st_size} bytes)")
    print(f"[OK] SHA-256: {manifest['rootfs_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
