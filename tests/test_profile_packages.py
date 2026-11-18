from pathlib import Path
import json, re

def test_flash_and_webui_profile_packages_agree():
    root = Path(__file__).resolve().parents[1]
    catalog = json.loads((root / 'src/container-profiles.json').read_text(encoding='utf-8'))['profiles']
    bootstrap = (root / 'src/module/container-bootstrap.sh').read_text(encoding='utf-8')
    flash = (root / 'src/module/customize.sh').read_text(encoding='utf-8')
    for profile, entry in catalog.items():
        expected = ' '.join(entry['packages'])
        assert f'{profile}) echo "{expected}"' in bootstrap
        assert f'{profile}) _profile_packages="{expected}"' in flash
    assert all(name in catalog['minimal']['packages'] for name in ('nodejs','npm','python3','python3-pip'))
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
