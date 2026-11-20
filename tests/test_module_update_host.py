import json
import os
from pathlib import Path
import zipfile

from test_install_transaction import BASH, posix, ROOT, run_bash


def test_host_module_update_check_works_without_rootfs(tmp_path):
    module_dir = tmp_path / 'module'; module_dir.mkdir()
    (module_dir / 'module.prop').write_text('id=ancli\nversion=v1.2.662-beta\nversionCode=74\n')
    payload = tmp_path / 'release.json'
    payload.write_text(json.dumps([{
        'tag_name': 'v1.2.664-beta',
        'html_url': 'https://github.com/AHLLX/AnCLI/releases/tag/v1.2.664-beta',
        'assets': [{
            'name': 'ancli-76-v1.2.664-beta.zip',
            'browser_download_url': 'https://github.com/AHLLX/AnCLI/releases/download/v1.2.664-beta/ancli-76-v1.2.664-beta.zip',
        }],
    }]), encoding='utf-8')
    helper = (ROOT / 'src/module/module-update.sh').read_text(encoding='utf-8')
    helper = helper.replace('MODULE_DIR=/data/adb/modules/ancli', f'MODULE_DIR={posix(module_dir)}', 1)
    helper = helper.replace('ANCLI_DIR=/data/local/tmp/ancli', f'ANCLI_DIR={posix(tmp_path / "ancli-data")}', 1)
    helper = helper.replace(
        "_payload=$(curl -fsSL -A AnCLI --connect-timeout 15 --max-time 25 'https://api.github.com/repos/AHLLX/AnCLI/releases?per_page=1' 2>/dev/null)",
        '_payload=$(cat "$ANCLI_TEST_PAYLOAD")',
        1,
    )
    helper_path = tmp_path / 'module-update.sh'; helper_path.write_text(helper, encoding='utf-8')
    env = dict(os.environ,
               ANCLI_TEST_PAYLOAD=posix(payload),
               SCRIPT=posix(helper_path))
    result = run_bash('sh "$SCRIPT" check beta', env)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data['status'] == 'ok'
    assert data['channel'] == 'beta'
    assert data['current_version'] == 'v1.2.662-beta'
    assert data['latest_version'] == 'v1.2.664-beta'
    assert data['latest_version_code'] == 76
    assert data['has_update'] is True
    assert data['zip_url'].endswith('/ancli-76-v1.2.664-beta.zip')


def test_host_wrapper_routes_module_update_without_rootfs():
    wrapper = (ROOT / 'src/module/system/bin/ancli').read_text(encoding='utf-8')
    no_rootfs = wrapper.index('if [ ! -f /data/local/tmp/ancli/rootfs/bin/bash ]')
    check_route = wrapper.index('check-module)', no_rootfs)
    rootfs_start = wrapper.index('mkdir -p "/data/local/tmp/ancli/rootfs$ANCLI_TUI_CWD"', no_rootfs)
    download_route = wrapper.index('if [ "${1:-}" = "download-module" ]')
    assert download_route < no_rootfs and check_route < rootfs_start


def test_host_module_update_download_validates_root_module_metadata(tmp_path):
    module_dir = tmp_path / 'module'; module_dir.mkdir()
    (module_dir / 'module.prop').write_text('id=ancli\nversion=v1.2.662-beta\nversionCode=74\n')
    bundle = tmp_path / 'valid.zip'
    with zipfile.ZipFile(bundle, 'w') as archive:
        archive.writestr('module.prop', 'id=ancli\nversion=v1.2.664-beta\nversionCode=76\n')

    helper = (ROOT / 'src/module/module-update.sh').read_text(encoding='utf-8')
    helper = helper.replace('MODULE_DIR=/data/adb/modules/ancli', f'MODULE_DIR={posix(module_dir)}', 1)
    helper = helper.replace('ANCLI_DIR=/data/local/tmp/ancli', f'ANCLI_DIR={posix(tmp_path / "ancli-data")}', 1)
    helper = helper.replace(
        'curl -fLsS -L -A AnCLI --connect-timeout 20 --max-time 600 --progress-bar -o "$_partial" "$_url"',
        'cp "$ANCLI_TEST_ZIP" "$_partial"',
        1,
    )
    helper_path = tmp_path / 'module-update.sh'; helper_path.write_text(helper, encoding='utf-8')
    env = dict(os.environ, ANCLI_TEST_ZIP=posix(bundle), SCRIPT=posix(helper_path))
    args = 'download --url=https://github.com/AHLLX/AnCLI/releases/download/v1.2.664-beta/ancli-76-v1.2.664-beta.zip --version=v1.2.664-beta --code=76'
    result = run_bash('sh "$SCRIPT" ' + args, env)
    assert result.returncode == 0, result.stdout + result.stderr
    ready = next(line.removeprefix('ANCLI_MODULE_DOWNLOAD_READY ') for line in result.stdout.splitlines() if line.startswith('ANCLI_MODULE_DOWNLOAD_READY '))
    metadata = json.loads(ready)
    assert metadata['version'] == 'v1.2.664-beta'
    assert metadata['version_code'] == 76
    expected_path = posix(tmp_path / 'ancli-data/module-updates/ancli-76-v1.2.664-beta.zip')
    assert metadata['path'] == expected_path
    assert (tmp_path / 'ancli-data/module-updates/ancli-76-v1.2.664-beta.zip').is_file()

    mismatch = run_bash('sh "$SCRIPT" ' + args.replace('--code=76', '--code=77'), env)
    assert mismatch.returncode != 0 and 'ZIP 版本与 Release 信息不匹配' in mismatch.stdout
