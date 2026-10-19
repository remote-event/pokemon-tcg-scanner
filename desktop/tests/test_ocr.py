"""OCR worker protocol and failure fallback; no model downloads."""
from io import BytesIO
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from tcg_scanner.config import Config
from tcg_scanner.ocr import PaddleWorker, PrintingVerifier


class OCRTests(unittest.TestCase):
    def test_worker_failure_is_embedding_fallback(self):
        verifier = object.__new__(PrintingVerifier)
        verifier.reader = Mock()
        verifier.reader.read.side_effect = RuntimeError('GPU unavailable')
        with Image.new('RGB', (630, 880)) as im, BytesIO() as out:
            im.save(out, format='PNG')
            result = verifier.check(out.getvalue(), {'mode': 'none'}, [{'card_id': 'example-001'}])
        self.assertEqual(result['status'], 'error')
        self.assertIsNone(result['selected_card_id'])
        self.assertIn('GPU unavailable', result['error'])

    def test_worker_protocol_reuse_and_cleanup(self):
        # A tiny subprocess exercises the real pipe protocol without OCR weights.
        code = """import sys, json
print(json.dumps({'ready': True}), flush=True)
for line in sys.stdin:
    print(json.dumps({'lines': [], 'inference_seconds': 0}), flush=True)
"""
        popen = subprocess.Popen
        def launch(*args, **kwargs):
            return popen([sys.executable, '-u', '-c', code], **kwargs)
        worker = PaddleWorker(Config())
        try:
            with patch('tcg_scanner.ocr.subprocess.Popen', side_effect=launch) as factory:
                self.assertEqual(worker.recognize(b'x' * 200000)['lines'], [])
                process = worker.process
                worker.recognize(b'second image')
                self.assertIs(worker.process, process)
                self.assertEqual(factory.call_count, 1)
            worker.close()
            self.assertIsNotNone(process.poll())
            self.assertTrue(process.stdin.closed and process.stdout.closed)
        finally:
            worker.close()

    def test_worker_exit_and_timeout_release_process(self):
        popen = subprocess.Popen
        for code, message in [
            ("import sys,json,os; print(json.dumps({'ready':True}),flush=True); sys.stdin.readline(); os._exit(1)", 'exited'),
            ("import time; time.sleep(10)", 'timed out'),
        ]:
            with self.subTest(message=message):
                worker = PaddleWorker(Config())
                processes = []
                def launch(*args, **kwargs):
                    process = popen([sys.executable, '-u', '-c', code], **kwargs)
                    processes.append(process)
                    return process
                receive = worker._receive
                try:
                    with patch('tcg_scanner.ocr.subprocess.Popen', side_effect=launch), \
                         patch.object(worker, '_receive', side_effect=lambda _: receive(.2)):
                        with self.assertRaisesRegex(RuntimeError, message):
                            worker.recognize(b'image')
                    self.assertIsNone(worker.process)
                    self.assertIsNotNone(processes[0].poll())
                finally:
                    worker.close()

    def test_invalid_ocr_config(self):
        for values in ({'ocr': 'unknown'}, {'ocr_device': 'cuda'}, {'ocr_python': ''}):
            with self.assertRaises(ValueError):
                Config(**values)

    def test_invalid_or_oversized_response_cleans_up_and_can_restart(self):
        for reply in ["b'not-json\\n'", "b'x' * 2000001 + b'\\n'"]:
            with self.subTest(reply=reply):
                code = f"import sys,json; print(json.dumps({{'ready':True}}),flush=True); sys.stdin.readline(); sys.stdout.buffer.write({reply}); sys.stdout.buffer.flush()"
                worker = PaddleWorker(Config(), [sys.executable, '-u', '-c', code])
                try:
                    worker.start()
                    process, threads = worker.process, worker._threads[:]
                    with self.assertRaises(RuntimeError):
                        worker.recognize(b'image')
                    self.assertIsNotNone(process.poll())
                    self.assertTrue(all(not t.is_alive() for t in threads))
                    worker.command = [sys.executable, '-u', '-c', "import sys,json; print(json.dumps({'ready':True}),flush=True); sys.stdin.readline(); print(json.dumps({'lines':[]}),flush=True)"]
                    self.assertEqual(worker.recognize(b'new image')['lines'], [])
                finally:
                    worker.close()

    def test_worker_that_never_reads_cannot_block_the_server(self):
        code = "import json,time; print(json.dumps({'ready':True}),flush=True); time.sleep(60)"
        worker = PaddleWorker(Config(), [sys.executable, '-u', '-c', code])
        try:
            worker.start()
            process, threads = worker.process, worker._threads[:]
            with self.assertRaisesRegex(RuntimeError, 'input timed out'):
                worker.recognize(b'x' * 500000)
            self.assertIsNotNone(process.poll())
            self.assertTrue(all(not t.is_alive() for t in threads))
        finally:
            worker.close()


if __name__ == '__main__':
    unittest.main()
