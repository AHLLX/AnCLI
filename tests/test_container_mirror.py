from pathlib import Path
import os, subprocess
from test_install_transaction import BASH, posix, ROOT, run_bash

HELPER = ROOT / 'src/module/container-mirror.sh'

def env_for(base):
    return dict(os.environ, ANCLI_DIR=posix(base), HELPER=posix(HELPER))

def test_source_selection_and_architecture(tmp_path):
    env = env_for(tmp_path)
    r = run_bash('. "$HELPER"; ancli_mirror_rootfs_url ustc 24.04.5 amd64; ancli_mirror_apt_url official arm64; ! ancli_mirror_rootfs_url bad 24.04.5 amd64', env)
    assert r.returncode == 0, r.stderr
    assert 'mirrors.ustc.edu.cn/ubuntu-cdimage/' in r.stdout
    assert 'ports.ubuntu.com/ubuntu-ports' in r.stdout

def test_only_ubuntu_sources_are_rewritten_and_original_backed_up(tmp_path):
    sources = tmp_path / 'etc/apt/sources.list.d'; sources.mkdir(parents=True)
    ubuntu = sources / 'ubuntu.sources'; original = 'Types: deb\nURIs: http://old.example/ubuntu\nSuites: noble\n'
    ubuntu.write_text(original)
    extra = sources / 'custom.sources'; extra.write_text('URIs: http://user.example/repo\n')
    env = env_for(tmp_path)
    r = run_bash('. "$HELPER"; ancli_mirror_configure_apt "$ANCLI_DIR" http://new.example/ubuntu', env)
    assert r.returncode == 0, r.stderr
    assert 'URIs: http://new.example/ubuntu' in ubuntu.read_text()
    assert (sources / 'ubuntu.sources.ancli-backup').read_text() == original
    assert extra.read_text() == 'URIs: http://user.example/repo\n'

def test_speed_sample_is_bounded_and_cleaned_on_success_and_failure(tmp_path):
    sample = tmp_path / 'sample'; sample.write_bytes(b'\x1f\x8b' + b'a' * 1200000)
    env = env_for(tmp_path); env['SAMPLE'] = posix(sample)
    script = '. "$HELPER"; curl() { cat "$SAMPLE"; }; timeout() { shift; "$@"; }; ancli_mirror_speed_test tuna amd64'
    r = run_bash(script, env)
    assert r.returncode == 0, r.stderr
    assert 'ANCLI_SPEED_BYTES=1048576' in r.stdout
    assert not list(tmp_path.glob('.mirror-speed.*'))
    sample.write_text('not gzip')
    r = run_bash(script, env)
    assert r.returncode != 0 and 'ANCLI_SPEED_ERROR=download_failed' in r.stdout
    assert not list(tmp_path.glob('.mirror-speed.*'))
