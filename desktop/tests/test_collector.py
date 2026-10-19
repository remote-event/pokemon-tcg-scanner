import unittest
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock
from tcg_scanner.collector import CatalogMatcher, parse_number, padded_box
from tcg_scanner.config import Config


class CollectorMatchingTests(unittest.TestCase):
    def setUp(self):
        cards = {i:dict(card_id=i,name='Same artwork',set_id=s,set_name=s,collector_number=n,source_url='')
                 for i,s,n in [('a','s1','001'),('b','s2','001'),('c','s3','002'),('missing','s4','TG01')]}
        self.matcher = CatalogMatcher(cards, {'s1':{'cardCount':{'official':100}},'s2':{'cardCount':{'official':200}}},Config())
    def visual(self, **scores): return [dict(card_id=i,similarity=s) for i,s in scores.items()]
    def ocr(self, number='1', total=None, status='recognized'): return dict(status=status,number=number,total=total)
    def test_formatting(self):
        for text, expected in [('001 / 165',('1','165')),('TG01/TG30',('TG1','TG30')),('SV107/SV122',('SV107','SV122')),('015a',('15A',None)),('００１／１６５',('1','165')),('1★',('1★',None))]:
            self.assertEqual(parse_number(text), expected)
        for text in ['HP only','123/','a/b','1/2/3']: self.assertIsNone(parse_number(text))
    def test_full_catalog_lookup_outside_top_list(self):
        visual=self.visual(a=.91,c=.85)
        result=self.matcher.combine(visual,self.ocr(total='200'),visual+self.visual(b=.90))
        self.assertEqual(result['selected_card_id'],'b')
        self.assertEqual(result['ocr_matches'],['b'])
        self.assertEqual(result['matches'][0]['similarity'],.90)
        self.assertEqual(result['matches'][0]['embedding_rank'],3)
    def test_same_number_across_sets_is_not_unique(self):
        result=self.matcher.combine(self.visual(a=.91,b=.909),self.ocr())
        self.assertIsNone(result['selected_card_id']);self.assertEqual(result['status'],'ambiguous')
    def test_strong_contradiction_requires_confirmation(self):
        result=self.matcher.combine(self.visual(c=.97,a=.60),self.ocr())
        self.assertEqual(result['status'],'conflict');self.assertIsNone(result['selected_card_id'])
    def test_unknown_number_does_not_silently_auto_select(self):
        self.assertIsNone(self.matcher.combine(self.visual(a=.99,c=.3),self.ocr('999'))['selected_card_id'])
    def test_low_confidence_fallback_and_visual_tie(self):
        self.assertEqual(self.matcher.combine(self.visual(a=.95,c=.5),self.ocr(status='unreadable'))['selected_card_id'],'a')
        self.assertIsNone(self.matcher.combine(self.visual(a=.95,b=.949),self.ocr(status='error'))['selected_card_id'])
    def test_card_without_embedding_is_returned_for_confirmation(self):
        result=self.matcher.combine(self.visual(a=.95),self.ocr('TG1'))
        self.assertEqual(result['ocr_matches'],['missing']);self.assertIsNone(result['selected_card_id'])
        self.assertIsNone(result['matches'][0]['similarity'])
    def test_crop_padding(self):
        self.assertEqual(padded_box([0,2,30,12],32,14,.12),(0,0,32,14))
        with self.assertRaises(ValueError): padded_box([float('nan'),0,3,3],32,14,.12)

    def test_branches_overlap_and_unchanged_crop_is_cached(self):
        from tcg_scanner.live import LiveProcessor
        processor=LiveProcessor.__new__(LiveProcessor)
        processor.config=Config()
        processor.combined_cache=OrderedDict()
        processor.stats={'cache_hits':0}
        processor.matcher=self.matcher
        began=Event(); visual_done=Event()
        def read(_):
            began.set()
            self.assertTrue(visual_done.wait(2), 'Visual branch must run while OCR is active')
            return {'status':'unreadable'}
        def search(_):
            self.assertTrue(began.wait(2))
            processor.visual_timing={'embedding_ms':1.,'faiss_ms':.1}
            visual_done.set()
            return self.visual(a=.95,c=.6)
        processor.reader=Mock(read=Mock(side_effect=read))
        processor.search=Mock(side_effect=search)
        with ThreadPoolExecutor(1) as pool:
            processor.number_pool=pool
            first=processor.identify_crop(b'unchanged card')
            repeat=processor.identify_crop(b'unchanged card')
        self.assertEqual(first['selected_card_id'],'a')
        self.assertTrue(repeat['cache_hit'])
        processor.reader.read.assert_called_once()
        processor.search.assert_called_once()


if __name__ == '__main__': unittest.main()
