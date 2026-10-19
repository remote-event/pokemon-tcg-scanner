from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import Mock, patch

from tcg_scanner.history import History
from tcg_scanner.pricing import Prices, normalize, fetch_card
from tests.test_history import card


def feed(now, **variants):
    return {'id': 'one', 'pricing': {'tcgplayer': {'unit': 'USD', 'updated': datetime.fromtimestamp(now, timezone.utc).isoformat(), **variants}}}


class PriceTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.now = 1000000
        self.fetch = Mock(return_value=feed(self.now, holofoil={'marketPrice': .54}))
        self.changed = Mock()
        self.cache = Prices(self.directory.name, self.changed, self.fetch, lambda: self.now)
    def tearDown(self):
        self.cache.close(); self.directory.cleanup()
    def finish(self):
        self.cache.pool.submit(lambda: None).result(timeout=3)
    def test_nonblocking_deduplicated_and_persistent(self):
        gate = threading.Event()
        self.fetch.side_effect = lambda _: (gate.wait(3), feed(self.now, holofoil={'marketPrice': .54}))[1]
        try:
            with ThreadPoolExecutor(8) as pool:
                values = list(pool.map(self.cache.get, ['one']*20))
            self.assertTrue(all(v['status']=='loading' for v in values))
        finally:
            gate.set()
        self.finish()
        self.assertEqual(self.fetch.call_count, 1)
        self.assertEqual(self.cache.get('one')['min_cents'], 54)
        self.assertFalse(self.cache.get('one')['stale'])
        self.cache.close(); self.cache = Prices(self.directory.name, self.changed, self.fetch, lambda: self.now)
        self.assertEqual(self.cache.get('one')['min_cents'], 54)
        self.assertEqual(self.fetch.call_count, 1)
    def test_expired_prices_survive_failure_and_retry_is_bounded(self):
        self.cache.get('one'); self.finish(); self.now += 3601
        self.fetch.side_effect = OSError('offline')
        with patch('tcg_scanner.pricing.log.warning'):
            before = self.cache.get('one'); self.finish()
        self.assertEqual(before['min_cents'], 54)
        self.assertTrue(before['stale'])
        for _ in range(20): self.cache.get('one')
        self.assertEqual(self.fetch.call_count, 2)
        self.assertEqual(self.cache.get('one')['updated'], before['updated'])
    def test_missing_prices_are_cached_without_fabricated_zero(self):
        self.fetch.return_value = {'id': 'one'}
        self.cache.get('one'); self.finish()
        value = self.cache.get('one')
        self.assertEqual(value['status'], 'unavailable')
        self.assertIsNone(value['min_cents'])
        self.assertEqual(self.fetch.call_count, 1)
    def test_variant_ranges_zero_prices_and_only_market_prices(self):
        value = normalize(feed(self.now, normal={'marketPrice': .27}, **{'reverse-holofoil': {'marketPrice': .44}}))
        self.assertEqual((value['min_cents'], value['max_cents']), (27,44))
        self.assertEqual(value['quotes'][1]['label'], 'Reverse holofoil')
        self.assertEqual(normalize(feed(self.now, normal={'marketPrice': 0}))['min_cents'], 0)
        for invalid in (None, -1, True, float('nan'), float('inf'), '0.50'):
            self.assertIsNone(normalize(feed(self.now, normal={'marketPrice': invalid, 'lowPrice': .5}))['min_cents'])
        value = feed(self.now, holofoil={'marketPrice': .54}); value['pricing']['tcgplayer']['unit']='EUR'
        self.assertIsNone(normalize(value)['min_cents'])
    def test_old_provider_date_is_marked_stale_even_after_new_fetch(self):
        self.fetch.return_value = feed(self.now-3*86400, holofoil={'marketPrice': .54})
        self.cache.get('one'); self.finish()
        self.assertTrue(self.cache.get('one')['stale'])
        self.assertEqual(self.cache.get('one')['fetched_at'], self.now*1000)
    def test_invalid_ids_never_make_requests(self):
        for value in ('../secret', 'manual-../secret', 'http://host', 'one?redirect=host', '', 'x'*129):
            with self.assertRaises(ValueError): self.cache.get(value)
        self.fetch.assert_not_called()
    def test_custom_card_details_never_fetch_catalog_prices(self):
        value = self.cache.get('manual-12345678-1234-1234-1234-123456789012')
        self.finish()
        self.assertEqual(value['status'], 'unavailable')
        self.assertIsNone(value['min_cents'])
        self.assertEqual(value['quotes'], [])
        self.fetch.assert_not_called()

    def test_response_for_different_card_is_rejected(self):
        from io import BytesIO
        opener = Mock(); opener.open.return_value = BytesIO(b'{"id":"wrong"}')
        with patch('tcg_scanner.pricing.build_opener', return_value=opener):
            with self.assertRaises(ValueError): fetch_card('one')


class PriceHistoryTests(unittest.TestCase):
    def test_price_changes_reach_history_and_collection_values_include_quantities(self):
        with TemporaryDirectory() as root:
            history = History(root)
            history.prices = Prices(root, history.price_changed,
                                    lambda _: feed(1000000, normal={'marketPrice': .27}, reverse={'marketPrice': .44}), lambda: 1000000)
            try:
                history.import_rows([dict(id='scan', time=1, card=card(), verification='verified')])
                history.sync_collection([dict(card=card(), quantity=3)])
                before = history.revision()
                history.get('scan')
                history.prices.pool.submit(lambda: None).result(timeout=3)
                self.assertEqual(history.changes(before)['rows'][0]['price']['min_cents'], 27)
                value = history.collection_value()
                self.assertEqual((value['min_cents'],value['max_cents'],value['priced_copies']), (81,132,3))
                history.sync_collection([])
                self.assertEqual(history.collection_value()['total_copies'], 0)
                self.assertIsNone(history.collection_value()['min_cents'])
            finally:
                history.close()
