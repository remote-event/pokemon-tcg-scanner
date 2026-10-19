"""Resident desktop inference. No camera images are written or fetched from the web."""
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from io import BytesIO
import hashlib
import json
import logging
from time import monotonic, perf_counter
from uuid import uuid4

import numpy as np
from PIL import Image

from .detection import rectify_card, warp_card
from .evaluation import IdentificationSession
from .live_gate import ScanGate, distance
from .quality import assess, brighten, context, signature, thumbnail, uncertain_darkness

log = logging.getLogger(__name__)


class LiveState:
    def __init__(self):
        self.gate = ScanGate()
        self.aliases = set()
        self.recognition_id = None
        self.last_frame_at = None
        self.frames = deque(maxlen=3)
        self.context = None
        self.input_mode = 'frame'
        self.pending = None
        self.reset_uncertainty()

    def reset_uncertainty(self):
        self.uncertain_signature = None
        self.uncertain_attempts = 0
        self.uncertain_request_id = None
        self.uncertain_quality_score = float('-inf')

    def clear_view(self):
        self.frames.clear()

    def interrupt_view(self):
        self.gate.interrupt()
        self.clear_view()

    def reset_presentation(self):
        self.clear_view()
        self.context = None
        self.reset_uncertainty()


class LiveProcessor:
    """Resident visual and collector-number matching with explicit ambiguity handling."""
    def __init__(self, config):
        from .db import Database
        self.db = Database(config.root / "metadata.sqlite3")
        self.session = IdentificationSession(replace(config, ocr="none"), self.db)
        self.cache = OrderedDict()
        self.vectors = OrderedDict()
        self.combined_cache = OrderedDict()
        self.config = config
        self.reader = None
        self.number_pool = ThreadPoolExecutor(1, thread_name_prefix='tcg-number')
        self.stats = dict(frames=0, embeddings=0, cache_hits=0, recognitions=0, brightening_retries=0, quality_holds=0)
        try:
            self.session.load()
            if self.session.cropper is None:
                raise ValueError("The live server requires YOLO")
            self.cards = {row["card_id"]: row for row in self.session.metadata.values()}
            self.sets = {row[0]: json.loads(row[1]) for row in self.db.conn.execute(
                "SELECT set_id,raw_json FROM sets WHERE language=?", (config.language,))}
            # Number lookup spans the entire catalog, including cards lacking embeddings.
            for row in self.db.conn.execute('''SELECT c.card_id,c.name,c.collector_number,c.set_id,
                    s.name AS set_name,c.image_url FROM cards c LEFT JOIN sets s
                    ON s.language=c.language AND s.set_id=c.set_id WHERE c.language=?''', (config.language,)):
                if row['card_id'] not in self.cards:
                    self.cards[row['card_id']] = {**dict(row), 'source_url': (row['image_url'].rstrip('/')+'/high.png') if row['image_url'] else ''}
            from .collector import CatalogMatcher, CollectorReader
            self.matcher = CatalogMatcher(self.cards, self.sets, config)
            self.embedding_ids = {m['card_id']: eid for eid,m in self.session.metadata.items()}
            ocr_info = 'disabled'
            if config.ocr == 'paddle':
                self.reader = CollectorReader(config)
                try:
                    ocr_info = self.reader.start()
                except Exception as exc:
                    log.exception('Collector OCR unavailable; visual fallback enabled')
                    self.reader.close()
                    ocr_info = {'status': 'degraded', 'error': str(exc)}
            # Run real kernels before advertising readiness.
            rgb = np.full((640, 480, 3), 124, np.uint8)
            self.session.cropper.detect_live(rgb)
            with Image.fromarray(rgb) as image, BytesIO() as output:
                image.save(output, format="PNG")
                vector = self.session.embedder.encode([output.getvalue()])[0]
                self.session.index.search(vector[None], 1)
            import torch
            self.info = dict(protocol=1, gpu=torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
                             yolo=self.session.cropper.device, dinov2=self.session.embedder.device,
                             faiss=self.session.index.device, ocr=ocr_info, input_modes=['frame', 'card_crop_v1'],
                             references=self.session.index.ntotal)
            log.info("Models warmed: %s", self.info)
        except BaseException:
            self.close()
            raise

    def close(self):
        self.number_pool.shutdown(wait=True, cancel_futures=True)
        if self.reader is not None:
            self.reader.close()
        self.session.close()
        self.db.close()

    def wire_matches(self, matches):
        result = []
        for match in matches:
            card = self.cards[match["card_id"]]
            metadata = self.sets.get(card["set_id"], {})
            result.append(dict(score=float(match["similarity"]) if match['similarity'] is not None else None,
                ocr_support=match.get('ocr_support', False), embedding_rank=match.get('embedding_rank'), card=dict(
                id=card["card_id"], name=card["name"], set=card["set_name"],
                number=card["collector_number"] or "", image_url=card["source_url"] or "",
                code=metadata.get("abbreviation", {}).get("official"),
                total=metadata.get("cardCount", {}).get("official"))))
        return result

    def search(self, crop):
        started = perf_counter()
        key = hashlib.sha256(crop).digest()
        matches = self.cache.get(key)
        if matches is None:
            vector = self.session.embedder.encode([crop])[0]
            embedded = perf_counter()
            matches = self.session.store.search(vector, 10, index=self.session.index,
                                                metadata=self.session.metadata)
            self.stats["embeddings"] += 1
            self.cache[key] = matches
            self.vectors[key] = vector
            if len(self.cache) > 32:
                old, _ = self.cache.popitem(last=False)
                self.vectors.pop(old, None)
            self.visual_timing = dict(embedding_ms=(embedded-started)*1000, faiss_ms=(perf_counter()-embedded)*1000)
        else:
            self.cache.move_to_end(key)
            self.stats["cache_hits"] += 1
            self.visual_timing = dict(embedding_ms=0., faiss_ms=0.)
        self.last_vector = self.vectors.get(key)
        return matches

    def identify_crop(self, crop):
        started = perf_counter()
        key = hashlib.sha256(crop).digest()
        if key in self.combined_cache:
            self.stats['cache_hits'] += 1
            cached = self.combined_cache[key]
            return {**cached, 'cache_hit': True, 'timing_ms': {'total_ms': (perf_counter()-started)*1000}}
        future = self.number_pool.submit(self.reader.read, crop) if self.reader and self.config.inference_mode == 'concurrent' else None
        visual = self.search(crop)
        timing = dict(self.visual_timing)
        ocr = future.result() if future else (self.reader.read(crop) if self.reader else {'status':'disabled'})
        timing.update(ocr.get('timing_ms', {}))
        all_visual = visual
        if ocr['status'] == 'recognized':
            at = perf_counter()
            ids = self.matcher.lookup(ocr['number'], ocr.get('total'))
            # FAISS GPU top-k is limited to 2048. Keep its existing top-10 search;
            # score only additional catalog-number matches using the SAME persisted
            # normalized vectors and exact inner product on the embedding device.
            missing = [i for i in ids if i in self.embedding_ids and not any(m['card_id']==i for m in visual)]
            all_visual = [dict(m,embedding_rank=i) for i,m in enumerate(visual,1)]
            if missing:
                torch = self.session.embedder.torch
                references = np.stack([self.session.cpu_index.reconstruct(self.embedding_ids[i]) for i in missing])
                with torch.inference_mode():
                    matrix = torch.from_numpy(references).to(self.session.embedder.device)
                    query = torch.from_numpy(self.last_vector).to(self.session.embedder.device)
                    scores = (matrix @ query).float().cpu().numpy()
                for card_id,score in zip(missing,scores):
                    c = self.cards[card_id]
                    all_visual.append(dict(card_id=card_id,similarity=float(score),name=c['name'],set=c['set_name'],
                        collector_number=c['collector_number'],reference_image_url=c['source_url'],embedding_rank=None))
            timing['candidate_scoring_ms'] = (perf_counter()-at)*1000
        matching_at = perf_counter()
        result = self.matcher.combine(visual, ocr, all_visual)
        timing['matching_ms'] = (perf_counter()-matching_at)*1000
        timing['total_ms'] = (perf_counter()-started)*1000
        result.update(ocr=ocr, visual_matches=visual, timing_ms=timing, cache_hit=False)
        self.combined_cache[key] = result
        if len(self.combined_cache)>32:
            self.combined_cache.popitem(last=False)
        return result

    @staticmethod
    def margin(matches):
        return matches[0]["similarity"] - matches[1]["similarity"] if len(matches) > 1 else 0.

    def choose_matches(self, crop, quality):
        matches = self.search(crop)
        top = matches[0] if matches else None
        if quality.dark and (top is None or top["similarity"] < .8 or self.margin(matches) < .015):
            with Image.open(BytesIO(crop)) as image:
                enhanced = brighten(np.array(image.convert("RGB")))
            with Image.fromarray(enhanced) as image, BytesIO() as output:
                image.save(output, format="PNG")
                alternative = self.search(output.getvalue())
            self.stats["brightening_retries"] += 1
            if alternative:
                same = top is not None and top["card_id"] == alternative[0]["card_id"]
                if same:
                    return alternative if alternative[0]["similarity"] > top["similarity"] else matches
                # An enhancement must not win just because its similarity went up.
                # Disagreement needs a clear alternative; otherwise request a better frame.
                if alternative[0]["similarity"] >= .8 and self.margin(alternative) >= .015:
                    return alternative
                return []
        return matches

    def process(self, state, body, threshold):
        started = perf_counter()
        now = monotonic() * 1000
        if state.last_frame_at is not None and now - state.last_frame_at > 1500:
            state.gate.interrupt()  # Dropped network time is not evidence of removal.
            state.clear_view()
        state.last_frame_at = now
        self.stats["frames"] += 1
        if state.input_mode == 'card_crop_v1' and body == b'TCGUNCERTAIN':
            state.interrupt_view()
            return {'event':'observing', 'hint':'Show the whole card clearly', 'interval_ms':250}
        if state.input_mode == 'card_crop_v1' and body == b'TCGEMPTY':
            state.clear_view()
            if state.gate.absent(now, True):
                state.pending = None
                state.aliases.clear()
                state.recognition_id = None
                state.reset_presentation()
                return {'event':'removed', 'state':state.gate.state, 'interval_ms':250}
            return {'event':'observing', 'state':state.gate.state, 'interval_ms':125}
        with Image.open(BytesIO(body)) as image:
            if image.format != "JPEG" or min(image.size) < 32 or max(image.size) > 1600:
                raise ValueError("Send a JPEG with dimensions between 32 and 1600 pixels")
            rgb = np.array(image.convert("RGB"))
        if state.input_mode == 'card_crop_v1':
            h,w = rgb.shape[:2]
            corners, empty = np.float32([[8,8],[w-9,8],[w-9,h-9],[8,h-9]]), False
        else:
            corners, empty = self.session.cropper.detect_live(rgb, threshold)
        frame_light = float(np.percentile(thumbnail(rgb), 95))
        if corners is None and frame_light < 85:
            corners, retry_empty = self.session.cropper.detect_live(brighten(rgb), threshold)
            empty = empty and retry_empty
            self.stats["brightening_retries"] += 1
        response = {"event": "observing"}
        gate = state.gate
        if corners is None:
            uncertain = uncertain_darkness(rgb, state.context)
            state.clear_view()
            if uncertain:
                response["hint"] = "Add a little light"
                self.stats["quality_holds"] += 1
            if gate.absent(now, empty and not uncertain):
                state.pending = None
                state.aliases.clear()
                state.recognition_id = None
                state.reset_presentation()
                response.update(event="removed", camera={"reset": True})
        else:
            small = rectify_card(rgb, corners, (252, 352))
            # Ignore perspective-warp padding for quality statistics.
            quality = assess(small[5:-5, 5:-5])
            observed = signature(small)
            normalized = corners / np.array([rgb.shape[1], rgb.shape[0]], np.float32)
            center = normalized.mean(axis=0)
            response["camera"] = dict(x=float(center[0]), y=float(center[1]),
                                      exposure=.5 if quality.light < 65 else (-.33 if quality.glare > .12 else None))
            response["hint"] = quality.hint
            if state.uncertain_attempts and distance(state.uncertain_signature, observed, True) < .045:
                response['hint'] = quality.hint or ('Reading card…' if state.uncertain_attempts < 3 else 'Tilt the card slightly for a clearer scan')
            if not quality.usable:
                gate.interrupt()
                state.clear_view()
                self.stats["quality_holds"] += 1
            else:
                # Keep the last adequately lit background for disappearance checks.
                if not quality.dark or state.context is None:
                    state.context = context(rgb, corners)
                if state.uncertain_attempts >= 3 and quality.score > state.uncertain_quality_score + .2:
                    # A sharper, brighter, or less reflective view may have the same
                    # card signature. Let improved evidence retry without removal.
                    gate.accepted = None
                    gate.interrupt()
                    state.reset_uncertainty()
                ready = gate.observe(observed, normalized, now)
                if gate.count == 1:
                    state.clear_view()  # New card/unstable view: never reuse another card's pixels.
                while state.frames and now - state.frames[0][0] > 750:
                    state.frames.popleft()
                state.frames.append((now, body, corners.copy(), quality))
                if ready:
                    best = max(state.frames, key=lambda sample: sample[3].score)
                    if state.input_mode == 'card_crop_v1':
                        crop = best[1]
                    else:
                        with Image.open(BytesIO(best[1])) as image:
                            crop = warp_card(np.array(image.convert("RGB")), best[2])
                    combined = self.identify_crop(crop) if self.reader else None
                    matches = combined['matches'] if combined else self.choose_matches(crop, best[3])
                    state.pending = None
                    if combined and not combined['selected_card_id'] and matches:
                        if distance(state.uncertain_signature, observed, True) >= .045:
                            state.reset_uncertainty()
                            state.uncertain_signature = observed.copy()
                            state.uncertain_request_id = uuid4().hex
                        state.uncertain_attempts += 1
                        state.uncertain_quality_score = max(state.uncertain_quality_score, best[3].score)
                        retrying = state.uncertain_attempts < 3
                        if retrying:
                            gate.reject(observed, now)  # Retry a fresh stable view after the existing 1.2s cooldown.
                        else:
                            gate.accept(observed)  # Bound work on unchanged uncertain cards; a changed view rearms.
                        response.update(event='confirmation', request_id=state.uncertain_request_id, retrying=retrying,
                            matches=self.wire_matches(matches), visual_matches=self.wire_matches(combined['visual_matches']),
                            ocr=combined['ocr'], reason=combined['status'], timing_ms=combined['timing_ms'],
                            hint='Reading card…' if retrying else 'Tilt the card slightly for a clearer scan', _card_crop=crop)
                        response.update(state=gate.state, interval_ms=250, processing_ms=round((perf_counter()-started)*1000,1))
                        return response
                    top = matches[0] if matches else None
                    if top is None or not np.isfinite(top["similarity"]) or top["similarity"] < .65:
                        gate.reject(observed, now)
                        response["hint"] = quality.hint or "Move closer or tilt the card slightly"
                    elif gate.confirm(top["card_id"], top["similarity"], top["card_id"] in state.aliases):
                        gate.accept(observed)
                        state.reset_uncertainty()
                        if top["card_id"] not in state.aliases:
                            state.aliases = {top["card_id"]}
                            state.recognition_id = uuid4().hex
                            self.stats["recognitions"] += 1
                            response.update(event="recognized", recognition_id=state.recognition_id,
                                            hint='',
                                            matches=self.wire_matches(matches), verified=bool(combined and combined['status']=='ocr_supported'), checking=False,
                                            verification='ocr_supported' if combined and combined['status']=='ocr_supported' else 'identified',
                                            _card_crop=crop)
                            if combined:
                                response.update(ocr=combined['ocr'], timing_ms=combined['timing_ms'], visual_matches=self.wire_matches(combined['visual_matches']))
                            log.info("Recognized %s score=%.3f light=%.0f sharpness=%.0f glare=%.3f",
                                     top["card_id"], top["similarity"], best[3].light,
                                     best[3].sharpness, best[3].glare)
        response.update(state=gate.state, interval_ms=gate.interval_ms,
                        processing_ms=round((perf_counter() - started) * 1000, 1))
        return response
