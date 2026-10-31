"""Windows desk orchestration. No app data, DOM, automation writes or hidden window capture."""
import asyncio
import concurrent.futures
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parent.parent
os.environ["DESK_WIDGET"] = str(ROOT / "widget.json")
# 에이전트 셸(Claude Code PowerShell 도구 등)이 넣은 NO_COLOR가 desk로 띄운 앱·터미널까지 물려가 색이 꺼진다
os.environ.pop("NO_COLOR", None)
STATE = Path(os.environ.get("DESK_STATE", Path.home() / ".cache" / "desk"))
EXE = STATE / "desk-native.exe"
CREATE_NO_WINDOW = 0x08000000
BACKGROUND_FLAGS = CREATE_NO_WINDOW | 0x01000000 | 0x00000200  # break away from caller cleanup/job, own process group
QUIET = False
STOP_MSG = "중지됨: 사용자가 중지했습니다. 한 일과 멈춘 지점을 보고하고 새 요청을 기다리세요."


class DeskError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def read(name, default=""):
    try:
        return (STATE / name).read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError):
        return default


def write(name, value):
    target = STATE / name
    tmp = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
    tmp.write_text(value, encoding="utf-8")
    os.replace(tmp, target)


def load(name, default=None):
    try:
        return json.loads(read(name))
    except (ValueError, TypeError):
        return default


def save(name, value):
    write(name, json.dumps(value, ensure_ascii=False))


def check_stop():
    if (STATE / "stopped").exists():
        raise DeskError(3, STOP_MSG)


def log(message):
    with (STATE / "actions.log").open("a", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + message + "\n")
    write("panel-message", message)
    lines = load("panel-lines.json", [])
    if not lines or lines[-1] != message:
        save("panel-lines.json", (lines + [message])[-2:])


def run(argv, timeout=15, stopped=True, input_data=None):
    """Bound every native/UIA wait; stop takes effect while children are running too."""
    if stopped:
        check_stop()
    p = subprocess.Popen([str(a) for a in argv], stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         creationflags=CREATE_NO_WINDOW)
    start = time.monotonic()
    try:
        while True:
            if stopped:
                check_stop()
            try:
                pending_input, input_data = input_data, None
                out, err = p.communicate(input=pending_input, timeout=0.08)
                break
            except subprocess.TimeoutExpired:
                if time.monotonic() - start > timeout:
                    raise DeskError(2, "로컬 도구 응답 시간 초과: " + Path(str(argv[0])).name)
    except BaseException:
        # Give SendInput's finally blocks time to release held modifiers/buttons on stop.
        try:
            p.communicate(timeout=.5)
        except subprocess.TimeoutExpired:
            p.kill()
            p.communicate()
        raise
    if p.returncode:
        raise DeskError(p.returncode, err.decode("utf-8", "replace").strip() or out.decode("utf-8", "replace").strip() or "로컬 도구 실패")
    return out.decode("utf-8-sig", "replace").strip()


def build():
    source = ROOT / "windows" / "DeskNative.cs"
    digest = hashlib.sha256(source.read_bytes() + (ROOT / "widget.json").read_bytes()).hexdigest()
    if EXE.exists() and read("native.sha256") == digest:
        return
    had_panel = panel_alive()
    if had_panel:
        write("panel-close", "1")
        for _ in range(40):
            if not panel_alive():
                break
            time.sleep(.1)
    framework = Path(os.environ["WINDIR"]) / "Microsoft.NET" / "Framework64" / "v4.0.30319"
    refs = ["System.Drawing.dll", "System.Windows.Forms.dll", "System.Web.Extensions.dll"]
    refs += [str(framework / "WPF" / n) for n in ("UIAutomationClient.dll", "UIAutomationTypes.dll", "WindowsBase.dll")]
    temp = EXE.with_suffix(".new.exe")
    result = run([framework / "csc.exe", "/nologo", "/target:exe", "/platform:x64", "/out:" + str(temp),
                  *["/r:" + r for r in refs], source], timeout=40, stopped=False)
    if result:
        print(result, file=sys.stderr)
    os.replace(temp, EXE)
    write("native.sha256", digest)
    if had_panel:
        (STATE / "panel-close").unlink(missing_ok=True)
        start_panel()


def native(command, *args, structured=False, timeout=12, stopped=True, input_data=None):
    result = run([EXE, command, *args], timeout=timeout, stopped=stopped, input_data=input_data)
    return json.loads(result) if structured else result


def pid_alive(pid):
    if not pid:
        return False
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return False
    ctypes.windll.kernel32.CloseHandle(handle)
    return True


def panel_alive():
    for attempt in range(2):
        try:
            if not pid_alive(read("overlay.pid")):
                return False
            if time.time() - (STATE / "heartbeat").stat().st_mtime < 3:
                return True
        except (FileNotFoundError, ValueError):
            pass
        if attempt == 0:
            time.sleep(.1)
    return False


def ensure_session():
    check_stop()
    if not load("session.json") or not panel_alive():
        raise DeskError(2, "제어 세션 또는 중지 패널이 없습니다. 새 요청이면 desk begin '요청 요약'부터 실행하세요.")


