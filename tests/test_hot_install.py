from pathlib import Path
import os, subprocess
ROOT = Path(__file__).resolve().parents[1]
BASH = r'C:\Program Files\Git\bin\bash.exe' if os.name == 'nt' else '/bin/bash'
def posix(p):
    s=str(p).replace('\\','/')
    return '/'+s[0].lower()+s[2:] if len(s)>1 and s[1]==':' else s

def check(old, new):
    script='. "$POLICY"; ancli_hot_tree_compatible "$OLD" "$NEW"'
    env={**os.environ,'POLICY':posix(ROOT/'src/module/hot-install-policy.sh'),'OLD':posix(old),'NEW':posix(new)}
    return subprocess.run([BASH,'-c',script],env=env,capture_output=True,text=True).returncode

def test_matching_overlay_permits_hot_install(tmp_path):
    old=tmp_path/'old'; new=tmp_path/'new'
    for root in (old,new):
        (root/'bin').mkdir(parents=True); (root/'bin/ancli').write_text('same')
    assert check(old,new)==0
    (new/'bin/ancli').write_text('changed')
    assert check(old,new)!=0

def test_overlay_removed_or_added_requires_reboot(tmp_path):
    old=tmp_path/'old'; new=tmp_path/'new'
    for root in (old,new): (root/'bin').mkdir(parents=True)
    (old/'bin/extra').write_text('old')
    assert check(old,new)!=0
    (old/'bin/extra').unlink(); (new/'bin/extra').write_text('new')
    assert check(old,new)!=0

def test_new_install_without_prior_overlay_is_allowed(tmp_path):
    new=tmp_path/'new'; new.mkdir()
    assert check(tmp_path/'missing',new)==0

def test_request_is_gated_on_kernelsu_capability(tmp_path):
    policy=(ROOT/'src/module/hot-install-policy.sh').read_text(encoding='utf-8')
    policy=policy.replace('/data/adb/modules/ancli/system',posix(tmp_path/'no-old'))
    p=tmp_path/'policy.sh'; p.write_text(policy,encoding='utf-8')
    (tmp_path/'hotinstall.sh').write_text('#!/bin/sh\n')
    env={**os.environ,'POLICY':posix(p),'MODPATH':posix(tmp_path)}
    command='ui_print(){ :; }; . "$POLICY"; ancli_request_hot_install; printf "%s|%s" "${MODULE_HOT_INSTALL_REQUEST:-no}" "${MODULE_HOT_RUN_SCRIPT:-no}"'
    for flag,expected in [('false','no|no'),('true','true|hotinstall.sh')]:
        env['KSU_HAS_HOT_INSTALL']=flag
        r=subprocess.run([BASH,'-c',command],env=env,capture_output=True,text=True)
        assert r.returncode==0 and r.stdout==expected


def test_missing_hook_does_not_request_hot_install(tmp_path):
    policy=(ROOT/'src/module/hot-install-policy.sh').read_text(encoding='utf-8').replace('/data/adb/modules/ancli/system',posix(tmp_path/'none'))
    p=tmp_path/'policy.sh'; p.write_text(policy,encoding='utf-8')
    env={**os.environ,'POLICY':posix(p),'MODPATH':posix(tmp_path),'KSU_HAS_HOT_INSTALL':'true'}
    command='ui_print(){ :; }; . "$POLICY"; ancli_request_hot_install; printf "%s" "${MODULE_HOT_INSTALL_REQUEST:-no}"'
    r=subprocess.run([BASH,'-c',command],env=env,capture_output=True,text=True)
    assert r.returncode==0 and r.stdout=='no'
