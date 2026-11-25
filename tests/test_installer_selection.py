from pathlib import Path
import json
import os
import subprocess

SOURCE = Path(__file__).resolve().parents[1] / 'src/module/customize.sh'
BASH = r'C:\Program Files\Git\bin\bash.exe' if os.name == 'nt' else '/bin/bash'


def run_selection(tmp_path, initial=None, sequences=(), rootfs=False, healthy=False, old_version=None, mounted=False, expect_failure=False):
    base = tmp_path / 'ancli'
    base.mkdir()
    (base / 'rootfs').mkdir()
    if rootfs:
        (base / 'rootfs/bin').mkdir()
        (base / 'rootfs/bin/bash').write_text('preserve')
        (base / 'rootfs/personal').write_text('keep')
        if healthy:
            (base / 'rootfs/usr/bin').mkdir(parents=True)
            (base / 'rootfs/usr/bin/python3').write_text('python')
    config = base / 'container-config.json'
    if initial is not None:
        config.write_text(json.dumps(initial, indent=2), encoding='utf-8')
    eventdir = tmp_path / 'events'
    eventdir.mkdir()
    for i, events in enumerate(sequences, 1):
        (eventdir / str(i)).write_text(events, encoding='utf-8')
    fake = tmp_path / 'getevent'
    fake.write_text('''#!/bin/bash\nn=0; [ -f "$EVENT_DIR/count" ] && n=$(cat "$EVENT_DIR/count"); n=$((n+1)); echo "$n" > "$EVENT_DIR/count"\nif [ -f "$EVENT_DIR/$n" ]; then cat "$EVENT_DIR/$n"; sleep 5; fi\n''', encoding='utf-8')
    fake.chmod(0o755)
    source = SOURCE.read_text(encoding='utf-8')
    key_block = source[source.index('read_container_key() {'):source.index('ui_print "Select installer language')]
    i18n_block = source[source.index('installer_text() {'):source.index('if [ "$HOST_ARCH" = "unknown" ]')]
    block = source[source.index('# 1.2 容器尚未安装时'):source.index('\n_container_mode=$(sed', source.index('# 1.2 容器尚未安装时'))]
    combined = key_block + '\n' + i18n_block + '\n' + block
    combined = combined.replace('/system/bin/getevent', '"$ANCLI_TEST_GETEVENT"').replace('/system/bin/sleep 1', 'sleep 0.05')
    mounts = tmp_path / 'mounts'
    mounts.write_text('')
    old_prop = tmp_path / 'old-module.prop'
    if old_version is not None:
        old_prop.write_text('version=' + old_version + '\n')
    combined = combined.replace('"$ANCLI_DIR" = "/data/local/tmp/ancli"', '"$ANCLI_DIR" = "$ANCLI_TEST_ALLOWED"').replace('/proc/mounts', '"$ANCLI_TEST_MOUNTS"').replace('/data/adb/modules/ancli/module.prop', '"$ANCLI_TEST_OLD_PROP"')
    # Git Bash uses POSIX paths for external programs and shell redirections.
    def posix(path):
        value = str(path).replace('\\', '/')
        return '/' + value[0].lower() + value[2:] if len(value) > 1 and value[1] == ':' else value
    profile_data = posix(SOURCE.parent / 'container-profile-data.sh')
    script = tmp_path / 'test.sh'
    script.write_text(
        f'#!/bin/bash\nINSTALL_LANG=zh\n. "{profile_data}"\n'
        'ui_print() { printf \'%s\\n\' "$*"; }\nabort() { echo "$*"; exit 1; }\ndeploy_installer_assets() { :; }\nps() { :; }\n'
        + combined,
        encoding='utf-8',
    )
    if mounted:
        mounts.write_text('test ' + posix(base) + '/rootfs/bind x rw 0 0\n')
    env = dict(os.environ, ANCLI_DIR=posix(base), ROOTFS=posix(base / 'rootfs'),
               ANCLI_TEST_GETEVENT=posix(fake), EVENT_DIR=posix(eventdir),
               ANCLI_TEST_ALLOWED=posix(base), ANCLI_TEST_MOUNTS=posix(mounts),
               ANCLI_TEST_OLD_PROP=posix(old_prop), BIN_DIR=posix(base / 'bin'), MODULE_VERSION='v1.2.634-beta')
    result = subprocess.run([BASH, posix(script)], env=env, text=True, encoding="utf-8", capture_output=True, timeout=20)
    assert (result.returncode != 0) == expect_failure, result.stdout + result.stderr
    return result.stdout, json.loads(config.read_text(encoding='utf-8')) if config.exists() else None


def test_existing_deferred_config_offers_choice_and_keeps_values_without_keys(tmp_path):
    initial = {'profile': 'developer', 'install_mode': 'deferred', 'network_mode': 'manual', 'proxy_url': 'http://example.test:8080'}
    output, config = run_selection(tmp_path, initial)
    assert '发现已有容器配置' in output and '选择容器安装方式' in output
    assert config == initial