def wait_idle():
    ensure_session()
    idle = float(os.environ.get("DESK_USER_IDLE", "5"))
    announced = False
    while True:
        check_stop()
        if not panel_alive():
            raise DeskError(2, "중지 패널 연결이 끊겨 멈춤")
        try:
            age = time.time() - (STATE / "user-input").stat().st_mtime
        except FileNotFoundError:
            return
        if age >= idle:
            return
        if not announced:
            log("사용자 입력 감지 · 멈추면 " + str(idle) + "초 뒤 다시 확인")
            announced = True
        time.sleep(.1)


def window():
    w = native("window", structured=True)
    if not w["desktop"] or not w["hwnd"]:
        raise DeskError(2, "보호된 데스크톱·잠금 화면에서는 사용자에게 넘겨 주세요.")
    return w


def signature(w):
    # A page title can change while loading without a user moving the window.
    return w["hwnd"], w["pid"], w["box"]


def remember(w, focus=None):
    save("expect.json", {"window": w, "focus": w["focus"], "observed": time.time(), "inputEpoch": CLICK.input_epoch(STATE)})


def expected(keys=False):
    wait_idle()
    want = load("expect.json")
    now = window()
    if not want:
        raise DeskError(6, "마지막으로 본 화면이 없습니다. desk see부터 실행하세요.")
    if signature(want["window"]) != signature(now):
        raise DeskError(6, "마지막으로 본 창이 바뀌거나 이동해 입력하지 않음. desk see로 다시 확인하세요.")
    if keys and want["window"]["focus"] != now["focus"]:
        raise DeskError(6, "키보드 포커스가 바뀌어 입력하지 않음. desk see로 다시 확인하세요.")
    return now


def boxarg(b):
    return ",".join(str(int(v)) for v in b)


def screen_box():
    r = native("size", structured=True)
    return [r["X"], r["Y"], r["Width"], r["Height"]]


def intersection(a, b):
    x, y = max(a[0], b[0]), max(a[1], b[1])
    return [x, y, max(0, min(a[0]+a[2], b[0]+b[2])-x), max(0, min(a[1]+a[3], b[1]+b[3])-y)]


def region(args, w, default_full=False):
    if "--region" in args:
        try:
            b = [int(v) for v in args[args.index("--region")+1].split(",")]
        except (ValueError, IndexError):
            raise DeskError(2, "--region x,y,w,h")
        if len(b) != 4 or min(b[2:]) <= 0:
            raise DeskError(2, "캡처 영역 크기는 양수여야 합니다.")
    else:
        b = screen_box() if "--full" in args or default_full and "--window" not in args else w["box"]
    b = intersection(b, screen_box())
    if not b[2] or not b[3]:
        raise DeskError(2, "화면 밖 캡처 영역")
    return b


def capture(path, b):
    save("focus-viz.json", {"id": uuid.uuid4().hex, "mode": "none"})
    (STATE / "focus-hidden-ack").unlink(missing_ok=True)
    write("focus-hidden", "1")
    write("highlight", "")
    try:
        until = time.monotonic() + 1
        while panel_alive() and not (STATE / "focus-hidden-ack").exists():
            check_stop()
            if time.monotonic() > until:
                raise DeskError(2, "인식 영역 표시를 숨기지 못해 캡처를 멈춤")
            time.sleep(.02)
        native("capture", path, boxarg(b), structured=True)
    finally:
        (STATE / "focus-hidden").unlink(missing_ok=True)
    # Our panel is UI, not task content; never feed its text to OCR/detector.
    panel = read("panel-rect")
    if panel:
        from PIL import Image, ImageDraw
        pb = intersection([int(v) for v in panel.split(",")], b)
        if pb[2] and pb[3]:
            with Image.open(path) as im:
                ImageDraw.Draw(im).rectangle((pb[0]-b[0], pb[1]-b[1], pb[0]+pb[2]-b[0]-1, pb[1]+pb[3]-b[1]-1), fill="black")
                im.save(path)


async def ocr_async(path, b):
    from winrt.windows.storage import StorageFile, FileAccessMode
    from winrt.windows.graphics.imaging import BitmapDecoder, BitmapPixelFormat, BitmapAlphaMode
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.globalization import Language
    file = await StorageFile.get_file_from_path_async(str(path.resolve()))
    stream = await file.open_async(FileAccessMode.READ)
    try:
        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_converted_async(BitmapPixelFormat.BGRA8, BitmapAlphaMode.IGNORE)
        engines = []
        for tag in ("ko", "en-US"):
            lang = Language(tag)
            if OcrEngine.is_language_supported(lang):
                engines.append(OcrEngine.try_create_from_language(lang))
        if not engines:
            engine = OcrEngine.try_create_from_user_profile_languages()
            if engine:
                engines.append(engine)
        if not engines:
            raise DeskError(2, "Windows OCR 언어 팩이 없습니다. Windows 설정에서 한국어 또는 영어 OCR을 설치하세요.")
        # OcrEngine has a maximum bitmap dimension. Preserve screen coordinates when scaled.
        scale = 1.0
        if max(bitmap.pixel_width, bitmap.pixel_height) > OcrEngine.max_image_dimension:
            from PIL import Image
            scaled = path.with_suffix(".ocr.png")
            with Image.open(path) as im:
                scale = OcrEngine.max_image_dimension / max(im.size)
                im.resize((int(im.width*scale), int(im.height*scale))).save(scaled)
            return await ocr_scaled(scaled, b, scale, engines)
        return await recognize(bitmap, b, scale, engines)
    finally:
        stream.close()


