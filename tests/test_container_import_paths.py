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
