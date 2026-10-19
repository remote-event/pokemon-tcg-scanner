"""Save training curves from RF-DETR's CSV metrics, including resumed history."""
import argparse
import csv
import json
import math
import psutil
from pathlib import Path
import time


def read_epochs(path):
    epochs = {}
    with Path(path).open(newline='') as stream:
        for row in csv.DictReader(stream):
            try:
                epoch = int(float(row['epoch'])) + 1
            except (KeyError, ValueError, TypeError):
                continue
            metrics = epochs.setdefault(epoch, {})
            for key, value in row.items():
                if not key or key in ('epoch', 'step'):
                    continue
                try:
                    value = float(value)
                except (ValueError, TypeError):
                    continue
                if math.isfinite(value):
                    metrics[key] = value
    return dict(sorted(epochs.items()))


def render(run):
    run = Path(run)
    epochs = read_epochs(run / 'metrics.csv')
    if not epochs:
        return
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    panels = [
        ('Total loss', [('train/loss', 'Training'), ('val/loss', 'Validation')]),
        ('Box losses', [('train/loss_bbox', 'L1'), ('train/loss_giou', 'GIoU')]),
        ('Classification loss', [('train/loss_ce', 'Classification')]),
        ('Validation average precision', [('val/mAP_50', 'AP50'), ('val/mAP_50_95', 'AP50–95'), ('val/mAP_75', 'AP75')]),
        ('Validation precision / recall', [('val/precision', 'Precision'), ('val/recall', 'Recall'), ('val/F1', 'F1')]),
        ('Learning rate', [('train/lr', 'LR'), ('train/lr_max', 'Maximum'), ('train/lr_min', 'Minimum')]),
    ]
    with plt.style.context('seaborn-v0_8-whitegrid'):
        fig, axes = plt.subplots(2, 3, figsize=(15, 8), constrained_layout=True)
        for ax, (title, series) in zip(axes.flat, panels):
            for key, label in series:
                points = [(epoch, values[key]) for epoch, values in epochs.items() if key in values]
                if points:
                    xs, ys = zip(*points)
                    ax.plot(xs, ys, marker='.', linewidth=1.6, label=label)
            ax.set(title=title, xlabel='Epoch')
            ax.xaxis.get_major_locator().set_params(integer=True)
            if ax.lines:
                ax.legend(fontsize=8)
            else:
                ax.text(.5, .5, 'Not logged yet', ha='center', va='center', transform=ax.transAxes)
            if title.startswith('Validation'):
                ax.set_ylim(0, 1.02)
        fig.suptitle(f'Collector-number detector · RF-DETR Small · through epoch {max(epochs)}', fontsize=16)
        graphs = run / 'graphs'
        graphs.mkdir(exist_ok=True)
        for extension in ('png', 'svg'):
            temporary = graphs / f'.training-curves.tmp.{extension}'
            fig.savefig(temporary, dpi=150)
            temporary.replace(graphs / f'training-curves.{extension}')
        plt.close(fig)
    summary = dict(latest_epoch=max(epochs), latest_metrics=epochs[max(epochs)],
                   epochs=[{'epoch': epoch, **values} for epoch, values in epochs.items()])
    temporary = graphs / '.metrics-summary.tmp.json'
    temporary.write_text(json.dumps(summary, indent=2) + '\n')
    temporary.replace(graphs / 'metrics-summary.json')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--parent-pid', type=int)
    args = parser.parse_args()
    last_modified = None
    while True:
        if args.parent_pid and not psutil.pid_exists(args.parent_pid):
            break
        try:
            modified = (args.run / 'metrics.csv').stat().st_mtime_ns
            if modified != last_modified:
                render(args.run)
                last_modified = modified
        except FileNotFoundError:
            if not args.watch:
                raise
        except Exception as error:
            if not args.watch:
                raise
            print(f'Graph update will retry: {error}', flush=True)
        if not args.watch:
            break
        time.sleep(5)


if __name__ == '__main__':
    main()
