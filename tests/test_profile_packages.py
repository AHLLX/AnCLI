from pathlib import Path
import json, os, subprocess

BASH = r'C:\Program Files\Git\bin\bash.exe' if os.name == 'nt' else '/bin/bash'


def _posix(path):
    value = str(path).replace('\\', '/')
    return '/' + value[0].lower() + value[2:] if len(value) > 1 and value[1] == ':' else value


def test_flash_and_webui_profile_packages_agree():
    root = Path(__file__).resolve().parents[1]
    catalog = json.loads((root / 'src/container-profiles.json').read_text(encoding='utf-8'))['profiles']
    synced_profiles = root / 'src/module/ancli/container-profiles.json'
    if synced_profiles.exists():
        assert json.loads(synced_profiles.read_text(encoding='utf-8'))['profiles'] == catalog
    profile_data_path = root / 'src/module/container-profile-data.sh'
    profile_data = profile_data_path.read_text(encoding='utf-8')
    bootstrap = (root / 'src/module/container-bootstrap.sh').read_text(encoding='utf-8')
    flash = (root / 'src/module/customize.sh').read_text(encoding='utf-8')
    assert 'container-profile-data.sh' in bootstrap
    assert 'ancli_profile_packages "$1"' in bootstrap
    assert 'container-profile-data.sh' in flash
    assert 'ancli_profile_packages "$_container_profile"' in flash
    for profile, entry in catalog.items():
        expected = ' '.join(entry['packages'])
        assert f"ancli_profile_packages_{profile}() {{ printf '%s\\n' '{expected}'; }}" in profile_data
        r = subprocess.run(
            [
                BASH,
                '-c',
                f'. "{_posix(profile_data_path)}"; '
                f'ancli_profile_packages {profile}; '
                f'ancli_profile_name_zh {profile}; '
                f'ancli_profile_name_en {profile}; '
                f'ancli_profile_description_zh {profile}; '
                f'ancli_profile_description_en {profile}',
            ],
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=10,
        )
        assert r.returncode == 0, r.stderr
        lines = r.stdout.splitlines()
        assert lines == [
            expected,
            entry['name'],
            entry['name_en'],
            entry['description'],
            entry['description_en'],
        ]
    assert all(name in catalog['minimal']['packages'] for name in ('nodejs','npm','python3','python3-pip'))
    assert 'adb' not in catalog['minimal']['packages']
    assert 'adb' not in catalog['standard']['packages']
    assert 'adb' in catalog['developer']['packages']
    assert 'adb' in catalog['full']['packages']
    previous = set()
    for profile in ('minimal','standard','developer','full'):
        current = set(catalog[profile]['packages'])
        assert previous <= current
        previous = current

def test_node_runtime_uses_known_official_hashes_and_tls():
    root = Path(__file__).resolve().parents[1]
    script = (root / 'src/module/node-setup.sh').read_text(encoding='utf-8')
    assert 'NODE_VERSION=v24.22.3' in script
    assert '061ec3e3062115705b56b51fa9cc330ab50d34b6969387d4e4b1a50c6809acc2' in script
    assert '261af2af250dfd7e9f25cf151d1b2713a0adf8f10a9ad116c4947f68627affe8' in script
    assert '--insecure' not in script and ' -k ' not in script
