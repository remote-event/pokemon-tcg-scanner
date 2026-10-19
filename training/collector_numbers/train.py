"""Train the collector-number detector in an isolated RF-DETR environment."""
import argparse
from filelock import FileLock, Timeout
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--epochs', type=int, default=100)
    parser.add_argument('--batch-size', type=int, default=2)
    parser.add_argument('--grad-accum-steps', type=int, default=8)
    parser.add_argument('--resume', type=Path)
    args = parser.parse_args()
    dataset, output = args.dataset.resolve(), args.output.resolve()
    resume = args.resume.resolve() if args.resume else None
    manifest = json.loads((dataset / 'manifest.json').read_text())
    if output.exists() and any(output.iterdir()) and not args.resume:
        parser.error('Use a fresh output directory, or --resume with a training checkpoint')
    output.mkdir(parents=True, exist_ok=True)
    # Hold the lock for the whole run, including checkpoint writes.
    run_lock = FileLock(output / '.training.lock')
    try:
        run_lock.acquire(timeout=0)
    except Timeout:
        parser.error('This output directory already has a running trainer')
    try:
        train(args, dataset, output, resume, manifest)
    finally:
        run_lock.release()


def train(args, dataset, output, resume, manifest):
    report_script = Path(__file__).resolve().with_name('report.py')
    # Pretrained downloads belong to the training cache, not the repository root.
    cache = Path.home() / '.cache/tcg-scanner/rfdetr-weights'
    cache.mkdir(parents=True, exist_ok=True)
    os.chdir(cache)
    import torch
    from pytorch_lightning import seed_everything
    from rfdetr import RFDETRSmall
    if not torch.cuda.is_available():
        raise RuntimeError('CUDA is required for this training run')
    torch.set_num_threads(4)
    seed_everything(manifest['seed'], workers=True)
    model = RFDETRSmall(resolution=manifest['resolution'], gradient_checkpointing=True)
    options = dict(dataset_dir=str(dataset), output_dir=str(output), epochs=args.epochs,
                   batch_size=args.batch_size, grad_accum_steps=args.grad_accum_steps,
                   lr=1e-4, lr_encoder=1e-5, device='cuda', num_workers=2,
                   multi_scale=False, expanded_scales=False, scale_jitter=False,
                   # A mirrored printed number is not a plausible upright card scan.
                   aug_config={},
                   early_stopping=True, early_stopping_patience=15,
                   early_stopping_use_ema=True, checkpoint_interval=25,
                   tensorboard=False, wandb=False, run_test=True,
                   compute_val_loss=True, progress_bar='rich',
                   notes={'dataset_manifest': manifest, 'purpose': 'Locate collector-number text; OCR is separate'})
    if resume:
        options['resume'] = str(resume)
    (output / 'launch.json').write_text(json.dumps(options, indent=2) + '\n')
    (output / 'process.json').write_text(json.dumps({'pid': os.getpid()}) + '\n')
    print(f'Training RF-DETR Small on {torch.cuda.get_device_name(0)}', flush=True)
    reporter = subprocess.Popen([sys.executable, str(report_script), '--run', str(output),
                                 '--watch', '--parent-pid', str(os.getpid())])
    try:
        model.train(**options)
    finally:
        reporter.terminate()
        reporter.wait()
        if (output / 'metrics.csv').exists():
            subprocess.run([sys.executable, str(report_script), '--run', str(output)], check=False)
        (output / 'process.json').write_text(json.dumps({'pid': os.getpid(), 'running': False}) + '\n')
    (output / 'completed.json').write_text(json.dumps({'completed': True, 'epochs_requested': args.epochs}) + '\n')


if __name__ == '__main__':
    main()
