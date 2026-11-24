"""Transactional private archive; observed evidence and user input stay separate."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import uuid

from ..trip_api import TripError
from ..trip_store import validate_range


def utcnow():
    return datetime.now(timezone.utc).isoformat()


class EnergyStore:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def connection(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def initialize(self):
        with self.connection() as db:
            if db.execute('PRAGMA user_version').fetchone()[0] > 1:
                raise TripError('unsupported_archive_version')
            db.executescript("""
                CREATE TABLE IF NOT EXISTS records (
                    vehicle TEXT NOT NULL, id TEXT NOT NULL, kind TEXT NOT NULL,
                    day TEXT NOT NULL, payload TEXT NOT NULL, revision INTEGER NOT NULL,
                    PRIMARY KEY(vehicle,id));
                CREATE INDEX IF NOT EXISTS records_day ON records(vehicle,day);
                CREATE TABLE IF NOT EXISTS samples (
                    vehicle TEXT NOT NULL, source_time TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(vehicle,source_time));
                CREATE TABLE IF NOT EXISTS state (vehicle TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS capabilities (vehicle TEXT PRIMARY KEY, hybrid INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS revisions (
                    vehicle TEXT NOT NULL, id TEXT NOT NULL, revision INTEGER NOT NULL,
                    payload TEXT NOT NULL, PRIMARY KEY(vehicle,id,revision));
                PRAGMA user_version=1;
            """)

    def capabilities(self):
        with self.connection() as db:
            return {r['vehicle']: bool(r['hybrid']) for r in db.execute('SELECT * FROM capabilities')}

    def remember_capability(self, vehicle, value):
        if type(value) is not bool:
            return
        with self.connection() as db:
            db.execute('INSERT OR REPLACE INTO capabilities VALUES (?,?)', (vehicle, int(value)))

    @staticmethod
    def put(db, vehicle, record):
        old = db.execute('SELECT payload,revision FROM records WHERE vehicle=? AND id=?',
                         (vehicle, record['id'])).fetchone()
        revision = old['revision'] + 1 if old else 1
        if old:
            previous = json.loads(old['payload'])
            # Replaying automatic evidence never overwrites human corrections.
            for key in ('manual', 'hidden'):
                if key in previous:
                    record[key] = previous[key]
            db.execute('INSERT OR IGNORE INTO revisions VALUES (?,?,?,?)',
                       (vehicle, record['id'], old['revision'], old['payload']))
        record = dict(record, revision=revision, updated_at=utcnow())
        db.execute('INSERT OR REPLACE INTO records VALUES (?,?,?,?,?,?)',
                   (vehicle, record['id'], record['kind'], record['date'], json.dumps(record), revision))
        return record

    def query(self, vehicle, first, last):
        validate_range(first, last)
        with self.connection() as db:
            return [json.loads(r['payload']) for r in db.execute(
                'SELECT payload FROM records WHERE vehicle=? AND day BETWEEN ? AND ? ORDER BY day DESC,id DESC',
                (vehicle, first, last))]

    def detail(self, vehicle, identifier):
        with self.connection() as db:
            row = db.execute('SELECT payload FROM records WHERE vehicle=? AND id=?', (vehicle, identifier)).fetchone()
        if row is None:
            raise TripError('record_unavailable')
        return json.loads(row['payload'])

    def status(self, vehicle):
        with self.connection() as db:
            row = db.execute('SELECT payload FROM state WHERE vehicle=?', (vehicle,)).fetchone()
        return json.loads(row['payload']) if row else {}

    def save_manual(self, vehicle, kind, values, *, identifier=None, revision=None):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if identifier:
                old = db.execute('SELECT payload,revision FROM records WHERE vehicle=? AND id=?',
                                 (vehicle, identifier)).fetchone()
                if not old or old['revision'] != revision:
                    raise TripError('record_changed')
                record = json.loads(old['payload'])
                if record['kind'] != kind:
                    raise TripError('invalid_record')
            else:
                record = {'id': uuid.uuid4().hex, 'kind': kind, 'date': values['date'],
                          'source': 'manual', 'status': 'complete', 'created_at': utcnow()}
            record['manual'] = values
            # Manual-only rows follow the user's date; automatic rows retain evidence dates.
            if record['source'] == 'manual':
                record['date'] = values['date']
            if identifier:
                db.execute('INSERT OR IGNORE INTO revisions VALUES (?,?,?,?)',
                           (vehicle, identifier, old['revision'], old['payload']))
                # put() preserves previous human input when called by a recorder;
                # this edit explicitly replaces it after checking the revision.
                updated = dict(record, revision=old['revision'] + 1, updated_at=utcnow())
                db.execute('UPDATE records SET day=?,payload=?,revision=? WHERE vehicle=? AND id=?',
                           (record['date'], json.dumps(updated), updated['revision'], vehicle, identifier))
                return updated
            return self.put(db, vehicle, record)

    def set_hidden(self, vehicle, identifier, revision, hidden):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT payload,revision FROM records WHERE vehicle=? AND id=?',
                             (vehicle, identifier)).fetchone()
            if not old or old['revision'] != revision:
                raise TripError('record_changed')
            record = dict(json.loads(old['payload']), hidden=hidden, revision=revision + 1, updated_at=utcnow())
            db.execute('INSERT OR IGNORE INTO revisions VALUES (?,?,?,?)',
                       (vehicle, identifier, revision, old['payload']))
            db.execute('UPDATE records SET payload=?,revision=? WHERE vehicle=? AND id=?',
                       (json.dumps(record), revision + 1, vehicle, identifier))
