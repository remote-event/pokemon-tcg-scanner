from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
import struct
import unittest
from fastapi.testclient import TestClient
from tcg_scanner.server import create_app
from tests.test_history import PhotoProcessor, frame, card


class PendingProcessor(PhotoProcessor):
    def process(self,state,body,threshold):
        return dict(event='confirmation',request_id='a'*32,matches=[dict(score=.91,card=card()),
            dict(score=.90,card={**card(),'id':'alternative','set':'Other set'})],
            reason='ambiguous',_card_crop=body,interval_ms=250)


class ConfirmationTests(unittest.TestCase):
    def test_no_save_until_confirmation_and_idempotent_retry(self):
        with TemporaryDirectory() as directory:
            app=create_app(advertise=False,processor_factory=PendingProcessor,history_root=Path(directory))
            with TestClient(app,base_url='http://127.0.0.1',client=('127.0.0.1',1234)) as client:
                def action(**body): return client.post('/api/library/action',json=body).json()
                collection=action(action='create',name='Test')['id'];action(action='select',id=collection)
                with client.websocket_connect('/scan') as ws:
                    ws.send_json(dict(protocol=1,session=str(uuid4())));ws.receive_json()
                    ws.send_bytes(struct.pack('!Q',1)+frame());result=ws.receive_json()
                    self.assertEqual(result['event'],'confirmation')
                    self.assertEqual(client.get('/api/library').json()['total'],0)
                    self.assertEqual(len(client.get('/api/library/pending').json()['rows']),1)
                    self.assertEqual(client.get('/api/library/pending/'+'a'*32+'/image').status_code,200)
                    body={'request_id':'a'*32,'card_id':'alternative'}
                    confirmed=client.post('/api/library/confirm',json=body)
                    self.assertEqual(confirmed.status_code,200,confirmed.text)
                    self.assertEqual(ws.receive_json()['event'],'recognized')
                    self.assertEqual(client.post('/api/library/confirm',json=body).status_code,200)
                    view=client.get('/api/library',params={'collection_id':collection}).json()
                    self.assertEqual(view['total'],1)
                    self.assertEqual(view['rows'][0]['card']['id'],'alternative')
                    self.assertEqual(view['rows'][0]['verification'],'user_confirmed')
                    self.assertEqual(client.get('/api/library/pending').json()['rows'],[])

    def test_collection_change_rejects_stale_confirmation(self):
        with TemporaryDirectory() as directory:
            app=create_app(advertise=False,processor_factory=PendingProcessor,history_root=Path(directory))
            with TestClient(app,base_url='http://127.0.0.1',client=('127.0.0.1',1234)) as client:
                ids=[client.post('/api/library/action',json=dict(action='create',name=name)).json()['id'] for name in ('A','B')]
                client.post('/api/library/action',json=dict(action='select',id=ids[0]))
                with client.websocket_connect('/scan') as ws:
                    ws.send_json(dict(protocol=1,session=str(uuid4())));ws.receive_json()
                    ws.send_bytes(struct.pack('!Q',1)+frame());ws.receive_json()
                    client.post('/api/library/action',json=dict(action='select',id=ids[1]))
                    result=client.post('/api/library/confirm',json={'request_id':'a'*32,'card_id':'alternative'})
                    self.assertEqual(result.status_code,409)
                    self.assertEqual(client.get('/api/library').json()['total'],0)


if __name__=='__main__':unittest.main()