async def ocr_scaled(path, b, scale, engines):
    from winrt.windows.storage import StorageFile, FileAccessMode
    from winrt.windows.graphics.imaging import BitmapDecoder, BitmapPixelFormat, BitmapAlphaMode
    f = await StorageFile.get_file_from_path_async(str(path.resolve()))
    stream = await f.open_async(FileAccessMode.READ)
    try:
        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_converted_async(BitmapPixelFormat.BGRA8, BitmapAlphaMode.IGNORE)
        return await recognize(bitmap, b, scale, engines)
    finally:
        stream.close()
        path.unlink(missing_ok=True)


async def recognize(bitmap, b, scale, engines):
    rows = []
    for engine in engines:
        result = await engine.recognize_async(bitmap)
        for line in result.lines:
            words = list(line.words)
            if not words:
                continue
            rects = [w.bounding_rect for w in words]
            x, y = min(r.x for r in rects), min(r.y for r in rects)
            right, bottom = max(r.x+r.width for r in rects), max(r.y+r.height for r in rects)
            # Prefer Korean OCR for mixed Korean/English; keep nonduplicate English-only lines.
            row = {"box": [b[0]+x/scale, b[1]+y/scale, (right-x)/scale, (bottom-y)/scale], "text": line.text}
            if not any(abs(r["box"][0]-row["box"][0]) < 12 and abs(r["box"][1]-row["box"][1]) < 8 for r in rows):
                rows.append(row)
    return rows


def ocr(path, b):
    return asyncio.run(ocr_async(path, b))


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


CLICK = module("desk_click_evidence", "desk-click.py")


def detector_socket():
    return "\\\\.\\pipe\\desk-detect-" + hashlib.sha256(str(STATE).encode()).hexdigest()[:16]


def ensure_detector():
    weights = STATE / "omni" / "weights" / "icon_detect_v3" / "model.pt"
    if not weights.exists():
        return False
    sock = detector_socket()
    fuse = module("desk_fusion", "desk-see.py")
    revision = hashlib.sha256((ROOT / "desk-see.py").read_bytes() + (ROOT / "windows" / "local_vision.py").read_bytes()
                              + read("vision.json").encode()).hexdigest()
    if pid_alive(read("detector.pid")) and read("detector.revision") != revision:
        import psutil
        worker = psutil.Process(int(read("detector.pid")))
        if any(Path(a).name == "desk-see.py" for a in worker.cmdline()):
            worker.terminate()
            worker.wait(5)
        else:
            raise DeskError(2, "검출기 PID가 다른 프로세스를 가리켜 종료하지 않았습니다.")
    if pid_alive(read("detector.pid")):
        try:
            return fuse.request(sock, "ping", timeout=.5) == "ready"
        except (OSError, EOFError, TimeoutError):
            pass
    if not pid_alive(read("detector.pid")):
        if not (STATE / "detector.auth").exists():
            (STATE / "detector.auth").write_bytes(os.urandom(32))
        with (STATE / "detector.log").open("ab") as log_file:
            p = subprocess.Popen([sys.executable, "-X", "utf8", str(ROOT / "desk-see.py"), "serve", sock, str(weights)],
                                 stdin=subprocess.DEVNULL, stdout=log_file, stderr=log_file, creationflags=BACKGROUND_FLAGS)
        write("detector.pid", str(p.pid))
        write("detector.revision", revision)
    for _ in range(200):
        check_stop()
        if not pid_alive(read("detector.pid")):
            return False
        try:
            return fuse.request(sock, "ping", timeout=.2) == "ready"
        except (OSError, EOFError, TimeoutError):
            time.sleep(.1)
    return False


def detector(path, b):
    if not ensure_detector():
        return ""
    fuse = module("desk_fusion", "desk-see.py")
    return fuse.request(detector_socket(), f"{path}\t{b[0]}\t{b[1]}\t{b[2]}")


ROLE = {"Button":"AXButton", "Hyperlink":"AXLink", "Edit":"AXTextField", "ComboBox":"AXComboBox",
        "CheckBox":"AXCheckBox", "RadioButton":"AXRadioButton", "MenuItem":"AXMenuItem", "TabItem":"AXTab",
        "Slider":"AXSlider", "TreeItem":"AXRow", "ListItem":"AXRow", "DataItem":"AXCell", "Text":"Text"}


def host_window(w):
    session = load("session.json", {})
    return w["pid"] in session.get("host_pids", []) or w["app"].lower() in {"windowsterminal", "conhost", "powershell", "pwsh", "cmd", "codex", "tuidock", "gifiles"}


def blocked_host(w, keys=False):
    if host_window(w):
        raise DeskError(1, "에이전트가 실행되는 터미널·호스트 창에는 입력하거나 그 안의 명령을 검색하지 않습니다. desk open으로 대상 앱을 앞으로 가져오세요.")


def show_scan(elements, b, w):
    save("focus-viz.json", {"id": uuid.uuid4().hex, "mode": "scan", "frame": b, "hwnd": w["hwnd"],
                           "label": "화면 인식 · " + str(len(elements)) + "개 요소",
                           "items": [{"box": e["box"], "kind": "A" if "A" in e["src"] else "O" if e["kind"] == "글자" else "D"}
                                     for e in elements]})


