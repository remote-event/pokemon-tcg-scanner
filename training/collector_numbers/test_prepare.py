import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image
from prepare import orient_box, prepare, split_groups


class PreparationTest(unittest.TestCase):
    def test_grouped_split(self):
        groups = [[i, i + 1000] for i in range(100)] + [[i] for i in range(100, 400)]
        split = split_groups(groups)
        self.assertEqual({k: len(v) for k, v in split.items()}, {'valid': 100, 'test': 50, 'train': 350})
        self.assertEqual(split, split_groups(groups))
        ownership = {image: name for name, ids in split.items() for image in ids}
        self.assertEqual(len(ownership), 500)
        for group in groups:
            self.assertEqual(len({ownership[i] for i in group}), 1)

    def test_exif_rotation(self):
        self.assertEqual(orient_box([10, 20, 30, 40], 100, 200, 6), [140, 10, 40, 30])
        self.assertEqual(orient_box([10, 20, 30, 40], 100, 200, 8), [20, 60, 40, 30])

    def test_image_and_box_transforms(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source'
            source.mkdir()
            coco = dict(images=[], annotations=[], categories=[{'id': 1, 'name': 'number'}])
            for i in range(10):
                Image.new('RGB', (100, 200), (i * 10, 80, 150)).save(source / f'{i}.png')
                coco['images'].append(dict(id=i, file_name=f'{i}.png', width=100, height=200))
                coco['annotations'].append(dict(id=i, image_id=i, category_id=1, bbox=[10, 20, 30, 40]))
            (source / '_annotations.coco.json').write_text(json.dumps(coco))
            for mode, expected in [('stretch', [6.4, 6.4, 19.2, 12.8]), ('pad', [19.2, 6.4, 9.6, 12.8])]:
                destination = root / mode
                prepare(source, destination, resolution=64, mode=mode)
                data = json.loads((destination / 'train/_annotations.coco.json').read_text())
                self.assertEqual(data['annotations'][0]['bbox'], expected)
                with Image.open(destination / 'train' / data['images'][0]['file_name']) as image:
                    self.assertEqual(image.size, (64, 64))
                    self.assertEqual(len(set(image.getpixel((32, 32)))), 1)
            self.assertEqual(json.loads((source / '_annotations.coco.json').read_text()), coco)


if __name__ == '__main__':
    unittest.main()
