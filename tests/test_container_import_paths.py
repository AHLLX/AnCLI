import os
import re
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASH = r'C:\Program Files\Git\bin\bash.exe' if os.name == 'nt' else '/bin/bash'


def test_container_import_rejects_only_actual_traversal_paths():
    source = (ROOT / 'src/module/container-bootstrap.sh').read_text(encoding='utf-8')
    match = re.search(r"if awk '([^']+)' \"\$_import_tmp/entries\"; then", source)
    assert match, 'container import must validate archive member paths with awk'
    validator = match.group(1)

    cases = [
        ('.\n./bin\n./usr/bin/bash\n./usr/local/lib/tool/file.js\n', False),
        ('../outside\n./safe\n', True),
        ('./safe/../../outside\n', True),
        ('/absolute/path\n', True),
        ('./safe/.hidden\n', False),
        ('./etc/os-release -> ../usr/lib/os-release\n', False),
        ('../outside -> ../etc/os-release\n', True),
    ]
    for entries, should_reject in cases:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', delete=False) as stream:
            stream.write(entries)
            path = stream.name
        try:
            result = subprocess.run(
                [BASH, '-c', f"awk '{validator}' \"$1\"", 'test', path],
                capture_output=True,
                text=True,
                check=False,
            )
            assert (result.returncode == 0) is should_reject, (entries, result.stderr)
        finally:
            os.unlink(path)


def test_webui_import_picker_uses_content_signature_not_filename_extension():
    source = (ROOT / 'src/module/webroot/index.html').read_text(encoding='utf-8')
    assert source.count('accept="*/*"') == 2
    start = source.index('async function copyPickedFileToHost(file) {')
    end = source.index('\nasync function importContainerFromUi(file)', start)
    importer = source[start:end]
    assert not re.search(r"file\.name.*\.zip|/\\\.zip\$/i", importer)
    assert 'signature[0] !== 0x50' in importer
    assert 'signature[1] !== 0x4b' in importer
    assert 'signature[2] !== 0x03' in importer
    assert 'signature[3] !== 0x04' in importer


def test_webui_import_supports_older_managers_and_preserves_download_package():
    source = (ROOT / 'src/module/webroot/index.html').read_text(encoding='utf-8')
    start = source.index('async function copyPickedFileToHost(file) {')
    end = source.index('\nasync function importContainerFromUi(file)', start)
    importer = source[start:end]
    assert 'if (typeof fs === \'function\')' in importer
    assert "'/sdcard/Download/' + filename" in importer
    assert "'/storage/emulated/0/Download/' + filename" in importer
    assert 'size === file.size' in importer
    assert 'temporary: false' in importer
    assert "if (path && temporary)" in source
    assert 'Update the manager and try again' not in source
    assert '从 GitHub Releases 下载完整环境包后，在此选择导入。' in source
