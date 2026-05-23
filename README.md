# doi-fetch

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10+-green.svg)](https://www.python.org/)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/AKI-215/doi-fetch/pulls)

Batch DOI-to-metadata fetcher with **async concurrency** and **Zotero-compatible SQLite** output.

Give it a list of DOIs — it hits CrossRef (with DataCite fallback) in parallel, parses the metadata, and writes a SQLite database that Zotero can open directly. Also supports JSON and BibTeX export.

## Features

- **True concurrency** — `aiohttp` + `asyncio.Semaphore`, up to 30 parallel connections
- **Zotero-compatible SQLite** — mirrors `zotero.sqlite` schema exactly (items, itemData, creators, etc.)
- **Incremental** — `--merge` skips already-fetched DOIs, never refetches
- **Multi-format** — SQLite, JSON, BibTeX
- **Auto DOI extraction** — paste raw text, it finds all DOIs
- **CrossRef first**, DataCite fallback
- **Zero-config** — no API key required

## Install

```bash
pip install aiohttp
git clone https://github.com/AKI-215/doi-fetch.git
cd doi-fetch
```

Or via pip (coming soon to PyPI):

```bash
pip install doi-fetch
```

## Quick Start

```bash
# Two DOIs on command line → Zotero SQLite
python doi_fetch.py -d 10.1038/s41586-021-03819-2 10.1126/science.1258096

# From file, high concurrency
python doi_fetch.py -i dois.txt -c 20 -o library.sqlite

# Incremental: merge new DOIs into existing DB
python doi_fetch.py -i new_batch.txt -o library.sqlite --merge

# JSON or BibTeX output
python doi_fetch.py -i dois.txt --format json -o refs.json
python doi_fetch.py -i dois.txt --format bibtex -o refs.bib

# Extract DOIs embedded in text
python doi_fetch.py --from-text "See 10.1038/s41586-021-03819-2 and 10.1126/science.1258096" -o refs.json
```

## Usage

```
python doi_fetch.py [OPTIONS]

Options:
  -i, --input FILE       File with DOIs (one per line, or text containing DOIs)
  -d, --dois DOI [DOI...] DOIs directly on command line
  -o, --output FILE      Output file (default: zotero.sqlite)
  -c, --concurrency N    Concurrent requests (default: 10, max: 30)
  --merge                Merge into existing output (skip already-fetched)
  --format {sqlite|json|bibtex}  Output format (default: sqlite)
  --from-text TEXT       Extract DOIs from arbitrary text
```

## Output: Zotero SQLite (`--format sqlite`, default)

The generated `.sqlite` database mirrors Zotero's core schema exactly:

| Table | Content |
|-------|---------|
| `items` | One row per DOI — itemTypeID (22=journalArticle), Zotero-style key, timestamps |
| `itemData` | EAV links: `(itemID, fieldID, valueID)` |
| `itemDataValues` | Deduplicated strings — titles, abstracts, DOIs |
| `creators` | Deduplicated `(firstName, lastName)` |
| `itemCreators` | Ordered creator associations with creatorTypeID |
| `itemTypes` | All 40 Zotero item types |
| `fields` | All 123 Zotero fields |
| `creatorTypes` | All 37 creator types |
| `doi_fetch_log` | Per-DOI fetch status for `--merge` resume |

Drop the `.sqlite` next to a Zotero `storage/` folder and Zotero opens it directly.

### Example output item

```json
{
  "title": "Highly accurate protein structure prediction with AlphaFold",
  "authors": ["Jumper, John", "Evans, Richard", "..."],
  "year": 2021,
  "journal": "Nature",
  "volume": "596",
  "issue": "7873",
  "pages": "583-589",
  "doi": "10.1038/s41586-021-03819-2",
  "abstract": "Proteins are essential to life...",
  "publisher": "Springer Science and Business Media LLC",
  "citation_key": "Jumper2021highly"
}
```

## Output: JSON (`--format json`)

```json
{
  "entries": { "10.1038/...": { ... }, ... },
  "total": 445,
  "updated": "2026-05-22T10:30:00"
}
```

## Output: BibTeX (`--format bibtex`)

```bibtex
@article{Jumper2021highly,
  author = {Jumper, John and Evans, Richard and ...},
  title = {Highly accurate protein structure prediction with AlphaFold},
  journal = {Nature},
  year = {2021},
  volume = {596},
  number = {7873},
  pages = {583--589},
  doi = {10.1038/s41586-021-03819-2}
}
```

## Concurrency & Performance

| Concurrency | 445 DOIs | Notes |
|------------|----------|-------|
| 20 | ~50s | 364/445 first pass |
| 10 | ~13s | retry batch, 52/81 |
| 4 | ~6s | retry batch, 29/29 |

- CrossRef polite pool: ~10 req/s without API key
- Set `CROSSREF_API_KEY` env var for higher limits
- `asyncio.Semaphore` prevents overwhelming APIs
- Automatic `429 Too Many Requests` backoff (3s delay)

## API Sources

- **CrossRef** (primary) — richest metadata, free, no key required
- **DataCite** (fallback) — covers datasets, preprints, grey literature
- Set `CROSSREF_API_KEY` for higher rate limits if you have a Plus token

## Real-World Example

Used to fetch metadata for **445 iron-based alloy corrosion papers** from a WoS export:

```bash
# Extract DOIs from CSV
python -c "import csv; rows=list(csv.DictReader(open('papers.csv'))); \
  open('dois.txt','w').write('\n'.join(r['DOI'] for r in rows if r['DOI']))"

# Fetch all 445 in ~60s
python doi_fetch.py -i dois.txt -c 20 -o corrosion.sqlite

# Result: 445 items, 1485 creators, full metadata
```

## License

MIT — see [LICENSE](LICENSE).
