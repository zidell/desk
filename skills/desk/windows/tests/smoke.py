"""Run with the installed Windows venv. --ui uses a visible disposable fixture for ~1 minute.

Default tests never capture/control the user's desktop. Runtime test files stay in a temp directory.
UI tests stop immediately on an unexpected app change or an actual user stop.
"""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
UI = "--ui" in sys.argv
WIDGET = "--widget" in sys.argv
if UI:
    sys.argv.remove("--ui")
if WIDGET:
    sys.argv.remove("--widget")
TEMP = tempfile.TemporaryDirectory(prefix="desk-validation-")
STATE = Path(TEMP.name)
if os.environ.get("DESK_TEST_OUTPUT"):
    STATE = Path(os.environ["DESK_TEST_OUTPUT"])
    STATE.mkdir(parents=True,exist_ok=True)
ENV = {**os.environ, "DESK_STATE": str(STATE), "DESK_USER_IDLE": "1", "DESK_HANDOFF_IDLE": "1"}
CLI = [sys.executable,"-X","utf8",str(ROOT/"desk.py")]


def cli(*args, code=0, timeout=35):
    p=subprocess.run([*CLI,*map(str,args)],env=ENV,capture_output=True,encoding="utf-8",timeout=timeout)
    allowed=code if isinstance(code,tuple) else (code,)
    if p.returncode not in allowed:
        raise AssertionError(f"{args}: expected {code}, got {p.returncode}\n{p.stdout}\n{p.stderr}")
    return p.stdout


class Headless(unittest.TestCase):
    def test_01_native_build_and_persistent_stop(self):
        cli("stop")
        cli("say","중지 상태 검증",code=3)
        self.assertTrue((STATE/"desk-native.exe").exists())
        cli("key","a",code=3)
        self.assertIn("stopped",cli("status"))
        cli("end")
        self.assertTrue((STATE/"stopped").exists())
        (STATE/"stopped").unlink()  # explicit reset of the isolated test fixture, never a real user stop

    def test_02_unicode_launcher_and_exit_codes(self):
        installed=Path.home()/".local"/"bin"/"desk.exe"
        shutil.copy2(installed,STATE/"desk.exe")
        echo=STATE/"echo.py"
        echo.write_text("import json,sys\nif sys.argv[1]=='exit':sys.exit(int(sys.argv[2]))\nprint(json.dumps(sys.argv[1:],ensure_ascii=False))\n",encoding="utf-8")
        (STATE/"desk-launch.json").write_text(json.dumps({"python":sys.executable,"entry":str(echo)}),encoding="utf-8")
        values=['한글 공백','따옴표 "hello"','100% ! & | < >','C:\\folder with spaces\\','emoji 😀']
        p=subprocess.run([str(STATE/"desk.exe"),*values],capture_output=True,encoding="utf-8",check=True)
        self.assertEqual(json.loads(p.stdout),values)
        for code in (1,3,5,6):
            p=subprocess.run([str(STATE/"desk.exe"),"exit",str(code)],capture_output=True)
            self.assertEqual(p.returncode,code)

    def test_03_local_ocr_coordinates(self):
        from PIL import Image,ImageDraw,ImageFont
        sys.path.insert(0,str(ROOT/"windows"))
        from desk_windows import ocr
        path=STATE/"ocr-fixture.png"
        im=Image.new("RGB",(700,150),"white")
        font=ImageFont.truetype(str(Path(os.environ["WINDIR"])/"Fonts"/"malgun.ttf"),40)
        ImageDraw.Draw(im).text((20,30),"검증 ABC 12345",font=font,fill="black")
        im.save(path)
        rows=ocr(path,[100,200,700,150])
        text="".join(r["text"] for r in rows).replace(" ","")
        self.assertIn("검증",text);self.assertIn("12345",text)
        self.assertTrue(all(r["box"][0]>=100 and r["box"][1]>=200 for r in rows))

    def test_04_fusion_and_ambiguity(self):
        sys.path.insert(0,str(ROOT/"windows"))
        from desk_windows import module,choose,DeskError
        o,a,d=[STATE/name for name in ("o.tsv","a.tsv","d.tsv")]
        o.write_text("10,10,50,20\t확인\n",encoding="utf-8")
        a.write_text("5,5,70,35\tAXButton\t확인\t\n",encoding="utf-8")
        d.write_text("5,5,70,35\t0.9\n",encoding="utf-8")
        rows=module("fusion_test","desk-see.py").fuse(str(o),str(a),str(d))
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]["src"],{"A","O","D"})
        for i,r in enumerate(rows,1):r["number"]=i
        duplicate={**rows[0],"number":2,"box":[100,5,70,35]}
        with self.assertRaises(DeskError) as caught:choose([rows[0],duplicate])
        self.assertEqual(caught.exception.code,1)

    def test_05_shared_scripts_compile(self):
        for path in [ROOT/"desk.py",ROOT/"desk-see.py",ROOT/"desk-ask.py",ROOT/"desk-click.py",*(ROOT/"windows").glob("*.py")]:
            compile(path.read_text(encoding="utf-8"),str(path),"exec")



