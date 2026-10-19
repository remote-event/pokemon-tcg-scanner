"""Replay real cropped cards over the LAN API using one temporary collection."""
import argparse
import json
from pathlib import Path
import struct
from time import perf_counter, sleep
from urllib.request import Request, urlopen
from uuid import uuid4


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server',required=True,help='URL of an isolated test server; this test changes its active collection')
    parser.add_argument('--images',type=Path,nargs='+',required=True,help='Already cropped card images')
    parser.add_argument('--output',type=Path,default=Path('artifacts/benchmarks/transport.json'))
    args=parser.parse_args()
    for path in args.images:
        if not path.is_file():parser.error(f'Image not found: {path}')
    from websockets.sync.client import connect
    import numpy as np
    def api(path,body=None):
        request=Request(args.server+path,data=json.dumps(body).encode() if body is not None else None,
                        headers={'Content-Type':'application/json'})
        with urlopen(request,timeout=15) as response:return json.load(response)
    previous=api('/api/library')['active_collection']
    collection=api('/api/library/action',dict(action='create',name='Transport benchmark '+str(uuid4())[:8]))['id']
    results=[]
    try:
        api('/api/library/action',dict(action='select',id=collection))
        with connect(args.server.replace('http://','ws://')+'/scan',open_timeout=10) as ws:
            ws.send(json.dumps(dict(protocol=1,session=str(uuid4()),input_mode='card_crop_v1')))
            ready=json.loads(ws.recv(timeout=10));assert ready['type']=='ready'
            sequence=0
            def send(body):
                nonlocal sequence
                sequence+=1;at=perf_counter();ws.send(struct.pack('!Q',sequence)+body)
                while True:
                    result=json.loads(ws.recv(timeout=15))
                    if result.get('sequence')==sequence:return result,(perf_counter()-at)*1000
            for path in args.images:
                for _ in range(4):send(b'TCGEMPTY');sleep(.13)
                started=perf_counter();body=path.read_bytes()
                for _ in range(10):
                    result,round_trip=send(body)
                    if result['event'] in ('recognized','confirmation'):
                        results.append(dict(file=path.name,event=result['event'],round_trip_ms=round_trip,
                            presentation_ms=(perf_counter()-started)*1000,processing_ms=result.get('processing_ms'),
                            timing_ms=result.get('timing_ms')))
                        if result['event']=='confirmation':
                            api('/api/library/confirm',dict(request_id=result['request_id'],card_id=None))
                            json.loads(ws.recv(timeout=10))
                        break
                    sleep(.13)
                else:raise AssertionError(f'No decision: {path}: {result}')
    finally:
        api('/api/library/action',dict(action='delete_collection',id=collection))
        if previous:api('/api/library/action',dict(action='select',id=previous))
    report=dict(note='WSL client over local LAN address. Includes transport, quality checks and persistence; excludes physical phone capture and on-device YOLO. Presentation includes 130 ms replay spacing and stability gate.',
        results=results,summary={key:dict(median=float(np.median([r[key] for r in results])),
             p95=float(np.percentile([r[key] for r in results],95))) for key in ('round_trip_ms','presentation_ms','processing_ms')})
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report['summary'],indent=2))


if __name__=='__main__':main()
