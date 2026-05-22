# doi-fetch

Batch DOI-to-metadata fetcher with async concurrency and Zotero-compatible SQLite output.

## Install

```bash
pip install aiohttp
git clone https://github.com/AKI-215/doi-fetch.git
```

## Quick start

```bash
# One-shot: 2 DOIs → Zotero SQLite
python doi_fetch.py -d 10.1038/s41586-021-03819-2 10.1126/science.1258096 -o library.sqlite

# From file, high concurrency
echo 10.1038/nature12373 > dois.txt
python doi_fetch.py -i dois.txt -c 20 -o library.sqlite

# Incremental: merge new DOIs into existing DB
python doi_fetch.py -i new_dois.txt -o library.sqlite --merge

# JSON / BibTeX output
python doi_fetch.py -i dois.txt --format json -o refs.json
python doi_fetch.py -i dois.txt --format bibtex -o refs.bib
```

## How it works

1. DOIs → **aiohttp** concurrent requests to CrossRef API (DataCite fallback)
2. Parsed metadata → written to a **Zotero-compatible SQLite** database

## Output: Zotero SQLite (default)

The generated `.sqlite` mirrors Zotero's exact schema:

| Table | Content |
|-------|---------|
| `items` | One row per DOI (itemTypeID=22 for journal articles) |
| `itemData` | EAV field-value links (itemID → fieldID → valueID) |
| `itemDataValues` | Deduplicated strings (titles, abstracts, DOIs) |
| `creators` | Deduplicated author names |
| `itemCreators` | Ordered creator associations |
| `itemTypes` / `fields` / `creatorTypes` | Full Zotero reference tables |
| `doi_fetch_log` | Tracks fetched DOIs for `--merge` resume |

Drop it next to a Zotero `storage/` folder and Zotero opens it directly.

## Options

| Flag | Description |
|------|-------------|
| `-i, --input FILE` | File with DOIs (or text containing DOIs) |
| `-d, --dois DOI...` | DOIs directly on command line |
| `-o, --output FILE` | Output file (default: zotero.sqlite) |
| `-c, --concurrency N` | Max concurrent requests (default: 10, max: 30) |
| `--merge` | Merge into existing output instead of overwrite |
| `--format sqlite\|json\|bibtex` | Output format (default: sqlite) |
| `--from-text TEXT` | Extract DOIs from arbitrary text |

## Concurrency

- `aiohttp` + `asyncio.Semaphore` — true async parallel HTTP
- Default 10 concurrent, configurable up to 30
- CrossRef first (richest metadata), DataCite fallback
- Auto backoff on `429 Too Many Requests`

## API notes

- CrossRef: free, no key required (~10 req/s polite pool)
- DataCite: free, no key required
- Set `CROSSREF_API_KEY` env var for higher rate limits
