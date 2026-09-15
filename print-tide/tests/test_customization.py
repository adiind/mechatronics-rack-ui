import copy, unittest
from light_studio import themes
from light_studio.layout import validate_settings, DEFAULTS
from light_studio.renderer import render_rope, compose_rope, render_accent

class Customization(unittest.TestCase):
    def test_library_and_validation(self):
        self.assertGreaterEqual(len(themes.names()),20)
        for n in themes.names():
            self.assertEqual(set(themes.palette(n)),set(themes.PALETTE_KEYS))
        for bad in [None, [], {'bogus':[1,2,3]}, {'water':[True,2,3]}, {'error':[300,0,0]}, {'idle':'#fff'}]:
            with self.assertRaises(ValueError):validate_settings({'palette_overrides':bad})
        with self.assertRaises(ValueError):validate_settings({'printing_motion':'chaos'})
    def test_custom_palette_does_not_mutate_base(self):
        original=copy.deepcopy(themes.palette('forest'))
        p=themes.palette('forest',{'water':[230,80,190],'rest':[10,20,30]})
        self.assertEqual(p['water'],(230,80,190));self.assertNotEqual(p['deep'],original['deep'])
        self.assertEqual(themes.palette('forest'),original)
    def test_empty_extensions_preserve_frames(self):
        for state in ['idle','preparing','printing','paused','error','finished','stopped','offline','unknown']:
            for t in [0,0.7,2.5]:
                self.assertEqual(render_rope(state,42,t,settings={'theme':'classic'}),render_rope(state,42,t,settings={'theme':'classic','palette_overrides':{},'printing_motion':'rain'}))
    def test_all_styles_geometry_and_still(self):
        for name in themes.names():
            for motion in ['rain','flow','comet','still']:
                opts={'theme':name,'printing_motion':motion,'palette_overrides':{'water':[210,80,170]},'ripples':False}
                for pct in [0,25,50,100]:
                    frame=compose_rope(render_rope('printing',pct,0.7,settings=opts),render_accent({},0.7))
                    self.assertEqual(len(frame),100);self.assertEqual(frame[:30],[[0,0,0]]*30)
                    self.assertTrue(all(type(c)==int and 0<=c<=255 for p in frame for c in p))
                still={**opts,'reduced_motion':True}
                self.assertEqual(render_rope('printing',50,0,settings=still),render_rope('printing',50,9,settings=still))
    def test_styles_never_move_progress_boundary_or_error_signature(self):
        for pct in [0,25,50,100]:
            still=render_rope('printing',pct,0,settings={'theme':'classic','printing_motion':'still'})
            fill=round(pct/100*60)
            for motion in ['flow','comet']:
                f=render_rope('printing',pct,1.7,settings={'theme':'classic','printing_motion':motion})
                self.assertEqual(f[fill:],still[fill:])
        for style in ['flow','comet','still']:
            self.assertEqual(render_rope('error',50,1,settings={'printing_motion':style}),render_rope('error',50,1))
    def test_new_motion_presets_fit_controller_budget(self):
        from light_studio import transport as tp
        from light_studio.studio import TICK_HZ
        for name in themes.names():
            for motion in ['flow','comet','still']:
                frames=[render_rope('printing',42,k/TICK_HZ,2,{'theme':name,'brightness':100,'printing_motion':motion,'palette_overrides':{'water':[230,80,190]}},speed='ludicrous') for k in range(120)]
                changes=[len(tp.diff_runs(a,b)) for a,b in zip(frames,frames[1:])]
                self.assertLessEqual(sum(changes)/len(changes),tp.NODE_RATE/TICK_HZ*.75,(name,motion))
                self.assertLessEqual(max(changes),tp.MAX_MSGS_PER_NODE_TICK,(name,motion))
