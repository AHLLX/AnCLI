"""Replay a dsh web token URL against a live dsh web server.

Usage:
    python scripts/probe_dsh_web_page.py --token <TOKEN> [--url http://127.0.0.1:3080]
    DSH_WEB_TOKEN=<TOKEN> python scripts/probe_dsh_web_page.py

Answers two questions at once: does the server accept that token (303 + cookie),
and if so does it then serve the SPA index and its assets.
"""
import argparse
import http.cookiejar
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def probe(url, label, opener, read=260):
    try:
        with opener.open(url, timeout=15) as resp:
            body = resp.read(read).decode(errors="replace")
            print(f"{label}: HTTP {resp.status}")
            print(f"    location={resp.headers.get('location')!r} set-cookie={'yes' if resp.headers.get('set-cookie') else 'no'}")
            print(f"    body[:120]={body[:120]!r}")
            return resp.status, body
    except urllib.error.HTTPError as exc:
        body = exc.read(read).decode(errors="replace")
        print(f"{label}: HTTP {exc.code} location={exc.headers.get('location')!r}")
        print(f"    body[:120]={body[:120]!r}")
        return exc.code, body
    except Exception as exc:
        print(f"{label}: FAILED {type(exc).__name__}: {exc}")
        return None, ""


def build_urls(base_url, token):
    parsed = urllib.parse.urlsplit(base_url.strip())
    base = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "")).rstrip("/")
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    query["token"] = [token]
    encoded_query = urllib.parse.urlencode(query, doseq=True)
    token_url = f"{base}/?{encoded_query}"
    return base, token_url


def main(argv=None):
    parser = argparse.ArgumentParser(description="Probe dsh web token exchange and SPA asset serving.")
    parser.add_argument("--token", default=os.environ.get("DSH_WEB_TOKEN", ""),
                        help="dsh web auth token (or set DSH_WEB_TOKEN)")
    parser.add_argument("--url", default=os.environ.get("DSH_WEB_URL", "http://127.0.0.1:3080"),
                        help="dsh web base URL (default: http://127.0.0.1:3080 or DSH_WEB_URL)")
    args = parser.parse_args(argv)

    token = (args.token or "").strip()
    if not token:
        print("[X] Missing dsh web token: pass --token or set DSH_WEB_TOKEN", file=sys.stderr)
        return 2

    base, token_url = build_urls(args.url, token)
    jar = http.cookiejar.CookieJar()

    print("== 1) raw token exchange (redirects NOT followed) ==")
    probe(token_url, "   GET /?token=…", urllib.request.build_opener(NoRedirect))

    print("\n== 2) full browser-like flow (cookie jar, redirects followed) ==")
    follow = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    status, body = probe(token_url, "   token URL     ", follow)
    print("   cookie jar:", [c.name[:22] + "…" for c in jar])
    status2, body2 = probe(f"{base}/", "   GET /         ", follow)

    print("\n== 3) SPA assets referenced by the index ==")
    assets = re.findall(r'(?:src|href)="([^"]+\.(?:js|css))"', body2 or body)
    print("   found:", assets[:4] or "none")
    for asset in assets[:2]:
        target = asset if asset.startswith("http") else base + ("" if asset.startswith("/") else "/") + asset
        probe(target, f"   {asset[:40]}", follow, read=80)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