def snapshot(args=(), display=True, use_detector=True, frame=None):
    wait_idle()
    observed_epoch = CLICK.input_epoch(STATE)
    if frame is None:
        before = window()
        b = region(args, before)
        raw = STATE / "screen.png"
        capture(raw, b)
    else:
        raw, b, before, frame_epoch = frame
        if frame_epoch != observed_epoch:
            raise DeskError(6, "결과 캡처 뒤 사용자 입력이 바뀌어 멈춤")
    warnings = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        fo = pool.submit(ocr, raw, b)
        fa = pool.submit(native, "uia", structured=True, timeout=6)
        fd = pool.submit(detector, raw, b) if use_detector else None
        try:
            texts = fo.result()
        except Exception as e:
            texts = []
            warnings.append("OCR: " + str(e))
        try:
            uia = fa.result()
            if uia["partial"]:
                warnings.append("UIA: 시간 예산 안에 일부만 읽음")
        except DeskError as e:
            if e.code in (3, 6):
                raise
            uia = {"elements": [], "focus": native("focus")}
            warnings.append("UIA: " + str(e))
        try:
            det = fd.result() if fd else ""
        except Exception as e:
            det = ""
            warnings.append("검출기: " + str(e))
    after = window()
    if signature(before) != signature(after):
        raise DeskError(6, "화면을 읽는 중 창이 바뀌어 결과를 사용하지 않음")
    if host_window(before):
        texts, det = [], ""
        uia["elements"] = []
        warnings.append("에이전트 호스트 창: 자기 명령을 대상 내용으로 읽지 않음. desk open으로 대상 앱을 선택하세요.")
    ocrfile, axfile, detfile = [STATE / n for n in ("ocr.tsv", "ax.tsv", "det.tsv")]
    ocrfile.write_text("".join(boxarg(r["box"])+"\t"+r["text"].replace("\t", " ")+"\n" for r in texts), encoding="utf-8")
    controls = []
    for r in uia["elements"]:
        if not all(intersection(r["box"], b)[2:]):
            continue
        if r["role"] == "Text":
            continue  # screenshot OCR reads visible text; never dump a document's accessible text wholesale
        controls.append(boxarg(intersection(r["box"], b))+"\t"+ROLE.get(r["role"], r["role"])+"\t"+r["name"][:120]+"\t"+r["state"]+"\n")
    axfile.write_text("".join(controls), encoding="utf-8")
    detfile.write_text(det if not det.startswith("ERROR") else "", encoding="utf-8")
    fuse = module("desk_fusion", "desk-see.py")
    elements = fuse.fuse(str(ocrfile), str(axfile), str(detfile))
    if display and not host_window(before):
        show_scan(elements, b, before)
    for i, e in enumerate(elements, 1):
        e["number"] = i
        e["src"] = "".join(c for c in "AODC" if c in e["src"])
    save("elements.json", {"window": before, "box": b, "elements": elements, "time": time.time()})
    CLICK.observe(STATE, raw, b, before, elements, uia["elements"], epoch=observed_epoch)
    remember(after, uia["focus"])
    if display:
        print("→ " + before["app"] + " · " + before["title"])
        for warning in warnings:
            print("⚠ " + warning)
        for e in elements:
            print_element(e)
        out = STATE / "shots" / ("see-"+uuid.uuid4().hex[:8]+".png")
        fuse.draw_marks(elements, str(raw), b[0], b[1], b[2], str(out))
        if any(w.startswith("OCR: ") for w in warnings):
            out.unlink(missing_ok=True)  # unread text cannot be masked: no picture rather than an unmasked one
            print("그림 없음: OCR 실패로 비밀값을 가릴 수 없어 그림을 내보내지 않음")
        else:
            fuse.redact_image(str(out), [(r["box"], r["text"]) for r in texts], b[0], b[1], b[2])
            print(out)
    for warning in warnings:
        log("읽기 상태: " + warning)
    return elements


def print_element(e):
    x, y, w, h = e["box"]
    print(f"{e['number']} · {int(x+w/2)},{int(y+h/2)} · {e['kind']} · {e['name']} {e.get('state','')} · [{e['src']}]")


def normal(s):
    s = re.sub(r"\s*\[[^]]*\]$", "", s.replace("(옆 글자) ", ""))
    return re.sub(r"\s+", " ", s.translate(str.maketrans("‐‑‒–—−\u00a0", "------ "))).strip().casefold()


def hits(elements, text):
    text = normal(text)
    exact = [e for e in elements if normal(e["name"]) == text]
    return exact or [e for e in elements if text in normal(e["name"])]


def choose(rows, nth=None):
    if nth:
        index = int(nth)-1
        if 0 <= index < len(rows):
            return rows[index]
        raise DeskError(1, "일치하는 요소 번호 범위 밖")
    if len(rows) == 1:
        return rows[0]
    for pred in (lambda e: e["kind"] != "글자", lambda e: "A" in e["src"]):
        pick = [e for e in rows if pred(e)]
        if len(pick) == 1:
            return pick[0]
    for e in rows:
        print_element(e)
    raise DeskError(1, "AMBIGUOUS: 여러 곳이 일치합니다. desk click-el 번호로 고르세요.")


