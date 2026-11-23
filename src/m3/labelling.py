"""Disk-backed inputs and deterministic, conservative reference matching."""
import csv
import hashlib
import json
import re
import sqlite3
import unicodedata
from pathlib import Path
from difflib import SequenceMatcher

FIELDS = ['arxiv_id', 'citation_key', 'groundtruth_arxiv_id', 'label_source']
ID = re.compile(r'(?<![\w.])(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?(?!\d)', re.I)


def ids(value):
    return {re.sub(r'v\d+$', '', x, flags=re.I).lower() for x in ID.findall(str(value or ''))}


def clean(value):
    value = unicodedata.normalize('NFKD', str(value or '')).casefold()
    value = re.sub(r'\\[a-zA-Z]+', ' ', value)
    return ' '.join(re.findall(r'[^\W_]+', value, re.UNICODE))


def prepare(row, reference=False):
    prefix = 'ref_' if reference else ''
    title = clean(row.get(prefix + 'title'))
    authors = row.get(prefix + 'authors') or []
    if isinstance(authors, str):
        try:
            authors = json.loads(authors)
        except ValueError:
            authors = authors.split(' and ')
    surnames = {clean(a).split()[-1] for a in authors if clean(a)}
    year = re.search(r'\b(?:19|20)\d{2}\b', str(row.get('ref_submission_date' if reference else 'year') or ''))
    found = ids(row.get('ref_arxiv_id')) if reference else set().union(*(ids(row.get(k)) for k in ('url', 'doi', 'title', 'citation_key')))
    return title, set(title.split()), surnames, year.group() if year else '', found


def score(a, b):
    title, tokens, authors, year, found = a
    other, words, names, date, target = b
    if found and target:
        return (1.0, 'id') if found == target else (0.0, 'id_conflict')
    if not title or not other:
        return 0.0, 'missing_title'
    overlap = len(tokens & words) / max(1, len(tokens | words))
    similarity = SequenceMatcher(None, title, other, autojunk=False).ratio()
    author = len(authors & names) / max(1, min(len(authors), len(names)))
    year_ok = bool(year and date and abs(int(year) - int(date)) <= 1)
    value = .85 * max(overlap, similarity) + .10 * author + .05 * year_ok
    # Short/common titles require corroboration; ID conflicts never use fuzzy fallback.
    strong = len(tokens) >= 4 and (title == other or similarity >= .94) and author > 0 and year_ok
    return value, 'metadata' if strong else 'review'


def rank(entry, references):
    a = prepare(entry)
    return sorted([(score(a, prepared), row) for row, prepared in references],
                  key=lambda x: (-x[0][0], x[1]['ref_arxiv_id']))


def match(entry, references, threshold=.94, margin=.04):
    ranked = rank(entry, references)
    if not ranked:
        return '', 0., 'no_reference'
    (value, reason), row = ranked[0]
    gap = value - ranked[1][0][0] if len(ranked) > 1 else value
    accepted = reason == 'id' or (reason == 'metadata' and value >= threshold and gap >= margin)
    # More than one ID in the entry is ambiguous, even if only one is in this paper.
    return (row['ref_arxiv_id'] if accepted else ''), value, reason if accepted else 'abstain_' + reason


def selection(path, owner=None, kind='auto'):
    with open(path, newline='', encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))
    seen = set()
    for row in rows:
        if row['arxiv_id'] in seen:
            raise ValueError('Duplicate publication in selection')
        seen.add(row['arxiv_id'])
    return [r for r in rows if r['kind'] == kind and (not owner or r['owner'] == owner)
            and r['partition'] in {'train', 'valid', 'test'}]


def records(path):
    path = Path(path)
    if path.suffix == '.csv':
        with path.open(newline='', encoding='utf-8-sig') as f:
            yield from csv.DictReader(f)
    else:
        import pyarrow.dataset as ds
        dataset = ds.dataset(str(path), format='parquet')
        for batch in dataset.to_batches(batch_size=8192):
            yield from batch.to_pylist()


class Store:
    """Read source shards once; index on paper, keep only one paper in memory."""
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute('CREATE TABLE IF NOT EXISTS rows (kind TEXT, paper TEXT, key TEXT, data TEXT, PRIMARY KEY(kind,paper,key))')
        self.db.execute('CREATE TABLE IF NOT EXISTS manifest (kind TEXT PRIMARY KEY, fingerprint TEXT)')

    def load(self, kind, paths):
        files = []
        for name in paths:
            p = Path(name)
            files.extend(sorted(p.rglob('*.parquet')) if p.is_dir() else [p])
        if not files:
            raise ValueError(f'No input files for {kind}')
        fingerprint = hashlib.sha256(json.dumps([(str(p.resolve()), p.stat().st_size, p.stat().st_mtime_ns) for p in files]).encode()).hexdigest()
        old = self.db.execute('SELECT fingerprint FROM manifest WHERE kind=?', (kind,)).fetchone()
        if old and old[0] == fingerprint:
            return
        key = 'citation_key' if kind == 'bib' else 'ref_arxiv_id'
        with self.db:
            self.db.execute('DELETE FROM rows WHERE kind=?', (kind,))
            for p in files:
                for row in records(p):
                    paper, identifier = row.get('arxiv_id'), row.get(key)
                    if not paper or not identifier:
                        raise ValueError(f'Missing arxiv_id/{key}: {p}')
                    if kind == 'ref':
                        normalized = ids(identifier)
                        if len(normalized) != 1:
                            raise ValueError(f'Invalid reference ID: {identifier}')
                        identifier = row[key] = next(iter(normalized))
                    data = json.dumps(row, ensure_ascii=False, default=str, sort_keys=True)
                    existing = self.db.execute('SELECT data FROM rows WHERE kind=? AND paper=? AND key=?', (kind, paper, identifier)).fetchone()
                    if existing and existing[0] != data:
                        raise ValueError(f'Conflicting duplicate {kind}: {paper}/{identifier}')
                    self.db.execute('INSERT OR IGNORE INTO rows VALUES (?,?,?,?)', (kind, paper, identifier, data))
            self.db.execute('INSERT OR REPLACE INTO manifest VALUES (?,?)', (kind, fingerprint))

    def paper(self, kind, paper):
        return [json.loads(r[0]) for r in self.db.execute('SELECT data FROM rows WHERE kind=? AND paper=? ORDER BY key', (kind, paper))]


def arguments(parser):
    parser.add_argument('--selection', required=True)
    parser.add_argument('--owner', help='MSSV; omit for all owners')
    parser.add_argument('--bibtex', nargs='+', required=True, help='M2 bibtex Parquet directories/files or CSV')
    parser.add_argument('--references', nargs='+', required=True, help='M1 references Parquet directories/files or CSV')
    parser.add_argument('--cache', required=True, help='Local SQLite index (not on shared source Drive)')
    parser.add_argument('--output', required=True, help='Labels CSV destination')


def write_csv(path, rows, fields=FIELDS):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def open_store(args):
    store = Store(args.cache)
    store.load('bib', args.bibtex)
    store.load('ref', args.references)
    return store
