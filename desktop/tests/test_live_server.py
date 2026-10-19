import struct
import time
import threading
import unittest
from uuid import uuid4
import numpy as np
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from tcg_scanner.live_gate import ScanGate
from tcg_scanner.server import create_app, local_address


class GateTests(unittest.TestCase):
    def setUp(self):
        self.gate = ScanGate()
        self.a = np.arange(24*32*3).reshape(32, 24, 3) % 83 / 100
        self.b = .95 - np.arange(24*32*3).reshape(32, 24, 3) % 47 / 100
        self.corners = np.array([[.1,.1],[.8,.1],[.8,.9],[.1,.9]])

    def lock(self):
        self.assertFalse(self.gate.observe(self.a, self.corners, 0))
        self.assertFalse(self.gate.observe(self.a, self.corners, 125))
        self.assertTrue(self.gate.observe(self.a, self.corners, 250))
        self.assertTrue(self.gate.confirm("a", .9))
        self.gate.accept(self.a)

    def test_move_rotate_and_brief_occlusion_retain_identity(self):
        self.lock()
        for t in range(500, 5000, 250):
            self.assertFalse(self.gate.observe(self.a, self.corners + .1, t))
        self.assertFalse(self.gate.observe(self.a[::-1, ::-1], self.corners, 5250))
        self.assertFalse(self.gate.absent(5500, True))
        self.assertFalse(self.gate.absent(5750, True))
        self.assertFalse(self.gate.observe(self.a, self.corners, 5800))
        for t in range(6000, 9000, 250):
            self.assertFalse(self.gate.absent(t, False))
        self.assertFalse(self.gate.observe(self.a, self.corners, 9250))

    def test_removal_requires_consecutive_empty_frames_and_elapsed_time(self):
        self.lock()
        self.assertFalse(self.gate.absent(300, True))
        self.assertFalse(self.gate.absent(425, True))
        self.assertFalse(self.gate.absent(550, True))
        self.assertTrue(self.gate.absent(800, True))
        self.assertIsNone(self.gate.accepted)

    def test_replacement_has_no_previous_card_cooldown(self):
        self.lock()
        self.assertFalse(self.gate.observe(self.b, self.corners, 500))
        self.assertEqual(self.gate.interval_ms, 125)
        self.assertFalse(self.gate.observe(self.b, self.corners, 625))
        self.assertTrue(self.gate.observe(self.b, self.corners, 750))
        self.assertTrue(self.gate.confirm("b", .9))
        self.gate.accept(self.b)
        self.assertFalse(self.gate.observe(self.a, self.corners, 875))
        self.assertFalse(self.gate.observe(self.a, self.corners, 1000))
        self.assertTrue(self.gate.observe(self.a, self.corners, 1125))

    def test_network_gap_is_not_removal(self):
        self.lock()
        self.gate.absent(500, True)
        self.gate.absent(750, True)
        self.gate.interrupt()
        self.assertFalse(self.gate.absent(6000, True))
        self.assertFalse(self.gate.observe(self.a, self.corners, 6250))

    def test_low_score_needs_consistent_identity(self):
        self.assertFalse(self.gate.confirm("a", .7))
        self.assertFalse(self.gate.confirm("b", .7))
        self.assertTrue(self.gate.confirm("b", .7))
        self.gate.interrupt()
        self.assertFalse(self.gate.confirm("b", .7))
        self.assertFalse(self.gate.confirm("b", float("nan")))
        self.assertFalse(self.gate.confirm("b", .64))

    def test_rejection_only_throttles_same_failed_view(self):
        self.lock()
        self.gate.reject(self.a, 300)
        for t in (425, 550):
            self.assertFalse(self.gate.observe(self.b, self.corners, t))
        self.assertTrue(self.gate.observe(self.b, self.corners, 675))


