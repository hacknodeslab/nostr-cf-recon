# nostr-cf-recon

PoC para medir qué porcentaje de relays Nostr públicos están detrás de un CDN
(Cloudflare, CloudFront, Fastly) y, por tanto, ocultan su IP origen.

## Motivación

Nostr se presenta como una red descentralizada, pero la centralización real no
se mide solo por número de relays — también por cuántos comparten infraestructura
oculta. Si una fracción significativa de relays está tras Cloudflare, una
incidencia en CF (corte, bloqueo regional, decisión de moderación) afecta a
buena parte del ecosistema de golpe, aunque "técnicamente" haya cientos de
relays distintos.

Este repo es el **paso 0** de una investigación más amplia: validar primero
*cuántos* relays están tras un CDN antes de invertir en *descubrir* su IP
origen real (fase 2: NIP-11 pubkey fingerprint, DNS history, Censys/Shodan,
validación con `--resolve`).

## Inspiración

Adaptación al ecosistema Nostr de técnicas de
[**CF-Hero**](https://github.com/musana/CF-Hero) — herramienta en Go para
recon anti-Cloudflare. En particular el patrón de check por rango CIDR
(`internal/dns/dns.go:53-80`). CF-Hero busca la IP origen; aquí solo medimos
la presencia del CDN como gate para decidir si la fase 2 vale la pena.

## Resultado del paso 0 (2026-05)

Sobre el export `nw-relays-all-20260503.xlsx` de
[nostr.watch](https://nostr.watch), filtrado a `in_rstate=True` +
`network=clearnet` (1079 hosts únicos, 1067 resueltos):

| Verdict | Count | % de resueltos |
| --- | ---: | ---: |
| cloudflare | 301 | 28.2% |
| cloudfront | 2 | 0.2% |
| fastly | 0 | 0% |
| direct | 764 | 71.6% |
| dns_error | 12 | — |

**~28% de los relays activos están detrás de un CDN — y Cloudflare es prácticamente
el único en uso.** Añadir CloudFront/Fastly no cambia la historia: el problema
de centralización en Nostr es, casi por entero, un problema de Cloudflare.

## Cómo funciona

1. Lee URLs/hostnames de un fichero (`wss://relay.x/`, `relay.x`, mezclados).
2. Normaliza a hostname y deduplica.
3. Resuelve A/AAAA con `socket.getaddrinfo` (`ThreadPoolExecutor`, 50 workers
   por defecto, timeout 5s).
4. Comprueba cada IP contra rangos publicados por cada CDN:
   - Cloudflare: `https://www.cloudflare.com/ips-v4` + `ips-v6`
   - CloudFront: `https://ip-ranges.amazonaws.com/ip-ranges.json` (service=CLOUDFRONT)
   - Fastly: `https://api.fastly.com/public-ip-list`
   - Las listas se cachean 7 días en `./.cdn_cache/`.
5. Reporta resumen + (opcional) detalle por host y volcado JSON.

`.onion` / `.i2p` se saltan (no resuelven por DNS).

## Uso

```bash
python3 -m venv .venv
.venv/bin/pip install rich openpyxl

# Si vienes de un export xlsx de nostr.watch:
.venv/bin/python extract_relays.py nw-relays-all-YYYYMMDD.xlsx relays.txt --online --clearnet

# Análisis:
.venv/bin/python check_cf.py relays.txt                       # resumen
.venv/bin/python check_cf.py relays.txt --detail              # un host por línea
.venv/bin/python check_cf.py relays.txt --json out.json       # vuelca estructurado
.venv/bin/python check_cf.py relays.txt --workers 100 --timeout 4
```

## Ficheros

- `check_cf.py` — script principal.
- `extract_relays.py` — helper para volcar el `url` de un xlsx de nostr.watch.
- `relays.txt`, `relays-online.txt` — listas de input (full / filtrada).
- `out.json`, `out-online.json` — resultados estructurados de las últimas corridas.
- `.cdn_cache/` — rangos CIDR cacheados (regenerados cada 7 días).

## Fuera de scope (de momento)

- Descubrir IP origen real (fase 2).
- CDNs sin lista pública (Akamai requiere ASN/CNAME heuristics).
- Subdomain enumeration, NIP-11 fetching, DNS history.
- Web UI, persistencia, fetcher automático de listas de relays.

## Filosofía

Validar primero, arquitecturar después. Si el número del paso 0 es relevante
(y lo es), la fase 2 se justifica. Si no, el proyecto se reorienta sin haber
gastado esfuerzo en infraestructura prematura.
