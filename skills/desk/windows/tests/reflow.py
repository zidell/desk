"""Visible regression tests. Uses only desk screen/UIA reads and real pointer events.

All captures, stop state and compiled fixtures are isolated from the user's desk session.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from PIL import Image, ImageDraw

ROOT=Path(__file__).resolve().parents[2]
TEMP=tempfile.TemporaryDirectory(prefix="desk-reflow-",ignore_cleanup_errors=True)
os.environ["DESK_STATE"]=TEMP.name
os.environ["DESK_REFLOW_SESSION"]=Path(TEMP.name).name
sys.path.insert(0,str(ROOT/"windows"))
import desk_windows as w
C=w.CLICK


class Evidence(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.state=Path(self.temp.name)
        self.image=self.state/"source.png"
        im=Image.new("RGB",(400,250),"white");ImageDraw.Draw(im).rectangle((60,60,180,110),fill="blue");im.save(self.image)
        C.observe(self.state,self.image,[0,0,400,250],"window",[{"box":[60,60,120,50]}])

    def tearDown(self):self.temp.cleanup()

    def test_region_window_and_invalidation(self):
        for point,window in [([500,90],"window"),([90,90],"other")]:
            with self.assertRaises(C.StaleClick):C.prepare(self.state,point,window)
        C.invalidate(self.state)
        with self.assertRaises(C.StaleClick):C.prepare(self.state,[90,90],"window")

    def test_immutable_observation_and_before_reflow(self):
        plan=C.prepare(self.state,[90,90],"window")
        Image.new("RGB",(400,250),"red").save(self.image)
        with self.assertRaises(C.StaleClick):C.verify_initial(plan,self.image,[0,0,400,250],None)
        self.assertTrue((self.state/"observation.json").exists())

    def test_one_pixel_move_and_identity_replacement(self):
        plan=C.prepare(self.state,[90,90],"window")
        old={"id":"old","role":"Button","name":"same","box":[60,60,120,50],"enabled":True}
        for changed in [{**old,"box":[61,60,120,50]},{**old,"id":"new"}]:
            with self.assertRaises(C.StaleClick):C.finalize(self.state,plan,self.image,[0,0,400,250],old,changed)
            C.verify_initial({**plan,"observedBinding":old},self.image,[0,0,400,250],changed)
        # A node mismatch is only acceptable while the observed patch is intact.
        Image.new("RGB",(400,250),"red").save(self.image)
        with self.assertRaises(C.StaleClick):C.verify_initial({**plan,"observedBinding":old},self.image,[0,0,400,250],{**old,"id":"new"})

    def test_input_epoch_and_unknown_paint(self):
        plan=C.prepare(self.state,[90,90],"window")
        (self.state/"user-input").write_text("physical-input")
        with self.assertRaises(C.StaleClick):C.finalize(self.state,plan,self.image,[0,0,400,250],None,None)
        (self.state/"user-input").unlink()
        Image.new("RGB",(400,250),"red").save(self.image)
        with self.assertRaises(C.StaleClick):C.finalize(self.state,plan,self.image,[0,0,400,250],None,None)

    def test_unrelated_animation_allowed(self):
        plan=C.prepare(self.state,[90,90],"window")
        im=Image.open(self.image);ImageDraw.Draw(im).rectangle((350,200,399,249),fill="red");im.save(self.image)
        C.verify_initial(plan,self.image,[0,0,400,250],None)
        C.finalize(self.state,plan,self.image,[0,0,400,250],None,None)


class NativeReflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        w.STATE.mkdir(exist_ok=True);(w.STATE/"shots").mkdir(exist_ok=True)
        w.build();w.QUIET=True
        framework=Path(os.environ["WINDIR"])/"Microsoft.NET"/"Framework64"/"v4.0.30319"
        cls.exe=w.STATE/"ReflowFixture.exe"
        subprocess.run([str(framework/"csc.exe"),"/nologo","/target:winexe","/out:"+str(cls.exe),"/r:System.Drawing.dll","/r:System.Windows.Forms.dll",str(ROOT/"windows"/"tests"/"ReflowFixture.cs")],check=True)
        w.begin("리플로우 클릭 취소 회귀 검증")

    @classmethod
    def tearDownClass(cls):
        w.write("panel-close","1")
        until=time.monotonic()+3
        while w.panel_alive() and time.monotonic()<until:time.sleep(.05)

    def scenario(self,mode,reject,final=False,node_mismatch=False):
        os.environ["DESK_REFLOW_MODE"]=mode
        w.native("open",self.exe)
        proc_pid=None
        try:
            until=time.monotonic()+4
            while True:
                w.check_stop()
                candidates=[r for r in w.native("windows",structured=True) if r["title"]=="desk reflow "+mode+" "+os.environ["DESK_REFLOW_SESSION"]]
                if candidates:
                    proc_pid=candidates[0]["pid"]
                    if w.window()["pid"]==proc_pid:break
                    w.native("activate",candidates[0]["hwnd"])
                if time.monotonic()>until:self.fail("fixture did not come forward")
                time.sleep(.05)
            time.sleep(.2)
            win=w.window();b=win["box"]
            w.native("action","move",win["hwnd"],0,win["pid"],w.boxarg(b),b[0]+500,b[1]+80)
            time.sleep(.1)
            raw=w.STATE/"fixture.png";w.capture(raw,b)
            rows=w.native("uia",structured=True)["elements"]
            # Paint panel has no accessible action; use its known visible screenshot rectangle.
            target=next((e for e in rows if e["name"]=="TARGET" and e["role"]=="Button"),None)
            if target:
                box=target["box"]
            else:
                # Resolve the visible blue canvas from the captured pixels,
                # rather than assuming a DPI-dependent window decoration size.
                with Image.open(raw) as image:
                    mask=image.convert("RGB")
                    pixels=mask.load()
                    points=[(xx,yy) for yy in range(mask.height) for xx in range(mask.width) if pixels[xx,yy]==(65,105,225)]
                self.assertTrue(points,"visible blue canvas not found")
                left=min(p[0] for p in points);top=min(p[1] for p in points)
                box=[b[0]+left,b[1]+top,max(p[0] for p in points)-left+1,max(p[1] for p in points)-top+1]
            x,y=int(box[0]+box[2]/2),int(box[1]+box[3]/2)
            if node_mismatch:
                rows=[{**e,"id":"enumerated-wrapper"} if e is target else e for e in rows]
            C.observe(w.STATE,raw,b,win,[{"box":box}],rows);w.remember(win)
            if final:
                plan=C.prepare(w.STATE,[x,y],win,box)
                binding=w.native("click-bind",x,y,structured=True)
                C.finalize(w.STATE,plan,raw,b,binding,binding)
            if mode=="before":time.sleep(4.1)
            try:
                if final:w.native("action","click",win["hwnd"],0,win["pid"],w.boxarg(b),x,y)
                else:w.action("dclick" if mode=="double" else "click",[x,y],box,observed_box=box)
                self.assertFalse(reject,"stale click was accepted")
            except w.DeskError as e:
                if not reject:raise
                self.assertEqual(e.code,6,str(e))
            labels=[e["name"] for e in w.native("uia",structured=True)["elements"]]
            expected="CLICKS 0 TRAP 0" if reject else "CLICKS "+("2" if mode=="double" else "1")+" TRAP 0"
            self.assertIn(expected,labels)
            if not reject:
                with self.assertRaises(C.StaleClick):C.prepare(w.STATE,[x,y],win,box)
        finally:
            # Never resume after a real user stop. Close only our fixture through desk keys.
            if not (w.STATE/"stopped").exists() and w.window()["pid"]==proc_pid:
                w.remember(w.window());w.action("key",["alt+f4"])

    def test_01_before_reflow(self):self.scenario("before",True)
    def test_02_preview_reflow(self):self.scenario("move",True)
    def test_03_same_name_same_box_replaced(self):self.scenario("replace",True)
    def test_04_canvas_changes(self):self.scenario("paint",True)
    def test_05_unrelated_load(self):self.scenario("unrelated",False)
    def test_06_stable_click(self):self.scenario("stable",False)
    def test_07_double_click(self):self.scenario("double",False)
    def test_10_same_pixels_different_enumerated_node(self):self.scenario("stable",False,node_mismatch=True)
    def test_09_native_final_guard(self):self.scenario("before",True,final=True)

    def test_08_chrome_webpage_reflow(self):
        chrome=Path(os.environ["ProgramFiles"])/"Google"/"Chrome"/"Application"/"chrome.exe"
        if not chrome.exists():self.skipTest("Chrome not installed")
        title="desk-web-reflow-"+w.STATE.name
        page=w.STATE/"reflow.html"
        page.write_text('''<!doctype html><meta charset="utf-8"><title>'''+title+'''</title>
<style>body{font:24px sans-serif}button{position:absolute;left:80px;top:160px;width:240px;height:64px}#status{position:absolute;top:400px}</style>
<h1>Visible local reflow fixture</h1><button id="target">WEB TARGET</button><div id="status">CLICKS 0 TRAP 0</div>
<script>
let armed=false,once=false,clicks=0,traps=0;
window.addEventListener('mousemove',e=>{if(e.target.id!=='target')armed=true});
target.addEventListener('click',()=>{clicks++;statusText()});
function statusText(){document.getElementById('status').textContent='CLICKS '+clicks+' TRAP '+traps}
target.addEventListener('mouseenter',()=>{if(!armed||once)return;once=true;setTimeout(()=>{
target.style.top='260px';const trap=document.createElement('button');trap.textContent='WEB TRAP';trap.onclick=()=>{traps++;statusText()};document.body.append(trap);
},250)});
</script>''',encoding="utf-8")
        proc=subprocess.Popen([str(chrome),"--user-data-dir="+str(w.STATE/"chrome-profile"),"--no-first-run","--no-default-browser-check","--force-renderer-accessibility","--app="+page.as_uri(),"--window-size=900,700","--window-position=180,120"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        browser=None
        try:
            until=time.monotonic()+12
            while time.monotonic()<until:
                w.check_stop()
                hits=[r for r in w.native("windows",structured=True) if title in r["title"]]
                if hits:
                    browser=hits[0]
                    if w.window()["hwnd"]!=browser["hwnd"]:w.native("activate",browser["hwnd"])
                    break
                time.sleep(.2)
            self.assertIsNotNone(browser,"Chrome fixture did not open")
            win=w.window();b=win["box"]
            w.native("action","move",win["hwnd"],0,win["pid"],w.boxarg(b),b[0]+650,b[1]+130)
            time.sleep(.4)
            raw=w.STATE/"web.png";w.capture(raw,b)
            rows=w.native("uia",structured=True)["elements"]
            target=next(e for e in rows if e["name"]=="WEB TARGET" and e["role"]=="Button")
            box=target["box"];x,y=box[0]+box[2]//2,box[1]+box[3]//2
            C.observe(w.STATE,raw,b,win,[{"box":box}],rows);w.remember(win)
            with self.assertRaises(w.DeskError) as caught:w.action("click",[x,y],box,observed_box=box)
            self.assertEqual(caught.exception.code,6)
            names=[e["name"] for e in w.native("uia",structured=True)["elements"]]
            self.assertIn("CLICKS 0 TRAP 0",names)
            self.assertIn("WEB TRAP",names)
        finally:
            if browser and not (w.STATE/"stopped").exists() and w.window()["hwnd"]==browser["hwnd"]:
                w.remember(w.window());w.action("key",["alt+f4"])
            try:proc.wait(timeout=5)
            except subprocess.TimeoutExpired:pass


if __name__=="__main__":unittest.main(verbosity=2,failfast=True)
