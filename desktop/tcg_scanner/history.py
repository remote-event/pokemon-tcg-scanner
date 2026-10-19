"""Durable local scan journal and a shared, lazy TCGdex image cache."""
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import hashlib
import json
import logging
from pathlib import Path
import re
import sqlite3
import threading
from time import time, monotonic
from urllib.request import Request, build_opener, HTTPRedirectHandler
from uuid import uuid4

from PIL import Image
from .images import atomic_write, inspect_image
from .pricing import Prices

log = logging.getLogger(__name__)
ID = re.compile(r"[a-zA-Z0-9_-]{1,80}\Z")
ASSET = re.compile(r"https://assets\.tcgdex\.net/[a-zA-Z0-9_./-]+\Z")


def reference_url(value):
    if not isinstance(value, str) or len(value) > 2048 or not ASSET.fullmatch(value) or '..' in value:
        return None
    if not re.search(r"/(high|low)\.(png|jpg|webp)$", value):
        value = value.rstrip('/') + '/high.webp'
    return value


def jpeg(data, size):
    with Image.open(BytesIO(data)) as source:
        if source.width * source.height > 12_000_000:
            raise ValueError('Image too large')
        source.thumbnail((size, size), Image.Resampling.LANCZOS)
        with BytesIO() as output:
            source.convert('RGB').save(output, 'JPEG', quality=84)
            return output.getvalue()


