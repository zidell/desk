"""X11 capture/XTest input and XI2 physical input monitoring."""
import os
import struct
import time


class X11:
    name = "x11"

    def __init__(self, state, pump):
        from Xlib import X, XK, display
        from Xlib.ext import xinput, xtest
        self.X, self.XK, self.xinput, self.xtest = X, XK, xinput, xtest
        self.display = display.Display()
        self.root = self.display.screen().root
        self.state, self.pump = state, pump
        self.epoch = 0
        self.armed = False
        self.threshold = 10
        self.origin = self.pointer()
        self.held_keys, self.held_buttons = set(), set()
        self.escape = self.display.keysym_to_keycode(XK.string_to_keysym("Escape"))

    def screen_locked(self):
        from Xlib.ext import screensaver
        return self.display.has_extension(screensaver.extname) and self.root.screensaver_query_info().state==screensaver.StateOn

    def start(self):
        xi = self.xinput
        self.display.xinput_query_version()
        self.synthetic = {d.deviceid for d in self.display.xinput_query_device(xi.AllDevices).devices if "XTEST" in str(d.name).upper()}
        self.root.xinput_select_events([(xi.AllMasterDevices, xi.RawMotionMask | xi.RawKeyPressMask |
            xi.RawKeyReleaseMask | xi.RawButtonPressMask | xi.RawButtonReleaseMask)])
        self.display.sync()
        geometry=self.root.get_geometry();self.region=[0,0,geometry.width,geometry.height]
        if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
            raise RuntimeError("XWayland는 전체 Wayland 화면을 제어할 수 없습니다. X11 백엔드로 대체하지 않습니다.")

    def arm(self, threshold):
        self.threshold = threshold
        self.epoch = 0
        self.armed = True
        (self.state / "user-input").write_text("0")
        errors = []
        self.root.grab_key(self.escape, self.X.AnyModifier, False, self.X.GrabModeAsync, self.X.GrabModeAsync,
                           onerror=lambda *args: errors.append(args))
        self.display.sync()
        if errors:
            self.armed = False
            raise RuntimeError("전역 ESC를 확보하지 못해 입력을 허용하지 않습니다")

    def pointer(self):
        p = self.root.query_pointer()
        return [p.root_x, p.root_y]

    def activity(self):
        self.epoch += 1
        (self.state / "user-input").write_text(str(self.epoch))

    def stop(self):
        (self.state / "stopped").write_text("physical ESC\n")
        self.armed = False
        self.release()

    def heartbeat(self):
        while self.display.pending_events():
            event = self.display.next_event()
            if not self.armed:
                continue
            if event.type == self.X.KeyPress and event.detail == self.escape:
                self.stop()
                continue
            data = getattr(event, "data", None)
            kind = getattr(event, "evtype", None)
            source=getattr(data,"sourceid",None);detail=getattr(data,"detail",None)
            if isinstance(data,bytes) and len(data)>=12 and kind in (self.xinput.RawMotion,self.xinput.RawKeyPress,self.xinput.RawKeyRelease,self.xinput.RawButtonPress,self.xinput.RawButtonRelease):
                # XI2 raw cookies start at byte 10 of xXIRawEvent.
                _,_,detail,source=struct.unpack_from("=HIIH",data)
            if data is None or source is None or source in self.synthetic:
                continue
            if kind == self.xinput.RawMotion:
                p = self.pointer()
                if sum((a-b)**2 for a,b in zip(p,self.origin)) >= self.threshold**2:
                    self.origin = p
                    self.activity()
            elif kind in (self.xinput.RawKeyPress, self.xinput.RawKeyRelease, self.xinput.RawButtonPress, self.xinput.RawButtonRelease):
                self.activity()
                if kind == self.xinput.RawKeyPress and detail == self.escape:
                    self.stop()

    def atom(self, name):
        return self.display.intern_atom(name)

    def prop(self, window, name):
        value = window.get_full_property(self.atom(name), self.X.AnyPropertyType)
        return value.value if value else None

    def snapshot(self):
        self.heartbeat()
        held=self.display.query_keymap()
        physical=any(byte & ~(sum(1<<(key%8) for key in self.held_keys if key//8==index)) for index,byte in enumerate(held))
        mask=self.root.query_pointer().mask
        physical=physical or bool(mask & sum(1<<(7+b) for b in range(1,6)) & ~sum(1<<(7+b) for b in self.held_buttons if b<=5))
        current = self.prop(self.root, "_NET_ACTIVE_WINDOW")
        if current is None or not current[0]:
            return {"id": "", "pid": 0, "app": "", "title": "", "box": [0,0,0,0], "pointer": self.pointer(), "epoch": str(self.epoch), "armed": self.armed, "locked": self.screen_locked(),"physicalInputHeld":physical}
        window = self.display.create_resource_object("window", int(current[0]))
        geometry = window.get_geometry()
        origin = self.root.translate_coords(window, 0, 0)
        title = self.prop(window, "_NET_WM_NAME")
        if isinstance(title, bytes):
            title = title.decode("utf-8", "replace")
        pid = self.prop(window, "_NET_WM_PID")
        wmclass = window.get_wm_class() or ("", "")
        popups=[]
        for popup in self.root.query_tree().children:
            try:
                if popup.id==window.id or popup.get_attributes().map_state!=self.X.IsViewable:continue
                parent=popup.get_wm_transient_for();family=False
                for _ in range(16):
                    if parent is None:break
                    if parent.id==window.id:family=True;break
                    parent=parent.get_wm_transient_for()
                if family:
                    g=popup.get_geometry();p=self.root.translate_coords(popup,0,0)
                    popups.append({"id":str(popup.id),"box":[p.x,p.y,g.width,g.height]})
            except Exception:continue
        return {"id": str(window.id), "pid": int(pid[0]) if pid is not None else 0,
            "app": wmclass[-1], "title": str(title or window.get_wm_name() or ""),
            "box": [origin.x,origin.y,geometry.width,geometry.height], "pointer": self.pointer(),
            "epoch": str(self.epoch), "armed": self.armed, "locked": self.screen_locked(),"physicalInputHeld":physical,"popups":popups}

    def capture(self):
        from PIL import Image
        g = self.root.get_geometry()
        raw = self.root.get_image(0, 0, g.width, g.height, self.X.ZPixmap, 0xffffffff)
        # X servers commonly store depth-24 pixels in 32-bit little-endian BGRX.
        setup = self.display.display.info
        formats = [f for f in setup.pixmap_formats if f.depth == g.depth]
        if not formats or formats[0].bits_per_pixel != 32 or setup.image_byte_order != self.X.LSBFirst:
            raise RuntimeError("지원하지 않는 X11 픽셀 형식")
        return Image.frombytes("RGB", (g.width,g.height), raw.data, "raw", "BGRX"), [0,0,g.width,g.height]

    def windows(self):
        result=[]
        ids=self.prop(self.root,"_NET_CLIENT_LIST_STACKING")
        if ids is None:
            current=self.snapshot()
            return [current] if current.get("id") else []
        for ident in ids:
            try:
                w=self.display.create_resource_object("window",int(ident));pid=self.prop(w,"_NET_WM_PID")
                if w.get_attributes().map_state!=self.X.IsViewable:continue
                title=self.prop(w,"_NET_WM_NAME")
                if isinstance(title,bytes):title=title.decode("utf-8","replace")
                result.append({"id":str(w.id),"pid":int(pid[0]) if pid is not None else 0,"app":(w.get_wm_class() or ("",))[-1],"title":str(title or w.get_wm_name() or "")})
            except Exception:continue
        return result

    def activate(self,ident):
        if not self.armed or (self.state/"stopped").exists():return False
        from Xlib.protocol import event
        w=self.display.create_resource_object("window",int(ident))
        message=event.ClientMessage(window=w,client_type=self.atom("_NET_ACTIVE_WINDOW"),data=(32,[2,self.X.CurrentTime,0,0,0]))
        self.root.send_event(message,event_mask=self.X.SubstructureRedirectMask|self.X.SubstructureNotifyMask)
        self.display.flush();return True

    def allowed(self, expected):
        current = self.snapshot()
        return self.armed and not current["locked"] and not (self.state / "stopped").exists() and all(current[k] == expected[k] for k in ("id","pid","box","epoch"))

    def motion(self, x, y, window):
        if not self.allowed(window):
            return False
        self.xtest.fake_input(self.display, self.X.MotionNotify, x=int(x), y=int(y))
        self.display.sync(); self.origin = self.pointer()
        return True

    def button(self, button, down, window):
        number = {272:1,273:3,274:2}[button]
        if down and not self.allowed(window):
            return False
        if down:
            child=self.root.query_pointer().child
            active=self.display.create_resource_object("window",int(window["id"]))
            # The actual root child must contain the foreground window (possibly
            # inside a WM decoration frame), rather than an occluding other app.
            ancestors={active.id}
            for _ in range(32):
                if active.id==self.root.id:break
                active=active.query_tree().parent;ancestors.add(active.id)
            if not child:return False
            if child.id not in ancestors:
                popup=child;family=False
                for _ in range(16):
                    popup=popup.get_wm_transient_for()
                    if popup is None:break
                    if popup.id==int(window["id"]):family=True;break
                if not family:return False
        self.xtest.fake_input(self.display, self.X.ButtonPress if down else self.X.ButtonRelease, number)
        self.display.sync()
        if down: self.held_buttons.add(number)
        else: self.held_buttons.discard(number)
        return True

    def key(self, symbol, down, window):
        if down and not self.allowed(window):
            return False
        sym = self.XK.string_to_keysym(symbol)
        code = self.display.keysym_to_keycode(sym)
        if not code:
            return False
        # XI2 still monitors physical ESC while the passive grab is temporarily
        # released for this one tool-generated Escape delivery.
        own_esc = code == self.escape
        if own_esc: self.root.ungrab_key(self.escape, self.X.AnyModifier)
        self.xtest.fake_input(self.display, self.X.KeyPress if down else self.X.KeyRelease, code)
        if own_esc: self.root.grab_key(self.escape,self.X.AnyModifier,False,self.X.GrabModeAsync,self.X.GrabModeAsync)
        self.display.sync()
        if down: self.held_keys.add(code)
        else: self.held_keys.discard(code)
        return True

    def scroll(self, dx, dy, window):
        if not self.allowed(window): return False
        for amount,positive,negative in ((dy,5,4),(dx,7,6)):
            for _ in range(abs(round(amount))):
                if not self.allowed(window): return False
                code = positive if amount>0 else negative
                self.xtest.fake_input(self.display,self.X.ButtonPress,code)
                self.xtest.fake_input(self.display,self.X.ButtonRelease,code)
                self.display.sync()
        return True

    def release(self):
        for code in self.held_keys: self.xtest.fake_input(self.display,self.X.KeyRelease,code)
        for code in self.held_buttons: self.xtest.fake_input(self.display,self.X.ButtonRelease,code)
        self.held_keys.clear();self.held_buttons.clear();self.display.sync()

    def close(self):
        self.armed = False
        self.release()
        self.root.ungrab_key(self.escape,self.X.AnyModifier)
        self.display.sync()
        self.display.close()
