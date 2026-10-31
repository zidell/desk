"""Linux desk CLI and persistent visible session; display-specific code is in adapters."""
import csv
import fcntl
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parent.parent
STATE = Path(os.environ.get("DESK_STATE", Path.home()/".cache"/"desk")).expanduser().resolve()
STYLE = json.loads((ROOT/"widget.json").read_text())
STOP = "중지됨: 사용자가 컴퓨터 제어를 중지했습니다. 즉시 멈추고 새 요청을 기다리세요."


class DeskError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code=code


def module(name, filename):
    spec=importlib.util.spec_from_file_location(name,ROOT/filename)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result


CLICK=module("desk_click", "desk-click.py")
FUSION=module("desk_fusion", "desk-see.py")


def state_dir():
    STATE.mkdir(parents=True,exist_ok=True,mode=0o700)
    STATE.chmod(0o700)
    (STATE/"shots").mkdir(exist_ok=True,mode=0o700)


def socket_path():
    # AF_UNIX path length is bounded even if DESK_STATE is a long test directory.
    import hashlib
    runtime=Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
    return runtime/("desk-"+str(os.getuid())+"-"+hashlib.sha256(str(STATE).encode()).hexdigest()[:16]+".sock")


def send(argv, timeout=650):
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout);connection.connect(str(socket_path()))
        connection.sendall(json.dumps(argv,ensure_ascii=False).encode()+b"\n")
        data=b""
        while b"\n" not in data:
            chunk=connection.recv(65536)
            if not chunk:raise RuntimeError("desk 세션 연결이 닫혔습니다")
            data+=chunk
        return json.loads(data)


def log(message):
    from resources import rotate_log
    rotate_log(STATE/"actions.log")
    with (STATE/"actions.log").open("a",encoding="utf-8") as output:
        output.write(time.strftime("%Y-%m-%d %H:%M:%S ")+message.replace("\n"," ")+"\n")


