"""Evaluation correctness and CLI tests with real SQLite/FAISS and test-only vectors."""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from dataclasses import asdict, replace

import faiss
import numpy as np
from PIL import Image

from tcg_scanner.__main__ import main
from tcg_scanner.config import Config
from tcg_scanner.db import Database
from tcg_scanner.evaluation import IdentificationSession, evaluate_image, identify_image, report_path, save_report
from tcg_scanner.vectors import SearchIndex, VectorStore


class QueryEmbedder:
    key = "evaluation-test-model"
    dimension = 4
    device = "cpu"
    spec = {"test_only": True}

    def encode(self, bodies):
        return np.array([[1, 0, 0, 0] for _ in bodies], dtype="float32")


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = Config(data_dir=self.tmp.name)
        cli_config = self.config.root / 'test-config.toml'
        cli_config.write_text('\n'.join(f'{key} = {json.dumps(value)}' for key, value in asdict(self.config).items()))
        config_path = patch('tcg_scanner.__main__.default_config_path', return_value=cli_config)
        config_path.start()
        self.addCleanup(config_path.stop)
        self.db = Database(self.config.root / "metadata.sqlite3")
        self.addCleanup(lambda: self.db.close())
        self.model = QueryEmbedder()
        model_patch = patch("tcg_scanner.embeddings.DinoEmbedder", return_value=self.model)
        self.mock_model = model_patch.start()
        self.addCleanup(model_patch.stop)
        self.store = VectorStore.register(self.config, self.db, self.model)
        self.db.save_set("en", {"id": "test", "name": "Test Set"})
        for i in range(1, 7):
            card_id = f"test-{i:03}"
            self.db.seed_card("en", "test", {"id": card_id, "name": f"Card {i}", "localId": f"{i:03}"})
            url = f"https://assets.tcgdex.net/test/{i}"
            self.db.update_card("en", card_id, {"image_url": url, "metadata_status": "ready",
                "metadata_updated_at": time.time(), "raw_json": json.dumps({"id": card_id})})
            row = self.db.card("en", card_id)
            angle = (i - 1) * 0.1
            self.store.save(row["id"], np.array([np.cos(angle), np.sin(angle), 0, 0], dtype="float32"),
                            {"url": url + "/high.png", "sha256": "test-only", "kind": "test"})
        self.image = self.config.root / "my photo.png"
        with Image.new("RGB", (60, 82), "green") as im:
            im.save(self.image)

    def test_unlabeled_is_ungraded_and_reports_scores_and_timings(self):
        original = self.image.read_bytes()
        result = evaluate_image(self.config, self.db, self.image)
        self.assertEqual(len(result["matches"]), 5)
        self.assertEqual(result["reference_count"], 6)
        self.assertEqual([r["rank"] for r in result["matches"]], [1, 2, 3, 4, 5])
        self.assertEqual(result["matches"][0]["card_id"], "test-001")
        self.assertEqual(result["matches"][0]["set"], "Test Set")
        for key in ("expected_card_id", "expected_in_index", "top1_correct", "top5_correct"):
            self.assertIsNone(result["evaluation"][key])
        self.assertGreater(result["top_two_score_gap"], 0)
        for seconds in result["timing_seconds"].values():
            self.assertGreaterEqual(seconds, 0)
        timing = result["timing_seconds"]
        self.assertAlmostEqual(timing["query"], timing["detection_and_crop"] + timing["embedding"] + timing["search_and_metadata"])
        self.assertGreaterEqual(timing["total"], timing["query"])
        self.assertEqual(self.image.read_bytes(), original)
        self.assertFalse((self.config.root / "images").exists())
        self.assertEqual(self.db.conn.execute("SELECT count(*) FROM embeddings").fetchone()[0], 6)

    def test_labels_distinguish_top1_top5_and_outside_top5(self):
        for card_id, top1, top5, rank in [("test-001", True, True, 1),
                                        ("test-003", False, True, 3),
                                        ("test-006", False, False, 6)]:
            with self.subTest(card_id=card_id):
                evaluation = evaluate_image(self.config, self.db, self.image,
                                            expected_card=card_id, top_k=10)["evaluation"]
                self.assertTrue(evaluation["expected_in_index"])
                self.assertEqual(evaluation["expected_card"]["card_id"], card_id)
                self.assertEqual((evaluation["top1_correct"], evaluation["top5_correct"],
                                  evaluation["rank_in_results"]), (top1, top5, rank))
        result = evaluate_image(self.config, self.db, self.image, expected_card="test-006")
        self.assertIsNone(result["evaluation"]["rank_in_results"])
        self.assertFalse(result["evaluation"]["top5_correct"])

    def test_unknown_or_unsearchable_expected_card_is_reported(self):
        result = evaluate_image(self.config, self.db, self.image, expected_card="unknown-001")
        self.assertFalse(result["evaluation"]["expected_in_index"])
        self.assertFalse(result["evaluation"]["top1_correct"])
        self.assertIsNone(result["evaluation"]["expected_card"])
        self.db.update_card("en", "test-001", {"image_url": None})
        result = evaluate_image(self.config, self.db, self.image, expected_card="test-001")
        self.assertFalse(result["evaluation"]["expected_in_index"])
        self.assertIsNotNone(result["evaluation"]["expected_card"])
        self.assertFalse(result["evaluation"]["top5_correct"])

    def test_single_reference_has_no_score_gap(self):
        with self.db.conn:
            self.db.conn.execute("UPDATE embeddings SET status='failed' WHERE card_pk!=1")
        result = evaluate_image(self.config, self.db, self.image, expected_card="test-001")
        self.assertEqual(len(result["matches"]), 1)
        self.assertIsNone(result["top_two_score_gap"])
        self.assertTrue(result["evaluation"]["top5_correct"])

    def test_missing_and_corrupt_image_fail_before_model_load(self):
        self.mock_model.reset_mock()
        with self.assertRaises(OSError):
            evaluate_image(self.config, self.db, self.config.root / "missing.png")
        self.image.write_bytes(b"not an image")
        with self.assertRaises((ValueError, OSError)):
            evaluate_image(self.config, self.db, self.image)
        self.mock_model.assert_not_called()

    def test_empty_or_incompatible_references_fail_clearly(self):
        self.model.key = "incompatible-model"
        with self.assertRaisesRegex(ValueError, "Model/preprocessing changed"):
            evaluate_image(self.config, self.db, self.image)
        self.model.key = "evaluation-test-model"
        with self.db.conn:
            self.db.conn.execute("UPDATE embeddings SET status='failed'")
        self.mock_model.reset_mock()
        with self.assertRaisesRegex(ValueError, "No valid reference"):
            evaluate_image(self.config, self.db, self.image)
        self.mock_model.assert_not_called()

    def test_reports_are_unique_and_never_overwrite(self):
        result = evaluate_image(self.config, self.db, self.image)
        path = report_path(self.config)
        self.assertNotEqual(path, report_path(self.config))
        save_report(result, path)
        self.assertEqual(json.loads(path.read_text()), result)
        with self.assertRaises(ValueError):
            report_path(self.config, path)
        with self.assertRaises(FileExistsError):
            save_report(result, path)
        with self.assertRaises(ValueError):
            report_path(self.config, self.image)

    def test_cli_evaluate_saves_report_and_mismatch_is_success(self):
        self.db.close()
        try:
            output = self.config.root / "results" / "one.json"
            stdout = StringIO()
            with redirect_stdout(stdout):
                code = main(["evaluate", str(self.image), "--data-dir", self.tmp.name,
                             "--faiss-device", "cpu", "--crop", "none", "--ocr", "none", "--expected-card", "test-003", "--output", str(output)])
            self.assertEqual(code, 0)
            result = json.loads(stdout.getvalue())
            self.assertEqual(json.loads(output.read_text()), result)
            self.assertFalse(result["evaluation"]["top1_correct"])
            self.assertTrue(result["evaluation"]["top5_correct"])
            self.assertEqual(result["evaluation"]["rank_in_results"], 3)
            with redirect_stdout(StringIO()):
                self.assertEqual(main(["evaluate", str(self.image), "--data-dir", self.tmp.name,
                                       "--output", str(output)]), 1)
        finally:
            self.db = Database(self.config.root / "metadata.sqlite3")

    def test_identify_reuses_verified_index_and_existing_output_fields(self):
        with patch.object(VectorStore, "ensure_index", autospec=True,
                          side_effect=VectorStore.ensure_index) as check:
            result = identify_image(self.config, self.db, self.image, top_k=2)
        self.assertEqual(check.call_count, 1)
        self.assertEqual(len(result["matches"]), 2)
        self.assertEqual(result["reference_count"], 6)
        self.assertIn("note", result)
        self.assertNotIn("evaluation", result)

    def test_session_reuses_model_index_and_metadata_across_queries(self):
        session = IdentificationSession(self.config, self.db)
        with patch.object(VectorStore, "ensure_index", autospec=True,
                          side_effect=VectorStore.ensure_index) as index_check, \
             patch.object(VectorStore, "search_metadata", autospec=True,
                          side_effect=VectorStore.search_metadata) as metadata_check:
            first = session.identify(self.image)
            with self.assertRaises(OSError):
                session.identify(self.config.root / "missing.png")
            second = evaluate_image(self.config, self.db, self.image, session=session)
        self.assertEqual(self.mock_model.call_count, 1)
        self.assertEqual(index_check.call_count, 1)
        self.assertEqual(metadata_check.call_count, 1)
        self.assertEqual(first["matches"], [{k: v for k, v in r.items() if k != "rank"}
                                           for r in second["matches"]])
        self.assertEqual(second["session_query_number"], 2)
        self.assertEqual(second["timing_seconds"]["model_load"], 0)
        self.assertEqual(second["timing_seconds"]["reference_setup"], 0)
        self.assertNotIn("vector", next(iter(session.metadata.values())))
        self.assertNotIn("raw_json", next(iter(session.metadata.values())))
        with self.assertRaisesRegex(ValueError, "Another tcg-scanner"):
            Database(self.config.root / "metadata.sqlite3")

    def test_scan_cli_continues_after_bad_path_and_releases_lock(self):
        self.db.close()
        try:
            stdout = StringIO()
            paths = f'/missing/photo.png\n"{self.image}"\n{self.image}\n:quit\n'
            with patch("sys.stdin", StringIO(paths)), redirect_stdout(stdout), redirect_stderr(StringIO()):
                code = main(["scan", "--data-dir", self.tmp.name, "--faiss-device", "cpu", "--crop", "none", "--ocr", "none"])
            results = [json.loads(line) for line in stdout.getvalue().splitlines()]
            self.assertEqual(code, 0)
            self.assertIn("error", results[0])
            self.assertEqual([r["session_query_number"] for r in results[1:]], [1, 2])
            self.assertEqual(self.mock_model.call_count, 1)
            for result in results[1:]:
                self.assertEqual(json.loads(Path(result["report_path"]).read_text()), result)
            self.assertEqual(len(list((self.config.root / "evaluations").glob("*.json"))), 2)
            with patch("sys.stdin", StringIO("")), redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                self.assertEqual(main(["scan", "--data-dir", self.tmp.name,
                                       "--faiss-device", "cpu", "--crop", "none", "--ocr", "none"]), 0)
        finally:
            self.db = Database(self.config.root / "metadata.sqlite3")

    def test_faiss_explicit_cuda_fails_and_auto_falls_back(self):
        index = self.store.ensure_index()
        with patch.object(faiss, "get_num_gpus", return_value=0, create=True):
            with self.assertRaisesRegex(RuntimeError, "FAISS CUDA unavailable"):
                SearchIndex(index, "cuda")
            self.assertEqual(SearchIndex(index, "auto").device, "cpu")
        with self.assertRaisesRegex(ValueError, "faiss_device"):
            Config(faiss_device="invalid")

    @unittest.skipUnless(hasattr(faiss, "StandardGpuResources") and faiss.get_num_gpus() > 0,
                         "GPU FAISS and a GPU required")
    def test_gpu_cpu_agreement_with_sparse_ids_and_persistence(self):
        # Remove a middle ID to ensure GPU row offsets are never mistaken for SQLite IDs.
        with self.db.conn:
            self.db.conn.execute("DELETE FROM embeddings WHERE card_pk=2")
        cpu = self.store.ensure_index()
        persisted = self.store.path.read_bytes()
        gpu = SearchIndex(cpu, "cuda")
        query = self.model.encode([b"test"])
        cpu_scores, cpu_ids = cpu.search(query, 10)
        gpu_scores, gpu_ids = gpu.search(query, 10)
        np.testing.assert_array_equal(gpu_ids, cpu_ids)
        np.testing.assert_allclose(gpu_scores[:, :5], cpu_scores[:, :5], atol=1e-5)
        self.assertEqual(self.store.path.read_bytes(), persisted)
        self.assertEqual(faiss.read_index(str(self.store.path)).ntotal, 5)
        self.assertEqual(gpu.device, "cuda")

    def test_yolo_session_reuses_detector_and_embeds_only_cropped_pixels(self):
        config = replace(self.config, crop="yolo")
        with patch("tcg_scanner.detection.CardCropper") as factory, \
             patch.object(self.model, "encode", wraps=self.model.encode) as encode:
            factory.return_value.crop.return_value = (b"cropped-test-pixels", {"mode": "yolo"})
            session = IdentificationSession(config, self.db)
            first = session.identify(self.image)
            second = session.identify(self.image)
            self.assertEqual(factory.call_count, 1)
            self.assertEqual(factory.return_value.crop.call_count, 2)
            encode.assert_called_with([b"cropped-test-pixels"])
            self.assertEqual(first["crop"]["mode"], "yolo")
            self.assertEqual(first["model_key"], self.model.key)
            self.assertEqual(second["timing_seconds"]["detector_load"], 0)
            factory.return_value.crop.side_effect = ValueError("No card detected")
            with self.assertRaisesRegex(ValueError, "No card detected"):
                session.identify(self.image)
            self.assertEqual(encode.call_count, 2)
            factory.return_value.crop.side_effect = None
            self.assertEqual(session.identify(self.image)["session_query_number"], 3)

    def test_ocr_reranks_top10_without_changing_embedding_scores(self):
        config = replace(self.config, ocr="paddle")
        with patch("tcg_scanner.ocr.PrintingVerifier") as factory:
            verifier = factory.return_value
            verifier.check.return_value = {"status": "verified", "selected_card_id": "test-006"}
            session = IdentificationSession(config, self.db)
            try:
                result = evaluate_image(config, self.db, self.image, expected_card="test-006", session=session)
                self.assertEqual(result["matches"][0]["card_id"], "test-006")
                self.assertEqual(result["matches"][0]["embedding_rank"], 6)
                self.assertEqual(result["visual_matches"][0]["card_id"], "test-001")
                self.assertEqual(len(result["matches"]), 5)
                self.assertEqual(result["matches"][0]["similarity"], result["visual_matches"][5]["similarity"])
                self.assertGreater(result["top_two_score_gap"], 0)
                self.assertTrue(result["evaluation"]["top1_correct"])
                self.assertTrue(result["printing_verified"])
                self.assertEqual(result["selection_method"], "ocr_verified")
                self.assertEqual(verifier.check.call_args.args[0], self.image.read_bytes())
                for status in ("unreadable", "conflict", "ambiguous", "error", "incomplete_metadata"):
                    verifier.check.return_value = {"status": status, "selected_card_id": None}
                    fallback = session.identify(self.image)
                    self.assertEqual(fallback["matches"][0]["card_id"], "test-001")
                    self.assertEqual(fallback["selection_method"], "embedding")
                    self.assertFalse(fallback["printing_verified"])
                self.assertEqual(factory.call_count, 1)
            finally:
                session.close()
            verifier.close.assert_called_once()

    def test_oneshot_closes_ocr_worker(self):
        config = replace(self.config, ocr="paddle")
        with patch("tcg_scanner.ocr.PrintingVerifier") as factory:
            factory.return_value.check.return_value = {"status": "unreadable", "selected_card_id": None}
            identify_image(config, self.db, self.image)
            factory.return_value.close.assert_called_once()

    def test_evaluation_requires_at_least_five_requested_matches(self):
        with self.assertRaisesRegex(ValueError, "between 5 and 100"):
            evaluate_image(self.config, self.db, self.image, top_k=1)


if __name__ == "__main__":
    unittest.main()