def test_ignores_unrelated_events_and_persists_confirmed_choice(tmp_path):
    initial = {'profile': 'standard', 'install_mode': 'deferred', 'network_mode': 'manual', 'proxy_url': 'http://example.test:8080', 'estimated_size_mb': 900}
    output, config = run_selection(tmp_path, initial, [
        '/dev/input/event0: EV_SYN SYN_REPORT 00000000\n/dev/input/event0: EV_KEY KEY_VOLUMEDOWN DOWN\n',
        '/dev/input/event0: EV_KEY KEY_VOLUMEUP DOWN\n',
        '/dev/input/event0: EV_KEY KEY_VOLUMEDOWN DOWN\n',
    ])
    assert '> 已选 [3/5] 开发' in output
    assert config == {**initial, 'profile': 'developer', 'install_mode': 'installed', 'estimated_size_mb': 0}


def test_installed_rootfs_skips_selection_and_preserves_config(tmp_path):
    initial = {'profile': 'full', 'install_mode': 'installed', 'network_mode': 'system'}
    output, config = run_selection(tmp_path, initial, [DOWN], rootfs=True)
    assert '检测到未完成的旧 rootfs' in output and '选择容器安装方式' not in output
    assert config == initial


UP = '/dev/input/event0: EV_KEY KEY_VOLUMEUP DOWN\n'
DOWN = '/dev/input/event0: EV_KEY KEY_VOLUMEDOWN DOWN\n'


def test_incomplete_choose_repair_keeps_existing_files(tmp_path):
    initial = {'profile': 'minimal', 'install_mode': 'installed', 'network_mode': 'system'}
    output, config = run_selection(tmp_path, initial, [DOWN], rootfs=True)
    assert '检测到未完成的容器' in output and '已选择保留数据并修补' in output
    assert '--------------------------------' in output
    assert config == initial and (tmp_path / 'ancli/rootfs/personal').read_text() == 'keep'


def test_confirmed_clean_reopens_install_choice(tmp_path):
    initial = {'profile': 'minimal', 'install_mode': 'installed', 'network_mode': 'manual', 'proxy_url': 'http://example.test:8080'}
    output, config = run_selection(tmp_path, initial, [UP, UP, DOWN], rootfs=True)
    assert '清理将删除容器' in output and '重新选择安装方式' in output
    assert '选择容器安装方式' in output and config['install_mode'] == 'deferred'
    assert not (tmp_path / 'ancli/rootfs/personal').exists()
    assert config['network_mode'] == 'system'


def test_cancel_clean_preserves_everything(tmp_path):
    initial = {'profile': 'minimal', 'install_mode': 'installed', 'network_mode': 'manual'}
    output, config = run_selection(tmp_path, initial, [UP, DOWN], rootfs=True, expect_failure=True)
    assert '已取消清理' in output
    assert config == initial and (tmp_path / 'ancli/rootfs/personal').read_text() == 'keep'


def test_mounted_data_blocks_clean_even_after_confirmation(tmp_path):
    initial = {'profile': 'minimal', 'install_mode': 'installed', 'network_mode': 'system'}
    output, config = run_selection(tmp_path, initial, [UP, UP], rootfs=True, mounted=True, expect_failure=True)
    assert '容器目录仍有挂载' in output and '清理未完成' in output
    assert config == initial and (tmp_path / 'ancli/rootfs/personal').read_text() == 'keep'


def test_old_healthy_version_offers_choice(tmp_path):
    initial = {'profile': 'standard', 'install_mode': 'installed', 'network_mode': 'system'}
    output, config = run_selection(tmp_path, initial, [DOWN], rootfs=True, healthy=True, old_version='v1.2.630-beta')
    assert '检测到旧版容器（v1.2.630-beta）' in output
    assert config == initial and (tmp_path / 'ancli/rootfs/personal').exists()


def test_same_healthy_version_does_not_prompt(tmp_path):
    initial = {'profile': 'standard', 'install_mode': 'installed', 'network_mode': 'system'}
    output, config = run_selection(tmp_path, initial, [], rootfs=True, healthy=True, old_version='v1.2.634-beta')
    assert '音量上：清理数据' not in output and config == initial


def test_timeout_does_not_resume_old_container(tmp_path):
    initial = {'profile': 'minimal', 'install_mode': 'installed'}
    output, config = run_selection(tmp_path, initial, [], rootfs=True, expect_failure=True)
    assert '未检测到选择' in output and config == initial


def test_clear_option_removes_saved_container_configuration(tmp_path):
    initial = {'profile': 'standard', 'install_mode': 'deferred', 'network_mode': 'system'}
    output, config = run_selection(tmp_path, initial, [UP, UP, DOWN, DOWN, UP, DOWN])
    assert '清除将删除容器' in output
    assert '已清除容器和配置' in output
    assert config['profile'] == 'standard' and config['install_mode'] == 'deferred'

def test_mode_back_keeps_configuration_and_stops_flashing(tmp_path):
    initial = {'profile': 'standard', 'install_mode': 'deferred', 'network_mode': 'system'}
    output, config = run_selection(tmp_path, initial, [UP, UP, UP, DOWN], expect_failure=True)
    assert '返回上一步' in output
    assert config == initial

def test_profile_back_returns_to_setup_mode(tmp_path):
    initial = {'profile': 'standard', 'install_mode': 'deferred', 'network_mode': 'system', 'estimated_size_mb': 0}
    output, config = run_selection(tmp_path, initial, [DOWN, UP, UP, UP, DOWN, UP, DOWN])
    assert '> 已选 [5/5] 返回上一级' in output
    assert '返回容器安装方式选择' in output
    assert config == initial
