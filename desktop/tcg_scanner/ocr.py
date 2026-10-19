"""Collector-number recognition workers and printing verification."""
import atexit
import base64
from io import BufferedReader, BytesIO
import json
import os
from pathlib import Path
from queue import Empty, Full, Queue
import subprocess
import threading


class PaddleWorker:
    """Persistent model process with bounded, cross-platform pipe I/O."""
    MAX_RESPONSE = 2_000_000

    def __init__(self, config, command=None):
        self.config = config
        self.process = None
        self.info = {}
        self.lock = threading.RLock()
        self._threads = []
        self.command = command or [str(Path(config.ocr_python).expanduser()), '-I', '-B', '-u',
                                  str(Path(__file__).with_name('ocr_worker.py')), config.ocr_device, config.ocr_model]
        atexit.register(self.close)

    @staticmethod
    def _publish(queue, value, stopped):
        while not stopped.is_set():
            try:
                queue.put(value, timeout=.1)
                return
            except Full:
                continue

    @classmethod
    def _read_loop(cls, process, replies, stopped):
        try:
            while not stopped.is_set():
                line = process.stdout.readline(cls.MAX_RESPONSE + 1)
                if not line:
                    raise RuntimeError('Inference worker exited unexpectedly')
                if len(line) > cls.MAX_RESPONSE:
                    raise RuntimeError('Inference worker returned an oversized response')
                if not line.endswith(b'\n'):
                    raise RuntimeError('Inference worker returned an incomplete response')
                try:
                    result = json.loads(line)
                    if not isinstance(result, dict):
                        raise ValueError('Expected a JSON object')
                except (ValueError, UnicodeError) as exc:
                    raise RuntimeError('Inference worker returned invalid JSON') from exc
                cls._publish(replies, result, stopped)
        except (OSError, ValueError, RuntimeError) as exc:
            cls._publish(replies, exc, stopped)

    @classmethod
    def _write_loop(cls, process, requests, stopped, writing):
        while not stopped.is_set():
            try:
                payload, complete = requests.get(timeout=.1)
            except Empty:
                continue
            writing.set()
            try:
                view = memoryview(payload)
                while view and not stopped.is_set():
                    written = process.stdin.write(view[:65536])
                    if not written:
                        raise RuntimeError('Inference worker input closed')
                    view = view[written:]
                complete.put(None)
            except (OSError, ValueError, RuntimeError) as exc:
                complete.put(exc)
                return
            finally:
                writing.clear()

    def _receive(self, timeout):
        try:
            result = self._replies.get(timeout=timeout)
        except Empty as exc:
            raise RuntimeError('Inference worker timed out; retaining visual candidates') from exc
        if isinstance(result, Exception):
            raise RuntimeError(str(result)) from result
        if 'error' in result:
            raise RuntimeError(result['error'])
        return result

    def start(self):
        with self.lock:
            if self.process is None:
                environment = os.environ.copy()
                if Path('/usr/lib/wsl/lib/libcuda.so').exists():
                    environment['LD_LIBRARY_PATH'] = '/usr/lib/wsl/lib:' + environment.get('LD_LIBRARY_PATH', '')
                environment.setdefault('PYTHONUTF8', '1')
                self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0, env=environment)
                self.process.stdout = BufferedReader(self.process.stdout)
                self._replies, self._requests = Queue(maxsize=2), Queue(maxsize=1)
                self._stopped, self._writing = threading.Event(), threading.Event()
                self._threads = [
                    threading.Thread(target=self._read_loop, args=(self.process, self._replies, self._stopped),
                                     name='model-output', daemon=True),
                    threading.Thread(target=self._write_loop, args=(self.process, self._requests, self._stopped, self._writing),
                                     name='model-input', daemon=True)]
                for thread in self._threads:
                    thread.start()
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
            try:
                self.start()
                payload = json.dumps({'image': base64.b64encode(image).decode('ascii')}).encode() + b'\n'
                complete = Queue(maxsize=1)
                try:
                    self._requests.put((payload, complete), timeout=2)
                    error = complete.get(timeout=2)
                except (Empty, Full) as exc:
                    raise RuntimeError('Inference worker input timed out') from exc
                if error:
                    raise RuntimeError(f'Inference worker input failed: {error}') from error
                timeout = 30 if self.config.ocr_device == 'cpu' else 3
                return {**self.info, **self._receive(timeout)}
            except BaseException:
                self.stop()
                raise

    def stop(self):
        with self.lock:
            process, self.process = self.process, None
            if process is None:
                return
            self._stopped.set()
            # A stalled writer must be unblocked by terminating its reader first.
            if self._writing.is_set() and process.poll() is None:
                process.terminate()
            else:
                process.stdin.close()  # EOF allows a healthy worker to exit normally.
            try:
                process.wait(timeout=.5)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            for thread in self._threads:
                thread.join(timeout=2)
            process.stdin.close()
            process.stdout.close()
            self._threads = []

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
