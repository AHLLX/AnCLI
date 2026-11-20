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
    sample = tmp_path / 'sample'; sample.write_bytes(b'a' * 1200000)
    env = env_for(tmp_path); env['SAMPLE'] = posix(sample); env['ANCLI_SPEED_BYTES'] = '1048576'
    script = '''. "$HELPER"
curl() {
    _out=""
    while [ "$#" -gt 0 ]; do
        case "$1" in
            -o) _out="$2"; shift 2 ;;
            *) shift ;;
        esac
    done
    [ -n "$_out" ] && cp "$SAMPLE" "$_out"
}
timeout() { shift; "$@"; }
ancli_mirror_speed_test tuna amd64'''
    r = run_bash(script, env)
    assert r.returncode == 0, r.stderr
    assert 'ANCLI_SPEED_MIRROR=tuna' in r.stdout
    assert 'ANCLI_SPEED_BYTES=1048576' in r.stdout
    assert not list(tmp_path.glob('.mirror-speed.*'))
    sample.write_text('short')
    r = run_bash(script, env)
    assert r.returncode != 0 and 'ANCLI_SPEED_ERROR=download_failed' in r.stdout
    assert not list(tmp_path.glob('.mirror-speed.*'))
    r_bad = run_bash('. "$HELPER"; ! ancli_mirror_speed_test bad amd64 && ! ancli_mirror_speed_test tuna armv7', env)
    assert r_bad.returncode == 0, r_bad.stderr

    # ANCLI_SPEED_BYTES=000 must fall back to 1048576 (positive-integer guard); 00512 normalizes to 512
    sample.write_bytes(b'a' * 1200000)
    env_zero = env_for(tmp_path); env_zero['SAMPLE'] = posix(sample); env_zero['ANCLI_SPEED_BYTES'] = '000'
    r_zero = run_bash(script, env_zero)
    assert r_zero.returncode == 0, r_zero.stderr
    assert 'ANCLI_SPEED_BYTES=1048576' in r_zero.stdout
    assert not list(tmp_path.glob('.mirror-speed.*'))

    env_lead = env_for(tmp_path); env_lead['SAMPLE'] = posix(sample); env_lead['ANCLI_SPEED_BYTES'] = '00512'
    r_lead = run_bash(script, env_lead)
    assert r_lead.returncode == 0, r_lead.stderr
    assert 'ANCLI_SPEED_BYTES=512' in r_lead.stdout
    assert not list(tmp_path.glob('.mirror-speed.*'))
