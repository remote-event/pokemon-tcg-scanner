import hashlib
from io import BytesIO
import os
from pathlib import Path
import re
import tempfile

from PIL import Image


def safe_id(value):
    # Reject unexpected identifiers rather than map two IDs to the same filename.
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value):
        raise ValueError(f"Unsafe TCGdex identifier: {value!r}")
    return value


def inspect_image(data):
    with Image.open(BytesIO(data)) as im:
        if im.format not in {"PNG", "JPEG", "WEBP"}:
            raise ValueError("Unexpected image format")
        im.verify()
    with Image.open(BytesIO(data)) as im:
        im.load()  # verify() alone does not decode pixel data for every format.
        width, height = im.size
        if width < 1 or height < 1:
            raise ValueError("Empty image")
    return {"width": width, "height": height, "byte_size": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}


def image_path(config, set_id, card_id):
    return Path("images") / config.language / safe_id(set_id) / (
        safe_id(card_id) + "." + config.image_format)


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Unique temp names allow safe recovery after a killed process.
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".part", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
