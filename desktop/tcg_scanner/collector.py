"""Collector-number localization, recognition-only OCR, and catalog-wide matching."""
from io import BytesIO
import math
from pathlib import Path
import re
import unicodedata
from time import perf_counter

from PIL import Image

from .ocr import PaddleWorker


def normalize_number(value):
    """Preserve prefixes/suffixes and symbols; normalize numeric runs, not O/I guesses."""
    value = unicodedata.normalize('NFKC', str(value)).upper().strip()
    value = re.sub(r'\s+', '', value).translate(str.maketrans({'⁄': '/', '∕': '/', '／': '/', '–': '-', '−': '-', '☆': '★'}))
    if not re.fullmatch(r'(?=[A-Z0-9★.-]*\d)[A-Z0-9★.-]+', value):
        return None
    return re.sub(r'\d+', lambda m: str(int(m[0])), value)


def parse_number(text):
    parts = unicodedata.normalize('NFKC', text).translate(str.maketrans({'⁄': '/', '∕': '/'})).split('/')
    if len(parts) not in (1, 2):
        return None
    number = normalize_number(parts[0])
    total = normalize_number(parts[1]) if len(parts) == 2 else None
    if not number or (len(parts) == 2 and not total):
        return None
    return number, total


def padded_box(box, width, height, padding):
    if len(box) != 4 or not all(math.isfinite(v) for v in box):
        raise ValueError('Invalid collector-number box')
    x1, y1, x2, y2 = box
    if x2-x1 < 4 or y2-y1 < 3 or x2 <= 0 or y2 <= 0 or x1 >= width or y1 >= height:
        raise ValueError('Collector-number box is empty')
    # Small padding preserves neighboring characters without feeding the whole footer.
    px, py = max(2, (x2-x1)*padding), max(2, (y2-y1)*padding)
    return max(0, math.floor(x1-px)), max(0, math.floor(y1-py)), min(width, math.ceil(x2+px)), min(height, math.ceil(y2+py))


def overlap(a, b):
    x1, y1, x2, y2 = a; u1, v1, u2, v2 = b
    intersection = max(0, min(x2,u2)-max(x1,u1))*max(0, min(y2,v2)-max(y1,v1))
    return intersection / max(1, (x2-x1)*(y2-y1)+(u2-u1)*(v2-v1)-intersection)


class CollectorReader:
    def __init__(self, config):
        self.config = config
        weights = Path(config.collector_weights).expanduser()
        if not weights.is_absolute():
            weights = config.root / weights
        self.detector = PaddleWorker(config, [str(Path(config.collector_python).expanduser()), '-I', '-B', '-u',
            str(Path(__file__).with_name('collector_worker.py')), str(weights),
            'cuda' if config.ocr_device.startswith('gpu') else 'cpu', str(config.collector_confidence)])
        self.recognizer = PaddleWorker(config)
        self.available = False

    def start(self):
        info = {'detector': self.detector.start(), 'recognizer': self.recognizer.start()}
        self.available = True
        return info

    def read(self, card):
        started = perf_counter()
        timing = {'number_detection_ms': 0., 'number_crop_ms': 0., 'recognition_ms': 0.}
        if not self.available:
            return dict(status='error', error='OCR temporarily unavailable', readings=[], timing_ms=timing)
        try:
            result = self.detector.recognize(card)
            timing['number_detection_ms'] = (perf_counter()-started)*1000
            boxes = []
            for item in sorted(result['boxes'], key=lambda b: b['confidence'], reverse=True):
                if item['confidence'] >= self.config.collector_confidence and all(overlap(item['box'], b['box']) < .5 for b in boxes):
                    boxes.append(item)
            if not boxes:
                return dict(status='no_region', readings=[], timing_ms=timing)
            if len(boxes) > 3:
                return dict(status='ambiguous', readings=[], timing_ms=timing)
            readings = []
            with Image.open(BytesIO(card)) as image:
                for item in boxes:
                    at = perf_counter()
                    box = padded_box(item['box'], *image.size, self.config.collector_padding)
                    crop = image.crop(box).convert('RGB')
                    # TextRecognition performs its own height normalization. No detector,
                    # orientation classifier, document pipeline or full-image OCR is used.
                    with BytesIO() as output:
                        crop.save(output, format='PNG')
                        encoded = output.getvalue()
                    timing['number_crop_ms'] += (perf_counter()-at)*1000
                    at = perf_counter()
                    recognized = self.recognizer.recognize(encoded)
                    timing['recognition_ms'] += (perf_counter()-at)*1000
                    for line in recognized['lines']:
                        score = float(line['score'])
                        readings.append(dict(text=line['text'], confidence=score if math.isfinite(score) else 0.,
                            detector_confidence=item['confidence'], box=list(box), parsed=parse_number(line['text'])))
            good = [r for r in readings if r['parsed'] and r['confidence'] >= self.config.ocr_confidence]
            numbers = {tuple(r['parsed']) for r in good}
            status = 'recognized' if len(numbers) == 1 else ('ambiguous' if len(numbers) > 1 else 'unreadable')
            best = max(good, key=lambda r:r['confidence']) if good else None
            return dict(status=status, readings=readings, number=best['parsed'][0] if best else None,
                        total=best['parsed'][1] if best else None, text=best['text'] if best else None,
                        confidence=best['confidence'] if best else 0., timing_ms=timing)
        except Exception as exc:
            self.available = False  # Restart at server startup; never block camera requests on model reloading.
            return dict(status='error', error=f'{type(exc).__name__}: {exc}', readings=[], timing_ms=timing)

    def close(self):
        self.detector.close()
        self.recognizer.close()


