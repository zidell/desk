"""Stop-panel regression checks without starting a desktop control session."""
import ast
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


class StopPanelTests(unittest.TestCase):
    def setUp(self):
        source=Path(__file__).with_name("desk_linux.py")
        tree=ast.parse(source.read_text())
        session=next(node for node in tree.body if isinstance(node,ast.ClassDef) and node.name=="Session")
        methods=[node for node in session.body if isinstance(node,ast.FunctionDef) and node.name in {"stop","tick","hide_stopped","capture"}]
        subset=ast.Module(body=[ast.ClassDef(name="Session",bases=[],keywords=[],body=methods,decorator_list=[])],type_ignores=[])
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.state=Path(self.temp.name)
        namespace={"STATE":self.state,"CLICK":Mock(),"time":Mock()}
        exec(compile(ast.fix_missing_locations(subset),str(source),"exec"),namespace)
        self.session=namespace["Session"]()
        s=self.session
        s.panel=Mock();s.marks=SimpleNamespace(target=True,elements=[1],ripple=True,hide=Mock())
        s.backend=Mock();s.active=True;s.show_message=Mock();s.pump=Mock();s.place_panel=Mock();s.popup_pending=False
        s.backend.snapshot.return_value={"popups":False}

    def test_button_stop_hides_and_preserves_stop(self):
        s=self.session;s.stop()
        self.assertTrue((self.state/"stopped").exists())
        s.panel.hide.assert_called_once();s.marks.hide.assert_called_once()
        self.assertIsNone(s.marks.target);self.assertEqual(s.marks.elements,[])

    def test_physical_stop_hides_on_tick(self):
        (self.state/"stopped").write_text("physical ESC")
        self.session.tick()
        self.session.panel.hide.assert_called_once()
        self.session.backend.heartbeat.assert_not_called()
        self.assertTrue((self.state/"stopped").exists())

    def test_capture_finally_cannot_reshow_stopped_panel(self):
        def stop_during_capture():
            (self.state/"stopped").write_text("physical ESC")
            raise RuntimeError("capture interrupted")
        self.session.backend.capture.side_effect=stop_during_capture
        with self.assertRaisesRegex(RuntimeError,"interrupted"):self.session.capture()
        self.session.panel.show.assert_not_called()
        self.assertTrue((self.state/"stopped").exists())


if __name__=="__main__":unittest.main()
