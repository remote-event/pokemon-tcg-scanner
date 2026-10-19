from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import struct
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from PIL import Image
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from tcg_scanner.history import History, reference_url, jpeg
from tcg_scanner.server import create_app
from tests.test_live_server import FakeProcessor


def frame(size=(720, 1000)):
    output = BytesIO()
    Image.new('RGB', size, 'green').save(output, 'JPEG')
    return output.getvalue()


def card(name='one'):
    return dict(id=name, name=name, set='Test', number='1', total=10, code='T',
                image_url='https://assets.tcgdex.net/en/test/1/high.png')


def result(scan_id=None):
    return dict(recognition_id=scan_id or uuid4().hex, matches=[dict(card=card(), score=.9)])


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.store = History(self.directory.name)
    def tearDown(self):
        self.store.close()
        self.directory.cleanup()
    def test_card_crop_persists_and_thumbnail_is_smaller(self):
        value, body = result(), frame((630, 880))
        self.store.save(value, body)
        row = self.store.get(value['recognition_id'])
        self.assertEqual((self.store.root / row['scanned_image'].split('/')[-1]).read_bytes(), jpeg(body, 880))
        self.assertEqual(row['scanned_kind'], 'yolo_crop')
        with Image.open(self.store.root / row['scanned_thumbnail'].split('/')[-1]) as im:
            self.assertEqual(max(im.size), 480)
        instance = self.store.instance
        self.store.close()
        self.store = History(self.directory.name)
        self.assertEqual(instance, self.store.instance)
        self.assertEqual(row, self.store.get(value['recognition_id']))
    def test_recognition_reuses_the_exact_crop_sent_to_dinov2(self):
        from collections import OrderedDict
        from types import SimpleNamespace
        from unittest.mock import Mock
        import numpy as np
        from tcg_scanner.live import LiveProcessor, LiveState
        processor = LiveProcessor.__new__(LiveProcessor)
        processor.reader = None
        processor.stats = dict(frames=0, embeddings=0, cache_hits=0, recognitions=0)
        processor.cache = OrderedDict()
        processor.vectors = OrderedDict()
        corners = np.float32([[100,100],[620,100],[620,900],[100,900]])
        processor.session = SimpleNamespace(cropper=Mock(), embedder=Mock(), store=Mock(), index=None, metadata={})
        processor.session.cropper.detect_live.return_value = (corners, False)
        processor.session.embedder.encode.return_value = [np.ones(3)]
        processor.session.store.search.return_value = [dict(card_id='one', similarity=.9)]
        processor.wire_matches = lambda _: [dict(card=card(), score=.9)]
        state = LiveState()
        state.gate = Mock(state='locked', interval_ms=250)
        state.gate.observe.return_value = True
        state.gate.confirm.return_value = True
        from tcg_scanner.quality import Quality
        with patch('tcg_scanner.live.assess', return_value=Quality(150, 400, 0)):
            response = processor.process(state, frame(), .5)
        self.assertEqual(response['event'], 'recognized')
        self.assertEqual(response['_card_crop'], processor.session.embedder.encode.call_args.args[0][0])
        self.assertEqual(processor.session.cropper.detect_live.call_count, 1)
        with Image.open(BytesIO(response['_card_crop'])) as image:
            self.assertEqual(image.size, (630, 880))

    def test_phone_edit_preserves_photo_and_time_and_survives_stale_results(self):
        value=result('edit-test')
        self.store.save(dict(value,verification='identified'),frame((630,880)))
        before=self.store.get('edit-test')
        corrected=dict(before['card'],id='manual-example',name='Corrected name',set='Corrected set',number='070',image_url='')
        self.store.import_rows([dict(id='edit-test',time=before['time'],card=corrected,verification='manual')])
        self.store.verify(dict(value,verified=True))
        self.store.import_rows([dict(id='edit-test',time=before['time'],card=value['matches'][0]['card'],verification='identified')])
        after=self.store.get('edit-test')
        self.assertEqual(after['card'],corrected)
        self.assertEqual(after['time'],before['time'])
        self.assertEqual(after['scanned_image'],before['scanned_image'])
        self.assertIsNone(after['reference_image'])
        self.assertEqual(after['verification'],'manual')
        self.assertEqual(self.store.changes(before['revision'])['rows'][0]['card'],corrected)
        self.store.close();self.store=History(self.directory.name)
        self.assertEqual(self.store.get('edit-test')['card'],corrected)

    def test_saved_frames_are_recropped_once_without_changing_scan_metadata(self):
        from unittest.mock import Mock
        self.store.save(result('old-photo'), frame())
        old = self.store.get('old-photo')
        self.store.db.execute("UPDATE scans SET image_kind='frame' WHERE id='old-photo'")
        self.store.db.commit()
        cropper = Mock()
        cropper.crop.return_value = (frame((630, 880)), {})
        self.assertEqual(self.store.recrop_frames(cropper), 1)
        new = self.store.get('old-photo')
        self.assertNotEqual(new['scanned_image'], old['scanned_image'])
        self.assertEqual(new['card'], old['card'])
        self.assertEqual(new['time'], old['time'])
        self.assertEqual(self.store.recrop_frames(cropper), 0)
        self.assertEqual(cropper.crop.call_count, 1)
        self.assertTrue(self.store.media(old['scanned_image'].split('/')[-1]).exists())

    def test_collection_quantities_are_authoritative_persist_and_remove(self):
        self.store.save(result('photo'), frame())
        self.store.sync_collection([dict(card=card(), quantity=7), dict(card=card('two'), quantity=2)])
        listing = self.store.collection(limit=1)
        self.assertEqual((listing['total'], listing['copies']), (2, 9))
        self.assertEqual(listing['rows'][0]['quantity'], 7)  # One scan, seven owned.
        self.assertEqual(listing['rows'][0]['scanned_image'], self.store.get('photo')['scanned_image'])
        second = self.store.collection(after=listing['next'])
        self.assertEqual(second['rows'][0]['card']['id'], 'two')
        self.assertIsNone(second['rows'][0]['scanned_image'])
        self.assertIsNone(second['next'])
        revision = self.store.revision()
        self.store.sync_collection([dict(card=card('two'), quantity=2), dict(card=card(), quantity=7)])
        self.assertEqual(self.store.revision(), revision)
        self.store.close(); self.store = History(self.directory.name)
        self.assertEqual(self.store.collection()['copies'], 9)
        self.store.sync_collection([dict(card=card('two'), quantity=1)])
        self.assertEqual((self.store.collection()['total'], self.store.collection()['copies']), (1, 1))
        self.store.sync_collection([])
        self.assertEqual(self.store.collection()['rows'], [])
        self.assertTrue(self.store.collection()['synced'])
        self.assertEqual(self.store.listing()['total'], 1)

    def test_invalid_collection_does_not_replace_saved_quantities(self):
        self.assertFalse(self.store.collection()['synced'])
        entry = dict(card=card(), quantity=3)
        self.store.sync_collection([entry])
        for rows in ([entry, entry], [dict(card=card(), quantity=-1)], [dict(card=card(), quantity=True)],
                     [entry, dict(card={}, quantity=1)], {}, [None]):
            with self.assertRaises(ValueError):
                self.store.sync_collection(rows)
            self.assertEqual(self.store.collection()['copies'], 3)

    def test_replay_is_idempotent_and_identical_frames_share_files(self):
        value, body = result(), frame()
        self.store.save(value, body); self.store.save(value, body)
        self.assertEqual(self.store.listing()['total'], 1)
        self.store.save(result(), body)
        self.assertEqual(self.store.listing()['total'], 2)
        self.assertEqual(len(list(self.store.root.glob('*.jpg'))), 2)
    def test_manual_correction_preserves_photo_and_wins_over_late_ocr(self):
        value = result(); self.store.save(value, frame())
        old = self.store.get(value['recognition_id'])
        self.store.import_rows([dict(id=old['id'], time=1, card=card('manual'), verification='manual')])
        self.store.verify({**value, 'verified': True})
        row = self.store.get(old['id'])
        self.assertEqual(row['card']['id'], 'manual')
        self.assertEqual(row['scanned_image'], old['scanned_image'])
        self.assertEqual(row['time'], old['time'])
    def test_old_history_import_has_no_fabricated_photo_and_no_duplicates(self):
        rows = [dict(id='old', time=123, card=card(), verification='possible')]
        self.store.import_rows(rows); self.store.import_rows(rows)
        self.assertEqual(self.store.revision(), 1)
        self.assertEqual(self.store.listing()['total'], 1)
        self.assertIsNone(self.store.get('old')['scanned_image'])
    def test_pagination_and_changes_do_not_skip_equal_timestamps(self):
        self.store.import_rows([dict(id=f'old-{i:03}', time=123, card=card(), verification='possible') for i in range(50)])
        first = self.store.listing(limit=30); second = self.store.listing(before=first['next'], limit=30)
        self.assertEqual(len({r['id'] for r in first['rows']+second['rows']}), 50)
        self.assertIsNone(second['next'])
        self.assertEqual(len(self.store.changes(40)['rows']), 10)
    def test_invalid_batch_is_atomic_and_external_urls_never_become_fetches(self):
        row = dict(id='old', time=123, card=card(), verification='possible')
        with self.assertRaises(ValueError):
            self.store.import_rows([row, {**row, 'id': '../bad'}])
        self.assertEqual(self.store.listing()['total'], 0)
        for url in ('http://127.0.0.1/a', 'https://assets.tcgdex.net.evil/a', 'https://assets.tcgdex.net/../a', 'https://assets.tcgdex.net/a?x=1'):
            self.assertIsNone(reference_url(url))
        self.assertEqual(reference_url('https://assets.tcgdex.net/en/test/1'), 'https://assets.tcgdex.net/en/test/1/high.webp')
        with self.assertRaises(FileNotFoundError):
            self.store.media('../history.sqlite3')
    def test_reference_download_is_shared_across_concurrent_viewers_and_restart(self):
        self.store.save(result('test'), frame())
        name = self.store.get('test')['reference_image'].split('/')[-1]
        class Opener:
            def open(self, *args, **kwargs):
                return BytesIO(frame())
        with patch('tcg_scanner.history.build_opener', return_value=Opener()) as fetch:
            with ThreadPoolExecutor(4) as pool:
                paths = list(pool.map(self.store.reference, [name]*4))
            self.assertEqual(fetch.call_count, 1)
            self.assertTrue(all(p == paths[0] for p in paths))
            self.store.close(); self.store = History(self.directory.name)
            self.store.reference(name)
            self.assertEqual(fetch.call_count, 1)


