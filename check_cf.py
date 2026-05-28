"""PoC: what % of Nostr relays sit behind a CDN (Cloudflare, CloudFront, Fastly)?

Reads a list of relay URLs/hosts, resolves each to A/AAAA records, and
classifies every relay by CDN provider (or `direct`, `dns_error`, `skipped`).

CDN IP ranges are fetched once and cached under ./.cdn_cache/.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import socket
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

from rich.console import Console
from rich.table import Table

CACHE_DIR = ".cdn_cache"
CACHE_TTL_DAYS = 7

# Hardcoded fallback (CF-Hero list) used if the live fetch fails.
CLOUDFLARE_FALLBACK = [
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22",
    "103.31.4.0/22",   "141.101.64.0/18", "108.162.192.0/18",
    "190.93.240.0/20", "188.114.96.0/20", "197.234.240.0/22",
    "198.41.128.0/17", "162.158.0.0/15",  "104.16.0.0/13",
    "104.24.0.0/14",   "172.64.0.0/13",   "131.0.72.0/22",
    "2400:cb00::/32",  "2606:4700::/32",  "2803:f800::/32",
    "2405:b500::/32",  "2405:8100::/32",  "2a06:98c0::/29",
    "2c0f:f248::/32",
]


def _cache_path(name: str) -> str:
    return os.path.join(CACHE_DIR, name)


def _fresh(path: str) -> bool:
    if not os.path.exists(path):
        return False
    age = (os.path.getmtime(path) and (os.path.getmtime(path)))
    import time
    return (time.time() - os.path.getmtime(path)) < CACHE_TTL_DAYS * 86400


def _fetch(url: str, cache_name: str) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    p = _cache_path(cache_name)
    if _fresh(p):
        with open(p) as f:
            return f.read()
    req = urllib.request.Request(url, headers={"User-Agent": "nostr-cf-recon/0.1"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = resp.read().decode()
    with open(p, "w") as f:
        f.write(data)
    return data


def load_cloudflare() -> list[str]:
    try:
        v4 = _fetch("https://www.cloudflare.com/ips-v4", "cloudflare-v4.txt").splitlines()
        v6 = _fetch("https://www.cloudflare.com/ips-v6", "cloudflare-v6.txt").splitlines()
        return [c.strip() for c in v4 + v6 if c.strip()]
    except Exception:
        return CLOUDFLARE_FALLBACK


def load_cloudfront() -> list[str]:
    raw = _fetch("https://ip-ranges.amazonaws.com/ip-ranges.json", "aws-ip-ranges.json")
    data = json.loads(raw)
    nets = [p["ip_prefix"] for p in data.get("prefixes", []) if p.get("service") == "CLOUDFRONT"]
    nets += [p["ipv6_prefix"] for p in data.get("ipv6_prefixes", []) if p.get("service") == "CLOUDFRONT"]
    return nets


def load_fastly() -> list[str]:
    raw = _fetch("https://api.fastly.com/public-ip-list", "fastly.json")
    data = json.loads(raw)
    return data.get("addresses", []) + data.get("ipv6_addresses", [])


def build_provider_nets() -> dict[str, list]:
    providers = {
        "cloudflare": load_cloudflare(),
        "cloudfront": load_cloudfront(),
        "fastly":     load_fastly(),
    }
    return {
        name: [ipaddress.ip_network(c) for c in cidrs]
        for name, cidrs in providers.items()
    }


def normalize(line: str) -> str | None:
    s = line.strip()
    if not s or s.startswith("#"):
        return None
    if "://" not in s:
        s = "wss://" + s
    host = urlparse(s).hostname
    return host.lower() if host else None


def provider_for(ip: str, nets: dict[str, list]) -> str | None:
    addr = ipaddress.ip_address(ip)
    for name, ranges in nets.items():
        if any(addr in n for n in ranges):
            return name
    return None


def resolve(host: str, timeout: float) -> list[str]:
    socket.setdefaulttimeout(timeout)
    infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return sorted({i[4][0] for i in infos})


def classify(host: str, timeout: float, nets: dict[str, list]) -> dict:
    if host.endswith(".onion") or host.endswith(".i2p"):
        return {"host": host, "verdict": "skipped", "ips": [], "providers": [], "error": "onion/i2p"}
    try:
        ips = resolve(host, timeout)
    except (socket.gaierror, socket.timeout, OSError, UnicodeError) as e:
        return {"host": host, "verdict": "dns_error", "ips": [], "providers": [], "error": str(e)}
    matches = sorted({p for ip in ips if (p := provider_for(ip, nets))})
    if matches:
        verdict = matches[0] if len(matches) == 1 else "+".join(matches)
    else:
        verdict = "direct"
    return {"host": host, "verdict": verdict, "ips": ips, "providers": matches, "error": None}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", help="path to file with one relay URL/host per line")
    ap.add_argument("--workers", type=int, default=50)
    ap.add_argument("--timeout", type=float, default=5.0)
    ap.add_argument("--detail", action="store_true")
    ap.add_argument("--json", dest="json_out")
    args = ap.parse_args()

    console = Console()
    console.print("[dim]Loading CDN ranges (cached for 7d)…[/]")
    nets = build_provider_nets()
    for name, ranges in nets.items():
        console.print(f"  {name}: {len(ranges)} ranges")

    with open(args.input) as f:
        hosts, seen = [], set()
        for line in f:
            h = normalize(line)
            if h and h not in seen:
                seen.add(h)
                hosts.append(h)

    console.print(f"[bold]Resolving {len(hosts)} unique hosts with {args.workers} workers…[/]")

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(classify, h, args.timeout, nets) for h in hosts]
        for fut in as_completed(futures):
            results.append(fut.result())

    counts: dict[str, int] = {}
    for r in results:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1

    total = len(results)
    skipped = counts.get("skipped", 0)
    dns_err = counts.get("dns_error", 0)
    resolved = total - skipped - dns_err
    behind_any_cdn = sum(c for v, c in counts.items() if v not in {"direct", "dns_error", "skipped"})

    order = sorted(counts, key=lambda v: (
        v in {"dns_error", "skipped"},        # non-CDN status last
        v == "direct",                         # direct just before non-CDN status
        -counts[v],                            # then by descending count
    ))

    table = Table(title=f"Nostr relays by CDN — {total} hosts (resolved: {resolved})")
    table.add_column("verdict")
    table.add_column("count", justify="right")
    table.add_column("% of total", justify="right")
    table.add_column("% of resolved", justify="right")
    for v in order:
        n = counts[v]
        pct_total = f"{100*n/total:.1f}%" if total else "-"
        pct_res = f"{100*n/resolved:.1f}%" if resolved and v not in {"dns_error", "skipped"} else "-"
        table.add_row(v, str(n), pct_total, pct_res)
    console.print(table)
    if resolved:
        console.print(f"[bold]Behind any tracked CDN: {behind_any_cdn} / {resolved} resolved = {100*behind_any_cdn/resolved:.1f}%[/]")

    if args.detail:
        for r in sorted(results, key=lambda x: (x["verdict"], x["host"])):
            ips = ",".join(r["ips"]) or r["error"] or ""
            console.print(f"  [{r['verdict']:18}] {r['host']}  →  {ips}")

    if args.json_out:
        with open(args.json_out, "w") as f:
            json.dump({"counts": counts, "total": total, "resolved": resolved,
                       "behind_any_cdn": behind_any_cdn, "results": results}, f, indent=2)
        console.print(f"[dim]wrote {args.json_out}[/]")

    return 0


if __name__ == "__main__":
    sys.exit(main())
