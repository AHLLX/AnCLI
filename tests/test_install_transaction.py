from pathlib import Path
import os
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BASH = r'C:\Program Files\Git\bin\bash.exe' if os.name == 'nt' else '/bin/bash'


def posix(path):
    value = str(path).replace('\\', '/')
    return '/' + value[0].lower() + value[2:] if len(value) > 1 and value[1] == ':' else value


def run_bash(script, env):
    return subprocess.run([BASH, '-c', script], capture_output=True, text=True, encoding='utf-8', env=env, timeout=15)


def test_new_install_failure_removes_only_owned_staging(tmp_path):
    base = tmp_path / 'ancli'; base.mkdir()
    other = base / '.rootfs-install-unrelated'; other.mkdir(); (other / 'data').write_text('keep')
    env = dict(os.environ, ANCLI_DIR=posix(base), ROOTFS=posix(base / 'rootfs'), HELPER=posix(ROOT / 'src/module/install-transaction.sh'))
    r = run_bash('''. "$HELPER"; ( ancli_begin_rootfs || exit 2; trap 'ancli_rollback_rootfs' EXIT; echo partial > "$ROOTFS/data"; echo partial > "$ANCLI_ROOTFS_ARCHIVE"; exit 42 ); test $? -eq 42''', env)
    assert r.returncode == 0, r.stderr
    assert not (base / 'rootfs').exists()
    assert [p.name for p in base.iterdir()] == ['.rootfs-install-unrelated']
    assert (other / 'data').read_text() == 'keep'


def test_existing_incomplete_directory_is_never_removed(tmp_path):
    base = tmp_path / 'ancli'; rootfs = base / 'rootfs'; rootfs.mkdir(parents=True)
    (rootfs / 'personal').write_text('keep')
    env = dict(os.environ, ANCLI_DIR=posix(base), ROOTFS=posix(rootfs), HELPER=posix(ROOT / 'src/module/install-transaction.sh'))
    r = run_bash('. "$HELPER"; ancli_begin_rootfs', env)
    assert r.returncode != 0 and (rootfs / 'personal').read_text() == 'keep'


def test_success_commits_new_rootfs_atomically(tmp_path):
    base = tmp_path / 'ancli'; base.mkdir()
    env = dict(os.environ, ANCLI_DIR=posix(base), ROOTFS=posix(base / 'rootfs'), HELPER=posix(ROOT / 'src/module/install-transaction.sh'))
    r = run_bash('. "$HELPER"; ancli_begin_rootfs || exit 1; echo ok > "$ROOTFS/data"; ancli_commit_rootfs || exit 1; ancli_rollback_rootfs', env)
    assert r.returncode == 0, r.stderr
    assert (base / 'rootfs/data').read_text().strip() == 'ok'
    assert not (base / 'rootfs/.ancli-transaction').exists()


def test_network_detects_system_proxy_and_probes_without_disclosing_credentials(tmp_path):
    fake = tmp_path / 'bin'; fake.mkdir()
    for name, contents in {
        'settings': '#!/bin/sh\necho 127.0.0.1:8888\n',
        'dumpsys': '#!/bin/sh\nexit 0\n',
        'curl': '#!/bin/sh\nexit 0\n',
    }.items():
        p = fake / name; p.write_text(contents, encoding='utf-8'); p.chmod(0o755)
    env = dict(os.environ, PATH=posix(fake)+':'+os.environ.get('PATH',''), HELPER=posix(ROOT / 'src/module/install-network.sh'))
    for key in ('http_proxy','https_proxy','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','all_proxy'): env.pop(key, None)
    r = run_bash('. "$HELPER"; curl() { return 0; }; ancli_install_network system ""; printf "proxy=%s\\n" "$https_proxy"', env)
    assert r.returncode == 0 and '代理连接测试成功' in r.stdout
    assert 'proxy=http://127.0.0.1:8888' in r.stdout


def test_network_rejects_unsafe_proxy_before_interpolation(tmp_path):
    env = dict(os.environ, HELPER=posix(ROOT / 'src/module/install-network.sh'))
    r = run_bash('''. "$HELPER"; ancli_install_network manual 'http://example.com:80;bad' ''', env)
    assert r.returncode != 0


def test_android_shell_scripts_contain_no_crlf_or_bom():
    for path in (ROOT / 'src/module').glob('*.sh'):
        data = path.read_bytes()
        assert b'\r' not in data and not data.startswith(b'\xef\xbb\xbf'), str(path)