class PhotoProcessor(FakeProcessor):
    def process(self, state, body, threshold):
        value = super().process(state, b'A'*40, threshold)
        if value['event'] == 'recognized':
            value['matches'][0]['card'] = card()
            value['_card_crop'] = frame((630, 880))
        return value


class GalleryTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.app = create_app(advertise=False, processor_factory=PhotoProcessor, history_root=self.directory.name)
        self.client = TestClient(self.app, base_url='http://127.0.0.1', client=('127.0.0.1', 1))
        self.client.__enter__()
    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.directory.cleanup()
    def test_websocket_notification_and_camera_image_http_cache(self):
        group=self.app.state.runtime.history.action(dict(action='create',name='Test'))['id']
        self.app.state.runtime.history.action(dict(action='select',id=group))
        with self.client.websocket_connect('ws://127.0.0.1/history/events', headers={'origin': 'http://127.0.0.1'}) as events:
            self.assertGreaterEqual(events.receive_json()['revision'], 0)
            with self.client.websocket_connect('/scan') as scanner:
                scanner.send_json(dict(protocol=1, session=str(uuid4())))
                scanner.receive_json()
                scanner.send_bytes(struct.pack('!Q', 1)+frame())
                value = scanner.receive_json()
                self.assertEqual(value['event'], 'recognized')
                self.assertGreater(events.receive_json()['revision'], 0)
                row = self.client.get('/api/history').json()['rows'][0]
                response = self.client.get(row['scanned_image'])
                self.assertEqual(response.content, jpeg(frame((630, 880)), 880))
                self.assertNotIn('_card_crop', value)
                self.assertEqual(row['scanned_kind'], 'yolo_crop')
                self.assertIn('immutable', response.headers['cache-control'])
                self.assertEqual(self.client.get(row['scanned_image'], headers={'if-none-match': response.headers['etag']}).status_code, 304)
    def test_same_origin_gallery_allowed_external_browser_blocked(self):
        self.assertEqual(self.client.get('/').status_code, 200)
        self.assertEqual(self.client.get('/api/history', headers={'origin': 'http://127.0.0.1'}).status_code, 200)
        self.assertEqual(self.client.get('/api/history', headers={'origin': 'https://evil.example'}).status_code, 403)
        self.assertEqual(self.client.get('/api/history', headers={'host': 'evil.example'}).status_code, 403)
        self.assertEqual(self.client.get('/api/history', headers={'sec-fetch-site': 'cross-site'}).status_code, 403)
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect('ws://127.0.0.1/history/events', headers={'origin': 'https://evil.example'}):
                pass
    def test_legacy_sync_is_retired(self):
        self.assertEqual(self.client.post('/api/collection/sync',json=[]).status_code,410)
        self.assertEqual(self.client.post('/api/history/import',json=[]).status_code,410)
        self.assertEqual(self.client.get('/api/history?before=bad').status_code,400)
        self.assertEqual(self.client.post('/api/library/action',content=b'x'*270000,headers={'content-type':'application/json'}).status_code,413)
