from pathlib import Path
import tempfile
import unittest

from report import read_epochs


class MetricsHistoryTest(unittest.TestCase):
    def test_merge_sparse_rows_and_resumed_epoch(self):
        with tempfile.TemporaryDirectory() as directory:
            csv = Path(directory) / 'metrics.csv'
            csv.write_text('epoch,step,train/loss,val/mAP_50\n'
                           '0,21,,0.4\n0,21,8.2,\n1,43,6.1,0.7\n'
                           '1,43,,0.8\n2,65,nan,\n,,,\n')
            epochs = read_epochs(csv)
            self.assertEqual(epochs[1], {'train/loss': 8.2, 'val/mAP_50': .4})
            self.assertEqual(epochs[2], {'train/loss': 6.1, 'val/mAP_50': .8})
            self.assertEqual(epochs[3], {})


if __name__ == '__main__':
    unittest.main()
