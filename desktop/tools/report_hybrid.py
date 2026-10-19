"""Save Matplotlib latency plots and a compact summary from benchmark_hybrid.py."""
import argparse
from collections import Counter
import json
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report',type=Path)
    args=parser.parse_args()
    report=json.loads(args.report.read_text())
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    keys=['embedding_ms','faiss_ms','number_detection_ms','number_crop_ms','recognition_ms','matching_ms','total_ms']
    names=['Embedding','FAISS','Number detection','Crop encoding','Recognition','Matching','Combined']
    fig,ax=plt.subplots(figsize=(11,5))
    for offset,mode,color in [(-.2,'serial','#64748b'),(.2,'concurrent','#0891b2')]:
        values=[report['benchmark'][mode][key]['median_ms'] for key in keys]
        bars=ax.bar([i+offset for i in range(len(keys))],values,width=.38,label=mode.title(),color=color)
        ax.bar_label(bars,fmt='%.2f',fontsize=8,padding=2)
    ax.set_xticks(range(len(keys)),names,rotation=15,ha='right')
    ax.set_ylabel('Median milliseconds (lower is faster)')
    ax.set_title('RTX 4070 · warmed cropped-card inference · 12 real photos')
    ax.legend();ax.grid(axis='y',alpha=.15);ax.set_axisbelow(True)
    fig.text(.5,.01,'Concurrent stages overlap. Excludes phone capture, whole-card YOLO, transport and collection persistence.',ha='center',fontsize=9)
    fig.tight_layout(rect=[0,.05,1,1])
    for extension in ('png','svg'):fig.savefig(args.report.parent/f'latency.{extension}',dpi=160)
    summary={}
    for source in ('labeled_photo','held_out_scan_export'):
        rows=[r for r in report['results'] if r.get('source')==source]
        entry=dict(count=len(rows),statuses=dict(Counter(r['status'] for r in rows)))
        if source=='labeled_photo':
            entry.update(correct_automatic=sum(r['selected_card_id']==r['expected'] for r in rows),
                incorrect_automatic=sum(bool(r['selected_card_id']) and r['selected_card_id']!=r['expected'] for r in rows))
        else:entry['note']='Imported labels are previous matcher predictions, not independent ground truth.'
        summary[source]=entry
    summary['benchmark']=report['benchmark']
    (args.report.parent/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2))


if __name__=='__main__':main()
