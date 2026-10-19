"""Non-blocking, persistent TCGdex/TCGplayer price cache. Never receives images."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
import json
import logging
import math
from pathlib import Path
import re
import sqlite3
import threading
from time import time
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPRedirectHandler

log = logging.getLogger(__name__)
CARD_ID = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}\Z')
TTL = 3600
LABELS = {'normal': 'Normal', 'holo': 'Holofoil', 'holofoil': 'Holofoil',
          'reverse': 'Reverse holofoil', 'reverse-holofoil': 'Reverse holofoil',
          '1st-edition': 'First edition', '1st-edition-holofoil': 'First edition holofoil',
          'unlimited': 'Unlimited', 'unlimited-holofoil': 'Unlimited holofoil'}


def fetch_card(card_id):
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    request = Request('https://api.tcgdex.net/v2/en/cards/' + card_id,
                      headers={'User-Agent': 'tcg-scanner/0.7', 'Accept': 'application/json'})
    try:
        with build_opener(NoRedirect).open(request, timeout=8) as response:
            body = response.read(1024*1024+1)
        if len(body) > 1024*1024:
            raise ValueError('Card response too large')
        value = json.loads(body)
        if not isinstance(value, dict) or value.get('id') != card_id:
            raise ValueError('Pricing response is for a different card')
        return value
    except HTTPError as exc:
        if exc.code == 404:
            return {'id': card_id}
        raise


def normalize(value):
    provider = (value.get('pricing') or {}).get('tcgplayer') or {}
    quotes = []
    if provider.get('unit') == 'USD':
        for variant, data in provider.items():
            if not isinstance(data, dict) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', variant):
                continue
            amount = data.get('marketPrice')
            if type(amount) not in (int, float) or not math.isfinite(amount) or not 0 <= amount <= 10000000:
                continue
            cents = int((Decimal(str(amount))*100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
            quotes.append(dict(variant=variant, label=LABELS.get(variant, variant.replace('-', ' ').title()), cents=cents))
    updated = provider.get('updated')
    if isinstance(updated, str):
        try:
            stamp = datetime.fromisoformat(updated.replace('Z', '+00:00'))
            if stamp.tzinfo is None:
                updated = None
        except ValueError:
            updated = None
    else:
        updated = None
    return dict(source='TCGplayer via TCGdex', currency='USD', updated=updated, quotes=quotes,
                min_cents=min((q['cents'] for q in quotes), default=None),
                max_cents=max((q['cents'] for q in quotes), default=None))


class Prices:
    def __init__(self, root, changed=lambda card_id: None, fetch=fetch_card, clock=time):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(Path(root) / 'prices.sqlite3', check_same_thread=False)
        self.db.execute('CREATE TABLE IF NOT EXISTS prices(card_id TEXT PRIMARY KEY, payload TEXT NOT NULL, fetched REAL NOT NULL, retry REAL NOT NULL, failed INTEGER NOT NULL)')
        self.db.commit()
        self.changed, self.fetch, self.clock = changed, fetch, clock
        self.pool = ThreadPoolExecutor(1, thread_name_prefix='tcg-prices')
        self.pending = set()
        self.closed = False

    def get(self, card_id):
        if not CARD_ID.fullmatch(card_id):
            raise ValueError('Invalid card ID')
        if card_id.startswith('manual-'):
            return dict(normalize({}), card_id=card_id, fetched_at=None, status='unavailable', stale=False)
        now = self.clock()
        with self.lock:
            row = self.db.execute('SELECT payload,fetched,retry,failed FROM prices WHERE card_id=?', (card_id,)).fetchone()
            due = row is None or now >= row[2]
            if due and not self.closed and card_id not in self.pending and len(self.pending) < 64:
                self.pending.add(card_id)
                self.pool.submit(self._refresh, card_id)
            value = json.loads(row[0]) if row else normalize({})
            fetched = row[1] if row else 0
            source_stale = not value['updated'] or now - datetime.fromisoformat(value['updated'].replace('Z', '+00:00')).timestamp() > 48*3600
            return dict(value, card_id=card_id, fetched_at=int(fetched*1000) if fetched else None,
                        status='ready' if value['quotes'] else 'loading' if card_id in self.pending else 'unavailable',
                        stale=bool(not fetched or now-fetched >= TTL or source_stale or row and row[3]))

    def _refresh(self, card_id):
        try:
            payload = normalize(self.fetch(card_id))
            now = self.clock()
            with self.lock, self.db:
                self.db.execute('INSERT OR REPLACE INTO prices VALUES(?,?,?,?,0)',
                                (card_id, json.dumps(payload), now, now + (TTL if payload['quotes'] else 6*3600)))
        except Exception:
            log.warning('Price lookup unavailable for %s; keeping cached prices', card_id, exc_info=True)
            with self.lock, self.db:
                self.db.execute('INSERT OR IGNORE INTO prices VALUES(?,?,0,0,1)', (card_id, json.dumps(normalize({}))))
                self.db.execute('UPDATE prices SET retry=?,failed=1 WHERE card_id=?', (self.clock()+300, card_id))
        finally:
            with self.lock:
                self.pending.discard(card_id)
            self.changed(card_id)

    def refresh_due(self):
        with self.lock:
            ids = self.db.execute('SELECT card_id FROM prices WHERE retry<=? ORDER BY retry LIMIT 64', (self.clock(),)).fetchall()
        for (card_id,) in ids:
            self.get(card_id)

    def close(self):
        with self.lock:
            self.closed = True
        self.pool.shutdown(wait=True, cancel_futures=True)
        with self.lock:
            self.db.close()
