import subprocess
import time

SERIAL = "192.168.1.207:36657"
subprocess.run(["adb", "connect", SERIAL], capture_output=True)

cmd = [
    "adb", "-s", SERIAL, "shell", "su", "-c",
    "/data/adb/ksu/bin/agy -p '你好' --dangerously-skip-permissions"
]

print(">>> Starting headless AGY with prompt: '你好' ...")
start = time.time()
p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
out, err = p.communicate()
elapsed = time.time() - start

print(f"\n[Finished in {elapsed:.2f}s, Return Code: {p.returncode}]")
print("=== STDOUT ===")
print(out)
print("=== STDERR ===")
print(err)