def action(kind, args, box=None, text=None, observed_box=None):
    wait_idle()
    want = load("expect.json")
    if not want:
        raise DeskError(6, "마지막으로 본 화면이 없습니다. desk see로 확인하세요.")
    w = want["window"]
    blocked_host(w, kind in ("key", "type"))
    plan = None
    if kind in ("click", "dclick", "rclick", "drag"):
        plan = CLICK.prepare(STATE, [int(args[0]), int(args[1])], w, observed_box)
    epoch = want.get("inputEpoch", CLICK.input_epoch(STATE))
    request = {"kind": kind, "args": list(map(str, args)), "text": text or "",
               "expected": w, "epoch": epoch, "plan": plan}
    if kind == "sequence":
        import shutil
        actions = args
        for index, item in enumerate(actions):
            if item["kind"] in ("click", "dclick", "rclick", "drag"):
                point = item["args"]
                item["plan"] = CLICK.prepare(STATE, [int(point[0]), int(point[1])], w)
                patch = STATE / f"sequence-patch-{index}.png"
                shutil.copyfile(item["plan"]["pixels"], patch)
                item["plan"]["pixels"] = str(patch)
        request["actions"] = actions
        request["args"] = []
    log("조作: " + kind)
    result = native("step", structured=True, timeout=30,
                    input_data=json.dumps(request, ensure_ascii=False).encode("utf-8"))
    CLICK.invalidate(STATE)
    (STATE / "elements.json").unlink(missing_ok=True)
    if result["closed"]:
        (STATE / "expect.json").unlink(missing_ok=True)
        print("대상 창 닫힘")
        return
    after = result["after"]
    pre, post = STATE / "pre.png", STATE / "post.png"
    pre_box, post_box = result["preBox"], result["postBox"]
    changed, outside_changed = (True, True) if pre_box != post_box else image_changes(pre, post, pre_box, result["editBox"])
    remember(after)
    if not QUIET:
        if outside_changed:
            snapshot(frame=(post, post_box, after, epoch))
            return
        out = STATE / "shots" / ("action-"+uuid.uuid4().hex[:8]+".png")
        ruler(post, out, post_box)
        CLICK.observe(STATE, post, post_box, after, epoch=epoch)
        print("→ " + after["app"] + " · " + after["title"])
        print("입력 전달: " + kind)
        print(f"{out} (영역 {boxarg(post_box)} · 눈금 숫자 = 화면 좌표)")
    if not changed and kind in ("click", "dclick", "rclick"):
        raise DeskError(5, "눌렀지만 화면 변화가 확인되지 않음. 반환한 결과 그림으로 확인하세요.")


def image_changes(before, after, region_box, ignored_box=None):
    """Compare the existing captures; typing inside a known edit is not a new screen."""
    from PIL import Image, ImageChops, ImageDraw
    with Image.open(before) as left, Image.open(after) as right:
        if left.size != right.size:
            return True, True
        diff = ImageChops.difference(left.convert("RGB"), right.convert("RGB"))
    changed = diff.getbbox() is not None
    if changed and ignored_box:
        box = intersection(region_box, ignored_box)
        if min(box[2:]) > 0:
            sx, sy = diff.width/region_box[2], diff.height/region_box[3]
            rect = (int((box[0]-region_box[0])*sx), int((box[1]-region_box[1])*sy),
                    int((box[0]+box[2]-region_box[0])*sx)-1, int((box[1]+box[3]-region_box[1])*sy)-1)
            ImageDraw.Draw(diff).rectangle(rect, fill="black")
    return changed, diff.getbbox() is not None


