import unittest
from io import BytesIO
from types import SimpleNamespace
from collections import OrderedDict
from unittest.mock import patch
import cv2
import numpy as np
from PIL import Image
from tcg_scanner.quality import assess, context, signature, uncertain_darkness
from tcg_scanner.detection import rectify_card, warp_card
from tcg_scanner.live import LiveProcessor, LiveState
from tcg_scanner.live_gate import distance


def jpeg(rgb):
    out=BytesIO(); Image.fromarray(rgb).save(out,format='JPEG',quality=95); return out.getvalue()


def card_frame():
    rgb=np.full((480,360,3),40,np.uint8)
    rgb[40:440,40:320]=[160,190,210]
    for y in range(65,420,12):
        rgb[y:y+3,55:305]=[35,50,65]
    return rgb


CORNERS=np.float32([[40,40],[319,40],[319,439],[40,439]])
MATCHES=[dict(card_id='a',similarity=.9),dict(card_id='b',similarity=.7)]


class QualityTests(unittest.TestCase):
    def test_darkness_is_not_removal_but_empty_dark_table_is(self):
        frame=card_frame(); previous=context(frame,CORNERS)
        self.assertTrue(uncertain_darkness((frame*.08).astype('uint8'),previous))
        self.assertTrue(uncertain_darkness(np.zeros_like(frame),previous))
        self.assertFalse(uncertain_darkness(np.full_like(frame,40),previous))

    def test_exposure_change_keeps_signature_and_card_replacement_changes_it(self):
        crop=rectify_card(card_frame(),CORNERS,(252,352))
        self.assertLess(distance(signature(crop),signature((crop*.5).astype('uint8'))),.045)
        replacement=crop.copy();replacement[:]=replacement[:,::-1];replacement[:,:,0]=255
        self.assertGreater(distance(signature(crop),signature(replacement)),.045)

    def test_highlight_and_blur_lower_quality(self):
        crop=rectify_card(card_frame(),CORNERS,(252,352))[5:-5,5:-5]
        clear=assess(crop)
        blurred=assess(cv2.GaussianBlur(crop,(31,31),8))
        glare=crop.copy();glare[:,75:145]=255
        self.assertTrue(clear.usable)
        self.assertFalse(blurred.usable)
        self.assertLess(blurred.score,clear.score)
        self.assertGreater(assess(glare).glare,.2)
        self.assertLess(assess(glare).score,clear.score)


