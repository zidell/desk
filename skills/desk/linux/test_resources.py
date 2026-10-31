import os
os.environ['QT_QPA_PLATFORM']='offscreen'
import tempfile
import time
import unittest
from pathlib import Path
from resources import prune_shots,rotate_log

class ResourceTests(unittest.TestCase):
    def test_cache_bounds_keep_active_evidence_and_unrelated_files(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for i in range(20):
                p=root/(f'{i:032x}.png');p.write_bytes(b'x'*10);os.utime(p,(i,i))
            other=root/'user.png';other.write_bytes(b'user')
            active=root/(f'{0:032x}.png')
            prune_shots(root,[active],max_bytes=40,max_age=1000,now=100,keep_recent=0)
            self.assertTrue(active.exists());self.assertTrue(other.exists())
            self.assertLessEqual(sum(p.stat().st_size for p in root.glob('*.png')),44)
    def test_old_shots_expire(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/('a'*32+'.png');p.write_bytes(b'x');os.utime(p,(0,0))
            prune_shots(d,now=100,max_age=10,keep_recent=0)
            self.assertFalse(p.exists())
    def test_log_rotation_keeps_one_backup(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'actions.log'
            for i in range(20):
                p.write_text('x'*20);rotate_log(p,10)
            self.assertEqual(len(list(Path(d).iterdir())),1)
            self.assertEqual(p.with_name('actions.log.1').stat().st_size,20)
    def test_warning_failures_do_not_accumulate(self):
        from desk_linux import Session
        class Failed:
            def request(self,*a,**kw):raise TimeoutError()
        s=object.__new__(Session);s.accessibility_worker=Failed();s.pump=lambda *_:None;s.warnings=[]
        for _ in range(10000):self.assertEqual(s.accessibility({}),[])
        self.assertEqual(len(s.warnings),1)
    def test_hidden_marks_have_no_animation_timer(self):
        from PyQt6.QtWidgets import QApplication
        from widget import Marks
        import json
        app=QApplication.instance() or QApplication([])
        m=Marks(json.loads((Path(__file__).parent.parent/'widget.json').read_text()))
        self.assertFalse(m.timer.isActive())
        for _ in range(1000):
            m.show();self.assertTrue(m.timer.isActive())
            m.hide();self.assertFalse(m.timer.isActive())
        m.deleteLater();app.processEvents()

if __name__=='__main__':unittest.main()
