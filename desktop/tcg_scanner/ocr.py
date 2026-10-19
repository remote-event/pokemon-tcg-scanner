"""Optional printing verification; reference embeddings remain unchanged."""
import atexit
import base64
from io import BytesIO
import json
import os
from pathlib import Path
import select
import subprocess
import threading
from time import monotonic


class PaddleWorker:
    """One isolated interpreter per scan session; no image files or shell commands."""
    def __init__(self, config, command=None):
        self.config = config
        self.process = None
        self.info = {}
        self.lock = threading.RLock()
        self.command = command or [str(Path(config.ocr_python).expanduser()), '-I', '-B', '-u',
                                  str(Path(__file__).with_name('ocr_worker.py')), config.ocr_device, config.ocr_model]
        atexit.register(self.close)

    def _receive(self, timeout):
        deadline = monotonic() + timeout
        data = bytearray()
        fd = self.process.stdout.fileno()
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
                raise RuntimeError("PaddleOCR timed out; retaining embedding candidates")
            chunk = os.read(fd, 65536)
            if not chunk:
                raise RuntimeError("PaddleOCR worker exited unexpectedly")
            data.extend(chunk)
            if len(data) > 2_000_000:
                raise RuntimeError("PaddleOCR returned an oversized response")
            if data.endswith(b"\n"):
                result = json.loads(data)
                if "error" in result:
                    raise RuntimeError(result["error"])
                return result

    def start(self):
        with self.lock:
            if self.process is None:
                environment = os.environ.copy()
                if Path('/usr/lib/wsl/lib/libcuda.so').exists():
                    environment['LD_LIBRARY_PATH'] = '/usr/lib/wsl/lib:' + environment.get('LD_LIBRARY_PATH', '')
                self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0, env=environment)
                try:
                    self.info = self._receive(180)
                    if not self.info.get('ready'):
                        raise RuntimeError('Invalid inference worker startup response')
                except BaseException:
                    self.stop()
                    raise
            return self.info

    def recognize(self, image):
        with self.lock:
            return self._recognize(image)

    def _recognize(self, image):
        try:
            self.start()
            payload = json.dumps({"image": base64.b64encode(image).decode("ascii")}).encode() + b"\n"
            # Raw pipe writes may be partial; keep a bounded request, with no queued photos.
            view = memoryview(payload)
            fd = self.process.stdin.fileno()
            os.set_blocking(fd, False)
            deadline = monotonic() + 2
            while view:
                remaining = deadline - monotonic()
                if remaining <= 0 or not select.select([], [fd], [], remaining)[1]:
                    raise RuntimeError("PaddleOCR input timed out")
                try:
                    written = os.write(fd, view[:65536])
                    view = view[written:]
                except BlockingIOError:
                    continue
            return {**self.info, **self._receive(3)}
        except BaseException:
            self.stop()
            raise

    def stop(self):
        process, self.process = self.process, None
        if process is not None:
            # EOF lets Paddle release CUDA normally instead of logging SIGTERM as fatal.
            process.stdin.close()
            try:
                process.wait(timeout=.5)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            process.stdout.close()

    def close(self):
        self.stop()
        atexit.unregister(self.close)


class PrintingVerifier:
    def __init__(self, config, db, metadata):
        from .collector import CollectorReader, CatalogMatcher
        self.reader = CollectorReader(config)
        cards = {r['card_id']: dict(r) for r in db.conn.execute('''SELECT c.card_id,c.name,
            c.collector_number,c.set_id,s.name AS set_name FROM cards c LEFT JOIN sets s
            ON s.language=c.language AND s.set_id=c.set_id WHERE c.language=?''', (config.language,))}
        cards.update({r['card_id']: r for r in metadata.values()})
        sets = {r[0]: json.loads(r[1]) for r in db.conn.execute(
            "SELECT set_id,raw_json FROM sets WHERE language=?", (config.language,))}
        self.matcher = CatalogMatcher(cards, sets, config)
        try:
            self.reader.start()
        except Exception:
            self.reader.close()  # Reader reports unavailable; visual results remain usable.

    def check(self, body, crop_info, candidates):
        try:
            from PIL import Image, ImageOps
            import numpy as np
            from .detection import rectify_card
            with Image.open(BytesIO(body)) as source:
                image = ImageOps.exif_transpose(source).convert('RGB')
                if crop_info.get('mode') == 'yolo':
                    image = Image.fromarray(rectify_card(np.asarray(image), np.asarray(crop_info['corners'], dtype=np.float32)))
                with BytesIO() as output:
                    image.save(output, format='PNG')
                    recognized = self.reader.read(output.getvalue())
            combined = self.matcher.combine(candidates, recognized)
            # Keep the command-line report's established verified status. Live scans
            # additionally score catalog matches outside FAISS top-k before selection.
            if combined['status'] == 'ocr_supported':
                combined['status'] = 'verified'
            elif combined['status'] == 'visual_fallback':
                combined['selected_card_id'] = None
            return {**recognized, **combined}
        except Exception as exc:
            return {"status": "error", "selected_card_id": None,
                    "error": f"{type(exc).__name__}: {exc}"}

    def close(self):
        self.reader.close()
