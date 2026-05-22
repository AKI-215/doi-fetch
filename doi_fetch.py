#!/usr/bin/env python3
"""
Batch DOI-to-Zotero-sqlite metadata fetcher with aiohttp concurrency.

Fetches metadata from CrossRef (primary) and DataCite (fallback),
writes to a SQLite database matching Zotero's core schema so the
file can be opened directly as a Zotero data directory.

Zotero-relevant tables written:
  items, itemData, itemDataValues, creators, itemCreators,
  itemTypes, fields, itemTypeFields, creatorTypes
"""

import asyncio
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import aiohttp
except ImportError:
    print("aiohttp is required: pip install aiohttp", file=sys.stderr)
    sys.exit(1)

CROSSREF_API = "https://api.crossref.org/works/{doi}"
DATACITE_API = "https://api.datacite.org/works/{doi}"
USER_AGENT = "DOI-Fetch/1.0 (https://github.com; mailto:dev@example.com)"
MAX_CONCURRENT = 30
DEFAULT_CONCURRENT = 10
DOI_RE_PATTERN = re.compile(r'\b10\.\d{4,}/[^\s"\'<>]+')

# ── Zotero itemTypeID constants (must match real Zotero) ────────────────
ITEM_TYPE_JOURNAL_ARTICLE = 22
ITEM_TYPE_BOOK = 7
ITEM_TYPE_BOOK_SECTION = 8
ITEM_TYPE_CONFERENCE_PAPER = 11
ITEM_TYPE_PREPRINT = 31
ITEM_TYPE_REPORT = 34
ITEM_TYPE_DATASET = 12
ITEM_TYPE_THESIS = 37
ITEM_TYPE_WEBPAGE = 40
ITEM_TYPE_DOCUMENT = 14   # fallback

# CrossRef type → Zotero itemTypeID
CROSSREF_TYPE_MAP = {
    "journal-article": ITEM_TYPE_JOURNAL_ARTICLE,
    "journal-issue": ITEM_TYPE_JOURNAL_ARTICLE,
    "book": ITEM_TYPE_BOOK,
    "book-chapter": ITEM_TYPE_BOOK_SECTION,
    "book-part": ITEM_TYPE_BOOK_SECTION,
    "book-section": ITEM_TYPE_BOOK_SECTION,
    "proceedings-article": ITEM_TYPE_CONFERENCE_PAPER,
    "proceedings": ITEM_TYPE_CONFERENCE_PAPER,
    "posted-content": ITEM_TYPE_PREPRINT,
    "report": ITEM_TYPE_REPORT,
    "report-series": ITEM_TYPE_REPORT,
    "dataset": ITEM_TYPE_DATASET,
    "dissertation": ITEM_TYPE_THESIS,
    "standard": ITEM_TYPE_DOCUMENT,
    "reference-entry": ITEM_TYPE_DOCUMENT,
    "component": ITEM_TYPE_DOCUMENT,
}
CREATOR_TYPE_AUTHOR = 8
CREATOR_TYPE_EDITOR = 10

