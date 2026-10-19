"""Small, deterministic quality checks; original pixels remain the scan record."""
from dataclasses import dataclass

import cv2
import numpy as np

_GAMMA = np.round((np.arange(256) / 255.) ** .65 * 255).astype(np.uint8)


def brighten(rgb):
    return cv2.LUT(rgb, _GAMMA)


def thumbnail(rgb):
    return cv2.resize(rgb, (192, 256), interpolation=cv2.INTER_AREA)


@dataclass(frozen=True)
class Quality:
    light: float
    sharpness: float
    glare: float

    @property
    def dark(self):
        return self.light < 85

    @property
    def usable(self):
        return self.light >= 18 and self.sharpness >= 25

    @property
    def score(self):
        # Bounded sharpness prevents noise winning over an unobstructed image.
        return min(self.sharpness / 250, 1.) + min(self.light / 140, 1.) - 4 * self.glare

    @property
    def hint(self):
        if self.light < 40:
            return "Add a little light"
        if self.glare > .08:
            return "Tilt the card slightly to reduce glare"
        if self.sharpness < 25:
            return "Hold steady or tap the card to focus"
        return ""


def assess(rgb):
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    p10, p90 = np.percentile(gray, (10, 90))
    # Measure edges relative to contrast, without unlimited amplification in darkness.
    normalized = gray.astype(np.float32) * (128 / max(float(p90 - p10), 32))
    sharpness = float(cv2.Laplacian(normalized, cv2.CV_32F).var())
    # Only a *suspected* highlight metric: printed white areas are possible too.
    # Use this for frame ranking/hints, never as proof that a card is invalid.
    clipped = (rgb.min(axis=2) >= 245).astype(np.uint8)
    count, _, stats, _ = cv2.connectedComponentsWithStats(clipped)
    largest = max(stats[1:, cv2.CC_STAT_AREA], default=0) if count > 1 else 0
    return Quality(float(p90), sharpness, float(largest / clipped.size))


def signature(rgb):
    small = cv2.resize(rgb, (24, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    light = float(np.percentile(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), 90))
    # Retain color/structure while tolerating global exposure changes.
    return np.clip(small * (.75 / max(light, 32)), 0, 1)


def uncertain_darkness(rgb, previous_context=None):
    """Do not infer removal from black frames or a sudden exposure drop.

    A normally exposed dark table can still establish removal when the background
    outside the previous card remains visible and similarly lit.
    """
    gray = cv2.cvtColor(thumbnail(rgb), cv2.COLOR_RGB2GRAY)
    if previous_context is not None:
        mask, before = previous_context
        outside = gray[mask]
        if outside.size:
            after = float(np.percentile(outside, 90))
            if before >= 8 and after < before * .5:
                return True
            if before >= 8 and .65 * before <= after <= 1.6 * before:
                return False
    return float(np.percentile(gray, 95)) < 45


def context(rgb, corners):
    small = thumbnail(rgb)
    mask = np.ones(small.shape[:2], np.uint8)
    points = corners * np.array([small.shape[1] / rgb.shape[1], small.shape[0] / rgb.shape[0]])
    cv2.fillConvexPoly(mask, np.round(points).astype(np.int32), 0)
    mask = cv2.erode(mask, np.ones((7, 7), np.uint8)).astype(bool)
    outside = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)[mask]
    return mask, float(np.percentile(outside, 90)) if outside.size else 0.
