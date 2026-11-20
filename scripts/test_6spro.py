"""Run basic device smoke checks via ADB against an AnCLI installation."""
import argparse
import os
import subprocess
import sys


def run(serial, title, command):
    print(f"\n>>> {title}")
    cmd = ["adb", "-s", serial, "shell", "su", "-c", command]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.stdout.strip():
        print("[STDOUT]:\n" + res.stdout.strip())
    if res.stderr.strip():
        print("[STDERR]:\n" + res.stderr.strip())
    return res.returncode


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run basic ADB smoke checks for AnCLI and agy.")
    parser.add_argument("--serial", default=os.environ.get("ANCLI_DEVICE_SERIAL", ""),
                        help="ADB device serial or host:port (or set ANCLI_DEVICE_SERIAL)")
    args = parser.parse_args(argv)

    serial = (args.serial or "").strip()
    if not serial:
        print("[X] Missing device serial: pass --serial or set ANCLI_DEVICE_SERIAL", file=sys.stderr)
        return 2

    if ":" in serial:
        subprocess.run(["adb", "connect", serial], capture_output=True)

    # 1. Verify ancli doctor
    run(serial, "TEST 1: ancli doctor --json", "/data/adb/ksu/bin/ancli doctor --json")

    # 2. Verify agy wrapper
    run(serial, "TEST 2: agy --version via host wrapper", "/data/adb/ksu/bin/agy --version")

    # 3. Verify GNU bash inside container
    run(serial, "TEST 3: GNU Bash execution inside container",
        "/data/local/tmp/ancli/bin/proot -r /data/local/tmp/ancli/rootfs /bin/bash -c 'echo BASH_VERSION=$BASH_VERSION'")

    # 4. Verify /proc readlink
    proc_code = "import os, glob; fds = glob.glob('/proc/self/fd/*'); [os.readlink(p) for p in fds if os.path.islink(p)]; print('PROC READLINK SUCCESSFUL, NO ASSERTION CRASH!')"
    run(serial, "TEST 4: /proc readlink without assertion crash",
        f"/data/local/tmp/ancli/bin/proot -r /data/local/tmp/ancli/rootfs -b /dev -b /proc -b /sys python3 -c \"{proc_code}\"")

    # 5. Verify LookPath for bash
    lookpath_code = "import shutil; p = shutil.which('bash'); print('LookPath(bash) result:', p); assert p is not None, 'bash not found in PATH'"
    run(serial, "TEST 5: LookPath(bash) verification in guest PATH",
        f"/data/local/tmp/ancli/bin/proot -r /data/local/tmp/ancli/rootfs -w /root /usr/bin/env PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/root/.local/bin:/system/bin python3 -c \"{lookpath_code}\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
