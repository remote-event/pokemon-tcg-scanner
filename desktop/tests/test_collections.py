from tempfile import TemporaryDirectory
from pathlib import Path
from io import BytesIO
from zipfile import ZipFile
import json
import struct
import unittest
from uuid import uuid4
from fastapi.testclient import TestClient
from tcg_scanner.collections import CollectionStore, Conflict
from tcg_scanner.history import History
from tcg_scanner.server import create_app
from tests.test_history import card, frame, result, PhotoProcessor


class CollectionsTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.store=CollectionStore(self.temp.name)
        self.a=self.store.action(dict(action='create',name='My binder'))['id']
        self.b=self.store.action(dict(action='create',name='Trades'))['id']
        self.store.action(dict(action='select',id=self.a))
    def tearDown(self):self.store.close();self.temp.cleanup()
    def save(self,id):self.store.save(result(id),frame());return self.store.get(id)
    def test_duplicate_scans_are_separate_and_selection_persists(self):
        first=self.save('first');second=self.save('second');self.save('first')
        self.assertEqual(self.store.view(self.a)['total'],2)
        self.assertEqual(first['scanned_image'],second['scanned_image'])
        self.store.close();self.store=CollectionStore(self.temp.name)
        self.assertEqual(self.store.active(),self.a)
        self.store.action(dict(action='select',id=self.b));self.save('third')
        self.assertEqual([r['id'] for r in self.store.view(self.b)['rows']],['third'])
    def test_edit_keeps_photo_time_and_move_preserves_entry(self):
        first=self.save('first')
        fields=dict(name='Corrected',set='My set',number='99',code='SET',total=100)
        self.store.action(dict(action='edit',ids=['first'],versions={'first':first['revision']},fields=fields))
        edited=self.store.get('first');self.assertEqual(edited['card']['name'],'Corrected')
        self.assertEqual((edited['time'],edited['scanned_image']),(first['time'],first['scanned_image']))
        self.assertIsNone(edited['reference_image'])
        with self.assertRaises(Conflict):self.store.action(dict(action='edit',ids=['first'],versions={'first':first['revision']},fields=fields))
        self.store.action(dict(action='move',ids=['first'],collection_id=self.b))
        self.assertEqual(self.store.view(self.a)['total'],0);self.assertEqual(self.store.get('first')['collection_id'],self.b)
        self.store.close();self.store=CollectionStore(self.temp.name);self.assertEqual(self.store.get('first')['card']['name'],'Corrected')
    def test_bulk_delete_is_atomic_and_shared_images_survive_until_last_scan(self):
        first=self.save('first');self.save('second');path=self.store.root/first['scanned_image'].split('/')[-1]
        with self.assertRaises(Conflict):self.store.action(dict(action='delete_scans',ids=['first','missing']))
        self.assertEqual(self.store.view(self.a)['total'],2)
        self.store.action(dict(action='delete_scans',ids=['first']));self.assertTrue(path.exists())
        self.save('first');self.assertIsNone(self.store.get('first'))
        self.store.action(dict(action='delete_scans',ids=['second']));self.assertFalse(path.exists())
        self.store.close();self.store=CollectionStore(self.temp.name)
        self.save('second');self.assertEqual(self.store.view(self.a)['total'],0)
    def test_delete_active_collection_requires_selection_and_does_not_touch_other(self):
        self.save('first');self.store.action(dict(action='select',id=self.b));self.save('second')
        self.store.action(dict(action='delete_collection',id=self.b))
        self.assertIsNone(self.store.active());self.assertIsNotNone(self.store.get('first'))
        with self.assertRaises(ValueError):self.save('third')
        self.store.action(dict(action='rename',id=self.a,name='Renamed'));self.assertEqual(self.store.view(self.a)['collection']['name'],'Renamed')
    def test_pagination_filter_and_invalid_bulk_operations(self):
        for i in range(43):self.save(f'item-{i}')
        one=self.store.view(self.a,limit=40);two=self.store.view(self.a,before=one['next'])
        self.assertEqual(len({r['id'] for r in one['rows']+two['rows']}),43)
        self.assertEqual(self.store.view(self.a,query='absent')['total'],0)
        for value in [dict(action='move',ids=['item-0'],collection_id='missing'),dict(action='delete_scans',ids=['item-0','item-0']),dict(action='rename',id=self.a,name=' '),dict(action='edit',ids=['item-0'],fields=dict(name='A',set='B',number='1',total=1.5))]:
            with self.assertRaises(ValueError):self.store.action(value)
        self.assertEqual(self.store.view(self.a)['total'],43)


    def test_export_all_scans_with_edits_and_original_photos_without_mutation(self):
        for i in range(43):
            self.save(f'item-{i}')
        self.store.action(dict(action='edit',ids=['item-0'],
            fields=dict(name='Pokémon / special',set='My set',number='007',code='',total=None)))
        self.store.action(dict(action='select',id=self.b))
        self.save('other-collection')
        revision = self.store.revision()
        with self.store.export_zip(self.a) as export, ZipFile(export) as bundle:
            self.assertIsNone(bundle.testzip())
            data = json.loads(bundle.read('collection.json'))
            self.assertEqual(data['collection']['name'], 'My binder')
            self.assertEqual(data['count'], 43)
            self.assertEqual(data['missing_image_count'], 0)
            self.assertEqual({c['id'] for c in data['cards']}, {f'item-{i}' for i in range(43)})
            edited = next(c for c in data['cards'] if c['id']=='item-0')
            self.assertEqual((edited['name'], edited['set'], edited['number']),
                             ('Pokémon / special', 'My set', '007'))
            photos = {c['image_path'] for c in data['cards']}
            self.assertEqual(len(photos), 1)
            self.assertEqual(set(bundle.namelist()), {'collection.json', 'images/', *photos})
            original = self.store.root / self.store.get('item-0')['scanned_image'].split('/')[-1]
            for photo in photos:
                self.assertEqual(bundle.read(photo), original.read_bytes())
        self.assertEqual(self.store.revision(), revision)
        self.assertEqual(self.store.active(), self.b)
        self.assertEqual(self.store.view(self.a)['total'], 43)

    def test_export_empty_missing_photos_and_unknown_collection(self):
        with self.store.export_zip(self.a) as export, ZipFile(export) as bundle:
            self.assertEqual(json.loads(bundle.read('collection.json'))['cards'], [])
            self.assertIn('images/', bundle.namelist())
        self.save('missing')
        photo = self.store.get('missing')['scanned_image'].split('/')[-1]
        (self.store.root / photo).unlink()
        with self.store.lock, self.store.db:
            self.store._insert('legacy', 100, card(), self.a, 'identified')
        with self.store.export_zip(self.a) as export, ZipFile(export) as bundle:
            data = json.loads(bundle.read('collection.json'))
            self.assertEqual(data['count'], 2)
            self.assertEqual(data['missing_image_count'], 2)
            self.assertTrue(all(c['image_path'] is None for c in data['cards']))
            self.assertEqual(set(bundle.namelist()), {'collection.json', 'images/'})
        with self.assertRaises(ValueError):
            self.store.export_zip('missing')
        with self.assertRaises(ValueError):
            self.store.export_zip('../outside')


