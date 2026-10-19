"""Local YOLO card-corner detection and in-memory perspective correction."""
from contextlib import redirect_stdout
import hashlib
from io import BytesIO
import os
from pathlib import Path
import sys

import numpy as np
from PIL import Image, ImageOps

MODEL_REPO = "duclvQ/tcg-card-detector"
MODEL_REVISION = "419633c3986296bf2e3df93ce94d28b1a3fb965d"
MODEL_FILE = "card_pose_yolov8n.pt"
CROP_PIPELINE = "yolov8-card-pose-warp-v1"
CROP_SIZE = (630, 880)


def validate_corners(corners, width, height):
    """Preserve trained TL/TR/BR/BL identities; sorting would lose card orientation."""
    quad = np.asarray(corners, dtype=np.float32)
    if quad.shape != (4, 2) or not np.isfinite(quad).all():
        raise ValueError("Card corners are invalid; retake with all four corners visible")
    if (quad < 0).any() or (quad[:, 0] > width - 1).any() or (quad[:, 1] > height - 1).any():
        raise ValueError("Card corners leave the photo; include the entire card")
    edges = np.roll(quad, -1, axis=0) - quad
    following = np.roll(edges, -1, axis=0)
    crosses = edges[:, 0] * following[:, 1] - edges[:, 1] * following[:, 0]
    area = 0.5 * np.sum(quad[:, 0] * np.roll(quad[:, 1], -1)
                        - quad[:, 1] * np.roll(quad[:, 0], -1))
    if (crosses <= 0).any() or (np.linalg.norm(edges, axis=1) < 12).any() or area < 256:
        raise ValueError("Card corners are unreliable; retake a clearer, closer photo")
    return quad


def rectify_card(rgb, corners, size=CROP_SIZE):
    import cv2
    height, width = rgb.shape[:2]
    quad = validate_corners(corners, width, height)
    out_w, out_h = size
    mx, my = out_w * 8 / 630, out_h * 8 / 880
    target = np.float32([[mx, my], [out_w - 1 - mx, my],
                         [out_w - 1 - mx, out_h - 1 - my], [mx, out_h - 1 - my]])
    matrix = cv2.getPerspectiveTransform(quad, target)
    return cv2.warpPerspective(rgb, matrix, size, flags=cv2.INTER_LINEAR,
                                    borderMode=cv2.BORDER_CONSTANT, borderValue=(124, 116, 104))


def warp_card(rgb, corners):
    with Image.fromarray(rectify_card(rgb, corners)) as image, BytesIO() as output:
        image.save(output, format="PNG")
        return output.getvalue()


class CardCropper:
    def __init__(self, config, device):
        os.environ["YOLO_AUTOINSTALL"] = "false"
        os.environ["YOLO_OFFLINE"] = "true"
        cache = config.root / "models" / "ultralytics"
        cache.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("YOLO_CONFIG_DIR", str(cache))
        with redirect_stdout(sys.stderr):
            from ultralytics import YOLO, settings
            from huggingface_hub import hf_hub_download
            settings.update({"sync": False})
            weights = hf_hub_download(MODEL_REPO, MODEL_FILE, revision=MODEL_REVISION)
            self.model = YOLO(weights, task="pose").to(device)
        if self.model.task != "pose" or self.model.model.kpt_shape[0] != 4:
            raise ValueError("Card detector must predict four ordered card corners")
        self.device = device
        self.confidence = config.detector_confidence
        self.spec = {"model": MODEL_REPO, "revision": MODEL_REVISION,
                     "weights_sha256": hashlib.sha256(Path(weights).read_bytes()).hexdigest(),
                     "pipeline": CROP_PIPELINE, "device": device,
                     "image_size": 480, "confidence_threshold": self.confidence}

    def crop(self, body):
        with Image.open(BytesIO(body)) as image:
            oriented = ImageOps.exif_transpose(image)
            try:
                with oriented.convert("RGB") as rgb_image:
                    rgb = np.array(rgb_image)
            finally:
                oriented.close()
        result = self._predict(rgb, self.confidence)
        count = len(result.boxes) if result.boxes is not None else 0
        if count == 0:
            raise ValueError("No card detected. Show the whole card and all four corners, "
                             "or use --crop none for an already-cropped image")
        if count != 1:
            raise ValueError(f"Detected {count} cards. Use one card per photo or crop the intended card manually")
        corners, confidence, corner_confidence = self._corners(result, rgb)
        cropped = warp_card(rgb, corners)
        return cropped, {"mode": "yolo", **self.spec, "detected_cards": count,
                         "detection_confidence": confidence, "corners": corners.tolist(),
                         "corner_confidence": None if corner_confidence is None else corner_confidence.tolist(),
                         "source_size": [rgb.shape[1], rgb.shape[0]],
                         "output_size": list(CROP_SIZE), "margin_pixels": 8,
                         "crop_sha256": hashlib.sha256(cropped).hexdigest()}

    def _predict(self, rgb, confidence):
        # Ultralytics NumPy inputs are BGR; warp/output remain RGB.
        try:
            with redirect_stdout(sys.stderr):
                result = self.model.predict(source=np.ascontiguousarray(rgb[:, :, ::-1]),
                                            imgsz=480, conf=confidence, iou=0.5,
                                            device=self.device, max_det=10, verbose=False,
                                            save=False, save_txt=False, save_crop=False, show=False)[0]
        finally:
            # Ultralytics caches the last source/result on its persistent predictor.
            # Keep model allocations, but release pixel buffers after every request.
            predictor = self.model.predictor
            if predictor is not None:
                predictor.batch = predictor.results = predictor.dataset = predictor.plotted_img = None
        return result

    @staticmethod
    def _corners(result, rgb, index=0):
        if result.keypoints is None:
            raise ValueError("Detector returned no card corners; retake the photo")
        confidence = float(result.boxes.conf[index].cpu())
        points = result.keypoints.xy[index].cpu().numpy()
        point_scores = result.keypoints.conf
        corner_confidence = None if point_scores is None else point_scores[index].cpu().numpy()
        if corner_confidence is not None and (not np.isfinite(corner_confidence).all()
                                             or (corner_confidence < 0.5).any()):
            raise ValueError("Card corners are obscured or uncertain; show all four corners")
        corners = validate_corners(points, rgb.shape[1], rgb.shape[0])
        return corners, confidence, corner_confidence

    def detect_live(self, rgb, confidence=.5):
        """Cheap observation, with weak/obscured cards distinct from clear removal."""
        result = self._predict(rgb, .25)
        if result.boxes is None or len(result.boxes) == 0:
            return None, True
        scores = result.boxes.conf.cpu().numpy()
        reliable = np.flatnonzero(scores >= .5)
        if len(reliable) != 1 or scores[reliable[0]] < confidence:
            return None, False
        try:
            corners, _, _ = self._corners(result, rgb, int(reliable[0]))
            return corners, False
        except ValueError:
            return None, False
