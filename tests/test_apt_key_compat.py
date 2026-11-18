from pathlib import Path
import os
import subprocess

SCRIPT = Path(__file__).resolve().parents[1] / 'src/module/apt-key-compat.sh'
BASH = r'C:\Program Files\Git\bin\bash.exe' if os.name == 'nt' else '/bin/bash'
ORIGINAL = '''#!/bin/sh\ncreate_new_keyring() { if [ ! -r "$FORCED_KEYRING" ]; then TRUSTEDFILE='/dev/null'; FORCED_KEYRING="$TRUSTEDFILE"; fi; }\n'''


def posix(path):
    value = str(path).replace('\\', '/')
    return '/' + value[0].lower() + value[2:] if len(value) > 1 and value[1] == ':' else value


def test_patches_exact_read_check_idempotently_without_disabling_gpg(tmp_path):
    key = tmp_path / 'usr/bin/apt-key'; key.parent.mkdir(parents=True)
    key.write_text(ORIGINAL)
    for _ in range(2):
        r = subprocess.run([BASH, posix(SCRIPT), posix(tmp_path)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
    result = key.read_text()
    assert 'cat "$FORCED_KEYRING" >/dev/null 2>&1' in result
    assert result.count('cat "$FORCED_KEYRING"') == 1
    assert "TRUSTEDFILE='/dev/null'" in result
    assert 'allow-unauthenticated' not in result


def test_unrecognized_apt_key_is_left_intact(tmp_path):
    key = tmp_path / 'usr/bin/apt-key'; key.parent.mkdir(parents=True); key.write_text('# future apt version\n')
    r = subprocess.run([BASH, posix(SCRIPT), posix(tmp_path)], capture_output=True, text=True)
    assert r.returncode == 0 and key.read_text() == '# future apt version\n'