class History:
    def __init__(self, root, data_root=None, changed=lambda: None, enable_prices=False):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.data_root = Path(data_root) if data_root else None
        self.changed = changed
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.root / 'history.sqlite3', check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT OR IGNORE INTO settings VALUES('revision','0');
            CREATE TABLE IF NOT EXISTS scans(
                id TEXT PRIMARY KEY, time INTEGER NOT NULL, card TEXT NOT NULL,
                verification TEXT NOT NULL, scanned TEXT, thumbnail TEXT,
                reference_url TEXT, reference_key TEXT, revision INTEGER NOT NULL);
            CREATE INDEX IF NOT EXISTS scan_time ON scans(time DESC,id DESC);
            CREATE INDEX IF NOT EXISTS scan_revision ON scans(revision);
            CREATE INDEX IF NOT EXISTS scan_card ON scans(json_extract(card,'$.id'),time DESC);
            CREATE TABLE IF NOT EXISTS collection(
                card_id TEXT PRIMARY KEY, card TEXT NOT NULL, quantity INTEGER NOT NULL,
                reference_url TEXT, reference_key TEXT);
            CREATE TABLE IF NOT EXISTS refs(key TEXT PRIMARY KEY,url TEXT NOT NULL,card_id TEXT NOT NULL);
        ''')
        if 'image_kind' not in {row[1] for row in self.db.execute('PRAGMA table_info(scans)')}:
            self.db.execute("ALTER TABLE scans ADD COLUMN image_kind TEXT NOT NULL DEFAULT 'frame'")
        self.db.execute("INSERT OR IGNORE INTO settings VALUES('instance',?)", (uuid4().hex,))
        self.db.commit()
        self.instance = self.db.execute("SELECT value FROM settings WHERE key='instance'").fetchone()[0]
        self.download_lock = threading.Lock()  # One reference download at a time, shared by all viewers.
        self.failed = {}
        self.downloads = ThreadPoolExecutor(2, thread_name_prefix='tcg-images')
        self.prices = Prices(self.root, self.price_changed) if enable_prices else None

    def close(self):
        if self.prices:
            self.prices.close()
        self.downloads.shutdown(wait=True, cancel_futures=True)
        with self.lock:
            self.db.close()

    def price_changed(self, card_id):
        with self.lock, self.db:
            rows = self.db.execute("SELECT id FROM scans WHERE json_extract(card,'$.id')=?", (card_id,)).fetchall()
            for row in rows:
                self.db.execute('UPDATE scans SET revision=? WHERE id=?', (self._next(), row['id']))
            if not rows:
                self._next()
        self.changed()

    def price(self, card_id):
        try:
            return self.prices.get(card_id) if self.prices else None
        except ValueError:
            return None  # Legacy/custom library IDs need not exist in TCGdex.

    def collection_value(self):
        with self.lock:
            rows = self.db.execute('SELECT card_id,quantity FROM collection').fetchall()
        low = high = priced = stale = pending = 0
        for row in rows:
            quote = self.price(row['card_id'])
            if quote and quote['min_cents'] is not None:
                low += quote['min_cents'] * row['quantity']
                high += quote['max_cents'] * row['quantity']
                priced += row['quantity']
                stale += row['quantity'] if quote['stale'] else 0
            pending += int(bool(quote and quote['status']=='loading'))
        return dict(currency='USD', min_cents=low if priced else None, max_cents=high if priced else None,
                    priced_copies=priced, total_copies=sum(row['quantity'] for row in rows), stale_copies=stale, pending=pending)

    def revision(self):
        with self.lock:
            return int(self.db.execute("SELECT value FROM settings WHERE key='revision'").fetchone()[0])

    def _next(self):
        revision = self.revision() + 1
        self.db.execute("UPDATE settings SET value=? WHERE key='revision'", (str(revision),))
        return revision

    def _reference(self, card):
        url = reference_url(card.get('image_url'))
        key = hashlib.sha256(url.encode()).hexdigest() if url else None
        if key:
            self.db.execute('INSERT OR IGNORE INTO refs VALUES(?,?,?)', (key, url, card['id']))
        return url, key

    def _wire(self, row):
        if row is None:
            return None
        return dict(id=row['id'], time=row['time'], card=json.loads(row['card']),
                    price=self.price(json.loads(row['card'])['id']),
                    verification=row['verification'], revision=row['revision'],
                    scanned_image='/media/' + row['scanned'] if row['scanned'] and row['image_kind'] == 'yolo_crop' else None,
                    scanned_kind=row['image_kind'] if row['scanned'] else None,
                    scanned_thumbnail='/media/' + row['thumbnail'] if row['thumbnail'] and row['image_kind'] == 'yolo_crop' else None,
                    reference_url=row['reference_url'],
                    reference_image='/reference/' + row['reference_key'] + '.jpg' if row['reference_key'] else None)

    def get(self, scan_id):
        with self.lock:
            return self._wire(self.db.execute('SELECT * FROM scans WHERE id=?', (scan_id,)).fetchone())

    def _store_crop(self, crop):
        # Reuse the perspective-corrected YOLO crop used by DINOv2, compressed for display.
        body = jpeg(crop, 880)
        digest = hashlib.sha256(body).hexdigest()
        original, thumb = digest + '.jpg', digest + '-thumb.jpg'
        if not (self.root / original).exists():
            atomic_write(self.root / original, body)
        if not (self.root / thumb).exists():
            atomic_write(self.root / thumb, jpeg(body, 480))
        return original, thumb

    def save(self, result, crop):
        scan_id = result['recognition_id']
        if not ID.fullmatch(scan_id):
            raise ValueError('Invalid scan ID')
        if not crop:
            raise ValueError('Recognized card has no YOLO crop')
        with self.lock:
            if self.get(scan_id):
                return
            original, thumb = self._store_crop(crop)
            card = result['matches'][0]['card']
            with self.db:
                url, key = self._reference(card)
                self.db.execute("INSERT INTO scans(id,time,card,verification,scanned,thumbnail,reference_url,reference_key,revision,image_kind) VALUES(?,?,?,?,?,?,?,?,?,'yolo_crop')",
                                (scan_id, int(time()*1000), json.dumps(card), 'identified' if result.get('verification') == 'identified' else 'possible', original, thumb, url, key, self._next()))
        self.changed()

    def recrop_frames(self, cropper):
        # Upgrade saved 0.6.0 camera frames once; preserve original files and all scan metadata.
        with self.lock:
            rows = self.db.execute("SELECT id,scanned FROM scans WHERE scanned IS NOT NULL AND image_kind='frame'").fetchall()
        converted = 0
        for row in rows:
            try:
                crop, _ = cropper.crop(self.media(row['scanned']).read_bytes())
                with self.lock, self.db:
                    original, thumb = self._store_crop(crop)
                    self.db.execute("UPDATE scans SET scanned=?,thumbnail=?,image_kind='yolo_crop',revision=? WHERE id=?",
                                    (original, thumb, self._next(), row['id']))
                converted += 1
            except Exception:
                log.exception('Could not crop saved scan %s; keeping its source for retry', row['id'])
        if converted:
            log.info('Converted %d saved camera images to YOLO card crops', converted)
            self.changed()
        return converted

    def verify(self, result):
        with self.lock:
            old = self.get(result['recognition_id'])
            if not old or old['verification'] in ('manual', 'identified') or not result.get('verified'):
                return
            card = result['matches'][0]['card']
            with self.db:
                url, key = self._reference(card)
                self.db.execute('UPDATE scans SET card=?,verification=?,reference_url=?,reference_key=?,revision=? WHERE id=?',
                                (json.dumps(card), 'verified', url, key, self._next(), old['id']))
        self.changed()

    def import_rows(self, entries):
        if not isinstance(entries, list) or len(entries) > 50:
            raise ValueError('Import at most 50 records')
        # Validate the whole batch before committing any rows.
        for e in entries:
            if not isinstance(e, dict) or not ID.fullmatch(e.get('id', '')):
                raise ValueError('Invalid scan ID')
            if not isinstance(e.get('time'), int) or not 0 <= e['time'] <= 4_102_444_800_000:
                raise ValueError('Invalid timestamp')
            card = e.get('card', {})
            if not isinstance(card, dict) or any(not isinstance(card.get(k), str) or len(card[k]) > 2048 for k in ('id', 'name', 'set', 'number', 'image_url')):
                raise ValueError('Invalid card')
            if e.get('verification') not in ('possible', 'verified', 'manual', 'identified'):
                raise ValueError('Invalid verification')
        changed = False
        with self.lock, self.db:
            for e in entries:
                old = self.get(e['id'])
                # Existing camera frames/timestamps survive imports. Manual choices win over OCR.
                if old and (old['verification'] == 'manual' and e['verification'] != 'manual' or
                            old['verification'] == 'verified' and e['verification'] == 'possible' or
                            old['card'] == e['card'] and old['verification'] == e['verification']):
                    continue
                url, key = self._reference(e['card'])
                if old:
                    self.db.execute('UPDATE scans SET card=?,verification=?,reference_url=?,reference_key=?,revision=? WHERE id=?',
                                    (json.dumps(e['card']), e['verification'], url, key, self._next(), e['id']))
                else:
                    self.db.execute('INSERT INTO scans(id,time,card,verification,scanned,thumbnail,reference_url,reference_key,revision) VALUES(?,?,?,?,NULL,NULL,?,?,?)',
                                    (e['id'], e['time'], json.dumps(e['card']), e['verification'], url, key, self._next()))
                changed = True
        if changed:
            self.changed()
        return len(entries)

    def sync_collection(self, entries):
        # The phone owns quantities. Replace atomically, including removed cards;
        # scan counts cannot represent manual quantity edits or undos.
        if not isinstance(entries, list) or len(entries) > 100000:
            raise ValueError('Invalid collection')
        seen = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError('Invalid collection row')
            card = entry.get('card')
            if not isinstance(card, dict) or any(not isinstance(card.get(k), str) or len(card[k]) > 4096 for k in ('id', 'name', 'set', 'number', 'image_url')):
                raise ValueError('Invalid card')
            if not card['id'] or card['id'] in seen:
                raise ValueError('Duplicate or empty card ID')
            seen.add(card['id'])
            if type(entry.get('quantity')) is not int or not 1 <= entry['quantity'] <= 1000000:
                raise ValueError('Invalid quantity')
        digest = hashlib.sha256(json.dumps(sorted(entries, key=lambda e: e['card']['id']), sort_keys=True).encode()).hexdigest()
        with self.lock, self.db:
            previous = self.db.execute("SELECT value FROM settings WHERE key='collection_digest'").fetchone()
            if previous and previous[0] == digest:
                return len(entries)
            self.db.execute('DELETE FROM collection')
            for entry in entries:
                url, key = self._reference(entry['card'])
                self.db.execute('INSERT INTO collection VALUES(?,?,?,?,?)',
                                (entry['card']['id'], json.dumps(entry['card']), entry['quantity'], url, key))
            self.db.execute("INSERT OR REPLACE INTO settings VALUES('collection_digest',?)", (digest,))
            self._next()
        self.changed()
        return len(entries)

    def collection(self, after='', limit=40):
        limit = max(1, min(limit, 100))
        with self.lock:
            revision = self.revision()
            entries = self.db.execute('SELECT * FROM collection WHERE card_id>? ORDER BY card_id LIMIT ?', (after, limit+1)).fetchall()
            rows = []
            for entry in entries[:limit]:
                scan = self.db.execute("SELECT * FROM scans WHERE json_extract(card,'$.id')=? ORDER BY (scanned IS NOT NULL AND image_kind='yolo_crop') DESC,time DESC,id DESC LIMIT 1", (entry['card_id'],)).fetchone()
                row = self._wire(scan) if scan else dict(time=None, verification=None, scanned_image=None, scanned_thumbnail=None)
                row.update(id=entry['card_id'], price=self.price(entry['card_id']), card=json.loads(entry['card']), quantity=entry['quantity'], revision=revision,
                           reference_url=entry['reference_url'], reference_image='/reference/' + entry['reference_key'] + '.jpg' if entry['reference_key'] else None)
                rows.append(row)
            totals = self.db.execute('SELECT count(*),coalesce(sum(quantity),0) FROM collection').fetchone()
            synced = self.db.execute("SELECT 1 FROM settings WHERE key='collection_digest'").fetchone() is not None
            return dict(rows=rows, next=entries[limit-1]['card_id'] if len(entries)>limit else None,
                        total=totals[0], copies=totals[1], revision=revision, synced=synced, value=self.collection_value())

    def listing(self, before='', limit=40):
        limit = max(1, min(limit, 100))
        where, args = '', []
        if before:
            stamp, scan_id = before.split(':', 1)
            if not ID.fullmatch(scan_id):
                raise ValueError('Invalid cursor')
            where, args = 'WHERE (time,id)<(?,?)', [int(stamp), scan_id]
        with self.lock:
            rows = self.db.execute(f'SELECT * FROM scans {where} ORDER BY time DESC,id DESC LIMIT ?', [*args, limit+1]).fetchall()
            last = rows[limit-1] if len(rows) > limit else None
            return dict(rows=[self._wire(r) for r in rows[:limit]],
                        next=f"{last['time']}:{last['id']}" if last else None,
                        total=self.db.execute('SELECT count(*) FROM scans').fetchone()[0], revision=self.revision())

    def changes(self, after, limit=100):
        if self.prices:
            self.prices.refresh_due()
        with self.lock:
            rows = self.db.execute('SELECT * FROM scans WHERE revision>? ORDER BY revision LIMIT ?', (after, limit+1)).fetchall()
            more = len(rows) > limit
            return dict(rows=[self._wire(r) for r in rows[:limit]], more=more,
                        revision=rows[limit-1]['revision'] if more else self.revision(),
                        total=self.db.execute('SELECT count(*) FROM scans').fetchone()[0])

    def media(self, name):
        if not re.fullmatch(r'[a-f0-9]{64}(?:-thumb)?\.jpg', name):
            raise FileNotFoundError(name)
        path = self.root / name
        if not path.is_file():
            raise FileNotFoundError(name)
        return path

    def reference(self, name):
        if not re.fullmatch(r'[a-f0-9]{64}\.jpg', name):
            raise FileNotFoundError(name)
        target = self.root / 'references' / name
        if target.exists():
            return target
        with self.download_lock:
            if target.exists():
                return target
            if self.failed.get(name, 0) > monotonic():
                raise OSError('Reference unavailable; retry shortly')
            with self.lock:
                ref = self.db.execute('SELECT * FROM refs WHERE key=?', (name[:-4],)).fetchone()
            if not ref:
                raise FileNotFoundError(name)
            try:
                body = None
                if self.data_root and (self.data_root / 'metadata.sqlite3').exists():
                    with sqlite3.connect(f'file:{self.data_root / "metadata.sqlite3"}?mode=ro', uri=True) as db:
                        row = db.execute('SELECT local_image_path FROM cards WHERE card_id=?', (ref['card_id'],)).fetchone()
                    if row and row[0]:
                        local = (self.data_root / row[0]).resolve()
                        if local.is_relative_to(self.data_root.resolve()) and local.is_file():
                            body = local.read_bytes()
                if body is None:
                    class NoRedirect(HTTPRedirectHandler):
                        def redirect_request(self, *args, **kwargs):
                            return None
                    # Only an official TCGdex image GET leaves the desktop. Never upload a frame.
                    request = Request(ref['url'], headers={'User-Agent': 'tcg-scanner/0.6'})
                    with build_opener(NoRedirect).open(request, timeout=8) as response:
                        body = response.read(8*1024*1024+1)
                    if len(body) > 8*1024*1024:
                        raise ValueError('Reference too large')
                inspect_image(body)
                atomic_write(target, jpeg(body, 900))
                self.failed.pop(name, None)
                return target
            except Exception:
                if len(self.failed) >= 256:
                    self.failed.clear()
                self.failed[name] = monotonic()+60
                log.warning('Reference image unavailable for %s', ref['card_id'], exc_info=True)
                raise
