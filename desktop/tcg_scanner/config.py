from dataclasses import dataclass, fields
from datetime import date
import math
import os
from pathlib import Path
import re
import tomllib
from urllib.parse import urlsplit


def default_config_path():
    """Keep per-install configuration out of source control."""
    if value := os.getenv("TCG_CONFIG"):
        return Path(value).expanduser().resolve()
    directory = Path(__file__).resolve().parents[1]
    local = directory / "config.local.toml"
    return local if local.is_file() else directory / "config.toml"


@dataclass(frozen=True)
class Config:
    data_dir: str = str(Path(__file__).resolve().parents[2] / "data")
    language: str = "en"
    api_base: str = "https://api.tcgdex.net/v2"
    concurrency: int = 4
    requests_per_second: float = 4.0
    timeout: float = 30.0
    retries: int = 4
    backoff: float = 1.0
    metadata_ttl_hours: float = 168.0
    catalog_ttl_hours: float = 24.0
    image_format: str = "png"
    since: str = "2023-03-31"
    model_name: str = "facebook/dinov2-small"
    model_revision: str = "main"
    batch_size: int = 8
    device: str = "auto"
    cpu_threads: int = 4
    faiss_device: str = "cpu"
    crop: str = "none"
    detector_confidence: float = 0.5
    ocr: str = "none"
    ocr_python: str = str(Path(__file__).resolve().parents[2] / ".venvs/paddle/bin/python")
    ocr_device: str = "gpu:0"
    ocr_model: str = "en_PP-OCRv5_mobile_rec"
    ocr_confidence: float = 0.85
    collector_python: str = str(Path(__file__).resolve().parents[2] / ".venvs/collector/bin/python")
    collector_weights: str = "models/collector-number/rfdetr-small.pth"
    collector_confidence: float = 0.5
    collector_padding: float = 0.12
    inference_mode: str = "concurrent"
    visual_confidence: float = 0.80
    visual_margin: float = 0.015
    ocr_visual_tolerance: float = 0.03

    def __post_init__(self):
        if not re.fullmatch(r"[a-z]{2}(?:-[a-z]{2})?", self.language):
            raise ValueError("language must be a TCGdex language code, e.g. en or zh-tw")
        if self.image_format not in {"png", "webp", "jpg"}:
            raise ValueError("image_format must be png, webp, or jpg")
        if self.concurrency < 1 or self.retries < 0:
            raise ValueError("concurrency must be positive and retries nonnegative")
        for key in ("requests_per_second", "timeout", "backoff",
                    "metadata_ttl_hours", "catalog_ttl_hours"):
            value = getattr(self, key)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{key} must be finite and nonnegative")
        if not self.requests_per_second or not self.timeout:
            raise ValueError("requests_per_second and timeout must be positive")
        if self.batch_size < 1 or self.cpu_threads < 1:
            raise ValueError("batch_size and cpu_threads must be positive")
        if self.device not in {"auto", "cpu", "cuda"}:
            raise ValueError("device must be auto, cpu, or cuda")
        if self.faiss_device not in {"auto", "cpu", "cuda"}:
            raise ValueError("faiss_device must be auto, cpu, or cuda")
        if self.ocr not in {"none", "paddle"}:
            raise ValueError("ocr must be none or paddle")
        if self.ocr_device not in {"cpu", "gpu:0"}:
            raise ValueError("ocr_device must be cpu or gpu:0")
        for key in ('ocr_confidence', 'collector_confidence', 'visual_confidence', 'visual_margin', 'ocr_visual_tolerance', 'collector_padding'):
            if not math.isfinite(getattr(self, key)) or not 0 <= getattr(self, key) <= 1:
                raise ValueError(f'{key} must be in [0, 1]')
        if self.inference_mode not in {'concurrent', 'serial'}:
            raise ValueError('inference_mode must be concurrent or serial')
        if not self.ocr_python.strip():
            raise ValueError("ocr_python is required")
        if self.crop not in {"none", "yolo"}:
            raise ValueError("crop must be none or yolo")
        if not math.isfinite(self.detector_confidence) or not 0 < self.detector_confidence <= 1:
            raise ValueError("detector_confidence must be in (0, 1]")
        if not self.model_name or not self.model_revision:
            raise ValueError("model_name and model_revision are required")
        date.fromisoformat(self.since)
        if urlsplit(self.api_base).scheme != "https":
            raise ValueError("api_base must use HTTPS")

    @property
    def root(self):
        return Path(self.data_dir).expanduser().resolve()

    @classmethod
    def load(cls, path: Path | None, overrides: dict):
        values = {}
        if path is not None:
            with path.open("rb") as f:
                values = tomllib.load(f)
        known = {f.name for f in fields(cls)}
        if unknown := values.keys() - known:
            raise ValueError(f"Unknown configuration keys: {sorted(unknown)}")
        for f in fields(cls):
            if (value := os.getenv("TCG_" + f.name.upper())) is not None:
                values[f.name] = f.type(value)
        values.update({k: v for k, v in overrides.items() if k in known and v is not None})
        # Validate TOML types too; avoid silent truncation of nonintegral worker counts.
        for f in fields(cls):
            if f.name in values and (isinstance(values[f.name], bool) or
                    not isinstance(values[f.name], (int, float) if f.type is float else f.type)):
                raise ValueError(f"Invalid type for {f.name}")
        # Interpreter and data paths are anchored to their config, not the launch directory.
        # Collector weights remain relative to data_dir (see CollectorReader).
        for key in ('data_dir', 'ocr_python', 'collector_python'):
            if path is not None and key in values and overrides.get(key) is None and 'TCG_'+key.upper() not in os.environ:
                anchored = path.parent / Path(values[key]).expanduser()
                # Resolving a venv's bin/python symlink selects the system interpreter
                # and loses its site-packages. Normalize the path without dereferencing it.
                values[key] = str(anchored.resolve()) if key == 'data_dir' else os.path.abspath(anchored)
        return cls(**values)
