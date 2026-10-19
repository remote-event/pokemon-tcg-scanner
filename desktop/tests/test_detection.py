"""Crop geometry and detector failure handling without downloading model weights."""
from io import BytesIO
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import numpy as np
from PIL import Image
import torch

from tcg_scanner.config import Config
from tcg_scanner.detection import CardCropper, CROP_SIZE, validate_corners, warp_card


def encoded(image, **kwargs):
    with BytesIO() as output:
        image.save(output, **kwargs)
        return output.getvalue()


class Boxes:
    def __init__(self, count):
        self.conf = torch.full((count,), 0.9)
    def __len__(self):
        return len(self.conf)


class DetectionTests(unittest.TestCase):
    def setUp(self):
        self.rgb = np.zeros((200, 140, 3), dtype=np.uint8)
        self.rgb[15:45, 15:45] = [255, 0, 0]
        self.rgb[15:45, 95:125] = [0, 255, 0]
        self.rgb[155:185, 95:125] = [0, 0, 255]
        self.rgb[155:185, 15:45] = [255, 255, 0]
        self.corners = np.float32([[20, 20], [120, 20], [120, 180], [20, 180]])
        with Image.fromarray(self.rgb) as im:
            self.body = encoded(im, format="PNG")

    def cropper(self, count=1, corner_confidence=None):
        result = SimpleNamespace(boxes=Boxes(count), keypoints=SimpleNamespace(
            xy=torch.tensor(self.corners[None]), conf=corner_confidence))
        cropper = object.__new__(CardCropper)
        cropper.model = Mock()
        cropper.model.predict.return_value = [result]
        cropper.device, cropper.confidence, cropper.spec = "cpu", 0.5, {"device": "cpu"}
        return cropper

    def test_warp_retains_rgb_corners_and_semantic_orientation(self):
        original = self.rgb.copy()
        with Image.open(BytesIO(warp_card(self.rgb, self.corners))) as im:
            self.assertEqual(im.size, CROP_SIZE)
            for point, color in [((20, 20), (255, 0, 0)), ((610, 20), (0, 255, 0)),
                                  ((610, 860), (0, 0, 255)), ((20, 860), (255, 255, 0))]:
                self.assertEqual(im.getpixel(point), color)
        # Rotate source 180 degrees; semantic corners restore its original upright view.
        rotated = self.rgb[::-1, ::-1].copy()
        rotated_quad = np.array([139, 199], dtype=np.float32) - self.corners
        self.assertEqual(warp_card(rotated, rotated_quad), warp_card(self.rgb, self.corners))
        np.testing.assert_array_equal(self.rgb, original)

    def test_invalid_geometry_rejected(self):
        for corners in [self.corners[[0, 2, 1, 3]], self.corners[[0, 3, 2, 1]],
                        np.zeros((4, 2)), np.full((4, 2), np.nan), self.corners + 300,
                        self.corners[:3], self.corners / 20]:
            with self.subTest(corners=corners), self.assertRaises(ValueError):
                validate_corners(corners, 140, 200)

    def test_predict_uses_bgr_and_never_saves_pixels(self):
        cropper = self.cropper()
        cropped, info = cropper.crop(self.body)
        self.assertEqual(cropped, warp_card(self.rgb, self.corners))
        self.assertEqual(info["mode"], "yolo")
        self.assertEqual(info["source_size"], [140, 200])
        args = cropper.model.predict.call_args.kwargs
        np.testing.assert_array_equal(args["source"], self.rgb[:, :, ::-1])
        self.assertFalse(args["save"] or args["save_txt"] or args["save_crop"] or args["show"])
        self.assertEqual(args["device"], "cpu")
        for field in ("batch", "results", "dataset", "plotted_img"):
            self.assertIsNone(getattr(cropper.model.predictor, field))

    def test_zero_multiple_and_uncertain_detections_do_not_fall_back(self):
        for count, message in [(0, "No card detected"), (2, "Detected 2 cards")]:
            cropper = self.cropper(count)
            with self.assertRaisesRegex(ValueError, message):
                cropper.crop(self.body)
            self.assertIsNone(cropper.model.predictor.results)
        cropper = self.cropper(corner_confidence=torch.tensor([[1, 1, 0.1, 1]]))
        with self.assertRaisesRegex(ValueError, "obscured or uncertain"):
            cropper.crop(self.body)

    def test_prediction_error_releases_pixels(self):
        cropper = self.cropper()
        cropper.model.predict.side_effect = RuntimeError("GPU error")
        with self.assertRaisesRegex(RuntimeError, "GPU error"):
            cropper.crop(self.body)
        self.assertIsNone(cropper.model.predictor.dataset)

    def test_exif_rotation_before_prediction(self):
        cropper = self.cropper()
        with Image.new("RGB", (200, 140), "white") as image:
            exif = Image.Exif()
            exif[274] = 6
            body = encoded(image, format="JPEG", exif=exif)
        _, info = cropper.crop(body)
        self.assertEqual(info["source_size"], [140, 200])
        self.assertEqual(cropper.model.predict.call_args.kwargs["source"].shape, (200, 140, 3))

    def test_config_rejects_invalid_crop_settings(self):
        for value in (0, -1, 2, float("nan")):
            with self.assertRaises(ValueError):
                Config(detector_confidence=value)
        with self.assertRaises(ValueError):
            Config(crop="invalid")
