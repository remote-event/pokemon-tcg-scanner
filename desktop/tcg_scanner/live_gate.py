"""Desktop equivalent of Android ScanGate; only tiny signatures are retained."""
import numpy as np


def distance(a, b, rotated=False):
    if a is None or b is None or a.shape != b.shape:
        return float("inf")
    direct = float(np.abs(a - b).mean())
    return min(direct, float(np.abs(a - b[::-1, ::-1]).mean())) if rotated else direct


class ScanGate:
    def __init__(self):
        self.reset()

    def reset(self):
        self.accepted = self.stable = self.corners = self.failed = None
        self.pending_identity = self.empty_since = None
        self.count = self.empty_count = 0
        self.failed_at = -10000
        self.state = "searching"

    @property
    def interval_ms(self):
        return 250 if self.state in ("searching", "locked") and self.empty_since is None else 125

    def interrupt(self):
        self.stable = self.corners = self.pending_identity = self.empty_since = None
        self.count = self.empty_count = 0
        self.state = "locked" if self.accepted is not None else "searching"

    def absent(self, now, clearly_empty):
        since, count = self.empty_since, self.empty_count
        self.interrupt()
        if not clearly_empty:
            return False
        self.empty_since = now if since is None else since
        self.empty_count = count + 1
        if self.empty_count >= 3 and now - self.empty_since >= 500:
            self.reset()
            return True
        return False

    def observe(self, signature, corners, now):
        self.empty_since = None
        self.empty_count = 0
        if distance(self.accepted, signature, True) < .045:
            self.stable = self.pending_identity = None
            self.count = 0
            self.state = "locked"
            return False
        consistent = distance(self.stable, signature) < .035 and distance(self.corners, corners) < .025
        if not consistent:
            self.pending_identity = None
        self.count = self.count + 1 if consistent else 1
        self.stable, self.corners = signature.copy(), corners.copy()
        self.state = "stabilizing"
        if self.count < 3:
            return False
        if distance(self.failed, signature, True) < .035 and now - self.failed_at < 1200:
            return False
        self.state = "identifying"
        return True

    def confirm(self, card_id, score, known=False):
        if not np.isfinite(score) or score < .65:
            return False
        if known or score >= .8 or self.pending_identity == card_id:
            return True
        self.pending_identity = card_id
        self.state = "stabilizing"
        return False

    def reject(self, signature, now):
        self.failed, self.failed_at, self.pending_identity = signature.copy(), now, None
        self.state = "locked" if self.accepted is not None else "searching"

    def accept(self, signature):
        self.accepted = signature.copy()
        self.stable = self.failed = self.pending_identity = None
        self.count = 0
        self.state = "locked"