# ── Static Zotero reference data (idempotent, shared across all items) ──
ZOTERO_FIELDS: list[tuple[int, str]] = [
    (1, 'title'), (2, 'abstractNote'), (3, 'artworkMedium'), (4, 'medium'),
    (5, 'artworkSize'), (6, 'date'), (7, 'language'), (8, 'shortTitle'),
    (9, 'archive'), (10, 'archiveLocation'), (11, 'libraryCatalog'),
    (12, 'callNumber'), (13, 'url'), (14, 'accessDate'), (15, 'rights'),
    (16, 'extra'), (17, 'audioRecordingFormat'), (18, 'seriesTitle'),
    (19, 'volume'), (20, 'numberOfVolumes'), (21, 'place'), (22, 'label'),
    (23, 'publisher'), (24, 'runningTime'), (25, 'ISBN'), (26, 'billNumber'),
    (27, 'number'), (28, 'code'), (29, 'codeVolume'), (30, 'section'),
    (31, 'codePages'), (32, 'pages'), (33, 'legislativeBody'),
    (34, 'authority'), (35, 'session'), (36, 'history'), (37, 'blogTitle'),
    (38, 'publicationTitle'), (39, 'websiteType'), (40, 'type'), (41, 'series'),
    (42, 'seriesNumber'), (43, 'edition'), (44, 'numPages'), (45, 'bookTitle'),
    (46, 'caseName'), (47, 'court'), (48, 'dateDecided'), (49, 'docketNumber'),
    (50, 'reporter'), (51, 'reporterVolume'), (52, 'firstPage'),
    (53, 'versionNumber'), (54, 'system'), (55, 'company'),
    (56, 'programmingLanguage'), (57, 'proceedingsTitle'),
    (58, 'conferenceName'), (59, 'DOI'), (60, 'identifier'),
    (61, 'repository'), (62, 'repositoryLocation'), (63, 'format'),
    (64, 'citationKey'), (65, 'dictionaryTitle'), (66, 'subject'),
    (67, 'encyclopediaTitle'), (68, 'distributor'), (69, 'genre'),
    (70, 'videoRecordingFormat'), (71, 'forumTitle'), (72, 'postType'),
    (73, 'committee'), (74, 'documentNumber'), (75, 'interviewMedium'),
    (76, 'issue'), (77, 'seriesText'), (78, 'journalAbbreviation'),
    (79, 'ISSN'), (80, 'letterType'), (81, 'manuscriptType'), (82, 'mapType'),
    (83, 'scale'), (84, 'country'), (85, 'assignee'), (86, 'issuingAuthority'),
    (87, 'patentNumber'), (88, 'filingDate'), (89, 'applicationNumber'),
    (90, 'priorityNumbers'), (91, 'issueDate'), (92, 'references'),
    (93, 'legalStatus'), (94, 'status'), (95, 'episodeNumber'),
    (96, 'audioFileType'), (97, 'archiveID'), (98, 'presentationType'),
    (99, 'meetingName'), (100, 'programTitle'), (101, 'network'),
    (102, 'reportNumber'), (103, 'reportType'), (104, 'institution'),
    (105, 'organization'), (106, 'nameOfAct'), (107, 'codeNumber'),
    (108, 'publicLawNumber'), (109, 'dateEnacted'), (110, 'thesisType'),
    (111, 'university'), (112, 'studio'), (113, 'websiteTitle'),
    (114, 'eventPlace'), (115, 'originalDate'), (116, 'originalPublisher'),
    (117, 'originalPlace'), (118, 'partNumber'), (119, 'partTitle'),
    (120, 'PMID'), (121, 'PMCID'), (122, 'priorityDate'), (123, 'sessionTitle'),
]

JOURNAL_ARTICLE_FIELDS: set[int] = {1, 2, 6, 7, 11, 13, 14, 15, 19, 32, 38, 59, 64, 76, 78, 79, 60}
BOOK_FIELDS: set[int] = {1, 2, 6, 7, 11, 13, 14, 15, 23, 25, 32, 43, 44, 18, 59, 64, 60}
BOOK_SECTION_FIELDS: set[int] = {1, 2, 6, 7, 11, 13, 14, 15, 19, 32, 45, 23, 25, 43, 59, 64, 60}
PREPRINT_FIELDS: set[int] = {1, 2, 6, 7, 11, 13, 14, 15, 59, 60}
CONFERENCE_FIELDS: set[int] = {1, 2, 6, 7, 11, 13, 14, 15, 57, 58, 19, 32, 23, 59, 64, 60}

ITEM_TYPE_FIELD_MAP: dict[int, set[int]] = {
    ITEM_TYPE_JOURNAL_ARTICLE: JOURNAL_ARTICLE_FIELDS,
    ITEM_TYPE_BOOK: BOOK_FIELDS,
    ITEM_TYPE_BOOK_SECTION: BOOK_SECTION_FIELDS,
    ITEM_TYPE_CONFERENCE_PAPER: CONFERENCE_FIELDS,
    ITEM_TYPE_PREPRINT: PREPRINT_FIELDS,
    ITEM_TYPE_THESIS: {1, 2, 6, 7, 11, 13, 14, 15, 23, 59, 64, 60, 110, 111},
    ITEM_TYPE_REPORT: {1, 2, 6, 7, 11, 13, 14, 15, 23, 59, 64, 60, 104},
    ITEM_TYPE_DOCUMENT: {1, 2, 6, 7, 11, 13, 14, 15, 59, 60},
}