def ruler(path, out, b, texts=None):
    from PIL import Image, ImageDraw, ImageFont
    with Image.open(path) as source:
        im = source.convert("RGB")
    # The agent sees this picture: paint over the spans the printed text would mask (desk-see.py redact).
    fuse = module("desk_fusion", "desk-see.py")
    rows = [(r["box"], r["text"]) for r in (ocr(path, b) if texts is None else texts)]
    fuse.paint_redactions(im, fuse.redact_rects(rows), b[0], b[1], b[2])
    dr = ImageDraw.Draw(im)
    font = ImageFont.truetype(str(Path(os.environ["WINDIR"])/"Fonts"/"arial.ttf"), 13)
    step = 50 if b[2] < 1000 else 100
    for x in range(((b[0]+step-1)//step)*step,b[0]+b[2],step):
        xx=x-b[0];dr.line((xx,0,xx,14),fill="cyan",width=2);dr.text((xx+2,2),str(x),font=font,fill="cyan",stroke_width=1,stroke_fill="black")
    for y in range(((b[1]+step-1)//step)*step,b[1]+b[3],step):
        yy=y-b[1];dr.line((0,yy,14,yy),fill="cyan",width=2);dr.text((2,yy+2),str(y),font=font,fill="cyan",stroke_width=1,stroke_fill="black")
    im.thumbnail((1800,1800))
    im.save(out)


def shot(args):
    wait_idle()
    observed_epoch = CLICK.input_epoch(STATE)
    w = window()
    b = region(args,w)
    raw = STATE / "shot-raw.png"
    capture(raw,b)
    out = STATE / "shots" / ("shot-"+uuid.uuid4().hex[:8]+".png")
    # Optional explicit output path follows the flags.
    rest = list(args)
    if "--region" in rest:
        i=rest.index("--region");del rest[i:i+2]
    rest=[a for a in rest if a!="--full"]
    if rest:
        out=Path(rest[0]).resolve()
    ruler(raw,out,b)
    CLICK.observe(STATE, raw, b, w, epoch=observed_epoch)
    remember(w)
    print(f"{out} (영역 {boxarg(b)} · 눈금 숫자 = 화면 좌표)")


def start_panel():
    with (STATE/"panel.log").open("ab") as f:
        subprocess.Popen([str(EXE),"panel"],stdin=subprocess.DEVNULL,stdout=f,stderr=f,creationflags=BACKGROUND_FLAGS)
    for _ in range(50):
        if panel_alive():
            return
        time.sleep(.1)
    raise DeskError(2,"중지 패널을 시작하지 못함: "+str(STATE/"panel.log"))


def begin(summary):
    (STATE/"stopped").unlink(missing_ok=True)
    (STATE/"panel-close").unlink(missing_ok=True)
    (STATE/"expect.json").unlink(missing_ok=True)
    (STATE/"elements.json").unlink(missing_ok=True)
    CLICK.invalidate(STATE)
    hosts=[]
    try:
        import psutil
        hosts=[p.pid for p in psutil.Process().parents()]
    except ImportError:
        hosts=[os.getppid()]
    save("session.json",{"id":uuid.uuid4().hex,"host_pids":hosts,"started":time.time()})
    save("panel-lines.json", [])
    log("AI ▸ "+summary)
    write("highlight", "")
    if not panel_alive():
        start_panel()
    ready=ensure_detector()
    print("제어 시작 · 중지: ESC 한 번 또는 패널 중지 버튼")
    print("로컬: Windows OCR + UI Automation" + (" + GPU/CPU 아이콘 검출" if ready else " · 검출기 미설치/시작 실패 (desk doctor)"))


def ask(args):
    if "--region" not in args or "--type" not in args:
        raise DeskError(2,"desk ask --region x,y,w,h --type 글자|숫자|선택|색|창|그림 '질문'")
    wait_idle();observed_epoch=CLICK.input_epoch(STATE);w=window();b=region(args,w)
    kind=args[args.index("--type")+1]
    record=module("desk_ask","desk-ask.py")
    if kind not in record.KINDS:
        raise DeskError(2,"지원하지 않는 질문 종류")
    rest=list(args)
    for opt in ("--region","--type"):
        i=rest.index(opt);del rest[i:i+2]
    question=" ".join(rest)
    if not question:
        raise DeskError(2,"질문이 필요합니다")
    id=time.strftime("%Y%m%d-%H%M%S")+"-"+uuid.uuid4().hex[:8]
    d=STATE/"ask";d.mkdir(exist_ok=True)
    path=d/(id+".png");capture(path,b)
    texts=ocr(path,b)
    (d/(id+".ocr")).write_text("\n".join(r["text"] for r in texts),encoding="utf-8")
    record.new(str(d),id,boxarg(b),kind,w["app"]+" · "+w["title"],question)
    out=STATE/"shots"/("ask-"+id+".png");ruler(path,out,b,texts)
    CLICK.observe(STATE, path, b, w, [{"box": r["box"]} for r in texts], epoch=observed_epoch)
    cfg=load("vision.json",{})
    if cfg.get("model") and not pid_alive(read("ask-worker.pid")):
        with (d/"worker.log").open("ab") as f:
            p=subprocess.Popen([sys.executable,"-X","utf8",str(ROOT/"desk-ask.py"),"work",str(d),cfg["model"]],stdin=subprocess.DEVNULL,stdout=f,stderr=f,creationflags=BACKGROUND_FLAGS)
        write("ask-worker.pid",str(p.pid))
    remember(w)
    print(out)
    print(f"질문 {id} · 직접 본 답을 desk ask-ref {id} '답'으로 남기세요. 로컬 모델 답은 비교용입니다.")


def doctor():
    info={"os":sys.platform,"python":sys.executable,"native":str(EXE),"state":str(STATE),"panel":panel_alive(),"stopped":(STATE/"stopped").exists()}
    # Load torch before WinRT: the reverse order can prevent c10.dll initialization on Windows.
    try:
        import torch
        info["torch"]=torch.__version__;info["cuda"]=torch.cuda.is_available()
        if info["cuda"]:
            info["gpu"]=torch.cuda.get_device_name(0)
    except (ImportError, OSError) as e:
        info["cuda"]=False
        info["torch_error"]=str(e)
    try:
        from winrt.windows.media.ocr import OcrEngine
        info["ocr_languages"]=[x.language_tag for x in OcrEngine.available_recognizer_languages]
    except Exception as e:
        info["ocr_error"]=str(e)
    if not any(t.split("-")[0] in ("ko","en") for t in info.get("ocr_languages",[])):
        info["ocr_hint"]="설정 → 시간 및 언어 → 언어 및 지역에서 한국어나 영어의 '광학 문자 인식' 언어 기능을 설치하세요"
    try:
        info["elevated"]=bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        info["elevated"]=None
    if info["elevated"] is False:
        info["elevated_hint"]="관리자 권한 앱은 일반 권한 desk의 입력을 막습니다(UIPI). 그런 앱을 다룰 때는 에이전트 터미널을 관리자 권한으로 실행하세요"
    from setup_windows import install_status
    info["skill_links"]=install_status()
    info["detector_weights"]=(STATE/"omni"/"weights"/"icon_detect_v3"/"model.pt").exists()
    info["local_vision"]=load("vision.json",{})
    info["desktop"]=native("window",structured=True,stopped=False)["desktop"]
    print(json.dumps(info,ensure_ascii=False,indent=2))


HELP="""desk begin '요청 요약' | end | stop | status | doctor | log [N]
desk say '지금 하는 일' | handoff '안내' [초]
desk see | shot [--full | --region x,y,w,h] [저장경로] | look | front
desk read [--window | --full | --region x,y,w,h]
desk find '글자' [--full] | wait-for '글자' [초] [--gone] [--full]
desk click-text '글자' [N] [--force] | click-el 번호 [--force]
desk click X Y | dclick X Y | rclick X Y | move X Y | drag X Y X2 Y2
desk scroll X Y up|down|left|right [횟수]
desk key ctrl+c | type [--keys] '텍스트' | wait [초]
desk open 앱.exe|URL|파일 | open --hwnd 창번호 | windows
desk ask --region x,y,w,h --type 글자|숫자|선택|색|창|그림 '질문'
desk ask-ref 질문ID '답' | ask-report [--list]
desk setup [--detector] [--vision] | install | update
출처: A=UI Automation, O=Windows OCR, D=로컬 검출기, C=로컬 그림 설명
종료: 1=없음/모호함, 2=환경 오류, 3=사용자 중지, 5=화면 변화 없음, 6=화면·포커스 변경
primary/cmd는 Windows에서 ctrl. Windows 키는 win. -q는 조작 뒤 전체 목록 생략.
"""


def dispatch(cmd,args):
    if cmd=="help": print(HELP);return
    # Stop and lifecycle reporting must not wait for a compiler, GPU initialization or command lock.
    if cmd=="stop":write("stopped",time.strftime("%Y-%m-%d %H:%M:%S"));print("중지함");return
    if cmd=="status":print("stopped" if (STATE/"stopped").exists() else "not stopped");print("panel: "+("on" if panel_alive() else "off"));return
    if cmd=="log":print("\n".join(read("actions.log").splitlines()[-int(args[0] if args else 30):]));return
    if cmd=="end":
        write("panel-close","1");(STATE/"session.json").unlink(missing_ok=True);(STATE/"expect.json").unlink(missing_ok=True);CLICK.invalidate(STATE);print("제어 끝");return
    if cmd in ("setup","install"):
        from setup_windows import setup
        setup(args,install=cmd=="install");return
    build()
    if cmd=="doctor": doctor();return
    if cmd=="begin":begin(" ".join(args));return
    if cmd in ("ask-ref","ask-report"):
        record=module("desk_ask","desk-ask.py");d=str(STATE/"ask")
        if cmd=="ask-ref":record.ref(d,args[0]," ".join(args[1:]));print("기록함")
        else:record.report(d,"--list" in args)
        return
    ensure_session()
    if cmd=="say":log("AI ▸ "+" ".join(args));return
    if cmd=="front":w=window();print(w["app"]+" · "+w["title"]);return
    if cmd=="windows":print(native("windows"));return
    if cmd in ("see","look"):snapshot();return
    if cmd=="shot":shot(args) if args else snapshot();return
    if cmd=="ask":ask(args);return
    if cmd=="wait":
        end=time.monotonic()+float(args[0] if args else 1)
        while time.monotonic()<end:check_stop();time.sleep(.08)
        return
    if cmd=="handoff":
        start=time.time();limit=float(args[1] if len(args)>1 else 180);log("사용자 차례: "+args[0]);seen=False
        while time.time()-start<limit:
            check_stop()
            if not panel_alive():raise DeskError(2,"중지 패널 연결이 끊김")
            stamp=(STATE/"user-input").stat().st_mtime if (STATE/"user-input").exists() else 0
            seen=seen or stamp>=start
            if seen and time.time()-stamp>=float(os.environ.get("DESK_HANDOFF_IDLE","10")):
                print("사용자 입력 후 대기 끝. desk see로 결과를 확인하세요.");return
            time.sleep(.1)
        raise DeskError(1,"사용자 입력 대기 시간 초과. 사용자에게 확인하세요.")
    if cmd=="open":
        wait_idle();log("앱·파일 열기: "+args[0])
        if args[0]=="--hwnd":native("activate",args[1])
        else:native("open",args[0])
        time.sleep(.7);snapshot(display=not QUIET,use_detector=False);return
    if cmd=="read":
        wait_idle();observed_epoch=CLICK.input_epoch(STATE);w=window();blocked_host(w);b=region(args,w,True);path=STATE/"read.png";capture(path,b)
        texts=ocr(path,b)
        redact=module("desk_fusion","desk-see.py").redact
        for r in texts:x,y,ww,hh=r["box"];print(f"{int(x+ww/2)},{int(y+hh/2)}\t{redact(r['text'])}")
        CLICK.observe(STATE,path,b,w,[{"box":r["box"]} for r in texts],epoch=observed_epoch)
        remember(w);return
    if cmd in ("find","wait-for"):
        text=args[0];limit=15.0
        if len(args)>1 and not args[1].startswith("--"):limit=float(args[1])
        until=time.monotonic()+limit
        while True:
            rows=hits(snapshot(["--full"] if "--full" in args else [],display=False,use_detector=False),text)
            if cmd=="find" or bool(rows)!=("--gone" in args):
                if rows:
                    for e in rows:print_element(e)
                    return
                if cmd=="wait-for":print("사라짐: "+text);return
                raise DeskError(1,"NOT_FOUND: "+text)
            if time.monotonic()>=until:raise DeskError(1,"기다린 글자가 나타나지 않거나 사라지지 않음: "+text)
            check_stop();time.sleep(.25)
    if cmd in ("click-el","click-text"):
        wait_idle();want=load("expect.json")
        if not want:raise DeskError(6,"마지막으로 본 화면이 없습니다. desk see로 확인하세요.")
        w=want["window"];blocked_host(w)
        if cmd=="click-el":
            cached=load("elements.json")
            if not cached or signature(cached["window"])!=signature(w):raise DeskError(6,"요소 목록이 현재 창과 다름. desk see부터 실행하세요.")
            old=next((e for e in cached["elements"] if e["number"]==int(args[0])),None)
            if not old:raise DeskError(1,"해당 요소 번호 없음")
            target=old  # validate against the immutable public observation; never silently relocate
        else:
            cached=load("elements.json")
            observation=load("observation.json")
            valid=(cached and observation and signature(cached["window"])==signature(w)
                   and observation.get("inputEpoch")==CLICK.input_epoch(STATE))
            rows=hits(cached["elements"],args[0]) if valid else []
            if not rows:
                rows=hits(snapshot(display=False),args[0])
            if not rows:raise DeskError(1,"NOT_FOUND: "+args[0])
            nth=next((s for s in args[1:] if s.isdigit()),None);target=choose(rows,nth)
        if "비활성" in target.get("state",""):raise DeskError(1,"비활성 컨트롤")
        if target["kind"]=="글자" and "D" not in target["src"] and "--force" not in args:raise DeskError(5,"글자로만 보이는 요소. 실제 클릭 대상인지 그림으로 확인하세요.")
        x,y,ww,hh=target["box"];action("click",[int(x+ww/2),int(y+hh/2)],target["box"],observed_box=target["box"]);return
    if cmd=="sequence":
        actions=json.loads(args[0])
        if not isinstance(actions,list) or not 1<=len(actions)<=20:raise DeskError(2,"sequence는 행동 1~20개의 JSON 배열입니다.")
        for item in actions:
            if item.get("kind") not in ("click","dclick","rclick","move","scroll","key","type"):raise DeskError(2,"지원하지 않는 묶음 동작")
            item["args"]=list(map(str,item.get("args",[])))
            item["text"]=item.get("text","")
        action("sequence",actions);return
    if cmd=="type":
        text=" ".join(a for a in args if a!="--keys")
        if "--keys" in args and not text.isascii():raise DeskError(2,"--keys는 영문·숫자 입력용입니다. 한글은 desk type을 쓰세요.")
        action("type",[],text=text)
        return
    if cmd=="key":action(cmd,[args[0]]);return
    if cmd in ("click","dclick","rclick","move","drag","scroll"):
        count={"click":2,"dclick":2,"rclick":2,"move":2,"drag":4,"scroll":3}[cmd]
        if len(args)<count:raise DeskError(2,"인자가 부족합니다. desk help")
        values=list(args)
        if cmd=="scroll":
            if values[2] not in ("up","down","left","right"):raise DeskError(2,"스크롤 방향: up|down|left|right")
            if len(values)==3:values.append("5")
        x,y=int(values[0]),int(values[1]);action(cmd,values,[x-12,y-12,24,24] if cmd in ("click","dclick","rclick","drag") else None);return
    raise DeskError(2,"알 수 없는 명령: "+cmd+" (desk help)")


def main(argv):
    global QUIET
    STATE.mkdir(parents=True,exist_ok=True);(STATE/"shots").mkdir(exist_ok=True)
    if argv and argv[0]=="-q":QUIET=True;argv=argv[1:]
    cmd=argv[0] if argv else "help"
    # Serialize readers and writers; stop/status bypass it to remain responsive.
    import msvcrt
    lock=None
    try:
        if cmd not in ("stop","status","log","doctor","help"):
            lock=(STATE/"command.lock").open("a+b");lock.seek(0);lock.write(b"0");lock.flush();lock.seek(0)
            try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
            except OSError:raise DeskError(2,"다른 desk 명령이 실행 중입니다. 중지는 desk stop")
        dispatch(cmd,argv[1:])
        # Keep runtime screenshots local and bounded; question records own their images separately.
        for old in sorted((STATE/"shots").glob("*.png"),key=lambda p:p.stat().st_mtime,reverse=True)[60:]:old.unlink(missing_ok=True)
        return 0
    except DeskError as e:
        print("desk: "+str(e),file=sys.stderr);return e.code
    except CLICK.StaleClick as e:
        print("desk: "+str(e),file=sys.stderr);return 6
    except (IndexError,ValueError) as e:
        print("desk: 잘못된 인자: "+str(e)+" (desk help)",file=sys.stderr);return 2
    except (ImportError, OSError) as e:
        print("desk: 로컬 실행 환경 오류: "+str(e)+" (desk setup / doctor)",file=sys.stderr);return 2
    except subprocess.CalledProcessError as e:
        print("desk: 설치 명령이 실패했습니다 (종료 "+str(e.returncode)+"). 위 오류를 확인하세요.",file=sys.stderr);return 2
    finally:
        if lock:lock.close()