@unittest.skipUnless(WIDGET,"--widget은 사용자 앱 입력 없이 위젯·인식 표시 구성 요소를 검증합니다")
class WidgetComponents(unittest.TestCase):
    def test_widget_scan_and_single_escape(self):
        framework=Path(os.environ["WINDIR"])/"Microsoft.NET"/"Framework64"/"v4.0.30319"
        exe=STATE/"widget-test.exe"
        refs=["System.Drawing.dll","System.Windows.Forms.dll","System.Web.Extensions.dll"]
        refs += [str(framework/"WPF"/n) for n in ("UIAutomationClient.dll","UIAutomationTypes.dll","WindowsBase.dll")]
        p=subprocess.run([str(framework/"csc.exe"),"/nologo","/target:exe","/main:WidgetTest","/platform:x64","/out:"+str(exe),*["/r:"+r for r in refs],str(ROOT/"windows"/"DeskNative.cs"),str(ROOT/"windows"/"tests"/"WidgetTest.cs")],capture_output=True,encoding="utf-8")
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        env={**ENV,"DESK_WIDGET":str(ROOT/"widget.json")}
        for phase in ("ESC","button"):
            p=subprocess.run([str(exe),str(STATE/("widget-"+phase)),phase],env=env,capture_output=True,encoding="utf-8",timeout=15)
            self.assertEqual(p.returncode,0,p.stdout+p.stderr)
            print(p.stdout.strip())


@unittest.skipUnless(UI,"--ui를 지정하면 보이는 검증 앱을 조작합니다")
class VisibleUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        csc=Path(os.environ["WINDIR"])/"Microsoft.NET"/"Framework64"/"v4.0.30319"/"csc.exe"
        cls.fixture=STATE/"DeskFixture.exe"
        subprocess.run([str(csc),"/nologo","/target:winexe","/out:"+str(cls.fixture),"/r:System.Drawing.dll","/r:System.Windows.Forms.dll",str(ROOT/"windows"/"tests"/"DeskFixture.cs")],check=True)
        cli("begin","desk Windows 독립 UI 검증")
        cli("-q","open",str(cls.fixture))

    @classmethod
    def tearDownClass(cls):
        # A real user stop must remain stopped and must not trigger more desktop actions.
        if not (STATE/"stopped").exists():
            cli("-q","key","alt+f4")
        cli("end")

    def test_01_text_input_and_visible_result(self):
        cli("-q","click-text","검증 입력",code=(0,5))
        cli("-q","type","한글 ABC 12345")
        cli("-q","click-text","확인")
        visible=cli("read","--window")
        self.assertIn("12345",visible)
        p=subprocess.run([str(STATE/"desk-native.exe"),"uia"],env=ENV,capture_output=True,encoding="utf-8",check=True)
        self.assertIn("RESULT: 한글 ABC 12345",[e["name"] for e in json.loads(p.stdout)["elements"]])

    def test_02_checkbox_state(self):
        cli("-q","click-text","검증 체크")
        self.assertIn("[켬]",cli("see"))

    def test_03_ambiguous_and_missing(self):
        cli("-q","click-text","동일",code=1)
        cli("find","없는문구-zxq123",code=1)

    def test_04_nochange_and_focus_guard(self):
        # First click gives the button focus; the second has no remaining visible change.
        cli("-q","click-text","변화 없음",code=(0,5))
        cli("-q","click-text","변화 없음",code=5)
        expect=json.loads((STATE/"expect.json").read_text(encoding="utf-8"))
        expect["window"]["box"][0]+=10
        (STATE/"expect.json").write_text(json.dumps(expect),encoding="utf-8")
        cli("-q","key","a",code=6)
        cli("see")

    def test_05_modal(self):
        cli("-q","click-text","대화상자")
        self.assertIn("모달",cli("front"))
        cli("-q","key","return")

    def test_06_own_synthetic_input_is_not_user_input(self):
        stamp=(STATE/"user-input").stat().st_mtime if (STATE/"user-input").exists() else 0
        cli("-q","key","tab")
        after=(STATE/"user-input").stat().st_mtime if (STATE/"user-input").exists() else 0
        self.assertEqual(stamp,after)


if __name__=="__main__":
    unittest.main(verbosity=2, failfast=UI or WIDGET)
