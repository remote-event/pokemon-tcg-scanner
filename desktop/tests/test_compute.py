"""Exercise Torch and FAISS together, including conflicting macOS OpenMP wheels."""
import subprocess
import sys
import unittest


class ComputeTests(unittest.TestCase):
    def test_torch_and_faiss_share_a_process_in_either_import_order(self):
        for imports in ('import faiss; import torch', 'import torch; import faiss'):
            with self.subTest(imports=imports):
                result = subprocess.run([sys.executable, '-c', imports + '''
import numpy as np
torch.set_num_threads(2)
faiss.omp_set_num_threads(2)
torch.manual_seed(0)
x = torch.nn.functional.normalize(torch.randn(128, 64), dim=1)
expected = (x @ x.T).numpy()
index = faiss.IndexFlatIP(64)
index.add(x.numpy())
for _ in range(3):
    scores, ids = index.search(x.numpy(), 1)
    np.testing.assert_array_equal(ids[:, 0], np.arange(128))
    np.testing.assert_allclose(scores[:, 0], expected.diagonal(), atol=1e-5)
    torch.nn.Linear(64, 32)(x).sum().backward()
'''], capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