class FakeProcessor:
    info = {"protocol": 1, "gpu": "test", "references": 2}
    stats = {}
    def close(self):
        pass
    def process(self, state, body, threshold):
        if body[0] == ord("X"):
            raise ValueError("Bad image")
        if body[0] == ord("0"):
            state.aliases.clear()
            state.recognition_id = None
            return {"event": "removed", "interval_ms": 125}
        name = chr(body[0])
        if name in state.aliases:
            return {"event": "observing", "interval_ms": 250}
        state.recognition_id = uuid4().hex
        state.aliases = {name}
        matches = [{"card": {"id": name}, "score": .9}]
        return {"event": "recognized", "recognition_id": state.recognition_id, "matches": matches, "checking": False}


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(advertise=False, processor_factory=FakeProcessor)
        self.client = TestClient(self.app, client=("127.0.0.1", 50000))
        self.client.__enter__()
        self.session = str(uuid4())
    def tearDown(self):
        self.client.__exit__(None, None, None)
    def hello(self, ws, **extra):
        ws.send_json({"protocol": 1, "session": self.session, **extra})
        return ws.receive_json()
    def frame(self, ws, sequence, name="A"):
        ws.send_bytes(struct.pack("!Q", sequence) + name.encode() * 40)
        while True:
            value = ws.receive_json()
            if value["type"] == "result":
                return value
    def test_health_and_private_network_boundary(self):
        self.assertEqual(self.client.get("/health").status_code, 200)
        self.assertEqual(self.client.get("/health", headers={"origin": "https://example.com"}).status_code, 403)
        for ip in ("10.0.2.2", "192.168.1.10", "172.16.3.1", "127.0.0.1"):
            self.assertTrue(local_address(ip))
        for ip in ("0.0.0.0", "8.8.8.8", "169.254.1.1", "100.64.0.1", "example.com"):
            self.assertFalse(local_address(ip))
    def test_recognize_once_resume_and_rearm(self):
        with self.client.websocket_connect("/scan") as ws:
            self.assertEqual(self.hello(ws)["type"], "ready")
            first = self.frame(ws, 1)
            self.assertEqual(first["event"], "recognized")
            self.assertEqual(self.frame(ws, 2)["event"], "observing")
        with self.client.websocket_connect("/scan") as ws:
            self.assertEqual(self.hello(ws)["result"]["recognition_id"], first["recognition_id"])
            self.assertEqual(self.frame(ws, 1)["event"], "observing")
            self.assertEqual(self.frame(ws, 2, "0")["event"], "removed")
            second = self.frame(ws, 3)
            self.assertNotEqual(second["recognition_id"], first["recognition_id"])
    def test_resume_alias_survives_server_session_loss(self):
        with self.client.websocket_connect("/scan") as ws:
            self.hello(ws, resume_aliases=["A"])
            self.assertEqual(self.frame(ws, 1)["event"], "observing")
            self.assertEqual(self.frame(ws, 2, "B")["event"], "recognized")
    def test_replacement_result_is_complete_and_resumes_latest_card(self):
        with self.client.websocket_connect("/scan") as ws:
            self.hello(ws)
            self.frame(ws, 1)
            second = self.frame(ws, 2, "B")
            self.assertFalse(second["checking"])
            self.assertEqual(second["matches"][0]["card"]["id"], "B")
        with self.client.websocket_connect("/scan") as ws:
            self.assertEqual(self.hello(ws)["result"]["recognition_id"], second["recognition_id"])

    def test_bad_frame_reports_error_and_stream_continues(self):
        with self.client.websocket_connect("/scan") as ws:
            self.hello(ws)
            self.assertEqual(self.frame(ws, 1, "X")["event"], "error")
            self.assertEqual(self.frame(ws, 2)["event"], "recognized")
    def test_latest_frame_replaces_queued_frames(self):
        processor = self.app.state.runtime.processor
        original = processor.process
        started, release = threading.Event(), threading.Event()
        seen = []
        def blocked(state, body, threshold):
            seen.append(chr(body[0]))
            if body[0] == ord("A"):
                started.set()
                assert release.wait(2)
            return original(state, body, threshold)
        processor.process = blocked
        with self.client.websocket_connect("/scan") as ws:
            self.hello(ws)
            ws.send_bytes(struct.pack("!Q", 1) + b"A" * 40)
            self.assertTrue(started.wait(2))
            ws.send_bytes(struct.pack("!Q", 2) + b"B" * 40)
            ws.send_bytes(struct.pack("!Q", 3) + b"C" * 40)
            time.sleep(.05)
            release.set()
            sequences = []
            while len(sequences) < 2:
                response = ws.receive_json()
                if response["type"] == "result":
                    sequences.append(response["sequence"])
            self.assertEqual(sequences, [1, 3])
            self.assertEqual(seen, ["A", "C"])

    def test_disconnect_during_inference_keeps_receipt(self):
        processor = self.app.state.runtime.processor
        original = processor.process
        started, release = threading.Event(), threading.Event()
        def blocked(state, body, threshold):
            started.set()
            assert release.wait(2)
            return original(state, body, threshold)
        processor.process = blocked
        with self.client.websocket_connect("/scan") as ws:
            self.hello(ws)
            ws.send_bytes(struct.pack("!Q", 1) + b"A" * 40)
            self.assertTrue(started.wait(2))
        release.set()
        with self.client.websocket_connect("/scan") as ws:
            ready = self.hello(ws)
            self.assertEqual(ready["result"]["event"], "recognized")
            self.assertEqual(self.frame(ws, 1)["event"], "observing")

    def test_manual_alias_is_resumed_only_for_matching_receipt(self):
        with self.client.websocket_connect("/scan") as ws:
            self.hello(ws)
            first = self.frame(ws, 1)
        with self.client.websocket_connect("/scan") as ws:
            self.hello(ws, resume_id=first["recognition_id"], resume_aliases=["A", "B"])
            self.assertEqual(self.frame(ws, 1, "B")["event"], "observing")

    def test_bad_protocol_and_browser_origin_rejected(self):
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect("/scan", headers={"origin": "https://example.com"}) as ws:
                ws.receive_json()
        with self.client.websocket_connect("/scan") as ws:
            ws.send_json({"protocol": 2, "session": self.session})
            with self.assertRaises(WebSocketDisconnect):
                ws.receive_json()


if __name__ == "__main__":
    unittest.main()