def test_interrupted_owned_transaction_is_cleaned_without_touching_other_data(tmp_path):
    base = tmp_path / 'ancli'; base.mkdir()
    stale = base / '.rootfs-install-99999999'; stale.mkdir()
    (stale / '.ancli-transaction').write_text('ancli-rootfs-transaction-v1\n')
    (stale / 'partial').write_text('partial')
    archive = base / '.ubuntu-base-install-99999999.tar.gz'; archive.write_bytes(b'partial')
    unrelated = base / '.rootfs-install-99999998'; unrelated.mkdir()
    (unrelated / 'personal').write_text('keep')
    env = dict(os.environ, ANCLI_DIR=posix(base), ROOTFS=posix(base / 'rootfs'), HELPER=posix(ROOT / 'src/module/install-transaction.sh'))
    r = run_bash('. "$HELPER"; ancli_cleanup_stale_rootfs', env)
    assert r.returncode == 0, r.stderr
    assert not stale.exists() and not archive.exists()
    assert (unrelated / 'personal').read_text() == 'keep'


def test_empty_wrapper_placeholder_does_not_block_new_install(tmp_path):
    base = tmp_path / 'ancli'; rootfs = base / 'rootfs'
    (rootfs / 'root/.ancli-work').mkdir(parents=True)
    env = dict(os.environ, ANCLI_DIR=posix(base), ROOTFS=posix(rootfs), HELPER=posix(ROOT / 'src/module/install-transaction.sh'))
    r = run_bash('. "$HELPER"; ancli_begin_rootfs || exit 1; echo ok > "$ROOTFS/data"; ancli_commit_rootfs', env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (rootfs / 'data').read_text().strip() == 'ok'


def test_placeholder_with_user_data_is_preserved(tmp_path):
    base = tmp_path / 'ancli'; rootfs = base / 'rootfs'
    (rootfs / 'root/.ancli-work').mkdir(parents=True)
    marker = rootfs / 'root/.ancli-work/important'; marker.write_text('keep')
    env = dict(os.environ, ANCLI_DIR=posix(base), ROOTFS=posix(rootfs), HELPER=posix(ROOT / 'src/module/install-transaction.sh'))
    r = run_bash('. "$HELPER"; ancli_begin_rootfs', env)
    assert r.returncode != 0 and marker.read_text() == 'keep'


def test_host_container_commands_do_not_create_rootfs():
    script = (ROOT / 'src/module/system/bin/ancli').read_text(encoding='utf-8')
    assert script.index('mkdir -p "/data/local/tmp/ancli/rootfs$ANCLI_TUI_CWD"') > script.index('if [ ! -f /data/local/tmp/ancli/rootfs/bin/bash ]')


def test_stopped_task_cleans_only_its_marked_staging(tmp_path):
    base=tmp_path/'ancli'; base.mkdir()
    owned=base/'.rootfs-install-111'; owned.mkdir()
    (owned/'.ancli-transaction').write_text('ancli-rootfs-transaction-v1\n')
    (owned/'.ancli-task').write_text('123_4\n')
    (owned/'partial').write_text('partial')
    other=base/'.rootfs-install-222'; other.mkdir()
    (other/'.ancli-transaction').write_text('ancli-rootfs-transaction-v1\n')
    (other/'.ancli-task').write_text('123_5\n')
    (other/'personal').write_text('keep')
    env=dict(os.environ, ANCLI_DIR=posix(base), ROOTFS=posix(base/'rootfs'), HELPER=posix(ROOT/'src/module/install-transaction.sh'))
    r=run_bash('. "$HELPER"; ancli_cleanup_stopped_task 123_4',env)
    assert r.returncode==0 and not owned.exists() and (other/'personal').read_text()=='keep'


def test_vpn_probe_distinguishes_connected_from_not_vpn_and_denied(tmp_path):
    helper=posix(ROOT/'src/module/install-network.sh')
    env=dict(os.environ, HELPER=helper)
    command=". \"$HELPER\"; dumpsys() { printf '%s\\n' \"$VPN_SNAPSHOT\"; }; ancli_detect_vpn"
    samples=[('  NetworkAgentInfo{ ni{VPN CONNECTED} nc{[ Transports: VPN Capabilities: INTERNET ]}}','connected'),('  NetworkAgentInfo{ ni{WIFI CONNECTED} nc{[ Transports: WIFI Capabilities: NOT_VPN&INTERNET ]}}','not_detected'),('permission denied','unknown')]
    for snapshot, expected in samples:
        r=run_bash(command,dict(env,VPN_SNAPSHOT=snapshot))
        assert r.returncode==0 and r.stdout.strip()=='ANCLI_VPN='+expected, r.stdout + r.stderr
