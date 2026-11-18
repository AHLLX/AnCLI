#!/bin/sh
set -eu
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
NODE_VERSION=v24.22.3
case "$(uname -m)" in x86_64|amd64) node_arch=x64; expected=061ec3e3062115705b56b51fa9cc330ab50d34b6969387d4e4b1a50c6809acc2 ;; aarch64|arm64) node_arch=arm64; expected=261af2af250dfd7e9f25cf151d1b2713a0adf8f10a9ad116c4947f68627affe8 ;; *) echo '[X] Unsupported Node architecture' >&2; exit 1 ;; esac
if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
    node_major=$(node -p 'Number(process.versions.node.split(".")[0])' 2>/dev/null || true)
    case "$node_major" in ''|*[!0-9]*) ;; *) if [ "$node_major" -ge 24 ]; then echo ">> Node $(node --version), npm $(npm --version) ready"; exit 0; fi ;; esac
fi
node_file="node-${NODE_VERSION}-linux-${node_arch}.tar.gz"
node_tmp=$(mktemp -d /tmp/ancli-node.XXXXXX)
trap 'rm -rf "$node_tmp"' EXIT HUP INT TERM
for base in "https://registry.npmmirror.com/-/binary/node/${NODE_VERSION}" "https://nodejs.org/dist/${NODE_VERSION}"; do
    echo ">> Downloading Node ${NODE_VERSION} ($node_arch)..."
    if curl -fLsS --connect-timeout 20 --max-time 600 --speed-limit 32768 --speed-time 30 "$base/$node_file" -o "$node_tmp/$node_file"; then
        actual=$(sha256sum "$node_tmp/$node_file" | cut -d' ' -f1)
        if [ -n "$expected" ] && [ "$expected" = "$actual" ]; then
            tar -xzf "$node_tmp/$node_file" -C /usr/local --strip-components=1
            hash -r
            node --version
            npm --version
            exit 0
        fi
        echo '[X] Node checksum mismatch' >&2
    fi
done
echo '[X] Unable to download or verify Node securely' >&2
exit 1
