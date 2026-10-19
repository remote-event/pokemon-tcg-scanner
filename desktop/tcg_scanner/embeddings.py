"""One versioned preprocessing/inference path for references and scanned cards."""
import hashlib
from io import BytesIO
import json
import math

import numpy as np
from PIL import Image, ImageOps, __version__ as pillow_version

PIPELINE_VERSION = "whole-card-pad-cls-l2-v1"


def whole_card(image, processor):
    """Pad enough that the official resize + center crop cannot cut card edges."""
    image = ImageOps.exif_transpose(image)
    rgba = image.convert("RGBA")
    color = tuple(round(v * 255) for v in processor.image_mean)
    rgb = Image.new("RGB", rgba.size, color)
    rgb.paste(rgba, mask=rgba.getchannel("A"))
    rgba.close()
    size = processor.size["shortest_edge"]
    crop = processor.crop_size
    # Two extra source pixels guard resize rounding at the crop boundary.
    side = math.ceil(max(rgb.size) * size / min(crop["height"], crop["width"])) + 2
    canvas = Image.new("RGB", (side, side), color)
    canvas.paste(rgb, ((side - rgb.width) // 2, (side - rgb.height) // 2))
    rgb.close()
    return canvas


class DinoEmbedder:
    def __init__(self, config):
        import torch
        import transformers
        from transformers import AutoConfig, AutoImageProcessor, AutoModel
        self.torch = torch
        torch.set_num_threads(config.cpu_threads)
        self.device = config.device
        if self.device == "auto":
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        if self.device == "cuda" and not torch.cuda.is_available():
            raise ValueError("CUDA is unavailable; use --device cpu or auto")
        model_config = AutoConfig.from_pretrained(config.model_name, revision=config.model_revision)
        if model_config.model_type != "dinov2":
            raise ValueError("Select a pretrained DINOv2 backbone, e.g. facebook/dinov2-small")
        revision = model_config._commit_hash
        if not revision:
            raise ValueError("Model must have an immutable Hugging Face commit revision")
        self.processor = AutoImageProcessor.from_pretrained(
            config.model_name, revision=revision, use_fast=False)
        self.model = AutoModel.from_pretrained(config.model_name, revision=revision,
                                              use_safetensors=True).to(self.device).eval()
        if ("shortest_edge" not in self.processor.size or not self.processor.do_center_crop
                or not self.processor.do_resize):
            raise ValueError("Unsupported preprocessing configuration for whole-card padding")
        self.dimension = self.model.config.hidden_size
        self.spec = {"model_name": config.model_name, "revision": revision,
                     "processor": self.processor.to_dict(), "pipeline": PIPELINE_VERSION,
                     "transformers": transformers.__version__, "pillow": pillow_version,
                     "dimension": self.dimension}
        self.key = hashlib.sha256(json.dumps(self.spec, sort_keys=True).encode()).hexdigest()

    def encode(self, bodies):
        images = []
        try:
            for body in bodies:
                with Image.open(BytesIO(body)) as im:
                    images.append(whole_card(im, self.processor))
            inputs = self.processor(images=images, return_tensors="pt").to(self.device)
            with self.torch.inference_mode():
                vectors = self.model(**inputs).last_hidden_state[:, 0, :].float()
                vectors = self.torch.nn.functional.normalize(vectors, p=2, dim=1)
            result = vectors.cpu().numpy().astype("float32", copy=True)
            if not np.isfinite(result).all():
                raise ValueError("Model produced non-finite embeddings")
            return result
        finally:
            for im in images:
                im.close()
