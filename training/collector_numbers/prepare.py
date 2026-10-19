"""Prepare a separate grayscale COCO dataset without changing the source export."""
import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import random

from PIL import Image, ImageOps


def orient_box(box, width, height, orientation):
    """Transform COCO pixel-edge coordinates with the same EXIF operation as Pillow."""
    def point(x, y):
        return {
            1: (x, y), 2: (width - x, y), 3: (width - x, height - y),
            4: (x, height - y), 5: (y, x), 6: (height - y, x),
            7: (height - y, width - x), 8: (y, width - x),
        }[orientation]
    x, y, w, h = box
    corners = [point(x, y), point(x + w, y), point(x, y + h), point(x + w, y + h)]
    xs, ys = zip(*corners)
    return [min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)]


def split_groups(groups, seed=42):
    """Exact 70/20/10 image counts while keeping each card's scans together."""
    groups = list(groups)
    random.Random(seed).shuffle(groups)
    count = sum(len(g) for g in groups)
    targets = {'valid': round(count * .2), 'test': round(count * .1)}
    result = {}
    for split, target in targets.items():
        reachable = {0: []}
        for index, group in enumerate(groups):
            for subtotal, indices in list(reachable.items()):
                total = subtotal + len(group)
                if total <= target and total not in reachable:
                    reachable[total] = indices + [index]
            if target in reachable:
                break
        if target not in reachable:
            raise ValueError(f'Cannot make exact {split} split without separating duplicate cards')
        selected = set(reachable[target])
        result[split] = [item for i in selected for item in groups[i]]
        groups = [g for i, g in enumerate(groups) if i not in selected]
    result['train'] = [item for group in groups for item in group]
    return result


def prepare(source, output, collection=None, resolution=512, mode='stretch', seed=42):
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError(f'Output already exists; choose a new directory: {output}')
    if resolution <= 0 or resolution % 32:
        raise ValueError('RF-DETR Small resolution must be positive and divisible by 32')
    raw = (source / '_annotations.coco.json').read_bytes()
    coco = json.loads(raw)
    images = {i['id']: i for i in coco['images']}
    if len(images) != len(coco['images']):
        raise ValueError('Duplicate image IDs')
    categories = {c['id'] for c in coco['categories']}
    annotations = defaultdict(list)
    seen_annotations = set()
    for ann in coco['annotations']:
        if ann['id'] in seen_annotations or ann['image_id'] not in images or ann['category_id'] not in categories:
            raise ValueError(f'Invalid annotation reference: {ann}')
        seen_annotations.add(ann['id'])
        image = images[ann['image_id']]
        x, y, w, h = ann['bbox']
        if not all(math.isfinite(v) for v in (x, y, w, h)) or min(x, y) < 0 or min(w, h) <= 0 or x + w > image['width'] or y + h > image['height']:
            raise ValueError(f'Invalid bounding box: {ann}')
        if ann.get('segmentation') or ann.get('keypoints'):
            raise ValueError('This preparation script accepts bounding-box annotations only')
        annotations[ann['image_id']].append(ann)
    card_ids = {}
    if collection:
        for card in json.loads(Path(collection).read_text())['cards']:
            if card.get('image_path') and card.get('card_id'):
                card_ids[Path(card['image_path']).name] = card['card_id']
    groups, hashes, group_keys = defaultdict(list), {}, {}
    # Union image IDs by both card identity and content hash, preventing either leak.
    parents = {i: i for i in images}
    def root(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i
    keys = {}
    for image_id, info in images.items():
        path = (source / info['file_name']).resolve()
        if not path.is_relative_to(source):
            raise ValueError('Image path escapes source directory')
        hashes[image_id] = hashlib.sha256(path.read_bytes()).hexdigest()
        with Image.open(path) as image:
            if image.size != (info['width'], info['height']):
                raise ValueError(f'Image dimensions differ from annotation metadata: {path}')
        group_keys[image_id] = card_ids.get(Path(info['file_name']).name, hashes[image_id])
        for key in ('hash:' + hashes[image_id], 'card:' + group_keys[image_id]):
            if key in keys:
                parents[root(image_id)] = root(keys[key])
            keys[key] = image_id
    for image_id in images:
        groups[root(image_id)].append(image_id)
    splits = split_groups(groups.values(), seed)
    manifest = dict(source=str(source), source_annotations_sha256=hashlib.sha256(raw).hexdigest(),
                    seed=seed, resolution=resolution, resize=mode, grayscale=True, auto_orient=True,
                    grouping='card_id and image SHA256' if collection else 'image SHA256',
                    categories=coco['categories'], splits={})
    output.mkdir(parents=True)
    for split, ids in splits.items():
        directory = output / split
        directory.mkdir()
        result = dict(info=coco.get('info', {}), licenses=coco.get('licenses', []),
                      categories=coco['categories'], images=[], annotations=[])
        entries = []
        for image_id in sorted(ids):
            info = images[image_id]
            with Image.open(source / info['file_name']) as original:
                orientation = original.getexif().get(274, 1)
                image = ImageOps.exif_transpose(original).convert('L').convert('RGB')
                width, height = image.size
                if mode == 'stretch':
                    resized_width = resized_height = resolution
                else:
                    scale = min(resolution / width, resolution / height)
                    resized_width, resized_height = round(width * scale), round(height * scale)
                sx, sy = resized_width / width, resized_height / height
                dx, dy = (resolution - resized_width) // 2, (resolution - resized_height) // 2
                resized = image.resize((resized_width, resized_height), Image.Resampling.LANCZOS)
                canvas = Image.new('RGB', (resolution, resolution), (0, 0, 0))
                canvas.paste(resized, (dx, dy))
                name = f'{image_id:06d}.png'
                canvas.save(directory / name)
            result['images'].append(dict(id=image_id, file_name=name, width=resolution, height=resolution))
            for ann in annotations[image_id]:
                x, y, w, h = orient_box(ann['bbox'], info['width'], info['height'], orientation)
                bbox = [x * sx + dx, y * sy + dy, w * sx, h * sy]
                result['annotations'].append({**ann, 'bbox': bbox, 'area': bbox[2] * bbox[3], 'segmentation': []})
            entries.append(dict(image_id=image_id, source=info['file_name'], sha256=hashes[image_id], group=group_keys[image_id]))
        (directory / '_annotations.coco.json').write_text(json.dumps(result, indent=2) + '\n')
        manifest['splits'][split] = dict(images=len(ids), annotations=len(result['annotations']), entries=entries)
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--collection', type=Path, help='Optional scanner export for grouping repeated card scans')
    parser.add_argument('--resolution', type=int, default=512)
    parser.add_argument('--mode', choices=['stretch', 'pad'], default='stretch')
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    manifest = prepare(**vars(args))
    print(json.dumps({s: {k: v for k, v in info.items() if k != 'entries'} for s, info in manifest['splits'].items()}, indent=2))
