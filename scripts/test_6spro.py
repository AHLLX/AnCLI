import subprocess
import shlex

SERIAL = "192.168.1.207:36657"
subprocess.run(["adb", "connect", SERIAL], capture_output=True)

def run(title, command):
    print(f"\n>>> {title}")
    cmd = ["adb", "-s", SERIAL, "shell", "su", "-c", command]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.stdout.strip():
        print("[STDOUT]:\n" + res.stdout.strip())
    if res.stderr.strip():
        print("[STDERR]:\n" + res.stderr.strip())
    return res.returncode

# 1. 验证 ancli doctor
run("TEST 1: ancli doctor --json", "/data/adb/ksu/bin/ancli doctor --json")

# 2. 验证 agy 启动
run("TEST 2: agy --version via host wrapper", "/data/adb/ksu/bin/agy --version")

# 3. 验证容器内 bash 专有语法
run("TEST 3: GNU Bash execution inside container", 
    "/data/local/tmp/ancli/bin/proot -r /data/local/tmp/ancli/rootfs /bin/bash -c 'echo BASH_VERSION=$BASH_VERSION'")

# 4. 验证 /proc 路径 readlink（之前崩溃的罪魁祸首）
proc_code = "import os, glob; fds = glob.glob('/proc/self/fd/*'); [os.readlink(p) for p in fds if os.path.islink(p)]; print('PROC READLINK SUCCESSFUL, NO ASSERTION CRASH!')"
run("TEST 4: /proc readlink without assertion crash", 
    f"/data/local/tmp/ancli/bin/proot -r /data/local/tmp/ancli/rootfs -b /dev -b /proc -b /sys python3 -c \"{proc_code}\"")

# 5. 验证 LookPath for bash 模拟
lookpath_code = "import shutil; p = shutil.which('bash'); print('LookPath(bash) result:', p); assert p is not None, 'bash not found in PATH'"
run("TEST 5: LookPath(bash) verification in guest PATH", 
    f"/data/local/tmp/ancli/bin/proot -r /data/local/tmp/ancli/rootfs -w /root /usr/bin/env PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/system/bin python3 -c \"{lookpath_code}\"")
