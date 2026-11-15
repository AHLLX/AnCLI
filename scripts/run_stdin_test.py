import subprocess

SERIAL = "192.168.1.207:36657"
subprocess.run(["adb", "connect", SERIAL], capture_output=True)

test_script = """
echo "=== TEST A: GNU Bash ==="
/data/local/tmp/ancli/bin/proot -r /data/local/tmp/ancli/rootfs /bin/bash -c 'echo GNU_BASH_READY: $BASH_VERSION'

echo "=== TEST B: /proc readlink without assertion crash ==="
/data/local/tmp/ancli/bin/proot -r /data/local/tmp/ancli/rootfs -b /dev -b /proc -b /sys /usr/bin/python3 -c '
import os, glob
fds = glob.glob("/proc/self/fd/*")
links = [os.readlink(p) for p in fds if os.path.islink(p)]
print("PROC READLINK OK: count =", len(links))
'

echo "=== TEST C: LookPath for bash ==="
/data/local/tmp/ancli/bin/proot -r /data/local/tmp/ancli/rootfs -w /root /usr/bin/env PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/system/bin python3 -c '
import shutil
p = shutil.which("bash")
print("LookPath bash result:", p)
'

echo "=== TEST D: AGY entrypoint ==="
/data/adb/ksu/bin/agy --version
"""

p = subprocess.Popen(
    ["adb", "-s", SERIAL, "shell", "su"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
)
out, err = p.communicate(input=test_script)
print("--- STDOUT ---")
print(out)
print("--- STDERR ---")
print(err)
