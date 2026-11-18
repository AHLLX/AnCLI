import importlib.util
import io
from pathlib import Path
import zipfile

spec = importlib.util.spec_from_file_location('ancli_download_test', Path(__file__).parents[1] / 'src/ancli-core.py')
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


def make_zip(version='v1.2.631-beta', code=43):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        archive.writestr('module.prop', f'id=ancli\nversion={version}\nversionCode={code}\n')
    return stream.getvalue()


class Response(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.headers = {'Content-Length': str(len(data))}


def setup(monkeypatch, tmp_path, data):
    monkeypatch.setattr(core, 'MODULE_UPDATE_DIR', str(tmp_path))
    monkeypatch.setattr(core, 'check_module_update', lambda channel: {
        'status': 'ok', 'zip_url': 'https://github.com/AHLLX/AnCLI/releases/download/v1.2.631-beta/ancli-43-v1.2.631-beta.zip',
        'latest_version': 'v1.2.631-beta', 'latest_version_code': 43})
    monkeypatch.setattr(core, '_urlopen_response', lambda *args, **kwargs: Response(data))


def test_download_writes_valid_zip_to_download_dir(tmp_path, monkeypatch):
    data = make_zip(); setup(monkeypatch, tmp_path, data)
    result = core.download_module_update('beta')
    assert Path(result['path']) == tmp_path / 'ancli-43-v1.2.631-beta.zip'
    assert Path(result['path']).read_bytes() == data
    assert not list(tmp_path.glob('*.part'))


def test_invalid_or_conflicting_download_never_deletes_existing_file(tmp_path, monkeypatch):
    target = tmp_path / 'ancli-43-v1.2.631-beta.zip'; target.write_bytes(b'user file')
    setup(monkeypatch, tmp_path, make_zip(version='v1.2.999'))
    assert core.download_module_update('beta') is None
    assert target.read_bytes() == b'user file'
    setup(monkeypatch, tmp_path, make_zip())
    assert core.download_module_update('beta') is None
    assert target.read_bytes() == b'user file'
    assert not list(tmp_path.glob('*.part'))


def test_identical_existing_download_is_reused(tmp_path, monkeypatch):
    data = make_zip(); target = tmp_path / 'ancli-43-v1.2.631-beta.zip'; target.write_bytes(data)
    setup(monkeypatch, tmp_path, data)
    assert core.download_module_update('beta')['path'] == str(target)
    assert target.read_bytes() == data
