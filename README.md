# doi-fetch · DOI 批量元数据抓取工具

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10+-green.svg)](https://www.python.org/)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/AKI-215/doi-fetch/pulls)

批量 DOI → 元数据 / Batch DOI → metadata

给定一批 DOI，并发的从 CrossRef（DataCite 兜底）抓取元数据，写入 **Zotero 兼容 SQLite**。同时支持 JSON 和 BibTeX 导出。

Give it a list of DOIs — it hits CrossRef (with DataCite fallback) in parallel, parses metadata, and writes a **Zotero-compatible SQLite** database. JSON and BibTeX export also supported.

---

## 功能 / Features

- **真正并发** — `aiohttp` + `asyncio.Semaphore`，最多 30 并发
- **Zotero SQLite** — 完全对齐 `zotero.sqlite` schema
- **增量追加** — `--merge` 模式，已抓取的不重复
- **多格式** — SQLite / JSON / BibTeX
- **智能提取** — 从任意文本中自动识别 DOI
- **零配置** — 无需 API Key
- **True concurrency** — up to 30 parallel connections
- **Zotero-compatible SQLite** — exact schema match
- **Incremental** — `--merge` skips already-fetched DOIs
- **Multi-format** — SQLite, JSON, BibTeX
- **Auto DOI extraction** — paste raw text, finds all DOIs
- **Zero-config** — no API key required

## 安装 / Install

```bash
pip install aiohttp
git clone https://github.com/AKI-215/doi-fetch.git
cd doi-fetch
```

## 快速开始 / Quick Start

```bash
# 命令行直接给 DOI / DOIs on command line
python doi_fetch.py -d 10.1038/s41586-021-03819-2 10.1126/science.1258096

# 从文件批量抓取 / From file, high concurrency
python doi_fetch.py -i dois.txt -c 20 -o library.sqlite

# 增量追加 / Incremental merge
python doi_fetch.py -i new_batch.txt -o library.sqlite --merge

# JSON / BibTeX 导出
python doi_fetch.py -i dois.txt --format json -o refs.json
python doi_fetch.py -i dois.txt --format bibtex -o refs.bib

# 从文本中提取 DOI / Extract from raw text
python doi_fetch.py --from-text "参见 10.1038/s41586-021-03819-2 和 10.1126/science.1258096"
```

## 选项 / Options

| 参数 | 说明 |
|------|------|
| `-i, --input FILE` | DOI 文件（每行一个，或含 DOI 的文本） |
| `-d, --dois DOI ...` | 命令行直接给定 DOI |
| `-o, --output FILE` | 输出文件（默认 `zotero.sqlite`） |
| `-c, --concurrency N` | 并发数（默认 10，最大 30） |
| `--merge` | 并入已有库，跳过已抓取 |
| `--format sqlite\|json\|bibtex` | 输出格式（默认 sqlite） |
| `--from-text TEXT` | 从文本中提取 DOI |

## 输出格式 / Output Formats

### SQLite（默认）

完全对齐 Zotero 核心 schema：

| 表 / Table | 内容 / Content |
|-----------|---------|
| `items` | 每条 DOI 一行，含 itemTypeID、Zotero 风格 key、时间戳 |
| `itemData` | EAV 关联 `(itemID, fieldID, valueID)` |
| `itemDataValues` | 去重字符串（标题、摘要、DOI 等） |
| `creators` | 去重 `(firstName, lastName)` |
| `itemCreators` | 有序作者关联 |
| `itemTypes` | 全部 40 种 Zotero 条目类型 |
| `fields` | 全部 123 个 Zotero 字段 |
| `creatorTypes` | 全部 37 种作者类型 |
| `doi_fetch_log` | 每条 DOI 抓取状态，用于 `--merge` 去重 |

把 `.sqlite` 放在 Zotero `storage/` 文件夹旁边即可直接打开。

### JSON

```json
{
  "entries": { "10.1038/...": { ... } },
  "total": 445,
  "updated": "2026-05-22T10:30:00"
}
```

### BibTeX

```bibtex
@article{Jumper2021highly,
  author = {Jumper, John and Evans, Richard and ...},
  title  = {Highly accurate protein structure prediction with AlphaFold},
  journal = {Nature},
  year   = {2021},
  volume = {596},
  doi    = {10.1038/s41586-021-03819-2}
}
```

## 并发性能 / Concurrency

| 并发数 | 445 篇 | 备注 |
|--------|--------|------|
| 20 | ~50s | 首轮 364/445 |
| 10 | ~13s | 重试 52/81 |
| 4 | ~6s | 重试 29/29 |

- CrossRef 礼貌池 ~10 req/s（无 Key）
- 设 `CROSSREF_API_KEY` 环境变量提升限速
- 自动 429 退避（3s）

## API 来源 / Sources

- **CrossRef**（主） — 最丰富的元数据，免费
- **DataCite**（备份） — 数据集、预印本、灰色文献
- 如有 Plus Token，设 `CROSSREF_API_KEY` 提升速率

## 实际案例 / Real-World Example

从 WoS 导出的 445 篇铁基合金腐蚀文献提取元数据：

```bash
# 从 CSV 提取 DOI
python -c "import csv; rows=list(csv.DictReader(open('papers.csv'))); \
  open('dois.txt','w').write('\n'.join(r['DOI'] for r in rows if r['DOI']))"

# 60 秒内抓完全部
python doi_fetch.py -i dois.txt -c 20 -o corrosion.sqlite

# 结果: 445 条目, 1485 作者, 完整元数据
```

## 许可 / License

MIT — 详见 [LICENSE](LICENSE)。