class CatalogMatcher:
    def __init__(self, cards, sets, config):
        self.cards, self.sets, self.config = cards, sets, config
        self.numbers = {}
        for card_id, card in cards.items():
            number = normalize_number(card.get('collector_number') or '')
            if number:
                self.numbers.setdefault(number, []).append(card_id)

    def lookup(self, number, total):
        ids = self.numbers.get(number, [])
        if total is None:
            return list(ids)
        result = []
        for card_id in ids:
            card = self.cards[card_id]
            official = self.sets.get(card['set_id'], {}).get('cardCount', {}).get('official')
            # Unknown/alphanumeric denominators cannot establish set identity. Retain
            # these candidates for visual disambiguation instead of inventing a set.
            if official is None or not total.isdigit() or str(official) == total:
                result.append(card_id)
        return result

    def combine(self, visual, ocr, all_visual=None):
        config = self.config
        visual = [dict(m) for m in visual]
        if not visual:
            return dict(status='no_match', selected_card_id=None, matches=[], ocr_matches=[])
        top = visual[0]
        margin = top['similarity']-visual[1]['similarity'] if len(visual)>1 else 1.
        if ocr['status'] not in ('recognized', 'ambiguous'):
            selected = top['card_id'] if top['similarity'] >= config.visual_confidence and margin >= config.visual_margin else None
            return dict(status='visual_fallback' if selected else 'ambiguous', selected_card_id=selected,
                        matches=visual, ocr_matches=[])
        if ocr['status'] == 'ambiguous':
            return dict(status='ambiguous', selected_card_id=None, matches=visual, ocr_matches=[])
        ids = self.lookup(ocr['number'], ocr.get('total'))
        scores = {m['card_id']: dict(m, embedding_rank=m.get('embedding_rank',i)) for i,m in enumerate(all_visual or visual,1)}
        supported = []
        for card_id in ids:
            card = self.cards[card_id]
            match = scores.get(card_id, dict(card_id=card_id, name=card['name'], set=card.get('set_name'),
                          collector_number=card['collector_number'], similarity=None, reference_image_url=card.get('source_url')))
            supported.append(dict(match, ocr_support=True))
        supported.sort(key=lambda m: m['similarity'] if m['similarity'] is not None else -1, reverse=True)
        selected = None
        if supported:
            best = supported[0]
            score = best['similarity']
            other = supported[1]['similarity'] if len(supported)>1 else -1
            other = other if other is not None else -1
            if (score is not None and score >= config.visual_confidence and
                    top['similarity']-score <= config.ocr_visual_tolerance and score-other >= config.visual_margin):
                selected = best['card_id']
        # Full OCR lookup is retained even when the UI list is bounded.
        candidates = supported[:10] + [m for m in visual if m['card_id'] not in ids][:10]
        return dict(status='ocr_supported' if selected else ('conflict' if not supported or
                    supported[0]['similarity'] is None or top['similarity']-supported[0]['similarity'] > config.ocr_visual_tolerance else 'ambiguous'),
                    selected_card_id=selected, matches=candidates, ocr_matches=ids)
