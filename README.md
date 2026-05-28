# nostr-cf-recon

PoC measuring what percentage of public Nostr relays sit behind a CDN
(Cloudflare, CloudFront, Fastly) and therefore hide their origin IP.

## Motivation

Nostr markets itself as a decentralized network, but real centralization isn't
just about how many relays exist — it's also about how many share hidden
infrastructure. If a meaningful fraction of relays sits behind Cloudflare, a
single CF event (outage, regional block, moderation decision) hits a large
chunk of the ecosystem at once, even though there are "technically" hundreds
of distinct relays.

This repo is **step 0** of a broader investigation: first validate *how many*
relays are behind a CDN before investing effort into *unmasking* their real
origin IP (phase 2: NIP-11 pubkey fingerprint, DNS history, Censys/Shodan,
validation via `--resolve`).

## Inspiration

Nostr-flavored adaptation of techniques from
[**CF-Hero**](https://github.com/musana/CF-Hero) — a Go tool for anti-Cloudflare
recon. In particular the CIDR-range check pattern (`internal/dns/dns.go:53-80`).
CF-Hero hunts for the origin IP; here we only measure CDN presence as a gate
to decide whether phase 2 is worth the effort.

## Step-0 result (2026-05)

Run against the `nw-relays-all-20260503.xlsx` export from
[nostr.watch](https://nostr.watch), filtered to `in_rstate=True` +
`network=clearnet` (1079 unique hosts, 1067 resolved):

| Verdict | Count | % of resolved |
| --- | ---: | ---: |
| cloudflare | 301 | 28.2% |
| cloudfront | 2 | 0.2% |
| fastly | 0 | 0% |
| direct | 764 | 71.6% |
| dns_error | 12 | — |

**~28% of active relays sit behind a CDN — and Cloudflare is virtually the
only one in use.** Adding CloudFront/Fastly barely moves the needle:
centralization in Nostr is, almost entirely, a Cloudflare story.

## How it works

1. Reads URLs/hostnames from a file (`wss://relay.x/`, `relay.x`, mixed).
2. Normalizes to hostname and deduplicates.
3. Resolves A/AAAA records with `socket.getaddrinfo`
   (`ThreadPoolExecutor`, 50 workers by default, 5s timeout).
4. Checks each IP against ranges published by each CDN:
   - Cloudflare: `https://www.cloudflare.com/ips-v4` + `ips-v6`
   - CloudFront: `https://ip-ranges.amazonaws.com/ip-ranges.json` (service=CLOUDFRONT)
   - Fastly: `https://api.fastly.com/public-ip-list`
   - Lists are cached for 7 days in `./.cdn_cache/`.
5. Prints a summary and (optionally) per-host detail plus a JSON dump.

`.onion` / `.i2p` hosts are skipped (they don't resolve over DNS).

## Usage

```bash
python3 -m venv .venv
.venv/bin/pip install rich openpyxl

# Starting from a nostr.watch xlsx export:
.venv/bin/python extract_relays.py nw-relays-all-YYYYMMDD.xlsx relays.txt --online --clearnet

# Analysis:
.venv/bin/python check_cf.py relays.txt                       # summary
.venv/bin/python check_cf.py relays.txt --detail              # one host per line
.venv/bin/python check_cf.py relays.txt --json out.json       # structured dump
.venv/bin/python check_cf.py relays.txt --workers 100 --timeout 4
```

## Files

- `check_cf.py` — main script.
- `extract_relays.py` — helper that dumps the `url` column of a nostr.watch xlsx.
- `relays.txt`, `relays-online.txt` — input lists (full / filtered).
- `out.json`, `out-online.json` — structured results from the latest runs.
- `.cdn_cache/` — cached CIDR ranges (refreshed every 7 days).

## Out of scope (for now)

- Discovering the real origin IP (phase 2).
- CDNs without a public IP list (Akamai requires ASN/CNAME heuristics).
- Subdomain enumeration, NIP-11 fetching, DNS history.
- Web UI, persistence, automated relay-list fetcher.

## Philosophy

Validate first, architect later. If the step-0 number is meaningful (and it
is), phase 2 justifies itself. If it weren't, the project would pivot
without having burned effort on premature infrastructure.
