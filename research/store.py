"""Transactional, versioned local research repository with immutable input blobs."""
from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


def now():
    return datetime.now(timezone.utc).isoformat()


def identity(value):
    value = str(value or '')
    if not value or len(value) > 64 or any(char not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for char in value):
        raise ValueError('invalid research identifier')
    return value


def dumps(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'), sort_keys=True)


def warehouse_research_root(warehouse):
    path = Path(warehouse)
    root = path.parent.parent if path.parent.name == 'providers' else path.parent
    return root / 'research'


class ResearchStore:
    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / 'research.sqlite'
        self.root.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1):
                raise RuntimeError('unsupported research schema')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS objects(kind TEXT, id TEXT, revision INTEGER, payload TEXT NOT NULL,
                    created_at TEXT NOT NULL, PRIMARY KEY(kind,id,revision));
                CREATE TABLE IF NOT EXISTS pointers(kind TEXT, id TEXT, revision INTEGER, PRIMARY KEY(kind,id));
                CREATE TABLE IF NOT EXISTS inputs(hash TEXT PRIMARY KEY, content BLOB NOT NULL);
                CREATE INDEX IF NOT EXISTS objects_by_created ON objects(kind,created_at);
                PRAGMA user_version=1;
            ''')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA foreign_keys=ON')
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def put(self, kind, payload, *, object_id=None, db=None, publish=True):
        kind, object_id = identity(kind), identity(object_id or uuid.uuid4().hex)
        if db is None:
            with self.connection() as transaction:
                transaction.execute('BEGIN IMMEDIATE')
                return self.put(kind, payload, object_id=object_id, db=transaction, publish=publish)
        revision = db.execute('SELECT COALESCE(MAX(revision),0)+1 FROM objects WHERE kind=? AND id=?', (kind, object_id)).fetchone()[0]
        item = {**payload, 'id': object_id, 'revision': revision, 'updated_at': now()}
        db.execute('INSERT INTO objects VALUES(?,?,?,?,?)', (kind, object_id, revision, dumps(item), item['updated_at']))
        if publish:
            db.execute('INSERT OR REPLACE INTO pointers VALUES(?,?,?)', (kind, object_id, revision))
        return item

    def get(self, kind, object_id, revision=None, *, db=None):
        kind, object_id = identity(kind), identity(object_id)
        if db is None:
            with self.connection() as transaction:
                return self.get(kind, object_id, revision, db=transaction)
        if revision is None:
            row = db.execute('SELECT o.payload FROM objects o JOIN pointers p USING(kind,id,revision) WHERE o.kind=? AND o.id=?', (kind, object_id)).fetchone()
        else:
            row = db.execute('SELECT payload FROM objects WHERE kind=? AND id=? AND revision=?', (kind, object_id, int(revision))).fetchone()
        if row is None:
            raise KeyError(f'{kind}/{object_id}')
        return json.loads(row[0])

    def list(self, kind, *, limit=100, offset=0):
        if not 1 <= int(limit) <= 200 or not 0 <= int(offset) <= 100000:
            raise ValueError('invalid research page')
        with self.connection() as db:
            rows = db.execute('SELECT o.payload FROM objects o JOIN pointers p USING(kind,id,revision) WHERE kind=? ORDER BY created_at DESC,id LIMIT ? OFFSET ?', (identity(kind), int(limit), int(offset))).fetchall()
            total = db.execute('SELECT COUNT(*) FROM pointers WHERE kind=?', (kind,)).fetchone()[0]
        return {'items': [json.loads(row[0]) for row in rows], 'total': total, 'limit': int(limit), 'offset': int(offset)}

    def save_frame(self, frame, *, db):
        clean = frame.reset_index(drop=True).astype(object).where(pd.notna(frame.reset_index(drop=True)), None)
        if 'date' in clean:
            clean['date'] = clean['date'].map(lambda value: value.isoformat() if hasattr(value, 'isoformat') else value)
        content = dumps({'columns': list(clean.columns), 'data': clean.values.tolist()}).encode()
        digest = hashlib.sha256(content).hexdigest()
        db.execute('INSERT OR IGNORE INTO inputs VALUES(?,?)', (digest, gzip.compress(content)))
        return digest

    def read_frame(self, digest):
        if len(str(digest)) != 64:
            raise ValueError('invalid input hash')
        with self.connection() as db:
            row = db.execute('SELECT content FROM inputs WHERE hash=?', (digest,)).fetchone()
        if row is None:
            raise KeyError('snapshot input missing')
        content = gzip.decompress(row[0])
        if hashlib.sha256(content).hexdigest() != digest:
            raise RuntimeError('snapshot checksum mismatch')
        payload = json.loads(content)
        frame = pd.DataFrame(payload['data'], columns=payload['columns'])
        if 'date' in frame:
            frame['date'] = pd.to_datetime(frame['date'])
        return frame
