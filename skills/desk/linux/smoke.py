#!/usr/bin/env python3
"""Screen evidence regression tests and opt-in real, disposable desktop UI tests."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT=Path(__file__).resolve().parent.parent
sys.pycache_prefix=str(Path.home()/".cache"/"desk"/"test-pycache")
spec=importlib.util.spec_from_file_location("evidence",ROOT/"desk-click.py")
E=importlib.util.module_from_spec(spec);spec.loader.exec_module(E)


class Evidence(unittest.TestCase):
    def setUp(self):
        from PIL import Image,ImageDraw
        self.directory=tempfile.TemporaryDirectory();self.state=Path(self.directory.name)
        self.image=self.state/"frame.png"
        im=Image.new("RGB",(400,200),"#334455");draw=ImageDraw.Draw(im);draw.rectangle((30,30,100,70),fill="white");draw.rectangle((200,100,230,130),fill="white");im.save(self.image)
        (self.state/"user-input").write_text("0")
        self.window=["window",100,[0,0,400,200]]
        self.binding={"id":"button1","role":"AXButton","name":"OK","box":[30,30,70,40],"enabled":True}
        E.observe(self.state,self.image,[0,0,400,200],self.window,[{"box":[30,30,70,40]}],[self.binding],epoch="0")

    def tearDown(self):self.directory.cleanup()
    def test_observation_is_immutable(self):
        from PIL import Image
        observed=json.loads((self.state/"observation.json").read_text())
        Image.new("RGB",(400,200),"red").save(self.image)
        self.assertNotEqual(Path(observed["image"]).read_bytes(),self.image.read_bytes())
    def test_unobserved_window_refused(self):
        with self.assertRaises(E.StaleClick):E.prepare(self.state,[50,50],["other",100,[0,0,400,200]])
    def test_outside_region_refused(self):
        with self.assertRaises(E.StaleClick):E.prepare(self.state,[500,50],self.window)
    def test_physical_input_epoch_refused(self):
        (self.state/"user-input").write_text("1")
        with self.assertRaises(E.StaleClick):E.prepare(self.state,[50,50],self.window)
    def test_same_pixels_allow_enumeration_node_difference(self):
        token=E.prepare(self.state,[50,50],self.window)
        E.verify_initial(token,self.image,[0,0,400,200],{**self.binding,"id":"hit-test-node"})
    def test_replaced_button_is_refused_at_final_check(self):
        token=E.prepare(self.state,[50,50],self.window)
        with self.assertRaises(E.StaleClick):E.finalize(self.state,token,self.image,[0,0,400,200],self.binding,{**self.binding,"id":"replacement"})
    def test_changed_canvas_is_refused(self):
        from PIL import Image
        token=E.prepare(self.state,[200,100],self.window)
        Image.new("RGB",(400,200),"red").save(self.image)
        with self.assertRaises(E.StaleClick):E.verify_initial(token,self.image,[0,0,400,200],None)
    def test_fractional_dpi_crop_matches_full_capture(self):
        from PIL import Image
        from unittest.mock import Mock,patch
        import desk_linux
        (self.state/"shots").mkdir()
        full=Image.effect_noise((512,288),80).convert("RGB");full_path=self.state/"full.png";full.save(full_path)
        session=object.__new__(desk_linux.Session)
        session.active=False
        session.observation=None
        session.panel=Mock();session.marks=Mock();session.marks.alive.return_value=False
        session.popup_pending=False
        session.pump=lambda *_:None;session.place_panel=lambda:None
        session.backend=Mock();session.backend.capture.return_value=(full.copy(),[0,0,366,206])
        session.backend.snapshot.return_value={"popups":[]}
        with patch.object(desk_linux,"STATE",self.state):
            path,bounds=session.capture([70.3,31.7,200.8,150.1])
        E.observe(self.state,path,bounds,self.window,epoch="0")
        token=E.prepare(self.state,[150,110],self.window,[140,100,40,30])
        E.verify_initial(token,full_path,[0,0,366,206],None)
    def test_ocr_literal_quote_keeps_tsv_rows_separate(self):
        from unittest.mock import patch
        import desk_linux
        header="level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        tsv=header+'5\t1\t1\t1\t1\t1\t10\t10\t15\t10\t96\t"\n'+'5\t1\t2\t1\t1\t1\t10\t30\t35\t10\t96\t다슈\n'
        session=object.__new__(desk_linux.Session)
        session.pump=lambda *_:None;session.accessibility=lambda _:[]
        session.warnings=[];session.occluded_panel_box=None
        with patch.object(desk_linux,"STATE",self.state),patch.object(desk_linux,"run",side_effect=["languages\neng\nkor\n",tsv]):
            elements,_=session.recognize(self.image,[0,0,400,200],{})
        self.assertEqual([e["name"] for e in elements],['"','다슈'])
        self.assertEqual(len((self.state/"ocr.tsv").read_text().splitlines()),2)
    def test_malformed_recognition_rectangles_are_ignored(self):
        import desk_linux
        ocr=self.state/"bad.tsv"
        ocr.write_text("5\tbad\nnan,0,20,20\tbad\n0,0,-1,4\tbad\n0,0,20,20\tvalid\n")
        self.assertEqual([e["name"] for e in desk_linux.FUSION.fuse(ocr,None,None)],["valid"])
    def test_panel_has_opaque_body_and_red_border(self):
        script='''import sys,json
from pathlib import Path
from PyQt6 import QtWidgets,QtGui,QtCore
sys.path.insert(0,sys.argv[1])
from widget import Panel
style=json.loads(Path(sys.argv[2]).read_text())
app=QtWidgets.QApplication([])
panel=Panel(style,QtCore.Qt.WindowType.FramelessWindowHint)
panel.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
panel.resize(style['width'],style['height'])
image=QtGui.QImage(panel.size(),QtGui.QImage.Format.Format_ARGB32_Premultiplied)
image.fill(QtCore.Qt.GlobalColor.transparent)
panel.render(image)
assert image.pixelColor(280,32).getRgb()==(20,20,20,255)
assert image.pixelColor(280,1).getRgb()==(219,43,43,255)
assert image.pixelColor(0,0).alpha()==0
'''
        environment={**os.environ,"QT_QPA_PLATFORM":"offscreen","PYTHONPYCACHEPREFIX":str(Path.home()/".cache/desk/test-pycache")}
        environment.pop("QT_WAYLAND_SHELL_INTEGRATION",None)
        result=subprocess.run([sys.executable,"-c",script,str(ROOT/"linux"),str(ROOT/"widget.json")],env=environment,capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)


def ui(existing=False):
    # A compositor/X server must be explicit. X11 tests normally use a nested
    # Xephyr DISPLAY; Wayland tests use the monitor approved through the portal.
    directory=Path(os.environ.get("DESK_STATE",Path.home()/".cache"/"desk"/("ui-test-"+time.strftime("%Y%m%d-%H%M%S"))))
    directory.mkdir(parents=True,exist_ok=True)
    env={**os.environ,"DESK_STATE":str(directory),"DESK_TEST_STATE":str(directory),"QT_LINUX_ACCESSIBILITY_ALWAYS_ON":"1"}
    if env.get("XDG_SESSION_TYPE")=="x11":env["DESK_FIXTURE_X11_EWMH"]="1"
    def command(*args,code=0):
        result=subprocess.run([sys.executable,str(ROOT/"desk.py"),*args],env=env,capture_output=True,text=True,timeout=660)
        if result.returncode==3:raise RuntimeError("사용자가 중지했습니다. 더 테스트하지 않습니다.")
        assert result.returncode in (code if isinstance(code,tuple) else (code,)),(args,result.returncode,result.stdout,result.stderr)
        return (result.stdout,result.returncode) if isinstance(code,tuple) else result.stdout
    def events():
        p=directory/"fixture.jsonl"
        rows=[json.loads(s) for s in p.read_text().splitlines()] if p.exists() else []
        return [r for r in rows if fixture and r.get("pid")==fixture.pid]
    def change(value):
        (directory/"fixture-control").write_text(value)
        deadline=time.monotonic()+3
        while (directory/"fixture-control").exists():
            if time.monotonic()>deadline:raise RuntimeError("fixture control timeout")
            time.sleep(.03)
    passed=[];fixture=None
    try:
        if not existing:command("begin","desk Linux UI 검증")
        (directory/"fixture-geometry.json").unlink(missing_ok=True)
        fixture=subprocess.Popen([sys.executable,str(ROOT/"linux"/"fixture.py")],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        for _ in range(100):
            if (directory/"fixture-geometry.json").exists():break
            if fixture.poll() is not None:raise RuntimeError(fixture.stderr.read().decode())
            time.sleep(.05)
        if env.get("XDG_SESSION_TYPE")=="wayland":
            windows=json.loads(command("windows"));target=next(w for w in windows if w["pid"]==fixture.pid)
            output=command("open","--id",target["id"])
        else:output=command("see")
        assert "Test button" in output and "Test checkbox" in output and "Test input" in output,output
        passed.append("실제 화면 OCR·접근성 목록")
        command("click-text","Test button")
        assert any(e["kind"]=="click" for e in events())
        passed.append("실제 버튼 클릭")
        command("see")
        observed=json.loads((directory/"observation.json").read_text())
        button=next(b for b in observed["bindings"] if b["name"]=="Test button")
        x,y,w,h=button["box"]
        command("rclick",str(x+w/2),str(y+h/2))
        passed.append("오른쪽 클릭으로 메뉴 열기")
        menu_observation=json.loads((directory/"observation.json").read_text())
        _,menu_code=command("click-text","Test menu item","--force",code=(0,1,6))
        if menu_code:
            assert not any(e["kind"]=="menu" for e in events())
            # An OCR-only menu can change paint on hover. The rejected click
            # must send zero presses; take a new public observation and judge
            # the still-visible target again, without weakening pixel guards.
            command("see")
            fresh=json.loads((directory/"observation.json").read_text())
            assert fresh["inputEpoch"]==menu_observation["inputEpoch"],"user intervened; aborting test"
            # The disposable fixture exposes one visible menu item. Use its
            # current popup geometry when highlighted text defeats OCR, as an
            # agent would use the returned image rather than inventing text.
            front=json.loads(command("front"))
            assert len(front.get("popups",[]))==1,front
            px,py,pw,ph=front["popups"][0]["box"]
            command("click",str(px+pw/2),str(py+ph/2))
        assert any(e["kind"]=="menu" for e in events()),events()
        passed.append("같은 앱 팝업 메뉴 실제 클릭")
        command("see");command("click-text","Test checkbox")
        assert any(e["kind"]=="checkbox" and e["value"] for e in events())
        passed.append("실제 체크박스 입력")
        command("see");command("click-text","Test input")
        text="리눅스 Wayland X11 한글 입력 성공"
        command("type",text)
        assert any(e["kind"]=="text" and e["text"]==text for e in events()),events()
        passed.append("한글·영문·공백 붙여넣기")
        observed=json.loads((directory/"observation.json").read_text())
        # Use known fixture instrumentation only to locate its input box; actual
        # input and screen evidence still go through desk's public CLI.
        if env.get("XDG_SESSION_TYPE")=="wayland":
            command("see");observed=json.loads((directory/"observation.json").read_text())
            field=next(b["box"] for b in observed["bindings"] if b["name"]=="Test input")
        else:field=json.loads((directory/"fixture-geometry.json").read_text())["input"]
        x,y,w,h=field
        command("drag",str(x+8),str(y+h/2),str(x+190),str(y+h/2))
        assert any(e["kind"]=="selection" and e["selected"] for e in events()),events()
        passed.append("실제 드래그로 글자 선택")
        command("key","primary+a");command("type","두 번째 입력")
        assert any(e["kind"]=="text" and e["text"]=="두 번째 입력" for e in events())
        passed.append("논리 primary 단축키")
        command("scroll",str(x+w/2),str(y+h/2),"down","2")
        assert any(e["kind"]=="wheel" and e["dy"]<0 for e in events()),events()
        passed.append("실제 휠 스크롤 전달")
        command("key","esc")
        assert not (directory/"stopped").exists()
        passed.append("도구 ESC와 사용자 중지 구분")
        command("see")
        original=json.loads((directory/"observation.json").read_text())
        binding=next(b for b in original["bindings"] if b["name"]=="Test button")
        count=sum(e["kind"]=="click" for e in events())
        change("reflow")
        x,y,w,h=binding["box"]
        command("click",str(x+w/2),str(y+h/2),code=6)
        assert sum(e["kind"]=="click" for e in events())==count
        passed.append("리플로우 후 오래된 좌표 클릭 0회")
        command("see");command("click-text","Test button")
        assert sum(e["kind"]=="click" for e in events())==count+1
        passed.append("새 관측 후 클릭 복구")
        command("-q","key","tab")
        command("click","100","100",code=6)
        passed.append("quiet 입력 뒤 오래된 관측 거부")
        command("see")
        change("focus")
        command("key","tab",code=6)
        passed.append("창 내부 포커스 변경 거부")
        command("see")
        command("move","-9999","-9999",code=2)
        passed.append("화면 밖 커서 좌표 거부")
        command("stop")
        for args in (("say","test"),("key","tab"),("see",)):
            result=subprocess.run([sys.executable,str(ROOT/"desk.py"),*args],env=env,capture_output=True)
            assert result.returncode==3,result
        passed.append("중지 뒤 읽기·입력·설명 모두 종료 코드 3")
        command("end")
        assert (directory/"stopped").exists()
        passed.append("end 뒤 영속 중지 유지")
        result={"passed":passed,"count":len(passed),"artifacts":str(directory)}
        (directory/"result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2))
        print(json.dumps(result,ensure_ascii=False,indent=2))
    finally:
        if fixture:
            (directory/"fixture-control").write_text("close")
            try:fixture.wait(timeout=3)
            except subprocess.TimeoutExpired:fixture.terminate();fixture.wait(timeout=3)
        subprocess.run([sys.executable,str(ROOT/"desk.py"),"end"],env=env,capture_output=True,timeout=10)


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--ui",action="store_true");parser.add_argument("--existing",action="store_true")
    args=parser.parse_args()
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Evidence))
    if not result.wasSuccessful():raise SystemExit(1)
    if args.ui:ui(args.existing)
