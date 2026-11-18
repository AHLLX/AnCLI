from pathlib import Path
import os
import subprocess
import pytest

SOURCE = Path(__file__).resolve().parents[1] / 'src/module/uninstall.sh'
BASH = r'C:\Program Files\Git\bin\bash.exe' if os.name == 'nt' else '/bin/bash'


def posix(path):
    value = str(path).replace('\\', '/')
    return '/' + value[0].lower() + value[2:] if len(value) > 1 and value[1] == ':' else value


@pytest.mark.parametrize("args", [[], ["--purge"]])
def test_uninstall_purges_data_and_preserves_unrelated_files(tmp_path, args):
    temp = tmp_path / 'local_tmp'; temp.mkdir()
    ancli = temp / 'ancli'; (ancli / 'rootfs/root').mkdir(parents=True)
    (ancli / 'rootfs/root/secret').write_text('test-secret')
    (ancli / 'container-config.json').write_text('{}')
    other_zip = temp / 'ancli-keep.zip'; other_zip.write_bytes(b'keep')
    stale_log = temp / '.ancli-uninstall-old.log'; stale_log.write_text('stale')
    active_log = temp / '.ancli-uninstall-active.log'; active_log.write_text('active')
    mount = tmp_path / 'mount'; mount.mkdir()
    module = tmp_path / 'modules/ancli'; (module / 'system').mkdir(parents=True)
    ksu = tmp_path / 'ksu/bin'; ksu.mkdir(parents=True)
    ap = tmp_path / 'ap/bin'; ap.mkdir(parents=True)
    (ksu / 'ancli').write_text('wrapper '+posix(ancli))
    (ksu / 'xdg-open').write_text('unrelated handler')
    (ksu / 'unrelated').write_text('other app')
    fakebin = tmp_path / 'fakebin'; fakebin.mkdir()
    for name in ('pkill', 'umount'):
        p = fakebin / name; p.write_text('#!/bin/sh\nexit 0\n'); p.chmod(0o755)
    s = SOURCE.read_text(encoding='utf-8')
    for old, new in [('/data/local/tmp/.ancli-uninstall-', posix(temp) + '/.ancli-uninstall-'),
                     ('/data/local/tmp/ancli_force_purge',posix(temp / 'ancli_force_purge')),
                     ('/data/local/tmp/ancli',posix(ancli)),
                     ('/mnt/ancli-data',posix(mount)),
                     ('/data/adb/modules/ancli',posix(module)),
                     ('/data/adb/ksu/bin',posix(ksu)),('/data/adb/ap/bin',posix(ap)),
                     ('/system/bin/toybox pkill','pkill')]:
        s = s.replace(old, new)
    script = tmp_path / 'uninstall.sh'; script.write_text(s,encoding='utf-8')
    env = dict(os.environ, PATH=posix(fakebin)+':'+os.environ.get('PATH',''), ANCLI_UNINSTALL_LOG=posix(active_log))
    result = subprocess.run([BASH, posix(script), *args], capture_output=True, text=True, encoding='utf-8', env=env, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not ancli.exists()
    assert other_zip.read_bytes() == b'keep'
    assert not stale_log.exists() and active_log.exists()
    assert not (ksu / 'ancli').exists() and (ksu / 'unrelated').exists()
    assert (ksu / 'xdg-open').exists()
    assert (module / 'remove').exists() and not (module / 'system').exists()
