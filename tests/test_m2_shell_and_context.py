import importlib.util
import json
import os
import subprocess
from pathlib import Path

from test_install_transaction import BASH, ROOT, posix, run_bash


def _load_core():
    spec = importlib.util.spec_from_file_location("ancli_core", ROOT / "src/ancli-core.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_bash_host_compat_no_loop_on_marked_host_and_forwards_real_guest_bash(tmp_path):
    raw = (ROOT / "src/bash-host-compat").read_bytes()
    assert b"\r" not in raw

    mock_bin_bash = tmp_path / "bin_bash"
    mock_usr_bash = tmp_path / "usr_bin_bash"
    mock_sh = tmp_path / "system_sh"
    mock_proot = tmp_path / "dot_proot"
    mock_os_rel = tmp_path / "os_release"
    mock_deb_ver = tmp_path / "debian_version"

    patched = (
        raw.decode("utf-8")
        .replace("#!/system/bin/sh", "#!/bin/sh", 1)
        .replace("/usr/bin/bash", posix(mock_usr_bash))
        .replace("/bin/bash", posix(mock_bin_bash))
        .replace("exec /system/bin/sh", f"exec {posix(mock_sh)}")
        .replace("/.proot", posix(mock_proot))
        .replace("/etc/os-release", posix(mock_os_rel))
        .replace("/etc/debian_version", posix(mock_deb_ver))
    )
    shim = tmp_path / "bash_compat"
    shim.write_text(patched, encoding="utf-8", newline="\n")
    shim.chmod(0o755)

    # Host /bin/bash is the AnCLI compat shim itself -> must NOT loop; must exec host sh
    mock_bin_bash.write_text(patched, encoding="utf-8", newline="\n")
    mock_bin_bash.chmod(0o755)
    mock_sh.write_text(
        "#!/bin/sh\nprintf 'HOST_SH:%s\\nPATH=%s\\n' \"$*\" \"$PATH\"\n",
        encoding="utf-8",
        newline="\n",
    )
    mock_sh.chmod(0o755)

    clean_env = dict(os.environ)
    clean_env.pop("ANCLI_IN_CONTAINER", None)
    clean_env.pop("PROOT_TMP_DIR", None)
    r_host = run_bash(f'"{posix(shim)}" --noprofile --norc -- -c "echo ok"', clean_env)
    assert r_host.returncode == 0, r_host.stderr
    assert "HOST_SH:-c echo ok" in r_host.stdout
    assert "/data/adb/ksu/bin" in r_host.stdout

    # Container mode with marked /bin/bash and real /usr/bin/bash -> skips marked, execs real
    mock_usr_bash.write_text("#!/bin/sh\nprintf 'GUEST_USR:%s\\n' \"$*\"\n", encoding="utf-8", newline="\n")
    mock_usr_bash.chmod(0o755)
    mock_proot.write_text("", encoding="utf-8", newline="\n")
    r_guest_usr = run_bash(f'"{posix(shim)}" -c "echo ok"', clean_env)
    assert r_guest_usr.returncode == 0, r_guest_usr.stderr
    assert r_guest_usr.stdout.strip() == "GUEST_USR:-c echo ok"

    # Container mode with real /bin/bash -> execs /bin/bash directly
    mock_bin_bash.write_text("#!/bin/sh\nprintf 'GUEST_BIN:%s\\n' \"$*\"\n", encoding="utf-8", newline="\n")
    mock_bin_bash.chmod(0o755)
    mock_proot.unlink()
    r_guest_bin = run_bash(
        f'ANCLI_IN_CONTAINER=1 "{posix(shim)}" -c "echo ok"',
        clean_env,
    )
    assert r_guest_bin.returncode == 0, r_guest_bin.stderr
    assert r_guest_bin.stdout.strip() == "GUEST_BIN:-c echo ok"

    # Host mode under set -u with PATH explicitly unset must not fail on PATH expansion
    r_unset_path = run_bash(
        f'env -u PATH /bin/sh -u "{posix(shim)}" -c "echo ok"',
        clean_env,
    )
    assert r_unset_path.returncode == 0, r_unset_path.stderr
    assert "HOST_SH:-c echo ok" in r_unset_path.stdout
    assert "PATH=/data/adb/ksu/bin:/data/adb/ap/bin:/data/adb/modules/ancli/system/bin:/system/bin:/system/xbin" in r_unset_path.stdout

    r_truly_unset = run_bash(
        f'/bin/sh -c \'unset PATH; set -u; set -- -c "echo ok"; . "{posix(shim)}"\'',
        clean_env,
    )
    assert r_truly_unset.returncode == 0, r_truly_unset.stderr
    assert "HOST_SH:-c echo ok" in r_truly_unset.stdout
    assert "PATH=/data/adb/ksu/bin:/data/adb/ap/bin:/data/adb/modules/ancli/system/bin:/system/bin:/system/xbin\n" in r_truly_unset.stdout

    # Container mode with marked /bin/bash under PATH="" and unset PATH; set -u must NOT loop
    non_comment = "\n".join(
        ln for ln in raw.decode("utf-8").splitlines() if not ln.lstrip().startswith("#")
    )
    assert "head" not in non_comment and "grep" not in non_comment
    mock_usr_bash.unlink()
    mock_bin_bash.write_text(
        "#!/system/bin/sh\n# AnCLI AGY host bash compatibility\necho LOOP_EXECUTED >&2\nexit 99\n",
        encoding="utf-8",
        newline="\n",
    )
    mock_bin_bash.chmod(0o755)
    mock_os_rel.write_text("ID=ubuntu\n", encoding="utf-8", newline="\n")

    r_empty_path_container = run_bash(
        f'PATH="" /bin/sh -u "{posix(shim)}" -c "echo ok"',
        clean_env,
    )
    assert r_empty_path_container.returncode == 0, r_empty_path_container.stderr
    assert "HOST_SH:-c echo ok" in r_empty_path_container.stdout
    assert "LOOP_EXECUTED" not in r_empty_path_container.stderr

    r_unset_path_container = run_bash(
        f'/bin/sh -c \'unset PATH; set -u; set -- -c "echo ok"; . "{posix(shim)}"\'',
        clean_env,
    )
    assert r_unset_path_container.returncode == 0, r_unset_path_container.stderr
    assert "HOST_SH:-c echo ok" in r_unset_path_container.stdout
    assert "LOOP_EXECUTED" not in r_unset_path_container.stderr

    # Container mode with marked /bin/bash + real /usr/bin/bash under PATH="" forwards to /usr/bin/bash
    mock_usr_bash.write_text("#!/bin/sh\nprintf 'GUEST_USR:%s\\n' \"$*\"\n", encoding="utf-8", newline="\n")
    mock_usr_bash.chmod(0o755)
    r_empty_path_fwd = run_bash(
        f'PATH="" /bin/sh -u "{posix(shim)}" -c "echo ok"',
        clean_env,
    )
    assert r_empty_path_fwd.returncode == 0, r_empty_path_fwd.stderr
    assert r_empty_path_fwd.stdout.strip() == "GUEST_USR:-c echo ok"


def test_runtime_context_template_aligns_and_finalizes_cleanly(tmp_path, monkeypatch):
    tpl_path = ROOT / "src/runtime-context/AGENTS.md"
    raw = tpl_path.read_bytes()
    assert b"\r" not in raw
    tpl = raw.decode("utf-8")

    core = _load_core()
    assert core.RUNTIME_CONTEXT_MARKER in tpl
    assert core.RUNTIME_CONTEXT_PENDING_MARKER in tpl
    assert "\n<!-- ancli:first-run:end -->\n工具通过宿主 wrapper" in tpl
    pending_idx = tpl.index(core.RUNTIME_CONTEXT_PENDING_MARKER)
    assert core._find_runtime_context_end(tpl, pending_idx) == tpl.index("\n" + core.RUNTIME_CONTEXT_END_MARKER)

    expected_path = (
        "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:"
        "/root/.local/bin:/data/adb/ksu/bin:/data/adb/ap/bin:/system/bin"
    )
    assert expected_path in tpl
    for key in core.GLOBAL_CONFIG_KEYS:
        assert key in tpl
    assert "60 秒" in tpl
    assert "未预装" in tpl

    ancli_dir = tmp_path / "ancli"
    rootfs = ancli_dir / "rootfs"
    (rootfs / "root/.agents").mkdir(parents=True)
    (rootfs / "root/.codex").mkdir(parents=True)
    (rootfs / "root/.gemini").mkdir(parents=True)
    (ancli_dir / "runtime-context").mkdir(parents=True)
    (ancli_dir / "runtime-context/AGENTS.md").write_text(tpl, encoding="utf-8")
    cfg = rootfs / "root/.ancli-config.json"
    cfg.write_text('{"lang": "zh", "agent_memory": "enabled"}\n', encoding="utf-8")
    installed = ancli_dir / "installed.json"
    installed.write_text("{}", encoding="utf-8")
    target = rootfs / "root/.agents/AGENTS.md"

    monkeypatch.setattr(core, "ANCLI_DIR", str(ancli_dir))
    monkeypatch.setattr(core, "ROOTFS", str(rootfs))
    monkeypatch.setattr(core, "CONFIG_FILE", str(cfg))
    monkeypatch.setattr(core, "INSTALLED_FILE", str(installed))
    monkeypatch.setattr(core, "SHARED_SKILL_DIR", str(rootfs / "root/.agents/skills"))
    monkeypatch.setattr(core, "_runtime_context_template_paths", lambda: (str(ancli_dir / "runtime-context/AGENTS.md"), str(tpl_path)))
    monkeypatch.setattr(core, "_runtime_context_target", lambda: (str(target.parent), str(target)))
    monkeypatch.setattr(core, "_runtime_context_compat_targets", lambda: [
        str(rootfs / "root/.codex/AGENTS.md"),
        str(rootfs / "root/.gemini/AGENTS.md"),
        str(rootfs / "root/.gemini/GEMINI.md"),
    ])
    monkeypatch.setattr(core, "_android_data_mounted", lambda: True)

    setup_res = core.setup_runtime_context(verbose=False)
    assert setup_res["status"] == "created"
    fin_res = core.finalize_runtime_context(verbose=False)
    assert fin_res["status"] == "finalized"
    final_text = target.read_text(encoding="utf-8")
    assert core.RUNTIME_CONTEXT_PENDING_MARKER not in final_text
    assert core.RUNTIME_CONTEXT_FINALIZED_MARKER in final_text
    assert core.RUNTIME_CONTEXT_END_MARKER in final_text
    assert "工具通过宿主 wrapper" in final_text
    assert "## 文件边界" in final_text

    # R3 follow-up alignment: forbidden bare apt-get install guidance removed; safe alternatives & faccessat2 kept
    assert "优先使用 apt 的本机架构包" not in tpl
    assert "apt-get install" in tpl
    assert "apt-get download" in tpl
    assert "dpkg-deb -x" in tpl
    assert "compare_paths2" in tpl
    assert "faccessat2" in tpl
    assert "os.access(..., os.X_OK)" in tpl
    assert "BROWSER=/usr/local/bin/xdg-open" in tpl
    assert ".dns_cache" in tpl
    assert "find /data/local/tmp/ancli" in tpl


def test_android_probe_escapes_multiline_json_and_accepts_list_flag_order(tmp_path):
    raw = (ROOT / "src/module/android-probe.sh").read_bytes()
    assert b"\r" not in raw
    probe = tmp_path / "android-probe.sh"
    probe.write_text(
        raw.decode("utf-8").replace("#!/system/bin/sh", "#!/bin/sh", 1).replace("/system/bin/stat", "stat"),
        encoding="utf-8",
        newline="\n",
    )
    probe.chmod(0o755)

    sample = tmp_path / "sample.txt"
    expected_content = 'line1\r\nline2\t"quote"\\slash\b\f\x01end\n'
    sample.write_bytes(expected_content.encode("utf-8"))

    r_read = run_bash(f'"{posix(probe)}" read "{posix(sample)}"', dict(os.environ))
    assert r_read.returncode == 0, r_read.stderr
    read_payload = json.loads(r_read.stdout)
    assert read_payload["status"] == "ok"
    assert read_payload["content"] == expected_content

    listing_dir = tmp_path / "dir"
    listing_dir.mkdir()
    (listing_dir / "alpha").write_text("a", encoding="utf-8")
    (listing_dir / "beta").write_text("b", encoding="utf-8")
    for args in (f'list --json "{posix(listing_dir)}"', f'list "{posix(listing_dir)}" --json'):
        r_list = run_bash(f'"{posix(probe)}" {args}', dict(os.environ))
        assert r_list.returncode == 0, r_list.stderr
        list_payload = json.loads(r_list.stdout)
        assert list_payload["status"] == "ok"
        assert sorted(list_payload["entries"]) == ["alpha", "beta"]


def test_container_bootstrap_config_and_status_contracts(tmp_path):
    raw = (ROOT / "src/module/container-bootstrap.sh").read_bytes()
    assert b"\r" not in raw
    ancli_dir = tmp_path / "ancli"
    mod_dir = tmp_path / "module"
    ancli_dir.mkdir()
    (ancli_dir / "bin").mkdir()
    mod_dir.mkdir()
    (mod_dir / "module.prop").write_text("id=ancli\nversion=v1.2.651-beta\nversionCode=63\n", encoding="utf-8")
    (ancli_dir / "container-profiles.json").write_text(
        (ROOT / "src/container-profiles.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (ancli_dir / "container-profile-data.sh").write_text(
        (ROOT / "src/module/container-profile-data.sh").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    for name, body in {
        "getprop": "#!/bin/sh\nexit 0\n",
        "dumpsys": "#!/bin/sh\nexit 0\n",
    }.items():
        p = fake_bin / name
        p.write_text(body, encoding="utf-8", newline="\n")
        p.chmod(0o755)

    mounts = tmp_path / "mounts"
    mounts.write_text("", encoding="utf-8", newline="\n")

    patched = (
        raw.decode("utf-8")
        .replace("#!/system/bin/sh", "#!/bin/sh", 1)
        .replace('ANCLI_DIR="/data/local/tmp/ancli"', f'ANCLI_DIR="{posix(ancli_dir)}"')
        .replace('MODULE_DIR="${MODDIR:-/data/adb/modules/ancli}"', f'MODULE_DIR="{posix(mod_dir)}"')
        .replace("/system/bin/dumpsys", "dumpsys")
        .replace("/proc/mounts", posix(mounts))
        .replace("/data/adb/ksu/bin/bash", posix(tmp_path / "no_ksu_bash"))
        .replace("/data/adb/ap/bin/bash", posix(tmp_path / "no_ap_bash"))
    )
    boot = tmp_path / "container-bootstrap.sh"
    boot.write_text(patched, encoding="utf-8", newline="\n")
    boot.chmod(0o755)
    env = dict(os.environ, PATH=posix(fake_bin) + ":" + os.environ.get("PATH", ""))

    r_cfg = run_bash(f'"{posix(boot)}" config --json', env)
    assert r_cfg.returncode == 0, r_cfg.stderr
    cfg_data = json.loads(r_cfg.stdout)
    assert cfg_data["effective_cwd"] == "/root/.ancli-work"
    assert cfg_data["update_channel"] == "stable"
    assert cfg_data["container_profile"] == "standard"
    assert set(cfg_data["supported_keys"]) == {
        "default_cwd", "lang", "proxy_mode", "proxy_url",
        "agent_memory", "update_channel", "container_profile",
    }

    r_set_cfg = run_bash(
        f'"{posix(boot)}" config --set default_cwd=/sdcard/projects update_channel=beta container_profile=developer --json',
        env,
    )
    assert r_set_cfg.returncode == 0, r_set_cfg.stderr
    set_cfg_data = json.loads(r_set_cfg.stdout)
    assert set_cfg_data["effective_cwd"] == "/sdcard/projects"
    assert set_cfg_data["update_channel"] == "beta"
    assert set_cfg_data["container_profile"] == "developer"

    r_clear_cwd = run_bash(f'"{posix(boot)}" config --set default_cwd= --json', env)
    assert r_clear_cwd.returncode == 0, r_clear_cwd.stderr
    clear_cwd_data = json.loads(r_clear_cwd.stdout)
    assert clear_cwd_data["default_cwd"] == ""
    assert clear_cwd_data["effective_cwd"] == "/root/.ancli-work"

    r_bad = run_bash(f'"{posix(boot)}" config --set unknown_key=1 --json', env)
    assert r_bad.returncode == 1
    assert json.loads(r_bad.stdout)["effective_cwd"] == "/root/.ancli-work"
    assert "[X]" in r_bad.stderr

    r_status = run_bash(f'"{posix(boot)}" status --json', env)
    assert r_status.returncode == 0, r_status.stderr
    st = json.loads(r_status.stdout)
    assert st["version_code"] == 63
    assert isinstance(st["config"], dict)
    assert set(st["profiles"].keys()) == {"minimal", "standard", "developer", "full"}
    assert st["container"]["config"]["profile"] == "developer"
    assert set(st["container"]["profiles"].keys()) == {"minimal", "standard", "developer", "full"}

    r_cset = run_bash(
        f'"{posix(boot)}" set --network=manual --proxy-url=http://127.0.0.1:7890',
        env,
    )
    assert r_cset.returncode == 0, r_cset.stderr
    assert json.loads(r_cset.stdout)["proxy_url"] == "http://127.0.0.1:7890"

    r_cclear = run_bash(f'"{posix(boot)}" set --proxy-url=', env)
    assert r_cclear.returncode == 0, r_cclear.stderr
    cclear = json.loads(r_cclear.stdout)
    assert cclear["proxy_url"] == ""
    assert cclear["config"]["proxy_url"] == ""

    # PRoot/dpkg/systemd hardening invariants in both container-bootstrap.sh and customize.sh
    cust_raw = (ROOT / "src/module/customize.sh").read_text(encoding="utf-8")
    boot_raw = raw.decode("utf-8")
    for label, text in (("container-bootstrap.sh", boot_raw), ("customize.sh", cust_raw)):
        assert "/usr/sbin/policy-rc.d" in text, label
        assert "02ancli-dpkg" in text, label
        assert "ancli_harden_systemd_dpkg" in text, label
        assert "systemd*.postinst" in text, label
        assert "udev.postinst" in text, label
        assert "PROOT_NO_SECCOMP=1" in text, label
        assert "_pb=\"${_pb%/}\"" in text or "_pb=${_pb%/}" in text, label

    # Execute ancli_harden_systemd_dpkg extracted from container-bootstrap.sh against mock dpkg info
    dpkg_info = tmp_path / "dpkg_info"
    dpkg_info.mkdir()
    (dpkg_info / "systemd.postinst").write_text("#!/bin/sh\nsystemd-sysusers\n", encoding="utf-8", newline="\n")
    (dpkg_info / "udev.postinst").write_text("#!/bin/sh\nudevadm control --reload\n", encoding="utf-8", newline="\n")
    (dpkg_info / "curl.postinst").write_text("#!/bin/sh\necho keep_curl\n", encoding="utf-8", newline="\n")
    fn_block = boot_raw.split("ancli_harden_systemd_dpkg() {", 1)[1].split("\n}\n", 1)[0]
    fn_patched = (
        "ancli_harden_systemd_dpkg() {"
        + fn_block.replace("\\$", "$").replace("/var/lib/dpkg/info", posix(dpkg_info))
        + "\n}\ndpkg() { return 0; }\nancli_harden_systemd_dpkg\n"
    )
    r_harden = run_bash(fn_patched, env)
    assert r_harden.returncode == 0, r_harden.stderr
    assert "exit 0" in (dpkg_info / "systemd.postinst").read_text(encoding="utf-8")
    assert "exit 0" in (dpkg_info / "udev.postinst").read_text(encoding="utf-8")
    assert "keep_curl" in (dpkg_info / "curl.postinst").read_text(encoding="utf-8")


def test_install_sh_lf_and_update_json_mirror_fallback(tmp_path):
    raw = (ROOT / "src/install.sh").read_bytes()
    assert b"\r" not in raw

    zip_dst = tmp_path / "ancli-module.zip"
    ksu_dir = tmp_path / "ksu"
    ksu_dir.mkdir()
    patched = (
        raw.decode("utf-8")
        .replace("#!/system/bin/sh", "#!/bin/sh", 1)
        .replace("/data/adb/magisk/util_functions.sh", posix(tmp_path / "no_magisk"))
        .replace("/data/adb/ksu", posix(ksu_dir))
        .replace("/data/local/tmp/ancli-module.zip", posix(zip_dst))
    )
    inst = tmp_path / "install.sh"
    inst.write_text(patched, encoding="utf-8", newline="\n")
    inst.chmod(0o755)

    r_bad_proxy = run_bash(f'ANCLI_GH_PROXY=bad-proxy "{posix(inst)}"', dict(os.environ))
    assert r_bad_proxy.returncode != 0
    assert "Invalid ANCLI_GH_PROXY" in r_bad_proxy.stdout

    script = f'''
curl() {{
    _out=""
    _url=""
    while [ "$#" -gt 0 ]; do
        case "$1" in
            -o) _out="$2"; shift 2 ;;
            --connect-timeout|--max-time) shift 2 ;;
            -*) shift ;;
            *) _url="$1"; shift ;;
        esac
    done
    case "$_url" in
        https://raw.githubusercontent.com/*) return 1 ;;
        https://gh-proxy.com/https://raw.githubusercontent.com/*)
            printf '{{"zipUrl":"https://github.com/AHLLX/AnCLI/releases/download/v1.2.651-beta/AnCLI.zip"}}\\n'
            return 0 ;;
        https://github.com/*) return 1 ;;
        https://gh-proxy.com/https://github.com/*)
            printf 'PKZIPDATA' > "$_out"
            return 0 ;;
    esac
    return 1
}}
export -f curl
. "{posix(inst)}"
'''
    r_ok = run_bash(script, dict(os.environ))
    assert r_ok.returncode == 0, r_ok.stdout + r_ok.stderr
    assert zip_dst.read_bytes() == b"PKZIPDATA"


def test_m2_shell_invariants_service_ancli_env_dsh_network_uninstall(tmp_path):
    service_txt = (ROOT / "src/module/service.sh").read_text(encoding="utf-8")
    assert "loader|loader-*" in service_txt
    assert 'chmod 755 "$INSTANT_BIN/$name"' in service_txt
    assert 'cp -f "$MODULE_DIR/system/bin/ancli" "$INSTANT_BIN/ancli"' in service_txt
    assert '".codex"' in service_txt
    assert 'chmod 777 "$_bridge_dir" "$_bridge_dir/reqs"' in service_txt
    assert 'mv "$_req" "${_req}.run"' in service_txt

    ancli_txt = (ROOT / "src/module/system/bin/ancli").read_text(encoding="utf-8")
    assert "|codex)" in ancli_txt or "|codex" in ancli_txt
    assert "npm_config_proxy npm_config_https_proxy" in ancli_txt
    assert 'no_proxy="localhost,127.0.0.1,::1,0.0.0.0"' in ancli_txt
    assert 'chmod 777 "$_bridge_dir" "$_bridge_dir/reqs"' in ancli_txt
    assert '[ ! -d "/proc/$_bridge_pid" ]' in ancli_txt
    assert 'mv "$_r" "${_r}.run"' in ancli_txt
    assert "/root/.local/bin:/data/adb/ksu/bin:/data/adb/ap/bin:/system/bin" in ancli_txt
    assert 'PWD="$PROOT_CWD"' in ancli_txt
    assert '-w "$PROOT_CWD"' in ancli_txt
    assert '_ancli_default_cwd=${_ancli_default_cwd%/}' in ancli_txt

    # ancli_env.sh live behavior in manual and direct modes
    env_src = (ROOT / "src/module/ancli_env.sh").read_text(encoding="utf-8")
    for d in ("/root/.codex", "/root/.agents", "/root/.dsh", "/root/.grok", "/root/.mimocode"):
        assert d in env_src
    ancli_dir = tmp_path / "ancli"
    cfg_dir = ancli_dir / "rootfs/root"
    cfg_dir.mkdir(parents=True)
    patched_env = tmp_path / "ancli_env.sh"
    patched_env.write_text(
        env_src.replace("/data/local/tmp/ancli", posix(ancli_dir)),
        encoding="utf-8",
        newline="\n",
    )
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    for name in ("getprop", "settings", "dumpsys", "ip", "chown", "chmod"):
        p = fakebin / name
        p.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8", newline="\n")
        p.chmod(0o755)
    base_env = dict(os.environ, PATH=posix(fakebin) + ":" + os.environ.get("PATH", ""))

    (cfg_dir / ".ancli-config.json").write_text(
        json.dumps({"proxy_mode": "manual", "proxy_url": "http://127.0.0.1:7890"}, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    r_manual = run_bash(
        f'. "{posix(patched_env)}"; printf "%s|%s|%s|%s|%s\\n" "$all_proxy" "$ALL_PROXY" "$npm_config_proxy" "$npm_config_https_proxy" "$no_proxy"',
        base_env,
    )
    assert r_manual.returncode == 0, r_manual.stderr
    assert r_manual.stdout.strip() == "http://127.0.0.1:7890|http://127.0.0.1:7890|http://127.0.0.1:7890|http://127.0.0.1:7890|localhost,127.0.0.1,::1,0.0.0.0"

    (cfg_dir / ".ancli-config.json").write_text(
        json.dumps({"proxy_mode": "manual", "proxy_url": "http://127.0.0.1:7890"}, separators=(",", ":")),
        encoding="utf-8",
        newline="\n",
    )
    r_manual_compact = run_bash(
        f'. "{posix(patched_env)}"; printf "%s|%s|%s\\n" "$all_proxy" "$ALL_PROXY" "$no_proxy"',
        base_env,
    )
    assert r_manual_compact.returncode == 0, r_manual_compact.stderr
    assert r_manual_compact.stdout.strip() == "http://127.0.0.1:7890|http://127.0.0.1:7890|localhost,127.0.0.1,::1,0.0.0.0"

    (cfg_dir / ".ancli-config.json").write_text(
        json.dumps({"proxy_mode": "direct", "proxy_url": "http://127.0.0.1:7890"}, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    r_direct = run_bash(
        f'. "{posix(patched_env)}"; printf "%s|%s|%s|%s\\n" "${{all_proxy:-}}" "${{ALL_PROXY:-}}" "${{npm_config_proxy:-}}" "${{no_proxy:-}}"',
        base_env,
    )
    assert r_direct.returncode == 0, r_direct.stderr
    assert r_direct.stdout.strip() == "|||localhost,127.0.0.1,::1,0.0.0.0"

    # dsh-web-control.sh invariants
    dsh_txt = (ROOT / "src/module/dsh-web-control.sh").read_text(encoding="utf-8")
    runner_body = dsh_txt.split("runner='", 1)[1].split("'\n", 1)[0]
    assert "'" not in runner_body
    assert "ancli_env.sh" in runner_body
    assert "secrets/dsh.env" in runner_body
    assert "npm_config_proxy npm_config_https_proxy" in runner_body
    assert "umask 022" in runner_body
    assert "PROOT_NO_SECCOMP=1" in runner_body
    assert "/mnt/ancli-data" in runner_body
    assert "/root/.local/bin:/data/adb/ksu/bin:/data/adb/ap/bin:/system/bin" in runner_body
    assert '-w "$proot_cwd"' in runner_body
    assert 'PWD="$proot_cwd"' in runner_body
    assert "/usr/local/bin/dsh web --no-open </dev/null" in runner_body
    assert 'ANCLI_BIND_ARGS="$ANCLI_BIND_ARGS -b $_b"' in runner_body
    assert "_b=${_b%/}" in runner_body
    assert 'task-stop.sh" "$id" --quiet' in dsh_txt

    runner_script = tmp_path / "dsh_runner.sh"
    runner_script.write_text(runner_body + "\n", encoding="utf-8", newline="\n")
    r_runner_syntax = subprocess.run([BASH, "-n", posix(runner_script)], capture_output=True, text=True, timeout=15)
    assert r_runner_syntax.returncode == 0, r_runner_syntax.stderr

    # Live dsh-web-control.sh default_cwd extraction & normalization (multi-line & compact JSON)
    dsh_cwd_block = dsh_txt.split('        _dsh_cwd=""\n', 1)[1].split('        pidfile="$expected_log.pid"', 1)[0]
    dsh_ancli_dir = tmp_path / "dsh_ancli"
    (dsh_ancli_dir / "rootfs/root/proj").mkdir(parents=True)
    dsh_cwd_fn = (
        f'ANCLI_DIR="{posix(dsh_ancli_dir)}"\n'
        + dsh_cwd_block
        + '\nprintf "%s\\n" "$proot_cwd"\n'
    )
    for cfg_body, expected_cwd in (
        ('{"default_cwd":"/root/proj///","lang":"zh"}\n', "/root/proj"),
        ('{\n  "default_cwd": "/"\n}\n', "/root/.ancli-work"),
        ('{"default_cwd": "/nonexistent/"}\n', "/root/.ancli-work"),
        ('{"lang": "zh"}\n', "/root/.ancli-work"),
    ):
        (dsh_ancli_dir / "rootfs/root/.ancli-config.json").write_text(cfg_body, encoding="utf-8", newline="\n")
        r_dcwd = run_bash(dsh_cwd_fn, dict(os.environ))
        assert r_dcwd.returncode == 0, r_dcwd.stderr
        assert r_dcwd.stdout.strip() == expected_cwd

    # Live system/bin/ancli default_cwd + PWD="$PROOT_CWD" forwarding check
    s_ancli_dir = tmp_path / "sys_ancli"
    (s_ancli_dir / "bin").mkdir(parents=True)
    (s_ancli_dir / "rootfs/bin").mkdir(parents=True)
    (s_ancli_dir / "rootfs/root/myproj").mkdir(parents=True)
    (s_ancli_dir / ".bridge/reqs").mkdir(parents=True)
    (s_ancli_dir / ".bridge/active").write_text("", encoding="utf-8", newline="\n")
    (s_ancli_dir / ".bridge/pid").write_text("999999", encoding="utf-8", newline="\n")
    (s_ancli_dir / "rootfs/bin/bash").write_text("#!/bin/sh\n", encoding="utf-8", newline="\n")
    (s_ancli_dir / "bin/ancli_env.sh").write_text("", encoding="utf-8", newline="\n")
    (s_ancli_dir / "bin/proot").write_text(
        "#!/bin/sh\nprintf 'ARGS=%s\\n' \"$*\"\n", encoding="utf-8", newline="\n"
    )
    (s_ancli_dir / "bin/proot").chmod(0o755)
    (s_ancli_dir / "rootfs/root/.ancli-config.json").write_text(
        '{\n  "default_cwd": "/root/myproj/"\n}\n', encoding="utf-8", newline="\n"
    )
    s_ancli_patched = (
        ancli_txt
        .replace("#!/system/bin/sh", "#!/bin/sh", 1)
        .replace("/data/local/tmp/ancli", posix(s_ancli_dir))
        .replace("/mnt/ancli-data", posix(tmp_path / "sys_mnt"))
        .replace("/data/adb", posix(tmp_path / "sys_adb"))
        .replace("/system/bin/sh", "/bin/sh")
        .replace("{ ! kill -0 \"$_bridge_pid\" 2>/dev/null && [ ! -d \"/proc/$_bridge_pid\" ]; }", "false")
    )
    s_ancli_bin = tmp_path / "sys_ancli_bin"
    s_ancli_bin.write_text(s_ancli_patched, encoding="utf-8", newline="\n")
    s_ancli_bin.chmod(0o755)
    r_sancli = run_bash(f'cd / && "{posix(s_ancli_bin)}" status --json', dict(os.environ))
    assert r_sancli.returncode == 0, r_sancli.stderr
    assert "-w /root/myproj " in r_sancli.stdout
    assert "PWD=/root/myproj " in r_sancli.stdout

    # Escaped quotes/backslashes + compact JSON + UTF-8 BOM in system/bin/ancli default_cwd
    tricky_cwd = r'/root/a"b\c'
    if os.name != "nt":
        (s_ancli_dir / "rootfs/root" / 'a"b\\c').mkdir(parents=True, exist_ok=True)
        s_ancli_tricky_bin = s_ancli_bin
    else:
        tricky_helper = '[() { case "${1:-}:${2:-}" in -d:*\'a"b\\c\') return 0 ;; esac; builtin [ "$@"; }\n'
        s_ancli_tricky_bin = tmp_path / "sys_ancli_tricky_bin"
        s_ancli_tricky_bin.write_text(
            s_ancli_patched.replace("#!/bin/sh\n", "#!/bin/sh\n" + tricky_helper, 1),
            encoding="utf-8",
            newline="\n",
        )
        s_ancli_tricky_bin.chmod(0o755)
    (s_ancli_dir / "bin/proot").write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$@\"\n", encoding="utf-8", newline="\n"
    )
    (s_ancli_dir / "rootfs/root/.ancli-config.json").write_bytes(
        b"\xef\xbb\xbf"
        + json.dumps(
            {"nested": {"default_cwd": "/root/wrong"}, "default_cwd": tricky_cwd},
            separators=(",", ":"),
        ).encode("utf-8")
    )
    r_sancli_tricky = run_bash(f'cd / && "{posix(s_ancli_tricky_bin)}" status --json', dict(os.environ))
    assert r_sancli_tricky.returncode == 0, r_sancli_tricky.stderr
    tricky_args = r_sancli_tricky.stdout.splitlines()
    assert "-w" in tricky_args and tricky_args[tricky_args.index("-w") + 1] == tricky_cwd
    assert f"PWD={tricky_cwd}" in tricky_args

    # task-stop.sh --quiet vs 1-arg cleanup output & session signaling
    ts_src = (ROOT / "src/module/task-stop.sh").read_text(encoding="utf-8")
    ts_root = tmp_path / "taskstop"
    ts_ancli = ts_root / "ancli"
    ts_ancli.mkdir(parents=True)
    ts_proc = ts_root / "proc"
    (ts_proc / "sys/kernel/random").mkdir(parents=True)
    (ts_proc / "sys/kernel/random/boot_id").write_text("boot-test-1\n", encoding="utf-8", newline="\n")
    (ts_ancli / "install-transaction.sh").write_text(
        "ancli_cleanup_stopped_task() { return 0; }\n", encoding="utf-8", newline="\n"
    )
    ts_kill_log = ts_root / "kill.log"
    ts_kill = fakebin / "mock_kill"
    ts_kill.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{posix(ts_kill_log)}"\n'
        f'for _a in "$@"; do\n'
        f'    case "$_a" in\n'
        f'        -[0-9]*) rm -rf "{posix(ts_proc)}/${{_a#-}}" ;;\n'
        f'        [0-9]*) rm -rf "{posix(ts_proc)}/$_a" ;;\n'
        f'    esac\n'
        f'done\nexit 0\n',
        encoding="utf-8",
        newline="\n",
    )
    ts_kill.chmod(0o755)
    ts_patched = (
        ts_src
        .replace("#!/system/bin/sh", "#!/bin/sh", 1)
        .replace("/data/local/tmp/ancli", posix(ts_ancli))
        .replace("/proc/", posix(ts_proc) + "/")
        .replace("/system/bin/kill", posix(ts_kill))
    )
    ts_script = ts_root / "task-stop.sh"
    ts_script.write_text(ts_patched, encoding="utf-8", newline="\n")
    ts_script.chmod(0o755)

    for task_id, extra_args, expect_msg in (
        ("1700000_1", [], True),
        ("1700000_2", ["--quiet"], False),
    ):
        ts_kill_log.write_text("", encoding="utf-8", newline="\n")
        (ts_proc / "4242").mkdir(exist_ok=True)
        (ts_proc / "4242/stat").write_text(
            "4242 (dsh) S 1 4242 4242 0 -1 0 0 0 0 0 0 0 0 0 20 0 1 0 98765 0 0 0\n",
            encoding="utf-8",
            newline="\n",
        )
        # Child process in same session (SID=4242) with changed PGID=5555
        (ts_proc / "4243").mkdir(exist_ok=True)
        (ts_proc / "4243/stat").write_text(
            "4243 (node) S 4242 5555 4242 0 -1 0 0 0 0 0 0 0 0 0 20 0 1 0 98766 0 0 0\n",
            encoding="utf-8",
            newline="\n",
        )
        (ts_ancli / f".webui_{task_id}.log.pid").write_text("4242 98765 boot-test-1\n", encoding="utf-8", newline="\n")
        r_ts = subprocess.run(
            [BASH, posix(ts_script), task_id, *extra_args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=base_env,
            timeout=15,
        )
        assert r_ts.returncode == 0, r_ts.stdout + r_ts.stderr
        assert (ts_ancli / f".webui_{task_id}.log.exit").read_text(encoding="utf-8").strip() == "143"
        assert "4243" in ts_kill_log.read_text(encoding="utf-8")
        if expect_msg:
            assert "Task stopped; owned temporary installation data cleaned. Existing container data preserved." in r_ts.stdout
        else:
            assert "Task stopped; owned temporary installation data cleaned." not in r_ts.stdout

    # install-network.sh behavior
    net_helper = posix(ROOT / "src/module/install-network.sh")
    r_net_https = run_bash(
        f'. "{net_helper}"; curl() {{ return 0; }}; ancli_install_network manual "https://127.0.0.1:7890/"; '
        'printf "%s|%s|%s\\n" "$https_proxy" "$no_proxy" "$NO_PROXY"',
        base_env,
    )
    assert r_net_https.returncode == 0, r_net_https.stderr
    assert "https://127.0.0.1:7890|localhost,127.0.0.1,::1,0.0.0.0|localhost,127.0.0.1,::1,0.0.0.0" in r_net_https.stdout

    r_net_bare = run_bash(f'. "{net_helper}"; ancli_install_network manual "8080"', base_env)
    assert r_net_bare.returncode != 0

    r_net_empty_skip = run_bash(
        f'. "{net_helper}"; ANCLI_SKIP_PROXY_CONNECTIVITY_TEST=1 ancli_install_network manual ""',
        base_env,
    )
    assert r_net_empty_skip.returncode == 0

    r_net_empty = run_bash(f'. "{net_helper}"; ancli_install_network manual ""', base_env)
    assert r_net_empty.returncode != 0

    # uninstall.sh dual bash marker cleanup
    uninst_src = (ROOT / "src/module/uninstall.sh").read_text(encoding="utf-8")
    temp_root = tmp_path / "uninst"
    u_ancli = temp_root / "ancli"
    u_ancli.mkdir(parents=True)
    u_mod = temp_root / "mod"
    u_mod.mkdir(parents=True)
    u_ksu = temp_root / "ksu/bin"
    u_ksu.mkdir(parents=True)
    u_ap = temp_root / "ap/bin"
    u_ap.mkdir(parents=True)
    (u_ksu / "bash").write_text("#!/system/bin/sh\n# AnCLI AGY host bash compatibility\n", encoding="utf-8")
    (u_ap / "bash").write_text("#!/system/bin/sh\n# AnCLI proot shim\n", encoding="utf-8")
    (u_ksu / "custom_bash").write_text("#!/system/bin/sh\n# user custom binary\n", encoding="utf-8")
    for tool in ("pkill", "umount"):
        p = fakebin / tool
        p.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8", newline="\n")
        p.chmod(0o755)
    u_patched = (
        uninst_src
        .replace("#!/system/bin/sh", "#!/bin/sh", 1)
        .replace("/data/local/tmp/.ancli-uninstall-", posix(temp_root) + "/.ancli-uninstall-")
        .replace("/data/local/tmp/ancli_force_purge", posix(temp_root / "force_purge"))
        .replace("/data/local/tmp/ancli", posix(u_ancli))
        .replace("/mnt/ancli-data", posix(temp_root / "mnt"))
        .replace("/data/adb/modules/ancli", posix(u_mod))
        .replace("/data/adb/ksu/bin", posix(u_ksu))
        .replace("/data/adb/ap/bin", posix(u_ap))
        .replace("/system/bin/toybox pkill", "pkill")
    )
    u_script = tmp_path / "uninstall.sh"
    u_script.write_text(u_patched, encoding="utf-8", newline="\n")
    u_script.chmod(0o755)
    r_u = subprocess.run(
        [BASH, posix(u_script)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=base_env,
        timeout=15,
    )
    assert r_u.returncode == 0, r_u.stdout + r_u.stderr
    assert not u_ancli.exists()
    assert (u_mod / "remove").exists()
    assert not (u_ksu / "bash").exists()
    assert not (u_ap / "bash").exists()
    assert (u_ksu / "custom_bash").exists()


def test_container_bootstrap_default_cwd_quotes_and_install_mkdir_root(tmp_path):
    raw = (ROOT / "src/module/container-bootstrap.sh").read_bytes()
    assert b"\r" not in raw

    ancli_dir = tmp_path / "ancli"
    mod_dir = tmp_path / "module"
    ancli_dir.mkdir()
    (ancli_dir / "bin").mkdir()
    mod_dir.mkdir()
    (mod_dir / "module.prop").write_text("id=ancli\nversion=v1.2.651-beta\nversionCode=63\r\n", encoding="utf-8", newline="\n")
    (ancli_dir / "container-profiles.json").write_text(
        (ROOT / "src/container-profiles.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (ancli_dir / "container-profile-data.sh").write_text(
        (ROOT / "src/module/container-profile-data.sh").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (ancli_dir / "container-mirror.sh").write_text(
        (ROOT / "src/module/container-mirror.sh").read_text(encoding="utf-8"),
        encoding="utf-8",
        newline="\n",
    )
    (ancli_dir / "install-transaction.sh").write_text(
        (ROOT / "src/module/install-transaction.sh").read_text(encoding="utf-8"),
        encoding="utf-8",
        newline="\n",
    )
    (ancli_dir / "install-network.sh").write_text(
        "ancli_install_network() { return 0; }\n",
        encoding="utf-8",
        newline="\n",
    )
    (ancli_dir / "apt-key-compat.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8", newline="\n")

    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    for name, body in {
        "getprop": "#!/bin/sh\nexit 0\n",
        "dumpsys": "#!/bin/sh\nexit 0\n",
    }.items():
        p = fake_bin / name
        p.write_text(body, encoding="utf-8", newline="\n")
        p.chmod(0o755)

    mounts = tmp_path / "mounts"
    mounts.write_text("", encoding="utf-8", newline="\n")

    patched = (
        raw.decode("utf-8")
        .replace("#!/system/bin/sh", "#!/bin/sh", 1)
        .replace('ANCLI_DIR="/data/local/tmp/ancli"', f'ANCLI_DIR="{posix(ancli_dir)}"')
        .replace('MODULE_DIR="${MODDIR:-/data/adb/modules/ancli}"', f'MODULE_DIR="{posix(mod_dir)}"')
        .replace("/system/bin/dumpsys", "dumpsys")
        .replace("/proc/mounts", posix(mounts))
        .replace("/data/adb/ksu/bin/bash", posix(tmp_path / "no_ksu_bash"))
        .replace("/data/adb/ap/bin/bash", posix(tmp_path / "no_ap_bash"))
        .replace(" /data/adb ", " ")
        .replace(" /dev ", " ")
        .replace(" /proc ", " ")
        .replace(" /sys ", " ")
    )
    boot = tmp_path / "container-bootstrap.sh"
    boot.write_text(patched, encoding="utf-8", newline="\n")
    boot.chmod(0o755)
    env = dict(os.environ, PATH=posix(fake_bin) + ":" + os.environ.get("PATH", ""))

    # 1. list_json & doctor_json strip CR from module.prop version
    for subcmd in ("list", "doctor"):
        r_ver = run_bash(f'"{posix(boot)}" {subcmd}', env)
        assert r_ver.returncode == 0, r_ver.stderr
        assert json.loads(r_ver.stdout)["ancli_version"] == "v1.2.651-beta"

    # 2. default_cwd with quotes & backslashes round-trips cleanly across config --set / --json
    tricky_cwd = r'/sdcard/a"b\c'
    r_set = run_bash(f'"{posix(boot)}" config --set \'default_cwd={tricky_cwd}\' --json', env)
    assert r_set.returncode == 0, r_set.stderr
    assert json.loads(r_set.stdout)["default_cwd"] == tricky_cwd
    assert json.loads((ancli_dir / "container-config.json").read_text(encoding="utf-8"))["default_cwd"] == tricky_cwd

    r_get = run_bash(f'"{posix(boot)}" config --json', env)
    assert r_get.returncode == 0, r_get.stderr
    assert json.loads(r_get.stdout)["default_cwd"] == tricky_cwd

    r_other = run_bash(f'"{posix(boot)}" config --set lang=en --json', env)
    assert r_other.returncode == 0, r_other.stderr
    other_data = json.loads(r_other.stdout)
    assert other_data["lang"] == "en"
    assert other_data["default_cwd"] == tricky_cwd
    assert json.loads((ancli_dir / "container-config.json").read_text(encoding="utf-8"))["default_cwd"] == tricky_cwd

    # Compact single-line .ancli-config.json with escaped quotes & backslashes
    (ancli_dir / "rootfs/root").mkdir(parents=True)
    (ancli_dir / "rootfs/root/.ancli-config.json").write_text(
        json.dumps({"lang": "zh", "proxy_mode": "system", "proxy_url": "", "default_cwd": tricky_cwd}),
        encoding="utf-8",
        newline="\n",
    )
    r_compact = run_bash(f'"{posix(boot)}" config --json', env)
    assert r_compact.returncode == 0, r_compact.stderr
    assert json.loads(r_compact.stdout)["default_cwd"] == tricky_cwd

    # 3. install_container creates $ROOTFS/root before writing ancli-container-setup.sh
    import shutil
    shutil.rmtree(ancli_dir / "rootfs")
    rootfs = ancli_dir / "rootfs"
    (rootfs / "bin").mkdir(parents=True)
    (rootfs / "etc/apt/sources.list.d").mkdir(parents=True)
    (rootfs / "etc/apt/sources.list.d/ubuntu.sources").write_text(
        "Types: deb\nURIs: http://archive.ubuntu.com/ubuntu\n", encoding="utf-8", newline="\n"
    )
    # Write ELF e_machine header at offset 18 matching host arch so get_rootfs_arch succeeds
    r_arch = run_bash("uname -m", env)
    arch_str = r_arch.stdout.strip()
    e_machine = b"\xb7\x00" if arch_str in ("aarch64", "arm64") else b"\x3e\x00"
    (rootfs / "bin/bash").write_bytes(b"\x7fELF" + b"\x00" * 14 + e_machine + b"\x00" * 16)

    captured_setup = tmp_path / "captured_setup.sh"
    for pdir in (mod_dir / "bin/x86_64", mod_dir / "bin/arm64"):
        pdir.mkdir(parents=True)
        proot_mock = pdir / "proot"
        proot_mock.write_text(
            f'#!/bin/sh\n'
            f'while [ "$#" -gt 0 ]; do\n'
            f'    case "$1" in\n'
            f'        -r|-b|-w) shift 2 ;;\n'
            f'        *) break ;;\n'
            f'    esac\n'
            f'done\n'
            f'if [ "$1" = "/root/ancli-container-setup.sh" ]; then\n'
            f'    cp -f "{posix(rootfs)}/root/ancli-container-setup.sh" "{posix(captured_setup)}"\n'
            f'fi\n'
            f'exit 0\n',
            encoding="utf-8",
            newline="\n",
        )
        proot_mock.chmod(0o755)

    assert not (rootfs / "root").exists()
    r_inst = run_bash(f'"{posix(boot)}" install --profile=minimal --mirror=ustc', env)
    assert r_inst.returncode == 0, r_inst.stdout + r_inst.stderr
    assert (rootfs / "root").is_dir()
    assert captured_setup.is_file()
    r_syntax = subprocess.run([BASH, "-n", posix(captured_setup)], capture_output=True, text=True, timeout=15)
    assert r_syntax.returncode == 0, r_syntax.stderr
    assert "ancli_harden_systemd_dpkg" in captured_setup.read_text(encoding="utf-8")


def test_dsh_web_control_start_preserves_escaped_default_cwd(tmp_path):
    raw = (ROOT / "src/module/dsh-web-control.sh").read_bytes()
    assert b"\r" not in raw

    ancli_dir = tmp_path / "ancli"
    (ancli_dir / "bin").mkdir(parents=True)
    (ancli_dir / "rootfs/root/.ancli-work").mkdir(parents=True)
    proc_dir = tmp_path / "proc"
    (proc_dir / "sys/kernel/random").mkdir(parents=True)
    boot_id = proc_dir / "sys/kernel/random/boot_id"
    boot_id.write_text("boot-test-dsh-1\n", encoding="utf-8", newline="\n")

    tricky_cwd = r'/root/a"b\c'
    if os.name != "nt":
        (ancli_dir / "rootfs/root" / 'a"b\\c').mkdir(parents=True, exist_ok=True)
        bracket_guard = ""
    else:
        bracket_guard = '[() { case "${1:-}:${2:-}" in -d:*\'a"b\\c\') return 0 ;; esac; builtin [ "$@"; }\n'

    (ancli_dir / "rootfs/root/.ancli-config.json").write_bytes(
        b"\xef\xbb\xbf"
        + json.dumps(
            {"nested": {"default_cwd": "/root/wrong"}, "default_cwd": tricky_cwd, "lang": "zh"},
            separators=(",", ":"),
        ).encode("utf-8")
    )
    (ancli_dir / "bin/ancli_env.sh").write_text("", encoding="utf-8", newline="\n")

    captured_args = tmp_path / "proot_args.txt"
    proot_mock = ancli_dir / "bin/proot"
    proot_mock.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$@" > "{posix(captured_args)}"\nexit 0\n',
        encoding="utf-8",
        newline="\n",
    )
    proot_mock.chmod(0o755)

    mock_setsid = tmp_path / "setsid"
    mock_setsid.write_text(
        f'#!/bin/sh\n'
        f'mkdir -p "{posix(proc_dir)}/$$"\n'
        f'printf "%s (sh) S 1 %s %s 0 -1 0 0 0 0 0 0 0 0 0 20 0 1 0 98765 0 0 0\\n" "$$" "$$" "$$" > "{posix(proc_dir)}/$$/stat"\n'
        f'exec "$@"\n',
        encoding="utf-8",
        newline="\n",
    )
    mock_setsid.chmod(0o755)

    patched = (
        raw.decode("utf-8")
        .replace("#!/system/bin/sh", "#!/bin/sh\n" + bracket_guard, 1)
        .replace("/data/local/tmp/ancli", posix(ancli_dir))
        .replace("/system/bin/setsid", posix(mock_setsid))
        .replace("/system/bin/sh", "/bin/sh")
        .replace("/proc/sys/kernel/random/boot_id", posix(boot_id))
        .replace("/proc/$$/stat", posix(proc_dir) + "/$$/stat")
        .replace("/proc/$_pid/stat", posix(proc_dir) + "/$_pid/stat")
        .replace("/mnt/ancli-data", posix(tmp_path / "mnt_data"))
    )
    ctrl = tmp_path / "dsh-web-control.sh"
    ctrl.write_text(patched, encoding="utf-8", newline="\n")
    ctrl.chmod(0o755)

    task_id = "1700000_9"
    log_path = posix(ancli_dir / f".webui_{task_id}.log")
    r_start = run_bash(f'"{posix(ctrl)}" start {task_id} "{log_path}"', dict(os.environ))
    assert r_start.returncode == 0, r_start.stdout + r_start.stderr
    assert "DSH backend session started" in r_start.stdout
    args = captured_args.read_text(encoding="utf-8").splitlines()
    assert "-w" in args and args[args.index("-w") + 1] == tricky_cwd
    assert f"PWD={tricky_cwd}" in args
    assert (ancli_dir / ".dsh-web.task").read_text(encoding="utf-8").strip() == task_id
