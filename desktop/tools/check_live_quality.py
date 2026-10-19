"""Offline CUDA replay of labeled phone photos; writes no production scan records.

Run from desktop/: ../.venv/bin/python -B tools/check_live_quality.py
Uses a temporary SQLite snapshot and resident production model implementations.
"""
import argparse
import os
os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', TCG_OFFLINE='1', YOLO_OFFLINE='true', YOLO_AUTOINSTALL='false')
from dataclasses import replace
from io import BytesIO
import json
from pathlib import Path
import shutil
import sqlite3
import sys
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import numpy as np
from PIL import Image,ImageOps
from tcg_scanner.config import Config, default_config_path
from tcg_scanner.live import LiveProcessor,LiveState


def jpeg(rgb):
    output=BytesIO();Image.fromarray(rgb).save(output,format='JPEG',quality=78);return output.getvalue()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',type=Path,required=True,help='JSON list of file paths relative to manifest and expected card IDs')
    parser.add_argument('--config',type=Path)
    parser.add_argument('--output',type=Path,default=ROOT.parent/'artifacts/benchmarks/live-quality.json')
    args=parser.parse_args()
    cases=json.loads(args.manifest.read_text())
    if not cases:parser.error('Provide at least one labeled photo')
    config=Config.load(args.config or default_config_path(),{})
    weights=Path(config.collector_weights).expanduser()
    if not weights.is_absolute():weights=config.root/weights
    results=[];now=0.
    with TemporaryDirectory(prefix='tcg-quality-replay-') as directory:
        target=Path(directory)
        source=sqlite3.connect(f'file:{config.root}/metadata.sqlite3?mode=ro',uri=True)
        destination=sqlite3.connect(target/'metadata.sqlite3')
        source.backup(destination);destination.close();source.close()
        shutil.copytree(config.root/'indexes',target/'indexes')
        processor=LiveProcessor(replace(config,data_dir=directory,collector_weights=str(weights)))
        print(json.dumps(processor.info),flush=True)
        def process(state,body):
            nonlocal now
            now+=.125
            with patch('tcg_scanner.live.monotonic',return_value=now):
                return processor.process(state,body,.5)
        try:
            for case in cases:
                name=Path(case['file']).stem
                path=args.manifest.parent/case['file']
                with Image.open(path) as image:
                    image=ImageOps.exif_transpose(image).convert('RGB');image.thumbnail((1280,1280),Image.Resampling.BILINEAR)
                    original=np.array(image)
                for condition,factor in [('original',1.),('dark25',.25),('dark08',.08)]:
                    body=jpeg(np.round(original*factor).astype('uint8'))
                    state=LiveState();events=[];selected=None;times=[]
                    for _ in range(6):
                        started=perf_counter();response=process(state,body);times.append((perf_counter()-started)*1000)
                        events.append(response['event'])
                        if response['event']=='recognized':
                            selected=response['matches'][0]['card']['id']
                    # A black camera frame must never clear a previously recognized card.
                    previous=state.recognition_id
                    dark_events=[process(state,jpeg(np.zeros_like(original)))['event'] for _ in range(6)]
                    assert 'removed' not in dark_events and state.recognition_id==previous
                    restored=[process(state,body)['event'] for _ in range(4)]
                    if previous:assert 'recognized' not in restored,(name,condition,'duplicate on restore')
                    results.append(dict(image=name,expected=case['expected'],condition=condition,selected=selected,
                                        recognitions=events.count('recognized'),
                                        holds_darkness=True,no_duplicate_on_restore=True,median_frame_ms=float(np.median(times))))
                    print(json.dumps(results[-1]),flush=True)
            summary={condition:{'n':sum(r['condition']==condition for r in results),
                               'correct_visual':sum(r['condition']==condition and r['selected']==r['expected'] for r in results),
                               'wrong_visual':sum(r['condition']==condition and r['selected'] not in (None,r['expected']) for r in results),
                               'deferred':sum(r['condition']==condition and r['selected'] is None for r in results)}
                     for condition in ('original','dark25','dark08')}
            out=args.output
            out.parent.mkdir(parents=True,exist_ok=True)
            out.write_text(json.dumps(dict(info=processor.info,stats=processor.stats,summary=summary,results=results),indent=2))
            print(json.dumps(summary,indent=2),flush=True)
            assert summary['original']['correct_visual']==summary['original']['n'],summary
        finally:processor.close()

if __name__=='__main__':main()