class ProcessorTests(unittest.TestCase):
    def setUp(self):
        self.processor=LiveProcessor.__new__(LiveProcessor)
        self.processor.stats=dict(frames=0,embeddings=0,cache_hits=0,recognitions=0,ocr=0,brightening_retries=0,quality_holds=0,ocr_retries=0)
        self.processor.cache=OrderedDict()
        self.processor.reader=None
        self.processor.session=SimpleNamespace(cropper=SimpleNamespace(detect_live=lambda rgb,threshold: (CORNERS,False)))
        self.processor.choose_matches=lambda crop,quality:MATCHES
        self.processor.wire_matches=lambda matches:matches
        self.state=LiveState(); self.t=0

    def frame(self, rgb):
        self.t+=.125
        with patch('tcg_scanner.live.monotonic',return_value=self.t):
            return self.processor.process(self.state,jpeg(rgb),.5)

    def lock(self):
        rgb=card_frame()
        self.frame(rgb);self.frame(rgb)
        result=self.frame(rgb)
        self.assertEqual(result['event'],'recognized')
        self.assertFalse(result['checking']);self.assertEqual(result['verification'],'identified')
        return result

    def test_darken_occlude_restore_without_duplicate_then_remove(self):
        original=self.lock()
        self.processor.session.cropper.detect_live=lambda rgb,threshold:(None,True)
        for _ in range(8):
            result=self.frame(np.zeros_like(card_frame()))
            self.assertEqual(result['event'],'observing')
            self.assertEqual(self.state.recognition_id,original['recognition_id'])
        self.processor.session.cropper.detect_live=lambda rgb,threshold:(CORNERS,False)
        result=self.frame(card_frame())
        self.assertEqual(result['event'],'observing')
        self.processor.session.cropper.detect_live=lambda rgb,threshold:(None,True)
        events=[self.frame(np.full_like(card_frame(),40))['event'] for _ in range(5)]
        self.assertIn('removed',events)
        self.assertIsNone(self.state.recognition_id)

    def test_saved_crop_uses_original_pixels_after_detector_brightening(self):
        dark=(card_frame()*.25).astype('uint8')
        self.processor.session.cropper.detect_live=lambda rgb,threshold: (CORNERS,False) if rgb.max()>80 else (None,True)
        self.frame(dark);self.frame(dark);result=self.frame(dark)
        self.assertEqual(result['event'],'recognized')
        decoded=np.array(Image.open(BytesIO(jpeg(dark))))
        self.assertEqual(result['_card_crop'],warp_card(decoded,CORNERS))
        self.assertGreaterEqual(self.processor.stats['brightening_retries'],3)

    def test_clearest_frame_is_selected_not_last_frame(self):
        clear=card_frame();glare=clear.copy();glare[100:360,170:177]=255
        self.frame(clear);self.frame(clear);result=self.frame(glare)
        self.assertEqual(result['event'],'recognized')
        self.assertEqual(result['_card_crop'],warp_card(np.array(Image.open(BytesIO(jpeg(clear)))),CORNERS))

    def test_replacement_never_uses_previous_card_crop(self):
        self.lock();replacement=card_frame();replacement[:,:,0]=230
        self.processor.choose_matches=lambda crop,quality:[dict(card_id='b',similarity=.95)]
        self.frame(replacement);self.frame(replacement);result=self.frame(replacement)
        self.assertEqual(result['event'],'recognized')
        self.assertEqual(result['_card_crop'],warp_card(np.array(Image.open(BytesIO(jpeg(replacement)))),CORNERS))

    def test_accepted_card_retains_identity_without_duplicate_results(self):
        original=self.lock()
        for _ in range(8):
            result=self.frame(card_frame())
            self.assertEqual(result['event'],'observing')
            self.assertEqual(self.state.recognition_id,original['recognition_id'])

    def test_enhancement_disagreement_with_small_margin_is_rejected(self):
        crop=warp_card((card_frame()*.25).astype('uint8'),CORNERS)
        calls=[]
        def search(data):
            calls.append(data)
            return [dict(card_id='a' if len(calls)==1 else 'b',similarity=.70 if len(calls)==1 else .82),dict(card_id='c',similarity=.69 if len(calls)==1 else .815)]
        self.processor.search=search
        quality=assess(rectify_card((card_frame()*.25).astype('uint8'),CORNERS,(252,352)))
        self.assertEqual(LiveProcessor.choose_matches(self.processor,crop,quality),[])
        self.assertEqual(len(calls),2)

    def prepare_uncertain_native_crop(self):
        from unittest.mock import Mock
        self.state.input_mode='card_crop_v1'
        self.processor.session.cropper.detect_live=Mock(side_effect=AssertionError('Phone already cropped this card'))
        self.processor.reader=object()
        self.processor.identify_crop=Mock(return_value=dict(status='ambiguous',selected_card_id=None,
            matches=MATCHES,visual_matches=MATCHES,ocr={'status':'unreadable'},timing_ms={}))
        return rectify_card(card_frame(),CORNERS)

    def test_native_crop_skips_detector_and_retries_are_bounded(self):
        rgb=self.prepare_uncertain_native_crop()
        self.frame(rgb);self.frame(rgb);result=self.frame(rgb)
        self.assertEqual(result['event'],'confirmation')
        self.assertTrue(result['retrying'])
        self.assertEqual(result['_card_crop'],jpeg(rgb))
        for _ in range(5): self.assertEqual(self.frame(rgb)['event'],'observing')
        self.processor.identify_crop.assert_called_once()
        pending=[result]+[r for r in (self.frame(rgb) for _ in range(50)) if r['event']=='confirmation']
        self.assertEqual(len(pending),3)
        self.assertEqual(len({r['request_id'] for r in pending}),1)
        self.assertFalse(pending[-1]['retrying'])
        self.assertEqual(self.processor.identify_crop.call_count,3)
        self.processor.session.cropper.detect_live.assert_not_called()

    def test_uncertain_card_automatically_saves_after_clearer_evidence(self):
        rgb=self.prepare_uncertain_native_crop()
        self.frame(rgb);self.frame(rgb);result=self.frame(rgb)
        self.assertEqual(result['event'],'confirmation')
        self.processor.identify_crop.return_value.update(status='ocr_supported',selected_card_id='a')
        results=[r for r in (self.frame(rgb) for _ in range(20))]
        recognized=[r for r in results if r['event']=='recognized']
        self.assertEqual(len(recognized),1)
        self.assertTrue(recognized[0]['verified'])
        self.assertEqual(recognized[0]['hint'],'')
        self.assertEqual(self.processor.identify_crop.call_count,2)
        self.assertEqual(self.state.uncertain_attempts,0)

    def test_replacement_can_scan_after_uncertain_card_exhausts_retries(self):
        rgb=self.prepare_uncertain_native_crop()
        for _ in range(35):self.frame(rgb)
        self.assertEqual(self.processor.identify_crop.call_count,3)
        self.processor.identify_crop.return_value.update(status='ocr_supported',selected_card_id='a')
        rgb[:,:,0]=230
        results=[r for r in (self.frame(rgb) for _ in range(5))]
        self.assertEqual(sum(r['event']=='recognized' for r in results),1)

    def test_improved_quality_retries_same_card_without_removal(self):
        from tcg_scanner.quality import Quality
        rgb=self.prepare_uncertain_native_crop()
        with patch('tcg_scanner.live.assess', return_value=Quality(80,100,0)):
            for _ in range(35):self.frame(rgb)
        self.assertEqual(self.processor.identify_crop.call_count,3)
        self.processor.identify_crop.return_value.update(status='ocr_supported',selected_card_id='a')
        with patch('tcg_scanner.live.assess', return_value=Quality(140,250,0)):
            results=[r for r in (self.frame(rgb) for _ in range(5))]
        self.assertEqual(sum(r['event']=='recognized' for r in results),1)

if __name__=='__main__':unittest.main()