ZOTERO_ITEM_TYPES: list[tuple[int, str]] = [
    (1, 'annotation'), (2, 'artwork'), (3, 'attachment'), (4, 'audioRecording'),
    (5, 'bill'), (6, 'blogPost'), (7, 'book'), (8, 'bookSection'),
    (9, 'case'), (10, 'computerProgram'), (11, 'conferencePaper'), (12, 'dataset'),
    (13, 'dictionaryEntry'), (14, 'document'), (15, 'email'), (16, 'encyclopediaArticle'),
    (17, 'film'), (18, 'forumPost'), (19, 'hearing'), (20, 'instantMessage'),
    (21, 'interview'), (22, 'journalArticle'), (23, 'letter'), (24, 'magazineArticle'),
    (25, 'manuscript'), (26, 'map'), (27, 'newspaperArticle'), (28, 'note'),
    (29, 'patent'), (30, 'podcast'), (31, 'preprint'), (32, 'presentation'),
    (33, 'radioBroadcast'), (34, 'report'), (35, 'standard'), (36, 'statute'),
    (37, 'thesis'), (38, 'tvBroadcast'), (39, 'videoRecording'), (40, 'webpage'),
]

ZOTERO_CREATOR_TYPES: list[tuple[int, str]] = [
    (1, 'artist'), (2, 'contributor'), (3, 'performer'), (4, 'composer'),
    (5, 'wordsBy'), (6, 'sponsor'), (7, 'cosponsor'), (8, 'author'),
    (9, 'commenter'), (10, 'editor'), (11, 'translator'), (12, 'seriesEditor'),
    (13, 'bookAuthor'), (14, 'counsel'), (15, 'programmer'), (16, 'reviewedAuthor'),
    (17, 'recipient'), (18, 'director'), (19, 'scriptwriter'), (20, 'producer'),
    (21, 'interviewee'), (22, 'interviewer'), (23, 'cartographer'), (24, 'inventor'),
    (25, 'attorneyAgent'), (26, 'podcaster'), (27, 'guest'), (28, 'presenter'),
    (29, 'castMember'), (30, 'originalCreator'), (31, 'host'), (32, 'narrator'),
    (33, 'executiveProducer'), (34, 'seriesCreator'), (35, 'chair'), (36, 'organizer'),
    (37, 'creator'),
]


def clean_doi(raw: str) -> str:
    doi = raw.strip()
    doi = re.sub(r'^https?://doi\.org/', '', doi)
    doi = re.sub(r'^doi:', '', doi, flags=re.IGNORECASE)
    return doi.rstrip('.')


