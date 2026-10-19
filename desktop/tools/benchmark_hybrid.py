"""Real-image benchmark using an isolated catalog snapshot; no collection writes."""
import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',TCG_OFFLINE='1')
from tcg_scanner.config import Config, default_config_path
from tcg_scanner.live import LiveProcessor


def main():
    root=Path(__file__).resolve().parents[2]
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=root/'artifacts/benchmarks/hybrid.json')
    parser.add_argument('--manifest',type=Path,required=True,help='JSON list: file (relative to manifest), expected card ID, cropped (boolean)')
    parser.add_argument('--config',type=Path)
    args=parser.parse_args()
    cases=json.loads(args.manifest.read_text())
    if not isinstance(cases,list) or not cases:parser.error('Manifest must be a nonempty JSON list')
    config=Config.load(args.config or default_config_path(),{})
    weights=Path(config.collector_weights).expanduser()
    if not weights.is_absolute():weights=config.root/weights
    with tempfile.TemporaryDirectory(prefix='tcg-hybrid-') as temporary:
        source=sqlite3.connect(f'file:{config.root / "metadata.sqlite3"}?mode=ro',uri=True)
        snapshot=sqlite3.connect(Path(temporary)/'metadata.sqlite3')
        source.backup(snapshot);snapshot.close();source.close()
        config=replace(config,data_dir=temporary,collector_weights=str(weights))
        processor=LiveProcessor(config)
        try:
            samples=[]
            for item in cases:
                started=perf_counter()
                try:
                    body=(args.manifest.parent/item['file']).read_bytes()
                    crop=body if item.get('cropped',False) else processor.session.prepare_image(body)[0]
                except ValueError as exc:
                    samples.append(dict(file=item['file'],expected=item['expected'],error=str(exc)));continue
                samples.append(dict(file=item['file'],expected=item['expected'],crop=crop,card_detection_ms=(perf_counter()-started)*1000,source='labeled_photo'))
            if not any('crop' in item for item in samples):raise ValueError('No usable cards in manifest')
            report=dict(hardware=processor.info,results=[],benchmark={})
            for item in samples:
                if 'crop' not in item:report['results'].append(item);continue
                outcome=processor.identify_crop(item['crop'])
                report['results'].append({**{k:v for k,v in item.items() if k!='crop'},**outcome})
                print(item['file'],outcome['status'],outcome['selected_card_id'],outcome['ocr'].get('number'),round(outcome['timing_ms']['total_ms'],1),flush=True)
            import numpy as np
            for mode in ['serial','concurrent']:
                processor.config=replace(config,inference_mode=mode)
                measured=[]
                for item in [i for i in samples if 'crop' in i][:12]:
                    processor.cache.clear();processor.vectors.clear();processor.combined_cache.clear()
                    measured.append(processor.identify_crop(item['crop'])['timing_ms'])
                report['benchmark'][mode]={key:{'median_ms':float(np.median([m.get(key,0.) for m in measured])),
                     'p95_ms':float(np.percentile([m.get(key,0.) for m in measured],95))} for key in measured[0]}
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(json.dumps(report,indent=2)+'\n')
            print(json.dumps(report['benchmark'],indent=2),flush=True)
        finally:processor.close()


if __name__=='__main__':main()
