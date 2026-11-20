"""Run a headless Antigravity CLI prompt on an ADB device."""
import argparse
import os
import shlex
import subprocess
import sys
import time


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run headless agy prompt on an ADB device.")
    parser.add_argument("--serial", default=os.environ.get("ANCLI_DEVICE_SERIAL", ""),
                        help="ADB device serial or host:port (or set ANCLI_DEVICE_SERIAL)")
    parser.add_argument("--prompt", default="你好",
                        help="Prompt text to pass to agy -p")
    args = parser.parse_args(argv)

    serial = (args.serial or "").strip()
    if not serial:
        print("[X] Missing device serial: pass --serial or set ANCLI_DEVICE_SERIAL", file=sys.stderr)
        return 2

    if ":" in serial:
        subprocess.run(["adb", "connect", serial], capture_output=True)

    cmd = [
        "adb", "-s", serial, "shell", "su", "-c",
        f"/data/adb/ksu/bin/agy -p {shlex.quote(args.prompt)} --dangerously-skip-permissions"
    ]

    print(f">>> Starting headless AGY on {serial} with prompt: {args.prompt!r} ...")
    start = time.time()
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out, err = p.communicate()
    elapsed = time.time() - start

    print(f"\n[Finished in {elapsed:.2f}s, Return Code: {p.returncode}]")
    print("=== STDOUT ===")
    print(out)
    print("=== STDERR ===")
    print(err)
    return p.returncode


if __name__ == "__main__":
    raise SystemExit(main())