def extract_dois(text: str) -> list[str]:
    return [clean_doi(m) for m in DOI_RE_PATTERN.findall(text)]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class DOIFetcher:
    def __init__(self, concurrency: int = DEFAULT_CONCURRENT, api_key: str = None):
        self.concurrency = min(concurrency, MAX_CONCURRENT)
        self.api_key = api_key or os.environ.get("CROSSREF_API_KEY")
        self.sem = asyncio.Semaphore(self.concurrency)
        self.session = None

    async def __aenter__(self):
        conn = aiohttp.TCPConnector(limit=self.concurrency + 5, limit_per_host=self.concurrency)
        hdrs = {"User-Agent": USER_AGENT}
        if self.api_key:
            hdrs["Crossref-Plus-API-Token"] = f"Bearer {self.api_key}"
        self.session = aiohttp.ClientSession(connector=conn,
            timeout=aiohttp.ClientTimeout(total=30), headers=hdrs)
        return self

    async def __aexit__(self, *a):
        if self.session:
            await self.session.close()

    async def _fetch_crossref(self, doi: str) -> dict | None:
        async with self.sem:
            try:
                async with self.session.get(CROSSREF_API.format(doi=doi)) as resp:
                    if resp.status == 429:
                        await asyncio.sleep(3)
                        return None
                    if resp.status in (404, 422):
                        return None
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                    return self._parse_crossref(doi, data.get("message", {}))
            except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError):
                return None

    async def _fetch_datacite(self, doi: str) -> dict | None:
        async with self.sem:
            try:
                async with self.session.get(DATACITE_API.format(doi=doi)) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                    return self._parse_datacite(doi, data.get("data", data))
            except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError):
                return None

    def _parse_crossref(self, doi: str, msg: dict) -> dict:
        import html as _html
        authors: list[tuple[str, str]] = []
        for a in msg.get("author", []):
            family = (a.get("family") or "").strip()
            given = (a.get("given") or "").strip()
            if family:
                authors.append((given, family))

        editors: list[tuple[str, str]] = []
        for e in msg.get("editor", []):
            family = (e.get("family") or "").strip()
            given = (e.get("given") or "").strip()
            if family:
                editors.append((given, family))

        # year
        dp = (msg.get("published-print") or msg.get("published-online") or {})
        year = None
        if dp.get("date-parts") and dp["date-parts"][0]:
            year = dp["date-parts"][0][0]
        if not year and msg.get("created", {}).get("date-parts"):
            year = msg["created"]["date-parts"][0][0]

        date_str = str(year) if year else None

        container = msg.get("container-title") or []
        journal = container[0] if container else None
        short_container = msg.get("short-container-title") or []
        journal_abbr = short_container[0] if short_container else None

        issue = str(msg["issue"]) if msg.get("issue") else None

        isbn = None
        if msg.get("isbn-type"):
            for e in msg["isbn-type"]:
                if e.get("value"):
                    isbn = e["value"]
                    break

        abstract = _html.unescape(re.sub(r"<[^>]+>", "", msg.get("abstract", "") or "")).strip() or None

        return {
            "doi": doi,
            "title": (msg.get("title") or [None])[0],
            "authors": authors,
            "editors": editors,
            "year": year,
            "date_str": date_str,
            "journal": journal,
            "journal_abbr": journal_abbr,
            "issn": (msg.get("ISSN") or [None])[0],
            "volume": str(msg["volume"]) if msg.get("volume") else None,
            "issue": issue,
            "pages": msg.get("page"),
            "abstract": abstract,
            "publisher": msg.get("publisher"),
            "isbn": isbn,
            "type": msg.get("type"),
            "language": msg.get("language"),
            "url": f"https://doi.org/{doi}",
            "source": "crossref",
            "fetched_at": _now(),
        }

    def _parse_datacite(self, doi: str, data: dict) -> dict:
        attrs = data.get("attributes", data)
        authors: list[tuple[str, str]] = []
        for a in attrs.get("creators", []):
            name = (a.get("name") or "").strip()
            given = (a.get("givenName") or "").strip()
            family = (a.get("familyName") or "").strip()
            if family and given:
                authors.append((given, family))
            elif name:
                parts = name.rsplit(" ", 1)
                authors.append((parts[0], parts[1]) if len(parts) == 2 else ("", name))

        container = attrs.get("container", {}) if isinstance(attrs.get("container"), dict) else {}
        return {
            "doi": doi,
            "title": attrs.get("title"),
            "authors": authors,
            "editors": [],
            "year": attrs.get("publicationYear"),
            "date_str": str(attrs["publicationYear"]) if attrs.get("publicationYear") else None,
            "journal": container.get("title"),
            "journal_abbr": None,
            "issn": None,
            "volume": str(attrs["volume"]) if attrs.get("volume") else None,
            "issue": str(attrs["issue"]) if attrs.get("issue") else None,
            "pages": f"{attrs.get('firstPage','')}–{attrs.get('lastPage','')}" if attrs.get("firstPage") else None,
            "abstract": attrs.get("description"),
            "publisher": attrs.get("publisher"),
            "isbn": None,
            "type": (attrs.get("types") or {}).get("resourceTypeGeneral"),
            "language": attrs.get("language"),
            "url": f"https://doi.org/{doi}",
            "source": "datacite",
            "fetched_at": _now(),
        }

    async def fetch_batch(self, dois: list[str]) -> dict[str, dict]:
        tasks = [self._fetch_crossref(d) for d in dois]
        raw = await asyncio.gather(*tasks, return_exceptions=True)
        results: dict[str, dict] = {}
        retry: list[str] = []
        for doi, r in zip(dois, raw):
            if isinstance(r, dict) and r is not None:
                results[doi] = r
            else:
                retry.append(doi)

        if retry:
            tasks2 = [self._fetch_datacite(d) for d in retry]
            fb = await asyncio.gather(*tasks2, return_exceptions=True)
            for doi, r in zip(retry, fb):
                if isinstance(r, dict) and r is not None:
                    results[doi] = r
        return results


