#!/system/bin/sh
# Work around PRoot reporting EACCES for faccessat2 on readable keyrings.
# Only amend apt-key's read-only existence check. gpgv still verifies the
# repository signatures against the original Ubuntu archive keyring.
rootfs="$1"
apt_key="$rootfs/usr/bin/apt-key"
[ -f "$apt_key" ] || exit 0
old='if [ ! -r "$FORCED_KEYRING" ]; then'
fixed='if ! { [ -r "$FORCED_KEYRING" ] || cat "$FORCED_KEYRING" >/dev/null 2>&1; }; then'
if grep -Fq "$fixed" "$apt_key"; then
    exit 0
fi
if grep -Fq "$old" "$apt_key"; then
    sed -i 's@if \[ ! -r "\$FORCED_KEYRING" \]; then@if ! { [ -r "$FORCED_KEYRING" ] || cat "$FORCED_KEYRING" >/dev/null 2>\&1; }; then@' "$apt_key" || exit 1
    grep -Fq "$fixed" "$apt_key" || exit 1
fi
