---
name: doi-fetch
description: Batch DOI-to-Zotero SQLite fetcher. Takes a list of DOIs, fetches full metadata from CrossRef/DataCite using aiohttp concurrency, writes Zotero-compatible SQLite. Also supports JSON and BibTeX output. Triggers: batch doi, doi fetch, doi metadata, 批量DOI, DOI查元数据, DOI to zotero, DOI to sqlite.
---

# DOI Fetch

Run from repo root:

```bash
pip install aiohttp
python doi_fetch.py -i dois.txt -c 20 -o library.sqlite
```

## Common commands

```bash
# From file, high concurrency
python doi_fetch.py -i dois.txt -c 20 -o output.sqlite

# Direct DOIs
python doi_fetch.py -d 10.1038/xxx 10.1126/yyy -o library.sqlite

# Incremental: skip already-fetched
python doi_fetch.py -i new.txt -o library.sqlite --merge

# JSON / BibTeX output
python doi_fetch.py -i dois.txt --format json -o refs.json
python doi_fetch.py -i dois.txt --format bibtex -o refs.bib

# Extract DOIs from raw text
python doi_fetch.py --from-text "see 10.1038/xxx and 10.1126/yyy"
```

## Options

| Flag | Description |
|------|-------------|
| `-i, --input FILE` | File with DOIs (or text containing them) |
| `-d, --dois DOI...` | DOIs on command line |
| `-o, --output FILE` | Output file (default: zotero.sqlite) |
| `-c, --concurrency N` | Concurrent requests (default: 10, max: 30) |
| `--merge` | Merge into existing DB, skip already-fetched |
| `--format sqlite|json|bibtex` | Output format (default: sqlite) |
| `--from-text TEXT` | Extract DOIs from raw text |

## Workflow

1. Ensure `aiohttp` is installed: `pip install aiohttp`
2. Collect DOIs from user
3. Choose output path and concurrency
4. Run fetcher; retry failed DOIs with `--merge` if any
5. Report counts and output path

## Output: Zotero SQLite

The `.sqlite` matches Zotero's schema (items, itemData, creators, etc.) so Zotero opens it directly when placed next to a `storage/` folder.