# ── Zotero SQLite builder ─────────────────────────────────────────────

class ZoteroDB:
    """Write fetched metadata into a Zotero-compatible SQLite database."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn: sqlite3.Connection | None = None
        self._next_item_id: int | None = None
        self._next_creator_id: int | None = None
        self._next_value_id: int | None = None
        self._value_cache: dict[str, int] = {}   # value → valueID
        self._creator_cache: dict[tuple[str, str], int] = {}  # (first,last) → creatorID

    def open(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)) or ".", exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=OFF")
        self._init_static_tables()
        self._next_item_id = self._max_id("items", "itemID") + 1
        self._next_creator_id = self._max_id("creators", "creatorID") + 1
        self._next_value_id = self._max_id("itemDataValues", "valueID") + 1

    def _max_id(self, table: str, col: str) -> int:
        r = self.conn.execute(f"SELECT COALESCE(MAX({col}), 0) FROM {table}").fetchone()
        return r[0]

    def _init_static_tables(self):
        c = self.conn
        c.execute("""CREATE TABLE IF NOT EXISTS itemTypes (
            itemTypeID INTEGER PRIMARY KEY, typeName TEXT, templateItemTypeID INT, display INT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS fields (
            fieldID INTEGER PRIMARY KEY, fieldName TEXT, fieldFormatID INT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS itemTypeFields (
            itemTypeID INT, fieldID INT, hide INT, orderIndex INT,
            PRIMARY KEY(itemTypeID, fieldID))""")
        c.execute("""CREATE TABLE IF NOT EXISTS creatorTypes (
            creatorTypeID INTEGER PRIMARY KEY, creatorType TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS items (
            itemID INTEGER PRIMARY KEY, itemTypeID INT, dateAdded TIMESTAMP,
            dateModified TIMESTAMP, clientDateModified TIMESTAMP,
            libraryID INT DEFAULT 1, key TEXT, version INT DEFAULT 0, synced INT DEFAULT 0)""")
        c.execute("""CREATE TABLE IF NOT EXISTS itemDataValues (
            valueID INTEGER PRIMARY KEY, value TEXT UNIQUE)""")
        c.execute("""CREATE TABLE IF NOT EXISTS itemData (
            itemID INT, fieldID INT, valueID INT, PRIMARY KEY(itemID, fieldID))""")
        c.execute("""CREATE TABLE IF NOT EXISTS creators (
            creatorID INTEGER PRIMARY KEY, firstName TEXT, lastName TEXT,
            fieldMode INT DEFAULT 0)""")
        c.execute("""CREATE TABLE IF NOT EXISTS itemCreators (
            itemID INT, creatorID INT, creatorTypeID INT, orderIndex INT,
            PRIMARY KEY(itemID, creatorID, creatorTypeID))""")
        c.execute("""CREATE TABLE IF NOT EXISTS doi_fetch_log (
            doi TEXT PRIMARY KEY, fetched_at TEXT, status TEXT, error TEXT)""")

        # Seed static tables if empty
        if not c.execute("SELECT 1 FROM itemTypes LIMIT 1").fetchone():
            c.executemany("INSERT OR IGNORE INTO itemTypes VALUES(?,?,NULL,0)", ZOTERO_ITEM_TYPES)
        if not c.execute("SELECT 1 FROM fields LIMIT 1").fetchone():
            c.executemany("INSERT OR IGNORE INTO fields(fieldID,fieldName) VALUES(?,?)", ZOTERO_FIELDS)
        if not c.execute("SELECT 1 FROM creatorTypes LIMIT 1").fetchone():
            c.executemany("INSERT OR IGNORE INTO creatorTypes VALUES(?,?)", ZOTERO_CREATOR_TYPES)
        # itemTypeFields: map fieldIDs to each itemType
        for type_id, field_set in ITEM_TYPE_FIELD_MAP.items():
            for fid in sorted(field_set):
                c.execute("INSERT OR IGNORE INTO itemTypeFields VALUES(?,?,0,0)", (type_id, fid))

    def _key(self) -> str:
        """Generate an 8-char Zotero-style key."""
        import random, string
        return "".join(random.choices(string.ascii_uppercase + string.digits, k=8))

    def _value_id(self, value: str | None) -> int | None:
        if value is None:
            return None
        if value in self._value_cache:
            return self._value_cache[value]
        vid = self._next_value_id
        self._next_value_id += 1
        self.conn.execute("INSERT OR IGNORE INTO itemDataValues(valueID,value) VALUES(?,?)", (vid, value))
        self._value_cache[value] = vid
        return vid

    def _creator_id(self, first: str, last: str) -> int:
        key = (first, last)
        if key in self._creator_cache:
            return self._creator_cache[key]
        cid = self._next_creator_id
        self._next_creator_id += 1
        self.conn.execute(
            "INSERT OR IGNORE INTO creators(creatorID,firstName,lastName) VALUES(?,?,?)",
            (cid, first, last))
        self._creator_cache[key] = cid
        return cid

    def insert_item(self, entry: dict) -> int | None:
        doi = entry["doi"]
        if self.conn.execute("SELECT 1 FROM doi_fetch_log WHERE doi=?", (doi,)).fetchone():
            return None  # already fetched

        item_type_id = CROSSREF_TYPE_MAP.get(entry.get("type"), ITEM_TYPE_JOURNAL_ARTICLE)
        field_set = ITEM_TYPE_FIELD_MAP.get(item_type_id, {1, 2, 6, 7, 11, 13, 14, 15, 59})
        now = _now()
        item_id = self._next_item_id
        self._next_item_id += 1
        key = self._key()

        self.conn.execute(
            "INSERT INTO items(itemID,itemTypeID,dateAdded,dateModified,libraryID,key,version,synced) "
            "VALUES(?,?,?,?,1,?,0,0)",
            (item_id, item_type_id, now, now, key))

        # Build field → value mapping
        fields: dict[int, str | None] = {}

        if 1 in field_set and entry.get("title"):
            fields[1] = entry["title"]
        if 2 in field_set and entry.get("abstract"):
            fields[2] = entry["abstract"]
        if 6 in field_set and entry.get("date_str"):
            fields[6] = entry["date_str"]
        if 7 in field_set and entry.get("language"):
            fields[7] = entry["language"]
        if 11 in field_set:
            fields[11] = f"DOI.org ({entry['source'].title()})"
        if 13 in field_set and entry.get("url"):
            fields[13] = entry["url"]
        if 14 in field_set:
            fields[14] = now
        if 15 in field_set:
            fields[15] = None
        if 19 in field_set and entry.get("volume"):
            fields[19] = entry["volume"]
        if 32 in field_set and entry.get("pages"):
            fields[32] = entry["pages"]
        if 38 in field_set and entry.get("journal"):
            fields[38] = entry["journal"]
        if 59 in field_set:
            fields[59] = doi
        if 60 in field_set:
            fields[60] = None
        if 76 in field_set and entry.get("issue"):
            fields[76] = entry["issue"]
        if 78 in field_set and entry.get("journal_abbr"):
            fields[78] = entry["journal_abbr"]
        if 79 in field_set and entry.get("issn"):
            fields[79] = entry["issn"]
        if 23 in field_set and entry.get("publisher"):
            fields[23] = entry["publisher"]
        if 25 in field_set and entry.get("isbn"):
            fields[25] = entry["isbn"]
        if 45 in field_set and entry.get("journal"):
            fields[45] = entry["journal"]
        if 57 in field_set and entry.get("journal"):
            fields[57] = entry["journal"]
        if 104 in field_set and entry.get("publisher"):
            fields[104] = entry["publisher"]

        ck = self._make_citation_key(entry)
        if 64 in field_set and ck:
            fields[64] = ck

        for fid, val in fields.items():
            if fid not in field_set:
                continue
            vid = self._value_id(val)
            if vid is not None:
                self.conn.execute(
                    "INSERT OR REPLACE INTO itemData(itemID,fieldID,valueID) VALUES(?,?,?)",
                    (item_id, fid, vid))

        # Creators
        for order, (first, last) in enumerate(entry.get("authors", [])):
            cid = self._creator_id(first, last)
            self.conn.execute(
                "INSERT OR IGNORE INTO itemCreators VALUES(?,?,?,?)",
                (item_id, cid, CREATOR_TYPE_AUTHOR, order))

        for order, (first, last) in enumerate(entry.get("editors", [])):
            cid = self._creator_id(first, last)
            self.conn.execute(
                "INSERT OR IGNORE INTO itemCreators VALUES(?,?,?,?)",
                (item_id, cid, CREATOR_TYPE_EDITOR, len(entry.get("authors", [])) + order))

        # Log
        self.conn.execute(
            "INSERT OR REPLACE INTO doi_fetch_log(doi,fetched_at,status) VALUES(?,?,?)",
            (doi, now, "ok"))
        self.conn.commit()
        return item_id

    def _make_citation_key(self, entry: dict) -> str:
        authors = entry.get("authors", [])
        last_name = "Unknown"
        if authors:
            _, last_name = authors[0]
        last_name = re.sub(r"[^a-zA-Z]", "", last_name)
        year = str(entry.get("year") or "")
        title = entry.get("title") or ""
        words = re.findall(r"\b[a-zA-Z]{4,}\b", title)
        keyword = words[0].lower() if words else "unknown"
        return f"{last_name}{year}{keyword}"

    def log_error(self, doi: str, error: str):
        self.conn.execute(
            "INSERT OR REPLACE INTO doi_fetch_log(doi,fetched_at,status,error) VALUES(?,?,?,?)",
            (doi, _now(), "error", error))
        self.conn.commit()

    def insert_batch(self, results: dict[str, dict], failed: set[str] | None = None) -> int:
        count = 0
        for doi, entry in results.items():
            iid = self.insert_item(entry)
            if iid:
                count += 1
        if failed:
            for doi in failed:
                self.log_error(doi, "fetch returned no data")
        return count

    def close(self):
        if self.conn:
            self.conn.close()

    def stats(self) -> dict:
        n_items = self.conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        n_creators = self.conn.execute("SELECT COUNT(*) FROM creators").fetchone()[0]
        n_values = self.conn.execute("SELECT COUNT(*) FROM itemDataValues").fetchone()[0]
        now = datetime.now(timezone.utc).isoformat()
        return {"db_path": self.db_path, "items": n_items, "creators": n_creators,
                "values": n_values, "updated": now}


# ── Main ──────────────────────────────────────────────────────────────

async def main():
    import argparse
    p = argparse.ArgumentParser(description="Batch DOI→Zotero SQLite fetcher")
    p.add_argument("-i", "--input", help="File with one DOI per line")
    p.add_argument("-d", "--dois", nargs="*", help="DOIs on command line")
    p.add_argument("-o", "--output", default="zotero.sqlite", help="Output SQLite file")
    p.add_argument("-c", "--concurrency", type=int, default=DEFAULT_CONCURRENT,
                   help=f"Concurrency (default {DEFAULT_CONCURRENT}, max {MAX_CONCURRENT})")
    p.add_argument("--merge", action="store_true", help="Merge into existing DB (don't overwrite)")
    p.add_argument("--format", choices=["sqlite", "json", "bibtex"], default="sqlite",
                   help="Output format (default: sqlite)")
    p.add_argument("--from-text", help="Extract DOIs from arbitrary text")
    args = p.parse_args()

    dois: list[str] = []
    if args.from_text:
        dois = extract_dois(args.from_text)
    if args.input:
        content = Path(args.input).read_text(encoding="utf-8")
        dois.extend(extract_dois(content))
    if args.dois:
        dois.extend([clean_doi(d) for d in args.dois])

    if not dois:
        print("No DOIs found.", file=sys.stderr)
        p.print_help()
        sys.exit(1)

    if args.merge and os.path.exists(args.output):
        with sqlite3.connect(args.output) as c:
            c.execute("CREATE TABLE IF NOT EXISTS doi_fetch_log (doi TEXT PRIMARY KEY, fetched_at TEXT, status TEXT, error TEXT)")
            done = {r[0] for r in c.execute("SELECT doi FROM doi_fetch_log WHERE status='ok'")}
            new_dois = [d for d in dois if d not in done]
            print(f"Skipping {len(dois) - len(new_dois)} already-fetched DOIs", file=sys.stderr)
            dois = new_dois
    elif not args.merge and os.path.exists(args.output):
        os.remove(args.output)

    if not dois:
        print("Nothing to fetch.", file=sys.stderr)
        sys.exit(0)

    print(f"Fetching {len(dois)} DOIs with concurrency={args.concurrency}...", file=sys.stderr)
    t0 = time.time()

    async with DOIFetcher(concurrency=args.concurrency) as fetcher:
        results = await fetcher.fetch_batch(dois)

    elapsed = time.time() - t0
    failed = {d for d in dois if d not in results}

    if args.format == "json":
        out = {"entries": results, "total": len(results),
               "updated": datetime.now(timezone.utc).isoformat()}
        Path(args.output).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Done: {len(results)}/{len(dois)} fetched ({elapsed:.1f}s) -> {args.output}", file=sys.stderr)
        return

    if args.format == "bibtex":
        lines = [_bibtex(e) for e in results.values()]
        Path(args.output).write_text("\n\n".join(lines) + "\n", encoding="utf-8")
        print(f"Done: {len(results)}/{len(dois)} fetched ({elapsed:.1f}s) -> {args.output}", file=sys.stderr)
        return

    # sqlite (default)
    db = ZoteroDB(args.output)
    db.open()
    n = db.insert_batch(results, failed)
    stats = db.stats()
    db.close()

    print(f"Done: {n} inserted, {len(dois)-n} failed ({elapsed:.1f}s)", file=sys.stderr)
    print(f"  DB: {stats['db_path']}  items={stats['items']}  creators={stats['creators']}", file=sys.stderr)


def _bibtex(e: dict) -> str:
    emap = {"journal-article": "article", "book": "book", "book-chapter": "incollection",
            "proceedings-article": "inproceedings", "dataset": "misc", "preprint": "misc"}
    t = emap.get(e.get("type"), "misc")
    key = e.get("citation_key", e["doi"].replace("/", "_"))
    authors = " and ".join(f"{ln}, {fn}" for fn, ln in e.get("authors", []))
    lines = [f"@{t}{{{key},"]
    if authors:
        lines.append(f"  author = {{{authors}}},")
    if e.get("title"):
        lines.append(f"  title = {{{e['title']}}},")
    if t == "article" and e.get("journal"):
        lines.append(f"  journal = {{{e['journal']}}},")
    if e.get("year"):
        lines.append(f"  year = {{{e['year']}}},")
    if e.get("volume"):
        lines.append(f"  volume = {{{e['volume']}}},")
    if e.get("issue"):
        lines.append(f"  number = {{{e['issue']}}},")
    if e.get("pages"):
        lines.append(f"  pages = {{{e['pages']}}},")
    if e.get("doi"):
        lines.append(f"  doi = {{{e['doi']}}},")
    if e.get("publisher") and t != "article":
        lines.append(f"  publisher = {{{e['publisher']}}},")
    if lines[-1].endswith(","):
        lines[-1] = lines[-1][:-1]
    lines.append("}")
    return "\n".join(lines)


if __name__ == "__main__":
    asyncio.run(main())
