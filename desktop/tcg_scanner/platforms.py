"""Platform choices shared by installation, configuration and the launcher."""
from pathlib import Path
import platform
import shutil
import subprocess


def venv_python(directory, system=None):
    return Path(directory) / ('Scripts/python.exe' if (system or platform.system()) == 'Windows' else 'bin/python')


def runtime_plan(device='auto', system=None, machine=None):
    system = system or platform.system()
    machine = (machine or platform.machine()).lower()
    arch = {'amd64': 'x86_64', 'arm64': 'aarch64'}.get(machine, machine)
    supported = {'Linux': {'x86_64'}, 'Windows': {'x86_64'}, 'Darwin': {'aarch64'}}
    if arch not in supported.get(system, set()):
        raise ValueError(f'No supported dependency set for {system} {machine}. Use Linux x86_64, Windows x64, or macOS 14+ with Apple silicon.')
    if device == 'auto':
        device = 'cpu'
        if system in {'Linux', 'Windows'} and arch == 'x86_64' and shutil.which('nvidia-smi'):
            try:
                probe = subprocess.run(['nvidia-smi', '--query-gpu=name', '--format=csv,noheader'],
                                       capture_output=True, text=True, timeout=10)
                if probe.returncode == 0 and probe.stdout.strip():
                    device = 'cuda'
            except (OSError, subprocess.TimeoutExpired):
                pass
    if device not in {'cpu', 'cuda'}:
        raise ValueError('Device must be auto, cpu or cuda')
    if device == 'cuda' and (system not in {'Linux', 'Windows'} or arch != 'x86_64'):
        raise ValueError('This CUDA dependency set supports Linux x86_64 and Windows x64. Use --device cpu on this platform.')
    torch_index = None if system == 'Darwin' else (
        'https://download.pytorch.org/whl/' + ('cu130' if device == 'cuda' else 'cpu'))
    return dict(system=system, architecture=arch, device=device,
                faiss_device='cuda' if system == 'Linux' and device == 'cuda' else 'cpu',
                ocr_device='gpu:0' if device == 'cuda' else 'cpu', torch_index=torch_index,
                paddle_index='https://www.paddlepaddle.org.cn/packages/stable/cu126/' if device == 'cuda' else None)
