"""Wayland transport: portal/PipeWire capture, compositor-specific safety and input.

KDE's session-scoped guard is loaded as a QML extension, never installed into system
plugin directories. Other compositors fail closed until a safety adapter exists.
"""
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import uuid


class Wayland:
    name = "wayland-kde"

    def __init__(self, state, pump):
        import dbus
        from dbus.mainloop.glib import DBusGMainLoop
        DBusGMainLoop(set_as_default=True)
        self.dbus = dbus
        self.bus = dbus.SessionBus()
        try:
            dbus.Interface(self.bus.get_object("org.freedesktop.portal.Desktop","/org/freedesktop/portal/desktop"),
                           "org.freedesktop.host.portal.Registry").Register("org.desk.Tool",dbus.Dictionary({},signature="sv"))
        except dbus.DBusException:
            pass  # Older portal versions identify the host application themselves.
        self.state, self.pump = Path(state), pump
        self.session = None
        self.pipeline = None
        self.frame = None
        self.frame_lock = threading.Lock()
        self.script_name = "desk-guard-" + str(os.getpid())
        self.guard = None

    def prepare_guard(self):
        if "KDE" not in os.environ.get("XDG_CURRENT_DESKTOP", "").upper():
            raise RuntimeError("이 Wayland compositor의 중지·사용자 개입 어댑터가 없습니다. 현재 KDE만 지원합니다.")
        try:
            existing=self.dbus.Interface(self.bus.get_object("org.kde.KWin","/DeskGuard",introspect=False),"org.desk.Guard")
            existing.Snapshot()
        except self.dbus.DBusException:
            pass
        else:
            raise RuntimeError("다른 desk Wayland 세션이 이미 안전 어댑터를 사용 중입니다. 그 세션을 먼저 종료하세요")
        root = Path(__file__).resolve().parent / "kwin"
        sources = [root / n for n in ("guard.cpp", "CMakeLists.txt")]
        import hashlib
        sdk=Path("/usr/include/kwin/config-kwin.h")
        version=subprocess.check_output(["kwin_wayland","--version"],stderr=subprocess.DEVNULL,timeout=5)
        import re
        info=str(self.dbus.Interface(self.bus.get_object("org.kde.KWin","/KWin"),"org.kde.KWin").supportInformation())
        running=re.search(r"KWin version:\s*(\S+)",info)
        binary=re.search(rb"kwin\s+(\S+)",version)
        headers=re.search(r'#define KWIN_PLUGIN_VERSION_STRING "([^"]+)"',sdk.read_text())
        if not running or not binary or not headers or len({running[1],binary[1].decode(),headers[1]})!=1:
            raise RuntimeError("실행 중 KWin·설치된 KWin·개발 SDK 버전이 일치하지 않아 어댑터를 로드하지 않습니다")
        digest=hashlib.sha256(b"".join(p.read_bytes() for p in sources)+sdk.read_bytes()+version).hexdigest()[:16]
        # QML modules are process-global and protected after import. Share one
        # immutable library per source revision across all isolated test states.
        build = Path.home()/".cache"/"desk"/"kwin-build"/digest
        lib = build / "libdeskguard.so"
        import fcntl
        build.parent.mkdir(parents=True,exist_ok=True)
        with build.with_suffix(".lock").open("w") as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            if not lib.exists():
                with (self.state / "build.log").open("w") as log:
                    for command in (["cmake", "-S", str(root), "-B", str(build), "-DCMAKE_BUILD_TYPE=Release"],
                                    ["cmake", "--build", str(build), "-j2"]):
                        subprocess.run(command, stdout=log, stderr=log, check=True, timeout=180)
        (build/"qmldir").write_text("module DeskGuard.Build"+digest+"\nplugin deskguard\n")
        qml = build / "main.qml"
        qml.write_text('import QtQuick\nimport "." as Desk\nDesk.Guard {}\n')
        scripting = self.dbus.Interface(self.bus.get_object("org.kde.KWin", "/Scripting"), "org.kde.kwin.Scripting")
        self.scripting = scripting
        script_id = scripting.loadDeclarativeScript(str(qml), self.script_name, signature="ss")
        if script_id < 0:
            raise RuntimeError("KWin 안전 어댑터를 로드하지 못했습니다")
        obj = self.bus.get_object("org.kde.KWin", "/Scripting/Script" + str(script_id))
        self.dbus.Interface(obj, "org.kde.kwin.Script").run()
        for _ in range(100):
            try:
                g = self.dbus.Interface(self.bus.get_object("org.kde.KWin", "/DeskGuard",introspect=False), "org.desk.Guard")
                json.loads(str(g.Snapshot()))
                self.guard = self.dbus.Interface(self.bus.get_object("org.kde.KWin", "/DeskGuard"), "org.desk.Guard")
                return
            except self.dbus.DBusException:
                self.pump(.03)
        raise RuntimeError("KWin 안전 어댑터 시작 실패: journalctl --user -b에서 QML 오류 확인")

    def request(self, interface, method, *args):
        dbus = self.dbus
        token = "desk" + uuid.uuid4().hex
        sender = self.bus.get_unique_name()[1:].replace(".", "_")
        path = "/org/freedesktop/portal/desktop/request/" + sender + "/" + token
        response = []
        match = self.bus.add_signal_receiver(lambda code, result: response.append((code, result)),
            signal_name="Response", dbus_interface="org.freedesktop.portal.Request", path=path)
        options = dict(args[-1]); options["handle_token"] = token
        portal = dbus.Interface(self.bus.get_object("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop"), interface)
        getattr(portal, method)(*args[:-1], dbus.Dictionary(options, signature="sv"))
        try:
            deadline = time.monotonic() + 600
            while not response:
                if time.monotonic() > deadline:
                    try:
                        dbus.Interface(self.bus.get_object("org.freedesktop.portal.Desktop", path), "org.freedesktop.portal.Request").Close()
                    except dbus.DBusException:
                        pass
                    raise RuntimeError("화면 공유 승인 대기 시간 초과")
                self.pump(.02)
            code, result = response[0]
            if code:
                raise RuntimeError("화면 공유가 취소되었거나 포털이 거부했습니다 (응답 " + str(code) + ")")
            return result
        finally:
            match.remove()

    def start(self):
        self.prepare_guard()
        if os.environ.get("DESK_CAPTURE","native")=="native":
            from PyQt6.QtGui import QGuiApplication
            screens=QGuiApplication.screens()
            wanted=os.environ.get("DESK_SCREEN")
            screen=next((s for s in screens if s.name()==wanted),None) if wanted else QGuiApplication.primaryScreen()
            if screen is None:raise RuntimeError("캡처할 모니터를 찾지 못했습니다: "+str(wanted))
            rect=screen.geometry();self.region=[rect.x(),rect.y(),rect.width(),rect.height()]
            self.capture()  # Prove screenshot authorization before enabling input.
            return
        dbus = self.dbus
        iface = "org.freedesktop.portal.ScreenCast"
        result = self.request(iface, "CreateSession", {"session_handle_token": "desk" + uuid.uuid4().hex})
        self.session = result["session_handle"]
        options = {"types": dbus.UInt32(1), "multiple": False, "cursor_mode": dbus.UInt32(1), "persist_mode": dbus.UInt32(2)}
        tokenfile = self.state / "portal-restore-token"
        if tokenfile.exists():
            options["restore_token"] = tokenfile.read_text().strip()
        self.request(iface, "SelectSources", self.session, options)
        result = self.request(iface, "Start", self.session, "", {})
        streams = result.get("streams", [])
        if len(streams) != 1:
            raise RuntimeError("현재 Linux는 한 번에 모니터 하나를 선택해야 합니다")
        node, properties = streams[0]
        position = properties.get("position")
        size = properties.get("size")
        if position is None or size is None:
            raise RuntimeError("포털이 논리 화면 위치·크기를 제공하지 않아 좌표를 검증할 수 없습니다")
        self.region = [int(v) for v in (*position, *size)]
        if "restore_token" in result:
            tokenfile.write_text(str(result["restore_token"])); tokenfile.chmod(0o600)
        portal = dbus.Interface(self.bus.get_object("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop"), iface)
        self.pipe_fd = portal.OpenPipeWireRemote(self.session, dbus.Dictionary({}, signature="sv")).take()
        import gi
        gi.require_version("Gst", "1.0")
        gi.require_version("GstVideo", "1.0")
        from gi.repository import Gst, GstVideo
        Gst.init(None)
        self.Gst, self.GstVideo = Gst, GstVideo
        self.pipeline = Gst.parse_launch(f"pipewiresrc fd={self.pipe_fd} path={int(node)} do-timestamp=true ! videoconvert ! video/x-raw,format=RGB ! appsink name=frames emit-signals=true max-buffers=1 drop=true sync=false")
        sink = self.pipeline.get_by_name("frames")
        sink.connect("new-sample", self.new_sample)
        self.pipeline.set_state(Gst.State.PLAYING)
        deadline = time.monotonic() + 12
        while self.frame is None:
            self.pump(.03)
            message = self.pipeline.get_bus().pop_filtered(Gst.MessageType.ERROR)
            if message:
                raise RuntimeError("PipeWire: " + str(message.parse_error()))
            if time.monotonic() > deadline:
                raise RuntimeError("PipeWire 화면 프레임을 받지 못했습니다")

    def new_sample(self, sink):
        from PIL import Image
        sample = sink.emit("pull-sample")
        info = self.GstVideo.VideoInfo.new_from_caps(sample.get_caps())
        buffer = sample.get_buffer()
        ok, mapped = buffer.map(self.Gst.MapFlags.READ)
        if not ok:
            return self.Gst.FlowReturn.ERROR
        try:
            image = Image.frombytes("RGB", (info.width, info.height), bytes(mapped.data), "raw", "RGB", info.stride[0])
            with self.frame_lock:
                self.frame = image
        finally:
            buffer.unmap(mapped)
        return self.Gst.FlowReturn.OK

    def arm(self, threshold):
        if not self.guard.Arm(str(self.state), float(threshold)):
            raise RuntimeError("안전 어댑터 활성화 실패")

    def heartbeat(self):
        if self.guard:
            self.guard.Heartbeat()

    def snapshot(self):
        return json.loads(str(self.guard.Snapshot()))

    def windows(self):
        return json.loads(str(self.guard.Windows()))

    def activate(self,ident):
        return bool(self.guard.Activate(ident))

    def capture(self):
        if os.environ.get("DESK_CAPTURE","native")=="native":
            from PIL import Image
            from PyQt6.QtGui import QGuiApplication
            from PyQt6.QtCore import QPoint
            point=self.snapshot()["pointer"]
            screen=QGuiApplication.screenAt(QPoint(round(point[0]),round(point[1])))
            if screen is None:raise RuntimeError("커서가 있는 모니터를 식별하지 못했습니다")
            if os.environ.get("DESK_SCREEN") and screen.name()!=os.environ["DESK_SCREEN"]:
                raise RuntimeError("커서를 DESK_SCREEN으로 지정한 모니터에 두어야 합니다")
            rect=screen.geometry();self.region=[rect.x(),rect.y(),rect.width(),rect.height()]
            path=self.state/"capture-current.png"
            path.unlink(missing_ok=True)
            # Spectacle is KDE's already-authorized screen capture executable.
            # Background/nonotify/new-instance avoids UI, clipboard and any
            # existing user Spectacle window. desk owns the visible stop panel.
            environment=dict(os.environ);environment.pop("QT_WAYLAND_SHELL_INTEGRATION",None)
            process=subprocess.Popen(["spectacle","--new-instance","--background","--nonotify","--current","--output",str(path)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,env=environment)
            deadline=time.monotonic()+7
            try:
                while process.poll() is None:
                    if time.monotonic()>deadline:raise RuntimeError("KDE 캡처 시간 초과")
                    self.pump(.01)
                error=process.stderr.read().decode("utf-8","replace")
                if process.returncode:raise RuntimeError("KDE 캡처 실패: "+error.strip())
                if not path.exists():raise RuntimeError("KDE 캡처 파일이 생성되지 않았습니다. Spectacle과 OpenGL compositor 동작을 확인하세요")
                with Image.open(path) as im:return im.convert("RGB"),list(self.region)
            finally:
                if process.poll() is None:process.kill();process.wait()
        self.pump(.04)
        with self.frame_lock:
            if self.frame is None:
                raise RuntimeError("화면 스트림이 닫혔습니다")
            return self.frame.copy(), list(self.region)

    @staticmethod
    def context(window):
        return json.dumps(window, ensure_ascii=False), str(window["epoch"])

    def motion(self, x, y, window):
        return bool(self.guard.Motion(float(x), float(y), *self.context(window)))

    def button(self, button, down, window):
        return bool(self.guard.Button(int(button), bool(down), *self.context(window)))

    def key(self, symbol, down, window):
        return bool(self.guard.Key(symbol, bool(down), *self.context(window)))

    def chord(self,symbols,window):
        return bool(self.guard.Keys(self.dbus.Array(symbols,signature="s"),*self.context(window)))

    def set_clipboard(self,text):
        # A layer-shell panel deliberately never has keyboard focus, hence no
        # valid Wayland serial for QClipboard.setText. KDE's clipboard service
        # owns a data-control selection. Only write the supplied task text;
        # never query clipboard contents or history.
        clipboard=self.dbus.Interface(self.bus.get_object("org.kde.klipper","/klipper"),"org.kde.klipper.klipper")
        clipboard.setClipboardContents(text)

    def scroll(self, dx, dy, window):
        return bool(self.guard.Scroll(float(dx), float(dy), *self.context(window)))

    def close(self):
        if self.guard:
            self.guard.Disarm()
        if self.pipeline:
            self.pipeline.set_state(self.Gst.State.NULL)
        if hasattr(self, "pipe_fd"):
            os.close(self.pipe_fd)
        if self.session:
            try:
                self.dbus.Interface(self.bus.get_object("org.freedesktop.portal.Desktop", self.session), "org.freedesktop.portal.Session").Close()
            except self.dbus.DBusException:
                pass
        if hasattr(self, "scripting"):
            self.scripting.unloadScript(self.script_name)
