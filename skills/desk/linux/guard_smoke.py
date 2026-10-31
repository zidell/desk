#!/usr/bin/env python3
"""Safety-component test in an explicitly nested KWin; never arms the real desktop.

The parent test controller uses desk's X11 backend on the nested compositor's
window. Those events arrive as real input at the nested compositor, separately
from the guard's own injection path. See docs/linux.md for the test contract.
"""
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

sys.pycache_prefix=str(Path.home()/".cache"/"desk"/"test-pycache")
from wayland import Wayland


def main():
    import dbus
    from gi.repository import GLib
    bus=dbus.SessionBus()
    pid=int(dbus.Interface(bus.get_object("org.freedesktop.DBus","/org/freedesktop/DBus"),"org.freedesktop.DBus").GetConnectionUnixProcessID("org.kde.KWin"))
    args=Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    if b"--x11-display" not in args or b"--socket" not in args:
        raise RuntimeError("실제 데스크톱에서는 실행하지 않습니다. X11 안의 별도 KWin만 테스트합니다")
    state=Path(os.environ["DESK_GUARD_TEST_STATE"]);state.mkdir(parents=True,exist_ok=True)
    def pump(seconds):
        deadline=time.monotonic()+seconds
        while True:
            while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
            if time.monotonic()>=deadline:return
            time.sleep(.005)
    guard=Wayland(state,pump);fixture=None
    def stage():
        p=state/"stage"
        return p.read_text().strip() if p.exists() else ""
    try:
        guard.prepare_guard()
        env={**os.environ,"DESK_TEST_STATE":str(state),"QT_LINUX_ACCESSIBILITY_ALWAYS_ON":"1"}
        python=sys.executable or str(Path.home()/".cache"/"desk"/"linux"/"bin"/"python")
        fixture=subprocess.Popen([python,str(Path(__file__).with_name("fixture.py"))],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        deadline=time.monotonic()+60
        (state/"ready").write_text("unarmed")
        while stage()!="arm":
            if time.monotonic()>deadline:raise RuntimeError("parent controller did not arm test")
            pump(.02)
        guard.arm(10)
        target=next(w for w in guard.windows() if w["pid"]==fixture.pid)
        assert guard.activate(target["id"])
        baseline=guard.snapshot()["pointer"];small=False;large=False
        (state/"armed").write_text("ready")
        deadline=time.monotonic()+90
        while not (state/"stopped").exists():
            guard.heartbeat()
            snapshot=guard.snapshot()
            (state/"snapshot.json").write_text(json.dumps(snapshot))
            distance=math.dist(baseline,snapshot["pointer"])
            if snapshot["epoch"]=="0" and 0<distance<10:
                small=True;(state/"small-pass").write_text("ok")
            if snapshot["epoch"]!="0" and distance>=10:
                large=True;(state/"large-pass").write_text("ok")
            if time.monotonic()>deadline:raise RuntimeError("parent controller did not send physical ESC")
            pump(.02)
        assert stage()=="expect-esc","Unexpected physical stop; aborting tests"
        assert small and large,"parent controller did not verify the 10px threshold"
        snapshot=guard.snapshot()
        assert not snapshot["armed"]
        assert not guard.motion(500,400,snapshot)
        pump(.2)
        events=[json.loads(s) for s in (state/"fixture.jsonl").read_text().splitlines()]
        assert not any(e.get("key")==16777216 for e in events),"physical ESC reached app"
        # A separate, explicitly synthetic test session checks lease expiry and
        # release of a held modifier. It never clears a user's stopped session.
        lease=state/"lease";lease.mkdir(exist_ok=True)
        assert guard.guard.Arm(str(lease),10.)
        snapshot=guard.snapshot();assert guard.key("Shift_L",True,snapshot)
        pump(3.6)  # No heartbeat: KWin must stop itself and release held keys.
        assert (lease/"stopped").exists() and not guard.snapshot()["armed"]
        events=[json.loads(s) for s in (state/"fixture.jsonl").read_text().splitlines()]
        assert any(e.get("key")==16777248 and e.get("pressed") is False for e in events),"lease did not release Shift"
        result={"pointerBelow10Ignored":True,"pointerAt10Counted":True,"physicalEscConsumed":True,"stoppedInjectionRefused":True,"leaseStopped":True,"heldShiftReleased":True}
        (state/"result.json").write_text(json.dumps(result))
        print(json.dumps(result))
    finally:
        guard.close()
        if fixture:fixture.terminate();fixture.wait(timeout=3)


if __name__=="__main__":main()