def run(argv, pump=None, timeout=15, input_data=None):
    process=subprocess.Popen(argv,stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
                             stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    start=time.monotonic()
    try:
        while True:
            try:
                pending,input_data=input_data,None
                out,err=process.communicate(input=pending,timeout=.05)
                break
            except subprocess.TimeoutExpired:
                if pump:pump(.005)
                if time.monotonic()-start>timeout:raise DeskError(2,"로컬 도구 시간 초과: "+Path(argv[0]).name)
        if process.returncode:raise DeskError(2,err.decode("utf-8","replace").strip() or "로컬 도구 실패")
        return out.decode("utf-8","replace")
    finally:
        if process.poll() is None:process.kill();process.communicate()


def doctor():
    import importlib.util
    import shutil
    session=os.environ.get("XDG_SESSION_TYPE", "wayland" if os.environ.get("WAYLAND_DISPLAY") else "x11")
    lines=["platform: Linux", "session: "+session,"desktop: "+os.environ.get("XDG_CURRENT_DESKTOP","unknown"),
           "state: "+str(STATE),"runtime: "+sys.executable]
    for name in ("PyQt6","PIL","dbus","gi","Xlib"):
        lines.append(name+": "+("installed" if importlib.util.find_spec(name) else "missing"))
    for name in ("tesseract","cmake","c++","spectacle","pkg-config"):
        lines.append(name+": "+str(shutil.which(name) or "missing"))
    if shutil.which("tesseract"):
        langs=subprocess.run(["tesseract","--list-langs"],capture_output=True,text=True,timeout=5).stdout.splitlines()[1:]
        lines.append("OCR languages: "+", ".join(langs))
    try:
        bus=subprocess.run(["busctl","--user","get-property","org.a11y.Bus","/org/a11y/bus","org.a11y.Status","IsEnabled"],
                           capture_output=True,text=True,timeout=5).stdout.strip()
        lines.append("accessibility bus: "+("enabled" if bus=="b true" else "disabled → 시스템 설정에서 보조 기술(접근성)을 켜거나 "
                     "gsettings set org.gnome.desktop.interface toolkit-accessibility true 후 다시 로그인"))
    except (OSError,subprocess.TimeoutExpired):
        lines.append("accessibility bus: unknown (busctl 없음) — OCR만으로 동작할 수 있음")
    lines.append("Wayland safety: KDE only; other compositors are refused")
    if session=="wayland":
        include=Path("/usr/include/LayerShellQt/window.h")
        cached=Path.home()/".cache/desk/headers/usr/include/LayerShellQt/window.h"
        lines.append("LayerShellQt headers: "+("installed" if include.exists() or cached.exists() else "missing"))
        lines.append("capture: KDE Spectacle" if os.environ.get("DESK_CAPTURE","native")=="native" else "capture: portal (approval required)")
    try:lines.append("session status: "+send(["status"],2)["output"])
    except (OSError,RuntimeError):lines.append("panel: off")
    print("\n".join(lines))
    return 0


class Session:
    def __init__(self, app):
        from PyQt6 import QtCore,QtGui,QtWidgets
        self.app=app;self.QtCore=QtCore;self.QtGui=QtGui;self.QtWidgets=QtWidgets
        self.backend=None;self.active=False;self.busy=False;self.lines=[];self.warnings=[];self.last_activity=0;self.last_epoch=None
        from accessibility_worker import AccessibilityWorker
        self.accessibility_worker=AccessibilityWorker()
        self.app.aboutToQuit.connect(self.accessibility_worker.close)
        self.quiet=False;self.observation=None;self.expected=None;self.expected_focus=None
        self.popup_pending=False;self.occluded_panel_box=None
        from widget import Panel,Marks
        self.panel=Panel(STYLE,flags=QtCore.Qt.WindowType.FramelessWindowHint|QtCore.Qt.WindowType.WindowStaysOnTopHint|QtCore.Qt.WindowType.Tool|QtCore.Qt.WindowType.WindowDoesNotAcceptFocus)
        self.panel.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.panel.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.panel.setObjectName("DeskPanel");self.panel.setWindowTitle("desk · 중지(ESC)")
        self.marks=Marks(STYLE)
        self.layer=None
        if os.environ.get("XDG_SESSION_TYPE")=="wayland" or os.environ.get("WAYLAND_DISPLAY") and os.environ.get("XDG_SESSION_TYPE")!="x11":
            from layer import load,configure
            self.layer=load();configure(self.layer,self.panel,margin=STYLE["bottomMargin"]);configure(self.layer,self.marks,overlay=True)
        self.panel.resize(STYLE["width"],STYLE["height"])
        self.panel.setStyleSheet(f'QLabel {{color:{STYLE["foreground"]};border:0;background:transparent;}} QPushButton {{color:white;background:{STYLE["accent"]};border:0;border-radius:10px;padding:8px;}}')
        layout=QtWidgets.QHBoxLayout(self.panel);layout.setContentsMargins(14,5,10,5)
        text=QtWidgets.QVBoxLayout();self.older=QtWidgets.QLabel();self.newer=QtWidgets.QLabel()
        self.older.setStyleSheet(f'font-size:{STYLE["olderFontSize"]}px;color:{STYLE["muted"]};')
        self.newer.setStyleSheet(f'font-size:{STYLE["newerFontSize"]}px;')
        text.addWidget(self.older);text.addWidget(self.newer);layout.addLayout(text,1)
        button=QtWidgets.QPushButton(STYLE["stopLabel"]);button.setFixedWidth(STYLE["stopWidth"]);button.clicked.connect(self.stop);layout.addWidget(button)
        self.timer=QtCore.QTimer();self.timer.timeout.connect(self.tick);self.timer.start(100)
        self.listener=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        socket_path().unlink(missing_ok=True)
        self.listener.bind(str(socket_path()));os.chmod(socket_path(),0o600);self.listener.listen(8);self.listener.setblocking(False)
        self.notifier=QtCore.QSocketNotifier(self.listener.fileno(),QtCore.QSocketNotifier.Type.Read)
        self.notifier.activated.connect(self.receive)
        import gi
        from gi.repository import GLib
        self.glib=GLib.MainContext.default()

    def pump(self, seconds=0):
        until=time.monotonic()+seconds
        while True:
            self.app.processEvents()
            while self.glib.pending():self.glib.iteration(False)
            if time.monotonic()>=until:return
            time.sleep(min(.005,max(0,until-time.monotonic())))

    def hide_stopped(self):
        self.panel.hide()
        self.marks.target=None;self.marks.elements=[];self.marks.ripple=None;self.marks.hide()

    def tick(self):
        if (STATE/"stopped").exists():
            self.hide_stopped()
            return
        if self.backend:
            try:
                self.backend.heartbeat()
                if self.active:
                    snapshot=self.backend.snapshot()
                    if self.last_epoch!=snapshot["epoch"]:
                        if self.last_epoch is not None:self.last_activity=time.monotonic()
                        self.last_epoch=snapshot["epoch"]
                    if (STATE/"stopped").exists():self.hide_stopped()
            except Exception:
                if self.active:self.stop()

    def show_message(self, message, record=True):
        if record:log(message)
        if not self.lines or self.lines[-1]!=message:self.lines=(self.lines+[message])[-2:]
        self.older.setText(self.lines[0][:50] if len(self.lines)>1 else "")
        self.newer.setText(self.lines[-1][:50])

    def stop(self):
        (STATE/"stopped").write_text("사용자 중지\n")
        CLICK.invalidate(STATE)
        self.hide_stopped()
        self.show_message("중지됨 · 새 요청을 기다립니다")

    def check(self):
        if (STATE/"stopped").exists():raise DeskError(3,STOP)
        if not self.active or not self.panel.isVisible():raise DeskError(2,"desk begin으로 보이는 제어 세션을 먼저 시작하세요")
        if not self.backend.snapshot().get("armed"):raise DeskError(2,"안전 어댑터가 비활성화되어 입력을 거부합니다")

    def idle(self):
        self.tick();self.check()
        idle=float(os.environ.get("DESK_USER_IDLE","5"))
        while time.monotonic()-self.last_activity<idle or self.backend.snapshot().get("physicalInputHeld",False):
            self.pump(.03);self.tick();self.check()

    @staticmethod
    def keywindow(window):
        return [window.get("id"),window.get("pid"),window.get("box")]

    def current(self, expected=None):
        self.check()
        window=self.backend.snapshot()
        if window.get("locked"):raise DeskError(6,"잠금 화면에서는 조작하지 않습니다")
        if not window.get("id") or not window.get("pid"):raise DeskError(6,"대상 창을 식별하지 못했습니다")
        # Do not use the agent's own host or a terminal as an accidental input target.
        if window["pid"]==os.getpid() or (any(n in window["app"].lower() for n in ("ghostty","konsole","gnome-terminal","wezterm","codex")) and window["id"]!=getattr(self,"explicit_terminal_id",None)):
            raise DeskError(1,"에이전트 터미널·호스트 창입니다. 대상 앱을 desk open으로 먼저 앞으로 가져오세요")
        if expected is not None and (self.keywindow(window)!=self.keywindow(expected) or window["epoch"]!=expected["epoch"]):
            raise DeskError(6,"마지막으로 본 뒤 창·위치·사용자 입력이 바뀌었습니다. desk see로 다시 확인하세요")
        return window

    def place_panel(self):
        screen=self.app.primaryScreen().availableGeometry()
        x=screen.x()+(screen.width()-STYLE["width"])//2;y=screen.y()+screen.height()-STYLE["height"]-STYLE["bottomMargin"]
        self.panel.move(x,y)
        if not self.layer and getattr(self.backend,"guard",None):
            self.backend.guard.PlacePanel(os.getpid(),x,y,STYLE["width"],STYLE["height"])

    def begin(self, label):
        if self.active:raise DeskError(2,"이미 제어 세션이 실행 중입니다. 중지 상태는 새 요청에서 end 후 begin으로 시작하세요")
        (STATE/"stopped").unlink(missing_ok=True);CLICK.invalidate(STATE)
        self.expected=None;self.expected_focus=None;self.observation=None
        session=os.environ.get("XDG_SESSION_TYPE", "wayland" if os.environ.get("WAYLAND_DISPLAY") else "x11")
        if session=="wayland":
            from wayland import Wayland
            self.backend=Wayland(STATE,self.pump)
        elif session=="x11":
            from x11 import X11
            self.backend=X11(STATE,self.pump)
        else:raise DeskError(2,"지원하지 않는 디스플레이 세션: "+session)
        self.show_message("시작 준비 · "+label)
        self.panel.show();self.pump(.08)
        try:
            self.backend.start()
            self.place_panel();self.pump(.05)
            self.backend.arm(STYLE["userPointerThresholdPixels"])
            self.active=True;self.last_epoch=self.backend.snapshot()["epoch"];self.last_activity=0
            self.show_message("제어 시작 · "+label)
            return "제어 시작: "+self.backend.name+" · 하단 중지 버튼 / 물리 ESC 한 번"
        except BaseException:
            self.active=False
            try:self.backend.close()
            finally:self.backend=None;self.panel.hide()
            raise

    def capture(self, region=None):
        from resources import prune_shots
        protected=[self.observation["image"]] if self.observation else []
        prune_shots(STATE/"shots",protected)
        self.marks.hidden_for_capture=True;self.marks.hide()
        keep_panel=self.popup_pending or bool(self.backend.snapshot().get("popups"))
        if not keep_panel:self.panel.hide()
        self.pump(.07)
        try:
            image,bounds=self.backend.capture()
        finally:
            if not keep_panel and self.active and not (STATE/"stopped").exists():self.panel.show();self.place_panel();self.pump(.01)
            self.marks.hidden_for_capture=False
        self.occluded_panel_box=None
        if keep_panel:
            # Re-mapping a top-level tool surface can dismiss a menu grab.
            # Keep the panel mapped and mask its visible area instead of reading
            # its text or pretending controls underneath it are visible.
            from PIL import ImageDraw
            screen=self.app.primaryScreen().geometry()
            panel=[screen.x()+(screen.width()-STYLE["width"])/2,screen.y()+screen.height()-STYLE["height"]-STYLE["bottomMargin"],STYLE["width"],STYLE["height"]+2]
            if not self.layer:
                r=self.panel.frameGeometry();panel=[r.x(),r.y(),r.width(),r.height()]
            self.occluded_panel_box=panel
            visible=CLICK.intersect(panel,bounds)
            if min(visible[2:])>0:
                x,y,w,h=visible;sx,sy=image.width/bounds[2],image.height/bounds[3]
                ImageDraw.Draw(image).rectangle((math.floor((x-bounds[0])*sx),math.floor((y-bounds[1])*sy),math.ceil((x+w-bounds[0])*sx),math.ceil((y+h-bounds[1])*sy)),fill="black")
        if region:
            if min(region[2:])<=0 or CLICK.intersect(region,bounds)!=list(region):raise DeskError(2,"캡처 영역이 선택한 화면 밖입니다")
            x,y,w,h=region;ox,oy,bw,bh=bounds
            sx,sy=image.width/bw,image.height/bh
            left,top,right,bottom=round((x-ox)*sx),round((y-oy)*sy),round((x+w-ox)*sx),round((y+h-oy)*sy)
            image=image.crop((left,top,right,bottom))
            # Preserve the real pixel-aligned origin and scale. Re-labeling a
            # rounded pixel crop with the requested fractional rectangle creates
            # 1px differences against full-screen evidence at fractional DPI.
            bounds=[ox+left/sx,oy+top/sy,(right-left)/sx,(bottom-top)/sy]
        path=STATE/"shots"/(uuid.uuid4().hex+".png");image.save(path)
        return path,bounds

    def accessibility(self, window):
        try:
            result=self.accessibility_worker.request(window,self.pump,timeout=5)
        except (OSError,EOFError,TimeoutError,ValueError):
            result={"bindings":[],"warnings":["접근성 도구 응답 실패: 화면 픽셀·OCR만 사용합니다"]}
        self.warnings.extend(result["warnings"])
        self.warnings=list(dict.fromkeys(self.warnings))[-32:]
        return result["bindings"]

    def recognize(self, path, bounds, window):
        ocr=self.ocr_lines(path,bounds)
        bindings=self.accessibility(window)
        if self.occluded_panel_box:bindings=[b for b in bindings if CLICK.area(CLICK.intersect(b["box"],self.occluded_panel_box))==0]
        bindings=[{**b,"box":CLICK.intersect(b["box"],bounds)} for b in bindings if min(CLICK.intersect(b["box"],bounds)[2:])>0]
        (STATE/"ocr.tsv").write_text("\n".join(ocr))
        (STATE/"ax.tsv").write_text("\n".join(",".join(map(str,b["box"]))+"\t"+b["role"]+"\t"+b["name"].replace("\t"," ").replace("\n"," ")+"\t"+b["state"] for b in bindings))
        (STATE/"det.tsv").write_text("")
        elements=FUSION.fuse(STATE/"ocr.tsv",STATE/"ax.tsv",STATE/"det.tsv")
        for element in elements:element["src"]=sorted(element["src"])
        return elements,bindings

    def ocr_lines(self, path, bounds):
        """Tesseract lines as "x,y,w,h<TAB>text" in screen coordinates."""
        languages=run(["tesseract","--list-langs"],self.pump,timeout=5).splitlines()[1:]
        language="kor+eng" if "kor" in languages and "eng" in languages else "eng" if "eng" in languages else languages[0]
        if "kor" not in languages:self.warnings.append("한국어 OCR 모델 없음: 영어 OCR만 사용합니다")
        tsv=run(["tesseract",str(path),"stdout","-l",language,"--psm","11","-c","tessedit_create_tsv=1"],self.pump,timeout=20)
        if not tsv.startswith("level\tpage_num\t"):raise DeskError(2,"OCR이 좌표 TSV를 반환하지 않았습니다")
        from PIL import Image
        with Image.open(path) as image:sx,sy=bounds[2]/image.width,bounds[3]/image.height
        lines={}
        for row in csv.DictReader(io.StringIO(tsv),delimiter="\t",quoting=csv.QUOTE_NONE):
            text=" ".join(row.get("text","").split())
            if not text or float(row.get("conf","-1"))<0:continue
            key=tuple(row[k] for k in ("page_num","block_num","par_num","line_num"))
            x,y,w,h=(int(row[k]) for k in ("left","top","width","height"))
            lines.setdefault(key,[]).append((x,y,w,h,text))
        ocr=[]
        for words in lines.values():
            x=min(w[0] for w in words);y=min(w[1] for w in words)
            right=max(w[0]+w[2] for w in words);bottom=max(w[1]+w[3] for w in words)
            box=[bounds[0]+x*sx,bounds[1]+y*sy,(right-x)*sx,(bottom-y)*sy]
            ocr.append(",".join(map(str,box))+"\t"+" ".join(w[4] for w in words))
        return ocr

    def observe(self, recognize=True, full=False, region=None):
        self.idle();window=self.current();self.warnings=[]
        front=window["box"]
        if window.get("popups"):
            boxes=[front]+[p["box"] for p in window["popups"]]
            x=min(b[0] for b in boxes);y=min(b[1] for b in boxes)
            front=[x,y,max(b[0]+b[2] for b in boxes)-x,max(b[1]+b[3] for b in boxes)-y]
        selected=region or (None if full else front)
        # A window may extend beyond the selected monitor. Capture its visible part only.
        if selected and hasattr(self.backend,"region"):selected=CLICK.intersect(selected,self.backend.region)
        path,bounds=self.capture(selected)
        elements,bindings=self.recognize(path,bounds,window) if recognize else ([],[])
        self.current(window)
        CLICK.observe(STATE,path,bounds,self.keywindow(window),elements,bindings,epoch=str(window["epoch"]))
        self.observation={"window":window,"image":str(path),"region":bounds,"elements":elements,"bindings":bindings}
        self.expected=window
        self.expected_focus=next((b for b in bindings if b.get("focused")),None)
        marked=path.with_name(path.stem+"-marked.png")
        if recognize:
            palette={key:tuple(int(STYLE[name][i:i+2],16) for i in (1,3,5)) for key,name in (("A","scanControl"),("O","scanText"),("D","scanDetection"))}
            FUSION.draw_marks(elements,str(path),bounds[0],bounds[1],bounds[2],str(marked),colors=palette)
            rows=FUSION.ocr_rows(STATE/"ocr.tsv")
        else:
            # The raw capture stays the click evidence; the agent gets a copy with secrets painted over.
            marked=path.with_name(path.stem+"-shown.png")
            tsv=STATE/"shot-ocr.tsv";tsv.write_text("\n".join(self.ocr_lines(path,bounds)))
            rows=FUSION.ocr_rows(tsv)
        FUSION.redact_image(str(path if not recognize else marked),rows,bounds[0],bounds[1],bounds[2],out=str(marked))
        if recognize and not window.get("popups"):
            self.marks.scan(elements,self.backend.region);self.place_marks()
        result=["→ "+window["app"]+" · "+window["title"], "region: "+",".join(map(str,bounds)), "image: "+str(marked)]
        for i,e in enumerate(elements,1):
            x,y,w,h=e["box"]
            result.append(f'{i} · {x+w/2:.1f},{y+h/2:.1f} · {e["kind"]} · {e["name"]}'+(" ["+e["state"]+"]" if e["state"] else "")+" · ["+"".join(sorted(e["src"]))+"]")
        result.extend("경고: "+w for w in self.warnings)
        return "\n".join(result)

    def require(self, result):
        self.check()
        if not result:raise DeskError(6,"입력 직전 안전 검사가 거부했습니다. 다시 화면을 확인하세요")

    def place_marks(self):
        self.pump(.005)
        if not self.layer and getattr(self.backend,"guard",None):
            self.backend.guard.PlaceOverlay(os.getpid(),*map(round,self.marks.region))

    def move(self,x,y,window):
        if not math.isfinite(x) or not math.isfinite(y) or not CLICK.contains(self.backend.region,x,y):
            raise DeskError(2,"커서 좌표가 선택한 화면 밖입니다")
        self.current(window)
        start=self.backend.snapshot()["pointer"]
        distance=math.dist(start,[x,y]);duration=distance/STYLE["cursorPointsPerSecond"]
        steps=max(1,math.ceil(duration/(STYLE["cursorFrameMilliseconds"]/1000)))
        began=time.monotonic()
        for i in range(1,steps+1):
            self.current(window)
            self.require(self.backend.motion(start[0]+(x-start[0])*i/steps,start[1]+(y-start[1])*i/steps,window))
            self.pump(max(0,began+duration*i/steps-time.monotonic()))

    def arrive(self,x,y,window,token,binding):
        """Glide onto the target and press without comparing pixels after movement starts.

        Hover feedback anywhere on the path (row tint, underline, tooltip) repaints the
        screen, so pixels are only checked before movement (verify_initial); afterwards
        only the accessible identity is compared."""
        self.move(x,y,window)
        path,bounds=self.capture()
        return path,bounds,self.binding_at(self.accessibility(window),x,y),True

    @staticmethod
    def binding_at(bindings,x,y):
        return min((b for b in bindings if b["enabled"] and b["role"]!="Text" and CLICK.contains(b["box"],x,y)),key=lambda b:CLICK.area(b["box"]),default=None)

    def click(self,x,y,mode="left",requested=None):
        self.idle()
        if not self.observation:raise DeskError(6,"desk see/shot으로 화면을 먼저 확인하세요")
        window=self.current(self.observation["window"])
        token=CLICK.prepare(STATE,[x,y],self.keywindow(window),requested)
        path,bounds=self.capture()
        binding=self.binding_at(self.accessibility(window),x,y)
        CLICK.verify_initial(token,path,bounds,binding)
        if not window.get("popups"):
            self.marks.mark_target(token["box"],self.backend.region);self.place_marks()
        path,bounds,current,settled=self.arrive(x,y,window,token,binding)
        CLICK.finalize(STATE,token,path,bounds,binding,current,settled)
        self.marks.target=None;self.marks.hide();self.pump(.005)
        self.current(window)
        button=273 if mode=="right" else 272
        try:
            self.require(self.backend.button(button,True,window))
        finally:self.backend.button(button,False,window)
        if mode=="double":
            if current is None:raise DeskError(6,"접근성 대상 없는 더블클릭은 첫 클릭 뒤 재확인이 필요합니다")
            self.current(window)
            if not CLICK.same_binding(current,self.binding_at(self.accessibility(window),x,y)):raise DeskError(6,"첫 클릭 뒤 대상이 바뀌었습니다")
            try:self.require(self.backend.button(button,True,window))
            finally:self.backend.button(button,False,window)
        self.popup_pending=mode=="right"
        try:result=self.after(window)
        finally:self.popup_pending=False
        if mode!="right" and not self.expected.get("popups"):
            self.marks.clicked(x,y,self.backend.region);self.place_marks()
        if self.observation:
            from PIL import Image,ImageChops
            with Image.open(path) as before,Image.open(self.observation["image"]) as after:
                if before.size==after.size and ImageChops.difference(before.convert("RGB"),after.convert("RGB")).getbbox() is None:
                    raise DeskError(5,"클릭 뒤 화면 변화 없음. 결과를 확인하세요.\n"+result)
        return result

    def after(self,window,refresh_focus=True):
        CLICK.invalidate(STATE);self.observation=None
        current=self.current()
        if current["epoch"]!=window["epoch"]:raise DeskError(6,"입력 도중 사용자가 개입했습니다. 다시 화면을 확인하세요")
        self.expected=current
        if refresh_focus:self.expected_focus=next((b for b in self.accessibility(current) if b.get("focused")),None)
        if self.quiet:return "입력 완료"
        path,bounds=self.capture()
        fresh=self.current()
        if fresh["epoch"]!=window["epoch"]:raise DeskError(6,"결과 화면을 읽는 중 사용자가 개입했습니다")
        self.expected=fresh
        # Expose a fresh screen-only observation for subsequent coordinate actions.
        CLICK.observe(STATE,path,bounds,self.keywindow(self.expected),epoch=self.expected["epoch"])
        self.observation={"window":self.expected,"image":str(path),"region":bounds,"elements":[],"bindings":[]}
        return "image: "+str(path)+"\nregion: "+",".join(map(str,bounds))

    def key(self,combo,window=None,repeat=1):
        self.idle()
        if window is None and self.expected is None:raise DeskError(6,"desk see/shot으로 대상 창을 먼저 확인하세요")
        window=self.current(window or self.expected)
        if self.expected_focus and not (repeat>1 and combo.lower() in ("ctrl","control","primary")):
            focus=next((b for b in self.accessibility(window) if b.get("focused")),None)
            same=bool(focus) and all(self.expected_focus.get(k)==focus.get(k) for k in ("id","role","name","enabled")) and all(abs(a-b)<=1 for a,b in zip(self.expected_focus["box"],focus["box"]))
            if not same:
                fields=("id","role","box","enabled")
                raise DeskError(6,"마지막으로 본 뒤 키보드 포커스가 바뀌었습니다. desk see로 다시 확인하세요\n"+json.dumps({"expected":{k:self.expected_focus.get(k) for k in fields},"current":{k:focus.get(k) for k in fields} if focus else None},ensure_ascii=False))
        aliases={"primary":"Control_L","ctrl":"Control_L","control":"Control_L","alt":"Alt_L","shift":"Shift_L","super":"Super_L","win":"Super_L",
          "return":"Return","enter":"Return","esc":"Escape","escape":"Escape","tab":"Tab","space":"space","backspace":"BackSpace","delete":"Delete",
          "up":"Up","down":"Down","left":"Left","right":"Right","home":"Home","end":"End","pageup":"Prior","pagedown":"Next"}
        parts=combo.split("+")
        if not all(parts):raise DeskError(2,"잘못된 키 조합")
        symbols=[aliases.get(p.lower(),p) for p in parts]
        if hasattr(self.backend,"chord"):
            for index in range(repeat):
                if index:time.sleep(.08)  # Bound tap timing; native ESC guard remains active.
                window=self.current(window)
                self.require(self.backend.chord(symbols,window))
            return window
        held=[]
        try:
            for symbol in symbols:self.require(self.backend.key(symbol,True,window));held.append(symbol)
        finally:
            for symbol in reversed(held):self.backend.key(symbol,False,window)
        return window

    def key_plan(self,plan):
        """Bounded held-key test through the normal guard, with physical-input cancellation."""
        self.idle();window=self.current(self.expected)
        if "userEpoch" not in window:raise DeskError(2,"이 어댑터는 실제 입력 구분을 지원하지 않습니다")
        if not isinstance(plan,list) or not 1<=len(plan)<=32:raise DeskError(2,"키 계획은 1..32 단계여야 합니다")
        total=0
        for step in plan:
            if not isinstance(step,list) or len(step)!=3 or not isinstance(step[0],str) or len(step[0])!=1 or step[0] not in "abcdefghijklmnopqrstuvwxyz" or not isinstance(step[1],bool) or not isinstance(step[2],int) or not 0<=step[2]<=2000:raise DeskError(2,"각 단계: [영문 키, 누름 여부, 0..2000ms]")
            total+=step[2]
        if total>10000:raise DeskError(2,"키 계획은 10초 이하여야 합니다")
        identity=self.keywindow(window);user_epoch=window["userEpoch"];held=[]
        def checked_window():
            current=self.current()
            if self.keywindow(current)!=identity or current.get("userEpoch")!=user_epoch:raise DeskError(6,"키 유지 검사 중 창 또는 실제 사용자 입력이 바뀌었습니다")
            return current
        try:
            for symbol,down,milliseconds in plan:
                current=checked_window()
                if down:
                    if symbol in held:raise DeskError(2,"이미 누른 키입니다: "+symbol)
                    self.require(self.backend.key(symbol,True,current));held.append(symbol)
                else:
                    if symbol not in held:raise DeskError(2,"누르지 않은 키입니다: "+symbol)
                    self.require(self.backend.key(symbol,False,current));held.remove(symbol)
                end=time.monotonic()+milliseconds/1000
                while time.monotonic()<end:self.pump(min(.01,max(0,end-time.monotonic())));checked_window()
        finally:
            for symbol in reversed(held):
                try:self.backend.key(symbol,False,self.backend.snapshot())
                except Exception:pass
        current=checked_window();self.expected=current;self.expected_focus=None
        CLICK.invalidate(STATE);self.observation=None
        if self.quiet:return "키 유지 검사 완료"
        path,bounds=self.capture();current=checked_window();self.expected=current
        CLICK.observe(STATE,path,bounds,self.keywindow(current),epoch=current["epoch"])
        self.observation={"window":current,"image":str(path),"region":bounds,"elements":[],"bindings":[]}
        return "image: "+str(path)+"\nregion: "+",".join(map(str,bounds))

    def dispatch(self,argv):
        self.quiet=bool(argv and argv[0]=="-q")
        if self.quiet:argv=argv[1:]
        command=argv[0] if argv else "help";args=argv[1:]
        if command=="begin":return self.begin(" ".join(args))
        if command=="status":return ("stopped" if (STATE/"stopped").exists() else "not stopped")+"; panel: "+("on" if self.panel.isVisible() else "off")
        if command=="stop":self.stop();return "중지함"
        if command=="end":
            if self.backend:self.backend.close();self.backend=None
            self.active=False;self.panel.hide();self.marks.hide();CLICK.invalidate(STATE);self.show_message("제어 끝")
            self.QtCore.QTimer.singleShot(0,self.app.quit);return "제어 끝"
        self.check()
        if command=="say":self.show_message(" ".join(args));return "기록함"
        if command=="ask-ref":
            if len(args)!=2:raise DeskError(2,"desk ask-ref ID '답'")
            ASK=module("desk_ask","desk-ask.py");ASK.ref(str(STATE/"ask"),args[0],args[1]);return "기록함"
        if command=="ask-report":
            ASK=module("desk_ask","desk-ask.py")
            import contextlib
            stream=io.StringIO()
            with contextlib.redirect_stdout(stream):ASK.report(str(STATE/"ask"),"--list" in args)
            return stream.getvalue()
        self.show_message(command+(" · "+" ".join(args)[:60] if args else ""))
        if command in ("see","look"):return self.observe()
        if command=="windows":return json.dumps(self.backend.windows(),ensure_ascii=False)
        if command=="front":return json.dumps(self.current(),ensure_ascii=False)
        if command=="ask":
            if "--region" not in args or "--type" not in args:raise DeskError(2,"desk ask --region x,y,w,h --type 종류 '질문'")
            region=[float(n) for n in args[args.index("--region")+1].split(",")]
            kind=args[args.index("--type")+1];question=args[-1]
            ASK=module("desk_ask","desk-ask.py")
            if kind not in ASK.KINDS:raise DeskError(2,"질문 종류 오류")
            output=self.observe(False,region=region)
            directory=STATE/"ask";directory.mkdir(exist_ok=True,mode=0o700);ident=time.strftime("%Y%m%d-%H%M%S-")+uuid.uuid4().hex[:6]
            import shutil
            shutil.copyfile(self.observation["image"],directory/(ident+".png"))
            ASK.new(str(directory),ident,",".join(map(str,region)),kind,self.expected["title"],question)
            return output+"\nquestion ID: "+ident+"\n로컬 비전 모델 미설치: 에이전트가 그림을 직접 보고 ask-ref로 기록하세요"
        if command in ("shot","read"):
            if command=="shot" and not args:return self.observe()
            region=None
            if "--region" in args:region=[float(n) for n in args[args.index("--region")+1].split(",")]
            return self.observe(command=="read",full="--full" in args or (command=="read" and "--window" not in args),region=region)
        if command in ("click","dclick","rclick"):
            if len(args)!=2:raise DeskError(2,"desk "+command+" X Y")
            return self.click(*map(float,args),mode={"click":"left","dclick":"double","rclick":"right"}[command])
        if command in ("click-el","click-text"):
            if not args:raise DeskError(2,"요소 번호 또는 이름이 필요합니다")
            if not self.observation or not self.observation["elements"]:
                if command=="click-text":self.observe()
                else:raise DeskError(6,"desk see로 새 요소 목록을 읽으세요")
            elements=self.observation["elements"]
            if command=="click-el":
                index=int(args[0])-1
                if index<0 or index>=len(elements):raise DeskError(1,"요소 번호가 없습니다")
                element=elements[index]
            else:
                candidates=[e for e in elements if e["name"]==args[0]] or [e for e in elements if args[0] in e["name"]]
                nth=next((int(a) for a in args[1:] if a.isdigit()),None)
                if not candidates:raise DeskError(1,"NOT_FOUND: "+args[0])
                if nth is None and len(candidates)!=1:raise DeskError(1,"AMBIGUOUS: 후보 "+str(len(candidates))+"개; see로 보고 click-el 번호를 고르세요")
                if nth is not None and not 1<=nth<=len(candidates):raise DeskError(1,"후보 번호 범위 밖")
                element=candidates[(nth or 1)-1]
            if element["kind"]=="글자" and "--force" not in args:raise DeskError(1,"OCR 글자입니다. 그림에서 컨트롤임을 확인한 뒤 --force를 사용하세요")
            if "비활성" in element["state"]:raise DeskError(1,"비활성 컨트롤입니다")
            x,y,w,h=element["box"];return self.click(x+w/2,y+h/2,requested=element["box"])
        if command=="key-plan":
            if len(args)!=1:raise DeskError(2,"desk key-plan JSON")
            return self.key_plan(json.loads(args[0]))
        if command=="key":
            repeat=1
            if len(args)==3 and args[0]=="--repeat":
                try:repeat=int(args[1])
                except ValueError:raise DeskError(2,"repeat must be an integer")
                if not 1<=repeat<=5:raise DeskError(2,"repeat must be 1..5")
                args=args[2:]
            if len(args)!=1:raise DeskError(2,"desk key [--repeat 1..5] COMBO")
            return self.after(self.key(args[0],repeat=repeat),refresh_focus=not(self.quiet and repeat>1 and args[0].lower() in ("ctrl","control","primary")))
        if command=="type":
            if len(args)!=1:raise DeskError(2,"Linux type은 클립보드 입력을 사용합니다: desk type '텍스트'")
            if self.expected is None:raise DeskError(6,"desk see/shot으로 입력할 창을 먼저 확인하세요")
            self.idle();window=self.current(self.expected) if self.expected else self.current()
            # Keep the offered selection alive for asynchronous Wayland clipboard
            # transfer. Never read the user's previous clipboard or history.
            if hasattr(self.backend,"set_clipboard"):self.backend.set_clipboard(args[0])
            else:self.app.clipboard().setText(args[0])
            self.pump(.02)
            self.key("primary+v",window);self.pump(.08)
            return self.after(window)
        if command=="move":
            self.idle();window=self.current(self.expected) if self.expected else self.current()
            if len(args)==4 and args[0]=="--modifiers":
                combinations={"super+alt":["Super_L","Alt_L"],"super+ctrl":["Super_L","Control_L"]}
                if args[1] not in combinations:raise DeskError(2,"지원하는 조합: super+alt, super+ctrl")
                x,y=map(float,args[2:]);start=self.backend.snapshot()["pointer"]
                # A single frame permits an intentional window geometry change
                # without relaxing the normal window/epoch guard between steps.
                limit=STYLE["cursorPointsPerSecond"]*STYLE["cursorFrameMilliseconds"]/1000
                if not all(map(math.isfinite,(x,y))) or math.dist(start,[x,y])>limit or not CLICK.contains(self.backend.region,x,y):
                    raise DeskError(2,"조합 이동은 화면 안에서 한 프레임 거리 이내로만 가능합니다")
                held=[]
                try:
                    for symbol in combinations[args[1]]:
                        self.require(self.backend.key(symbol,True,window));held.append(symbol)
                    self.require(self.backend.motion(*start,window))
                    self.current(window)
                    self.require(self.backend.motion(x,y,window))
                finally:
                    for symbol in reversed(held):self.backend.key(symbol,False,window)
                current=self.current()
                if any(current[k]!=window[k] for k in ("id","pid","epoch")):
                    raise DeskError(6,"조합 이동 중 대상 창 또는 사용자 입력이 바뀌었습니다")
                return self.after(current)
            x,y=map(float,args);self.move(x,y,window);CLICK.invalidate(STATE);self.observation=None;return "커서 이동 완료"
        if command=="scroll":
            if len(args) not in (3,4):raise DeskError(2,"desk scroll X Y up|down|left|right [N]")
            x,y=map(float,args[:2]);direction=args[2]
            if direction not in ("up","down","left","right"):raise DeskError(2,"스크롤 방향 오류")
            amount=float(args[3]) if len(args)>3 else 5
            self.idle();window=self.current(self.expected) if self.expected else self.current();self.move(x,y,window)
            self.require(self.backend.scroll(amount if direction=="right" else -amount if direction=="left" else 0,
                                             amount if direction=="down" else -amount if direction=="up" else 0,window));return self.after(window)
        if command=="drag":
            if len(args)!=4:raise DeskError(2,"desk drag X Y X2 Y2")
            x,y,x2,y2=map(float,args);self.idle()
            if not self.observation:raise DeskError(6,"desk see/shot으로 먼저 확인하세요")
            window=self.current(self.observation["window"]);token=CLICK.prepare(STATE,[x,y],self.keywindow(window))
            if not CLICK.contains(self.observation["region"],x2,y2):raise DeskError(6,"드래그 끝점도 관측한 화면 안이어야 합니다. desk shot --full로 확인하세요")
            path,bounds=self.capture();binding=self.binding_at(self.accessibility(window),x,y)
            CLICK.verify_initial(token,path,bounds,binding)
            if not window.get("popups"):
                self.marks.mark_target(token["box"],self.backend.region);self.place_marks()
            path,bounds,current,settled=self.arrive(x,y,window,token,binding)
            CLICK.finalize(STATE,token,path,bounds,binding,current,settled)
            try:self.require(self.backend.button(272,True,window));self.move(x2,y2,window)
            finally:self.backend.button(272,False,window)
            result=self.after(window);self.marks.clicked(x2,y2,self.backend.region);self.place_marks();return result
        if command in ("find","wait-for"):
            if not args:raise DeskError(2,"찾을 글자가 필요합니다")
            limit=float(args[1]) if command=="wait-for" and len(args)>1 and not args[1].startswith("--") else 15
            deadline=time.monotonic()+limit
            while True:
                output=self.observe(full="--full" in args)
                hits=[e for e in self.observation["elements"] if args[0] in e["name"]]
                if (not hits if "--gone" in args else bool(hits)):return output
                if command=="find" or time.monotonic()>deadline:raise DeskError(1,"NOT_FOUND: "+args[0])
                self.pump(.2);self.check()
        if command=="wait":
            deadline=time.monotonic()+float(args[0] if args else 1)
            while time.monotonic()<deadline:self.check();self.pump(.03)
            return "대기 완료"
        if command=="handoff":
            if not args:raise DeskError(2,"desk handoff '안내' [초]")
            self.show_message("사용자 차례 · "+args[0]);start=self.backend.snapshot()["epoch"]
            deadline=time.monotonic()+float(args[1] if len(args)>1 else 180);began=False
            while time.monotonic()<deadline:
                self.pump(.05);self.tick();self.check()
                began=began or self.backend.snapshot()["epoch"]!=start
                if began and time.monotonic()-self.last_activity>=10:CLICK.invalidate(STATE);self.observation=None;self.expected=None;return "입력이 멈췄습니다. desk see로 결과를 확인하세요"
            raise DeskError(1,"사용자 입력 대기 시간 초과")
        if command=="open":
            if len(args)==3 and args[0]=="--terminal" and args[1]=="--id":
                matches=[w for w in self.backend.windows() if w["id"]==args[2]]
                if len(matches)!=1 or not any(n in matches[0]["app"].lower() for n in ("ghostty","konsole","gnome-terminal","wezterm")):raise DeskError(2,"명시한 터미널 창을 찾지 못했습니다")
                self.explicit_terminal_id=args[2];args=args[1:]
            if len(args)==2 and args[0]=="--id":
                self.idle();self.expected=None;self.expected_focus=None;self.observation=None;CLICK.invalidate(STATE)
                self.require(self.backend.activate(args[1]));self.pump(.1);return self.observe()
            if len(args)!=1:raise DeskError(2,"desk open 앱.desktop|URL|파일 또는 desk open --id 창ID")
            target=args[0];self.idle();CLICK.invalidate(STATE);self.observation=None;self.expected=None
            environment=dict(os.environ);environment.pop("QT_WAYLAND_SHELL_INTEGRATION",None)
            if target.endswith(".desktop"):process=subprocess.Popen(["gtk-launch",target],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=environment)
            elif "://" in target or Path(target).exists():process=subprocess.Popen(["xdg-open",target],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=environment)
            else:raise DeskError(2,"Linux 앱은 desktop ID로 지정하세요 (예: org.kde.kwrite.desktop)")
            self.pump(.5)
            if process.poll() not in (None,0):raise DeskError(2,"앱 실행 실패")
            return self.observe()
        raise DeskError(2,"Linux에서 지원하지 않는 명령: "+command+"; desk help 참고")

    def receive(self,*unused):
        connection,_=self.listener.accept();connection.settimeout(.2)
        try:
            data=b""
            while b"\n" not in data:
                chunk=connection.recv(65536)
                if not chunk:raise ValueError("빈 요청")
                data+=chunk
                if len(data)>1024*1024:raise ValueError("요청이 너무 큽니다")
            argv=json.loads(data)
            if self.busy:raise DeskError(6,"다른 desk 명령이 실행 중입니다")
            self.busy=True
            try:result={"code":0,"output":self.dispatch(argv)}
            finally:self.busy=False
        except CLICK.StaleClick as error:result={"code":6,"output":str(error)}
        except DeskError as error:result={"code":error.code,"output":str(error)}
        except Exception as error:
            import traceback
            traceback.print_exc()
            result={"code":2,"output":type(error).__name__+": "+str(error)}
        if result["code"]:
            self.marks.target=None;self.marks.elements=[];self.marks.ripple=None;self.marks.hide()
            if result["code"]==6:
                CLICK.invalidate(STATE);self.observation=None;self.expected=None;self.expected_focus=None
        try:connection.sendall(json.dumps(result,ensure_ascii=False).encode()+b"\n")
        except (BrokenPipeError,ConnectionResetError):
            if self.active:self.stop()
        finally:connection.close()

    def close(self):
        self.accessibility_worker.close()
        if self.backend:self.backend.close();self.backend=None
        self.listener.close();socket_path().unlink(missing_ok=True);self.panel.hide();self.marks.hide()


HELP="""desk begin '요청' | say '설명' | status | stop | end | doctor
desk see | look | front | shot [--full|--region x,y,w,h] | read [--window]
desk click|dclick|rclick X Y | click-el N [--force] | click-text '이름' [N] [--force]
desk key primary+l | type '텍스트' | move X Y | drag X Y X2 Y2
desk scroll X Y up|down|left|right [N] | find '글자' | wait-for '글자' [초] [--gone]
desk windows | open --id 창ID | open 앱.desktop|URL|파일 | handoff '안내' [초] | wait 초 | log [N]
desk -q 명령 … : 중간 입력 뒤 결과 캡처 생략. 다음 좌표 클릭 전 새 see/shot 필요.
Linux: KDE Wayland / X11 자동 선택. KDE 캡처는 Spectacle, 패널은 LayerShellQt.
종료 코드: 1=없음·모호함·거부, 2=환경·인자 오류, 3=사용자 중지, 5=클릭 뒤 변화 없음, 6=오래된 화면·대상 변경.
"""


def main(argv):
    state_dir();os.environ.pop("NO_COLOR",None)
    wayland_session=os.environ.get("XDG_SESSION_TYPE")=="wayland" or bool(os.environ.get("WAYLAND_DISPLAY")) and os.environ.get("XDG_SESSION_TYPE")!="x11"
    if argv and argv[0] in ("begin","--serve") and wayland_session and "KDE" not in os.environ.get("XDG_CURRENT_DESKTOP","").upper():
        print("desk: 이 Wayland compositor의 안전 어댑터가 없습니다. 현재 KDE Wayland만 지원합니다.",file=sys.stderr)
        return 2
    tessdata=Path.home()/".cache"/"desk"/"tessdata"
    if (tessdata/"kor.traineddata").exists() and (tessdata/"eng.traineddata").exists():
        os.environ.setdefault("TESSDATA_PREFIX",str(tessdata))
    if argv==["--serve"]:
        lock=(STATE/"linux-session.lock").open("w")
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return 2
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtGui import QGuiApplication
        QGuiApplication.setDesktopFileName("org.desk.Tool")
        if os.environ.get("XDG_SESSION_TYPE")=="wayland" or os.environ.get("WAYLAND_DISPLAY") and os.environ.get("XDG_SESSION_TYPE")!="x11":
            os.environ["QT_WAYLAND_SHELL_INTEGRATION"]="layer-shell"
        app=QApplication(["desk"]);app.setQuitOnLastWindowClosed(False)
        session=Session(app)
        signal.signal(signal.SIGTERM,lambda *_:app.quit())
        try:return app.exec()
        finally:session.close()
    if not argv or argv[0] in ("help","--help","-h"):print(HELP);return 0
    if argv[0]=="doctor":return doctor()
    if argv[0]=="log":
        path=STATE/"actions.log";print("\n".join(path.read_text().splitlines()[-int(argv[1] if len(argv)>1 else 30):]) if path.exists() else "");return 0
    if argv[0]=="stop":
        (STATE/"stopped").write_text("desk stop\n");CLICK.invalidate(STATE)
        try:result=send(argv,2)
        except (OSError,RuntimeError):result={"code":0,"output":"중지함"}
    else:
        if argv[0] not in ("begin","status","end") and (STATE/"stopped").exists():print(STOP,file=sys.stderr);return 3
        try:result=send(argv)
        except (FileNotFoundError,ConnectionRefusedError):
            if argv[0] in ("status","end"):
                print(("stopped" if (STATE/"stopped").exists() else "not stopped")+"; panel: off");return 0
            if argv[0]!="begin":print("desk begin으로 먼저 시작하세요",file=sys.stderr);return 2
            with (STATE/"linux-session.log").open("a") as logfile:
                subprocess.Popen([sys.executable,str(ROOT/"desk.py"),"--serve"],stdin=subprocess.DEVNULL,stdout=logfile,stderr=logfile,start_new_session=True)
            for _ in range(100):
                try:result=send(argv);break
                except (FileNotFoundError,ConnectionRefusedError):time.sleep(.05)
            else:print("Linux 세션 시작 실패: "+str(STATE/"linux-session.log"),file=sys.stderr);return 2
    if argv[0]=="end" and not result["code"]:
        deadline=time.monotonic()+3
        while socket_path().exists() and time.monotonic()<deadline:time.sleep(.02)
    print(result["output"],file=sys.stderr if result["code"] else sys.stdout)
    return result["code"]


if __name__=="__main__":raise SystemExit(main(sys.argv[1:]))