class MigrationTests(unittest.TestCase):
    def test_old_scans_images_sessions_and_extra_copies_migrate_once_without_backup(self):
        with TemporaryDirectory() as root:
            old=History(root);old.save(result('old'),frame());photo=old.get('old')['scanned_image']
            old.sync_collection([dict(card=card(),quantity=3)]);old.close()
            store=CollectionStore(root)
            try:
                self.assertEqual(store.view('imported')['total'],3)
                payload=dict(client='phone',backup=dict(format='tcg-scanner-library',version=2,batches=[dict(id='session',started=100,ended=None)],collection=[dict(card=card(),quantity=3)],history=[dict(id='remote:old',time=100,card=card(),matches=result()['matches'],verification='identified',batch_id='session')]))
                store.migrate_phone(payload);store.migrate_phone(payload)
                self.assertEqual(store.listing()['total'],3)
                self.assertNotEqual(store.get('old')['collection_id'],'imported')
                self.assertEqual(store.get('old')['scanned_image'],photo)
                self.assertTrue((Path(root)/photo.split('/')[-1]).exists())
                self.assertFalse((Path(root)/'migration-backups').exists())
                store.action(dict(action='delete_scans',ids=['old']));store.migrate_phone(payload)
                self.assertIsNone(store.get('old'))
            finally:store.close()


class CollectionApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.app=create_app(advertise=False,processor_factory=PhotoProcessor,history_root=self.temp.name)
        self.client=TestClient(self.app,base_url='http://127.0.0.1',client=('127.0.0.1',1));self.client.__enter__()
    def tearDown(self):self.client.__exit__(None,None,None);self.temp.cleanup()
    def action(self,**value):
        response=self.client.post('/api/library/action',json=value,headers={'origin':'http://127.0.0.1'});self.assertEqual(response.status_code,200,response.text);return response.json()
    def test_web_subproject_is_served_with_export_action(self):
        root = Path(__file__).resolve().parents[2] / 'web'
        for url, filename in [('/', 'index.html'), ('/web/gallery.js', 'gallery.js'),
                              ('/web/gallery.css', 'gallery.css')]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, (root / filename).read_bytes())
        self.assertIn('Export ZIP', self.client.get('/').text)
        self.assertEqual(self.client.get('/web/tests/collections.mjs').status_code, 404)

    def test_export_download_headers_content_and_access(self):
        id = self.action(action='create',name='Export binder')['id']
        response = self.client.get('/api/library/export', params={'collection_id':id})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['content-type'], 'application/zip')
        self.assertEqual(response.headers['content-disposition'], f'attachment; filename="collection-{id}.zip"')
        self.assertEqual(int(response.headers['content-length']), len(response.content))
        self.assertEqual(response.headers['cache-control'], 'no-store')
        with ZipFile(BytesIO(response.content)) as bundle:
            data = json.loads(bundle.read('collection.json'))
            self.assertEqual(data['collection']['id'], id)
            self.assertEqual(data['cards'], [])
        self.assertEqual(self.client.get('/api/library/export', params={'collection_id':'missing'}).status_code, 400)
        self.assertEqual(self.client.get('/api/library/export', params={'collection_id':id},
                         headers={'origin':'https://evil.example'}).status_code, 403)

    def test_full_scanner_web_workflow_and_reconnect(self):
        id=self.action(action='create',name='Binder')['id']
        session=str(uuid4())
        with self.client.websocket_connect('/scan') as scan:
            scan.send_json(dict(protocol=1,session=session));self.assertEqual(scan.receive_json()['collections_version'],1)
            scan.send_bytes(struct.pack('!Q',1)+frame());self.assertEqual(scan.receive_json()['event'],'waiting')
            with self.client.websocket_connect('ws://127.0.0.1/history/events',headers={'origin':'http://127.0.0.1'}) as events:
                events.receive_json();self.action(action='select',id=id);self.assertGreater(events.receive_json()['revision'],0)
                scan.send_bytes(struct.pack('!Q',2)+frame());response=scan.receive_json();self.assertEqual(response['event'],'recognized')
                scan_id=response['recognition_id'];row=self.client.get('/api/history/'+scan_id).json()
                self.assertEqual(row['collection_id'],id);self.assertEqual(self.client.get(row['scanned_image']).status_code,200)
                self.action(action='edit',ids=[scan_id],fields=dict(name='Edited card',set='Edited set',number='7',code='',total=None))
                other=self.action(action='create',name='Trades')['id'];self.action(action='move',ids=[scan_id],collection_id=other)
                self.assertEqual(self.client.get('/api/library',params={'collection_id':other}).json()['total'],1)
                self.action(action='delete_scans',ids=[scan_id]);self.assertEqual(self.client.get('/api/history/'+scan_id).status_code,404)
        with self.client.websocket_connect('/scan') as scan:
            scan.send_json(dict(protocol=1,session=session));scan.receive_json()
            self.assertEqual(self.client.get('/api/history').json()['total'],0)
    def test_writes_require_json_and_local_same_origin_and_old_sync_cannot_resurrect(self):
        value=dict(action='create',name='Bad')
        self.assertEqual(self.client.post('/api/library/action',json=value,headers={'origin':'https://evil.example'}).status_code,403)
        self.assertEqual(self.client.post('/api/library/action',data='{}').status_code,415)
        self.assertEqual(self.client.post('/api/history/import',json=[]).status_code,410)
        self.assertEqual(self.client.post('/api/collection/sync',json=[]).status_code,410)
        self.assertEqual(self.client.get('/api/library').json()['collections'],[])
