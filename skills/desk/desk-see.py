#!/usr/bin/env python3
"""desk-see: one numbered list of what is on the front window, from three local sources.

  OCR   (macOS Vision, desk-helper ocrb)  text and where it is
  AX    (accessibility, desk-helper ax)   kind, name and exact frame of each on-screen control
  DET   (OmniParser icon_detect_v3)       boxes of anything clickable-looking, from pixels alone
                                          (icons, unlabeled buttons, vertical text, canvas UIs)

  serve SOCK WEIGHTS                     keep the detector loaded; answers detect requests
  detect SOCK IMAGE OX OY PW             boxes in screen points: "x,y,w,h<TAB>conf"
                                         (the server also captions unnamed boxes for fuse: OmniParser
                                         icon_caption, Florence-2 based, MIT — "paperclip icon")
  fuse OCR AX DET OUT [IMAGE OX OY PW MARKS]   merge into OUT ("N<TAB>cx,cy<TAB>kind<TAB>name<TAB>src
                                         <TAB>x,y,w,h") and draw numbered boxes on IMAGE into MARKS;
                                         with DESK_CAPTION_LATER=1 the unnamed boxes are not captioned
                                         but listed in OUT.todo ("N<TAB>x,y,w,h") for:
  caption SOCK TSV TODO IMAGE OX OY PW OUT   TSV with those boxes named by the captioner, into OUT

The detector code is adapted from microsoft/OmniParser util/yolov9.py (CC-BY-4.0); the weights are
OmniParser-v2.0 icon_detect_v3 (MIT).
"""
import os
import re
import socket
import sys
import time

# ── detector (server side; torch is imported only here) ─────────────────────────────────────────


def load_detector(weights):
    import numpy as np
    import torch
    from PIL import Image
    from torchvision.ops import batched_nms

    device = torch.device("cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu")
    model = torch.jit.load(weights, map_location=device).eval()
    half = device.type in ("mps", "cuda")  # retain macOS path; use NVIDIA tensor cores on Windows
    if half:
        model = model.half()
    strides = (8, 16, 32)

    @torch.inference_mode()
    def predict(path, conf=0.05, imgsz=1280, iou=0.5):
        image = Image.open(path).convert("RGB")
        tw = th = ((imgsz + 31) // 32) * 32
        scale = min(tw / image.width, th / image.height)
        rw, rh = int(image.width * scale), int(image.height * scale)
        pl, pt = (tw - rw) // 2, (th - rh) // 2
        padded = Image.new("RGB", (tw, th), (114, 114, 114))
        padded.paste(image.resize((rw, rh), Image.Resampling.LANCZOS), (pl, pt))
        x = torch.from_numpy(np.asarray(padded, dtype=np.float32).transpose(2, 0, 1) / 255.0).unsqueeze(0).to(device)
        with torch.jit.optimized_execution(False):
            outputs = [o.float() for o in model(x.half() if half else x)]
        logits, boxes = [], []
        for i, stride in zip(range(0, len(outputs), 2), strides):
            lg, dist = outputs[i : i + 2]
            b, _, h, w = lg.shape
            lg = lg.permute(0, 2, 3, 1).reshape(b, -1, lg.shape[1])
            dist = dist.permute(0, 2, 3, 1).reshape(b, -1, 4) * stride
            gy, gx = torch.meshgrid(torch.arange(h, device=device), torch.arange(w, device=device), indexing="ij")
            anchors = (torch.stack((gx, gy), dim=-1).reshape(-1, 2) + 0.5) * stride
            lt, rb = dist.chunk(2, dim=-1)
            boxes.append(torch.cat((anchors - lt, anchors + rb), dim=-1))
            logits.append(lg)
        scores, cls = torch.cat(logits, dim=1).sigmoid()[0].max(dim=-1)
        bx = torch.cat(boxes, dim=1)[0]
        keep = scores > conf
        scores, cls, bx = scores[keep], cls[keep], bx[keep]
        bx[:, [0, 2]] = ((bx[:, [0, 2]] - pl) / scale).clamp(0, image.width)
        bx[:, [1, 3]] = ((bx[:, [1, 3]] - pt) / scale).clamp(0, image.height)
        k = batched_nms(bx, scores, cls, iou)[:400]
        return image.width, bx[k].cpu().tolist(), scores[k].cpu().tolist()

    return predict


def load_captioner(weights_dir):
    """Short English descriptions of icon crops. Results are cached by crop, so a screen seen again
    costs nothing for the icons it already had."""
    import hashlib

    import torch
    from transformers import AutoModelForCausalLM, AutoProcessor

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    dtype = torch.float16 if device == "mps" else torch.float32
    proc = AutoProcessor.from_pretrained("microsoft/Florence-2-base", trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(weights_dir, torch_dtype=dtype, trust_remote_code=True).to(device).eval()
    cache = {}

    @torch.inference_mode()
    def caption(image_path, ox, oy, pw, boxes):
        from PIL import Image

        im = Image.open(image_path).convert("RGB")
        k = im.width / pw
        crops, keys, out = [], [], [""] * len(boxes)
        for i, (x, y, w, h) in enumerate(boxes):
            c = im.crop((int((x - ox) * k), int((y - oy) * k), int((x - ox + w) * k), int((y - oy + h) * k))).resize((64, 64))
            key = hashlib.md5(c.tobytes()).hexdigest()
            if key in cache:
                out[i] = cache[key]
            else:
                crops.append(c)
                keys.append((i, key))
        for start in range(0, len(crops), 64):
            batch = crops[start : start + 64]
            inputs = proc(images=batch, text=["<CAPTION>"] * len(batch), return_tensors="pt", do_resize=False).to(device, dtype)
            gen = model.generate(input_ids=inputs["input_ids"], pixel_values=inputs["pixel_values"],
                                 max_new_tokens=20, num_beams=1, do_sample=False)
            for (i, key), text in zip(keys[start : start + 64], proc.batch_decode(gen, skip_special_tokens=True)):
                cache[key] = out[i] = text.strip().replace("\t", " ").replace("\n", " ")
        return out

    return caption


def serve(sock_path, weights):
    import warnings

    warnings.filterwarnings("ignore")
    os.environ["HF_HUB_OFFLINE"] = "1"  # the model code is already cached; never pull a newer one
    if sock_path.startswith("\\\\.\\pipe\\"):
        return serve_pipe(sock_path, weights)
    predict = load_detector(weights)
    caption_dir = os.path.join(os.path.dirname(os.path.dirname(weights)), "icon_caption")
    try:
        caption = load_captioner(caption_dir) if os.path.isdir(caption_dir) else None
    except Exception as e:
        print(f"captioner unavailable: {e}", file=sys.stderr)
        caption = None
    try:
        os.unlink(sock_path)
    except FileNotFoundError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock_path)
    srv.listen(4)
    srv.settimeout(60)
    last = time.time()
    while True:
        try:
            conn, _ = srv.accept()
        except socket.timeout:
            if time.time() - last > 1800:  # idle half an hour: free the memory
                break
            continue
        last = time.time()
        with conn:
            try:
                req = conn.makefile().readline().rstrip("\n").split("\t")
                if req[0] == "caption":
                    path, ox, oy, pw = req[1], float(req[2]), float(req[3]), float(req[4])
                    boxes = [[float(v) for v in b.split(",")] for b in req[5].split(";") if b]
                    texts = caption(path, ox, oy, pw, boxes) if caption else [""] * len(boxes)
                    conn.sendall(("\n".join(texts) + "\n").encode())
                    continue
                path, ox, oy, pw = req[0], float(req[1]), float(req[2]), float(req[3])
                width, boxes, scores = predict(path)
                k = width / pw  # pixels per point
                out = []
                for (x1, y1, x2, y2), s in zip(boxes, scores):
                    out.append(f"{int(ox + x1 / k)},{int(oy + y1 / k)},{int((x2 - x1) / k)},{int((y2 - y1) / k)}\t{s:.2f}")
                conn.sendall(("\n".join(out) + "\n").encode())
            except Exception as e:  # keep serving
                conn.sendall(f"ERROR\t{e}\n".encode())
    os.unlink(sock_path)


def request(sock_path, line, timeout=30):
    if sock_path.startswith("\\\\.\\pipe\\"):
        from multiprocessing.connection import Client
        with Client(sock_path, family="AF_PIPE", authkey=pipe_key()) as conn:
            conn.send_bytes(line.encode("utf-8"))
            started = time.monotonic()
            while not conn.poll(.1):
                from pathlib import Path
                state = Path(os.environ.get("DESK_STATE", Path.home() / ".cache" / "desk"))
                if (state / "stopped").exists():
                    raise InterruptedError("desk stopped")
                if time.monotonic() - started > timeout:
                    raise TimeoutError("local detector timed out")
            return conn.recv_bytes().decode("utf-8")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(sock_path)
    s.sendall((line + "\n").encode())
    data = b""
    while chunk := s.recv(65536):
        data += chunk
    return data.decode()


def unnamed(elements):
    return [e for e in elements if not e["name"] and e["kind"] != "글자" and e["box"][2] >= 6 and e["box"][3] >= 6][:120]


def name_unnamed(elements, sock_path, image, ox, oy, pw):
    """Gives each unnamed element the captioner's description of its pixels, marked as such."""
    todo = unnamed(elements)
    if not todo or not sock_path or not (sock_path.startswith("\\\\.\\pipe\\") or os.path.exists(sock_path)):
        return
    boxes = ";".join(",".join(str(int(v)) for v in e["box"]) for e in todo)
    try:
        texts = request(sock_path, f"caption\t{image}\t{ox}\t{oy}\t{pw}\t{boxes}", timeout=60).split("\n")
    except OSError:
        return
    for e, t in zip(todo, texts):
        if t.strip():
            e["name"] = f"(그림) {t.strip()}"
            e["src"].add("C")


def caption_later(sock_path, tsv, todo_path, image, ox, oy, pw, out):
    """The captioning fuse skipped (DESK_CAPTION_LATER): names the listed rows of TSV, numbers kept."""
    todo = [line.rstrip("\n").split("\t") for line in open(todo_path, encoding="utf-8") if line.strip()]
    texts = request(sock_path, f"caption\t{image}\t{ox}\t{oy}\t{pw}\t{';'.join(b for _, b in todo)}", timeout=60).split("\n") if todo else []
    named = {n: t.strip() for (n, _), t in zip(todo, texts) if t.strip()}
    rows = [line.rstrip("\n").split("\t") for line in open(tsv, encoding="utf-8")]
    with open(out, "w", encoding="utf-8") as f:
        for r in rows:
            if r[0] in named:
                r[3] = f"(그림) {named[r[0]]}" + r[3]  # what is left of the name is its state (" [켬]")
                r[4] = "".join(c for c in "AODC" if c in r[4] + "C")
            f.write("\t".join(r) + "\n")


def detect(sock_path, image, ox, oy, pw):
    sys.stdout.write(request(sock_path, f"{image}\t{ox}\t{oy}\t{pw}"))


def pipe_key():
    from pathlib import Path
    state = Path(os.environ.get("DESK_STATE", Path.home() / ".cache" / "desk"))
    return (state / "detector.auth").read_bytes()


def serve_pipe(endpoint, weights):
    """Windows CPython has no AF_UNIX. Authenticated local named pipes carry bytes, never pickle."""
    from multiprocessing.connection import Listener
    from pathlib import Path
    predict = load_detector(weights)
    state = Path(os.environ.get("DESK_STATE", Path.home() / ".cache" / "desk"))
    caption = None
    cfg = state / "vision.json"
    if cfg.exists():
        try:
            import json
            sys.path.insert(0, str(Path(__file__).resolve().parent / "windows"))
            from local_vision import load_captioner as load_windows_captioner
            caption = load_windows_captioner(json.loads(cfg.read_text(encoding="utf-8"))["model"])
        except Exception as e:
            print(f"captioner unavailable: {e}", file=sys.stderr)
    with Listener(endpoint, family="AF_PIPE", authkey=pipe_key()) as server:
        import threading
        activity = [time.monotonic(), False]
        def reap_idle():
            while True:
                time.sleep(30)
                if not activity[1] and time.monotonic() - activity[0] > 1800:
                    os._exit(0)  # named-pipe accept is blocking; release idle GPU memory
        threading.Thread(target=reap_idle, daemon=True).start()
        while True:
            try:
                conn = server.accept()
            except (OSError, EOFError):
                continue
            with conn:
                activity[:] = [time.monotonic(), True]
                try:
                    req = conn.recv_bytes(1024*1024).decode("utf-8").split("\t")
                    if req[0] == "ping":
                        conn.send_bytes(b"ready")
                        continue
                    if req[0] == "caption":
                        path, ox, oy, pw = req[1], float(req[2]), float(req[3]), float(req[4])
                        boxes = [[float(v) for v in b.split(",")] for b in req[5].split(";") if b]
                        texts = caption(path, ox, oy, pw, boxes) if caption else [""]*len(boxes)
                        conn.send_bytes(("\n".join(texts)+"\n").encode("utf-8"))
                        continue
                    path, ox, oy, pw = req[0], float(req[1]), float(req[2]), float(req[3])
                    width, boxes, scores = predict(path)
                    k = width / pw
                    lines = [f"{int(ox+x1/k)},{int(oy+y1/k)},{int((x2-x1)/k)},{int((y2-y1)/k)}\t{s:.2f}"
                             for (x1,y1,x2,y2),s in zip(boxes,scores)]
                    conn.send_bytes(("\n".join(lines)+"\n").encode("utf-8"))
                except (BrokenPipeError, EOFError):
                    pass
                except Exception as e:
                    try:
                        conn.send_bytes(f"ERROR\t{e}\n".encode("utf-8"))
                    except OSError:
                        pass
                finally:
                    activity[:] = [time.monotonic(), False]


# ── redaction ───────────────────────────────────────────────────────────────────────────────────
# Screen text goes to the agent (and its model API). Common secret shapes are partly masked with *
# before any text leaves this machine: the middle is hidden, the head and tail stay to tell which one it is. Email addresses
# are left alone. Images handed to the agent get the same spans painted over (redact_image).

def _keep(s, head, tail=0):
    """Mask letters/digits of s after the first `head` and before the last `tail`, keeping separators."""
    idx = [i for i, ch in enumerate(s) if ch.isalnum()]
    hide = set(idx[head:len(idx) - tail if tail else None])
    return "".join("*" if i in hide else ch for i, ch in enumerate(s))


def _keep_long(s):
    """For long generated keys: hide max(10, half) of the letters/digits in the middle, show the rest at both ends.
    10 hidden random characters are already past guessing (~60 bits), and more visible head/tail tells keys apart:
    20 → 5+10+5, 40 → 10+20+10. Short tokens keep 2+2 so most of them stays hidden."""
    n = sum(c.isalnum() for c in s)
    hidden = max(10, -(-n // 2))
    if n - hidden < 4:
        return _keep(s, 2, 2)
    head = (n - hidden) // 2
    return _keep(s, head, n - hidden - head)


_B = r"(?<![A-Za-z0-9])"   # not glued to a longer word or number
_E = r"(?![A-Za-z0-9])"
def _middle(s, keep):
    """Hide every character but the first and last `keep` (labelled values may be any characters)."""
    return s[:keep] + "*" * (len(s) - 2 * keep) + s[-keep:] if len(s) > 2 * keep else "*" * len(s)


_WORDS = None
def _words():
    """English words of 3–6 and 12–16 letters (redact-words.txt, from macOS /usr/share/dict/words): bundled so
    Windows and Linux judge the same. Empty set if missing — then every group counts as random."""
    global _WORDS
    if _WORDS is None:
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "redact-words.txt"), encoding="utf-8") as f:
                _WORDS = {line.strip() for line in f if line.strip()}
        except OSError:
            _WORDS = set()
    return _WORDS


def _is_word(w):
    """w or its stem is in the word list (the list has base forms: "responsibilities" → "responsibility")."""
    words = _words()
    stems = {w, w[:-1], w[:-2], w[:-3] + "y", w[:-3], w[:-3] + "e", w[:-2] + "e"}
    return any(s in words for s in stems)


def _randomish(s):
    """A token that looks generated, not named. 20+ characters mixing letters and digits: always.
    Shorter: letters and digits keep alternating. Names put them in runs (IMG20261030, iPhone15ProMax,
    utf8mb4: 1–3 switches); keys and IDs switch often (a8Kd93Lx02Qz: 6). Shorter tokens need more switches."""
    kinds = ["d" if c.isdigit() else "a" for c in s if c.isalnum()]
    if kinds.count("d") < 2 or kinds.count("a") < 2:
        return False
    if len(s) >= 20:
        return True   # 20+ glued characters mixing letters and digits are almost always a key or token
    if "-" in s:
        return False  # short hyphenated names: AWS4-HMAC-SHA256, ap-northeast-1, x86-64
    switches = sum(a != b for a, b in zip(kinds, kinds[1:]))
    mixed_case = any(c.islower() for c in s) and any(c.isupper() for c in s)
    if len(s) >= 12:
        return switches >= 4 or (mixed_case and switches >= 3)
    return switches >= 5


def _random_groups(s):
    """Grouped key, not words: a digit, a hyphen, or two or more groups that are not English words.
    A vowel test is not enough — app password "sfzh dosm yayb buhg" has a vowel in every group."""
    if any(c.isdigit() for c in s) or "-" in s:
        return True
    return sum(not _is_word(g.lower()) for g in s.split()) >= 2


def _digits(s):
    return sum(c.isdigit() for c in s)


def _mixed(s, digits=2, letters=2):
    return _digits(s) >= digits and sum(c.isalpha() for c in s) >= letters


REDACTIONS = [
    # A value after a secret's label: 비밀번호: …, password=…, api_key: …, token=…, 인증번호 123456
    (re.compile(r"(?i)((?:비밀\s?번호|암호|패스워드|인증\s?(?:번호|코드)|보안\s?코드|password|passwd|pwd|passcode|secret|"
                r"client[_ ]?secret|api[_ -]?key|access[_ -]?key|private[_ -]?key|token|access_token|refresh_token|otp|pin)"
                r"\s*[:=：]\s*|(?:인증\s?(?:번호|코드)|verification code|OTP)\s+)([^\s,;&\"'<>]{4,})"),
     lambda m: m.group(1) + _middle(m.group(2), 2 if len(m.group(2)) < 8 else 3)),
    # JWT: eyJ….eyJ….signature
    (re.compile(_B + r"eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), lambda m: _keep(m.group(), 6, 4)),
    # Prefixed API tokens: sk-/sk-ant-/sk-proj-, sk_live_/pk_live_/rk_, ghp_/github_pat_, xoxb-, glpat-, npm_, hf_,
    # GOCSPX-, SG., AIza, AKIA/ASIA
    (re.compile(_B + r"(sk-(?:ant-|proj-)?|[spr]k_(?:live|test)_|gh[pousr]_|github_pat_|xox[abprs]-|glpat-|npm_|hf_|"
                r"GOCSPX-|SG\.|AIza|AKIA|ASIA)([A-Za-z0-9_\-.]{12,})" + _E),
     lambda m: m.group(1) + _keep(m.group(2), 4, 4)),
    # Telegram bot token: 123456789:AA…
    (re.compile(_B + r"\d{8,10}:[A-Za-z0-9_-]{30,}" + _E), lambda m: _keep(m.group(), 4, 4)),
    # 주민등록번호·외국인등록번호: 900101-1234567, 9001011234567 → 900101-1******
    (re.compile(_B + r"\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\s?-?\s?[1-8]\d{6}" + _E), lambda m: _keep(m.group(), 7)),
    # 운전면허번호: 11-12-123456-12 → 11-12-******-12
    (re.compile(_B + r"\d{2}-\d{2}-\d{6}-\d{2}" + _E), lambda m: _keep(m.group(), 4, 2)),
    # 휴대폰: 010-1234-5678, 010 1234 5678, 01012345678, +82 10-1234-5678 → 010-****-5678
    (re.compile(r"(?<![A-Za-z0-9+])(?:\+82[\s-]?|0)1[016789][\s.-]?\d{3,4}[\s.-]?\d{4}" + _E),
     lambda m: _keep(m.group(), 4 if m.group().startswith("+") else 3, 4)),
    # 카드·16자리 숫자: 1234-5678-9012-3456, 1234567890123456 → 1234-****-****-3456; Amex 3782-822463-10005
    (re.compile(_B + r"\d{4}(?:[\s-]?\d{4}){3}" + _E), lambda m: _keep(m.group(), 4, 4)),
    (re.compile(_B + r"3[47]\d{2}[\s-]?\d{6}[\s-]?\d{5}" + _E), lambda m: _keep(m.group(), 4, 4)),
    # 계좌번호: 숫자 묶음 3~4개, 숫자 10~14자리 (110-123-456789, 3333-01-1234567, 123-456789-01-234)
    (re.compile(_B + r"\d{2,6}(?:-\d{2,8}){2,3}" + _E),
     lambda m: _keep(m.group(), 3, 3) if 10 <= _digits(m.group()) <= 14 else m.group()),
    # 여권번호: M12345678, MA1234567
    (re.compile(_B + r"[A-Z]{1,2}\d{7,8}" + _E), lambda m: _keep(m.group(), 2, 2) if len(m.group()) == 9 else m.group()),
    # Google OAuth: refresh token 1//0…, access token ya29.…, authorization code 4/0… (shows up in redirect URLs)
    (re.compile(_B + r"(1//0|ya29\.|4/0)([A-Za-z0-9_\-.]{20,})" + _E), lambda m: m.group(1) + _keep(m.group(2), 2, 4)),
    # 4×4 grouped keys: Google app password "abcd efgh ijkl mnop", Apple "abcd-efgh-ijkl-mnop", license keys.
    # OCR misreads a letter now and then ("sfzh" → "sfizh"), so a group may be 3–6 characters
    (re.compile(_B + r"[A-Za-z0-9]{3,6}(?:([ -])[A-Za-z0-9]{3,6})(?:\1[A-Za-z0-9]{3,6}){2,3}" + _E),
     lambda m: _keep(m.group(), 4, 4) if _random_groups(m.group()) else m.group()),
    # Any glued token 8+ long that looks generated (_randomish): API keys, secrets, IDs without a known prefix.
    # Head and tail stay so the agent can still tell which one it is; exact values come from the CLI, not the screen
    (re.compile(_B + r"[A-Za-z0-9_\-+/=]{8,}" + _E),
     lambda m: _keep_long(m.group()) if _randomish(m.group()) else m.group()),
    # The same app password with the spaces lost: 16 lowercase letters that are not one English word
    (re.compile(_B + r"[a-z]{16}" + _E), lambda m: m.group() if _is_word(m.group()) else _keep(m.group(), 4, 4)),
    # CD/product keys: 5×5 (Windows), 4–6-char groups of three or more (XXXXX-XXXXX-XXXXX), uppercase or digits
    (re.compile(_B + r"[A-Z0-9]{4,6}(?:-[A-Z0-9]{4,6}){2,}" + _E),
     lambda m: _keep(m.group(), 5, 5) if any(c.isdigit() for c in m.group()) or len(m.group().split("-")) >= 4 else m.group()),
    # A bare 16+ character key mixing letters and digits (no separators): app passwords, serials, hex/base64 secrets
    (re.compile(_B + r"[A-Za-z0-9+/]{16,}={0,2}" + _E),
     lambda m: _keep_long(m.group()) if _mixed(m.group()) or (len(m.group()) >= 30 and re.search("[a-z]", m.group())
                                                               and re.search("[A-Z]", m.group()) and re.search(r"[\d/+]", m.group()))
     else m.group()),
]


def redact(text):
    if not text:
        return text
    for pattern, fix in REDACTIONS:
        text = pattern.sub(fix, text)
    return text


def redact_rects(rows):
    """Screen-point rectangles over the hidden middle of each OCR line that redact() changes.

    rows: [(box [x, y, w, h] in screen points, text)]. redact() keeps the length, so the hidden
    characters' share of the line gives their span; half a character of padding covers glyph width."""
    rects = []
    for box, text in rows:
        masked = redact(text)
        if masked == text or len(masked) != len(text):
            continue
        hidden = [i for i, (a, b) in enumerate(zip(text, masked)) if a != b]
        n = len(text)
        x, y, w, h = box
        char = w / max(n, 1)
        lo, hi = x + char * hidden[0] - char / 2, x + char * (hidden[-1] + 1) + char / 2
        rects.append([lo, y - 2, hi - lo, h + 4])
    return rects


def redact_image(image, rows, ox, oy, pw, out=None):
    """Paint over redacted spans in an image of the screen region starting at (ox, oy), pw points wide."""
    rects = redact_rects(rows)
    if not rects:
        if out and out != image:
            import shutil
            shutil.copyfile(image, out)
        return 0
    from PIL import Image, ImageDraw
    with Image.open(image) as source:
        im = source.convert("RGB")
    paint_redactions(im, rects, ox, oy, pw)
    im.save(out or image)
    return len(rects)


def ocr_rows(path):
    return [(r[0], r[1]) for r in read_lines(path, 2) if r[1].strip()]


def paint_redactions(im, rects, ox, oy, pw):
    from PIL import ImageDraw
    k = im.width / pw
    dr = ImageDraw.Draw(im)
    for x, y, w, h in rects:
        dr.rectangle([(x - ox) * k, (y - oy) * k, (x - ox + w) * k, (y - oy + h) * k], fill=(48, 48, 48))


# ── fusion ──────────────────────────────────────────────────────────────────────────────────────

WORDY = re.compile(r"[가-힣A-Za-z]{2}")  # a label has words, not toolbar glyphs (":=^")
LABELLED = {"체크박스", "스위치", "선택", "슬라이더", "입력칸", "메뉴버튼"}

KIND = {
    "AXButton": "버튼", "AXLink": "링크", "AXTextField": "입력칸", "AXTextArea": "입력칸",
    "AXSearchField": "입력칸", "AXComboBox": "입력칸", "AXCheckBox": "체크박스", "AXSwitch": "스위치",
    "AXToggle": "스위치", "AXRadioButton": "선택", "AXPopUpButton": "메뉴버튼", "AXMenuButton": "메뉴버튼",
    "AXMenuItem": "메뉴항목", "AXRow": "행", "AXCell": "칸", "AXTab": "탭", "AXSlider": "슬라이더",
    "AXDisclosureTriangle": "펼치기", "AXIncrementor": "증감", "AXDockItem": "독",
}


def area(b):
    return max(b[2], 0) * max(b[3], 0)


def inter(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    return max(0, x2 - x1) * max(0, y2 - y1)


def iou(a, b):
    i = inter(a, b)
    return i / (area(a) + area(b) - i) if i else 0.0


def center(b):
    return b[0] + b[2] / 2, b[1] + b[3] / 2


def inside(pt, b, pad=1):
    return b[0] - pad <= pt[0] <= b[0] + b[2] + pad and b[1] - pad <= pt[1] <= b[1] + b[3] + pad


def parse_box(s):
    import math
    box = [float(v) for v in s.split(",")]
    if len(box) != 4 or not all(math.isfinite(v) for v in box) or min(box[2:]) <= 0:
        raise ValueError("invalid screen rectangle")
    return box


def read_lines(path, fields):
    rows = []
    if not path or not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as source:
        lines = source.readlines()
    for line in lines:
        parts = line.rstrip("\n").split("\t")
        if len(parts) < fields or parts[0] == "ERROR":
            continue
        try:
            rows.append([parse_box(parts[0])] + parts[1:])
        except ValueError:
            pass
    return rows


def fuse(ocr_path, ax_path, det_path):
    ocr = [{"box": r[0], "text": redact(r[1].strip()), "used": False} for r in read_lines(ocr_path, 2) if r[1].strip()]
    ax = [{"box": r[0], "role": r[1], "name": redact(r[2].strip()) if len(r) > 2 else "",
           "state": r[3].strip() if len(r) > 3 else ""} for r in read_lines(ax_path, 2)]
    det = []
    for r in sorted(read_lines(det_path, 2), key=lambda r: -float(r[1])):  # the detector repeats itself
        if area(r[0]) >= 36 and not any(iou(r[0], d["box"]) > 0.6 for d in det):
            det.append({"box": r[0], "conf": float(r[1])})

    elements = []
    # 1. Accessibility controls: exact kind and frame. Near-duplicates (a row and its only cell) keep one.
    for a in sorted(ax, key=lambda a: area(a["box"])):
        if area(a["box"]) < 16:
            continue
        dup = next((e for e in elements if iou(e["box"], a["box"]) > 0.85), None)
        if dup:
            if not dup["name"] and a["name"]:
                dup["name"] = a["name"]
            if not dup["state"]:
                dup["state"] = a["state"]
            continue
        elements.append({"box": a["box"], "kind": KIND.get(a["role"], "클릭영역"), "name": a["name"], "src": {"A"},
                         "state": a["state"]})

    # 1b. A generic clickable area ("클릭영역": a <div> with a click handler) that wraps several controls is
    #     a container, not a target; one that wraps a single control of about its size is that control.
    def contains(outer, inner):
        return inter(outer["box"], inner["box"]) >= 0.9 * area(inner["box"]) and outer is not inner
    drop = set()
    for g in elements:
        if g["kind"] != "클릭영역":
            continue
        kids = [e for e in elements if contains(g, e) and id(e) not in drop]
        if len(kids) >= 2:
            drop.add(id(g))
        elif len(kids) == 1 and iou(g["box"], kids[0]["box"]) > 0.5:
            if not kids[0]["name"]:
                kids[0]["name"] = g["name"]
            drop.add(id(g))
    elements = [e for e in elements if id(e) not in drop]

    # 2. Text inside a control names it (when the control had no name) and is folded into it when the
    #    control is about that text's size; text in a big container stays its own entry.
    for o in ocr:
        c = center(o["box"])
        holders = [e for e in elements if inside(c, e["box"])]
        if not holders:
            continue
        e = min(holders, key=lambda e: area(e["box"]))
        if not e["name"]:
            e["name"] = o["text"]
        if area(e["box"]) <= 6 * max(area(o["box"]), 1) or o["text"] in e["name"]:
            o["used"] = True
            e["src"].add("O")

    # 3. Detector boxes: confirm a control, mark a text as clickable, or add what the other two missed.
    for d in det:
        b = d["box"]
        match = max(elements, key=lambda e: iou(e["box"], b), default=None)
        if match and (iou(match["box"], b) > 0.4 or (inside(center(b), match["box"]) and area(b) > 0.3 * area(match["box"]))):
            match["src"].add("D")
            continue
        texts = [o for o in ocr if inside(center(o["box"]), b)]
        # Inside a control and showing (part of) its name: the same thing, seen by the detector.
        holder = min((e for e in elements if inside(center(b), e["box"])), key=lambda e: area(e["box"]), default=None)
        if holder and texts and holder["name"] and all(o["text"] in holder["name"] or holder["name"] in o["text"] for o in texts):
            holder["src"].add("D")
            for o in texts:
                o["used"] = True
            continue
        single = next((o for o in texts if iou(o["box"], b) > 0.45), None)
        if single:
            single["det"] = True
            continue
        if any(inside(center(b), e["box"]) and area(e["box"]) < 4 * area(b) for e in elements):
            continue
        name = " ".join(o["text"] for o in texts)[:80]
        for o in texts:
            o["used"] = True
        elements.append({"box": b, "kind": "상자", "name": name, "src": {"D"} | ({"O"} if texts else set()), "state": ""})

    # 4. The rest of the text, as text.
    for o in ocr:
        if not o["used"]:
            elements.append({"box": o["box"], "kind": "글자", "name": o["text"], "src": {"O", "D"} if o.get("det") else {"O"},
                             "state": ""})

    # 5. A switch, box, field or slider without a name of its own is named by the text on its row to
    #    the left (a settings row: "자동으로 밝기 조절 ……… [switch]"), marked as borrowed.
    for e in elements:
        if e["name"] or e["kind"] not in LABELLED:
            continue
        cx, cy = center(e["box"])
        row = [t for t in elements if WORDY.search(t["name"]) and t["kind"] in ("글자", "칸", "행", "클릭영역", "상자") and t is not e
               and abs(center(t["box"])[1] - cy) <= max(e["box"][3], 14) / 2 + 4 and center(t["box"])[0] < cx
               and cx - center(t["box"])[0] < 900]
        if row:
            label = max(row, key=lambda t: center(t["box"])[0])
            e["name"] = f"(옆 글자) {label['name']}"

    def key(e):
        cx, cy = center(e["box"])
        return (round(cy / 10), cx)

    elements.sort(key=key)
    return elements


def draw_marks(elements, image, ox, oy, pw, out, colors=None):
    from PIL import Image, ImageDraw, ImageFont

    im = Image.open(image).convert("RGB")
    k = im.width / pw
    shrink = min(1.0, 1800 / max(im.width, im.height))
    px = 1 / shrink
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", int(12 * px))
    except OSError:
        font = ImageFont.load_default()
    overlay = Image.new("RGBA", im.size, (0, 0, 0, 0))
    dr = ImageDraw.Draw(overlay)
    colors = colors or {"A": (40, 160, 255), "D": (255, 60, 160), "O": (120, 200, 80)}
    for i, e in enumerate(elements, 1):
        if e["kind"] == "글자" and "D" not in e["src"]:
            continue  # plain text is found by its words; numbering it would bury the controls
        x, y, w, h = e["box"]
        r = [(x - ox) * k, (y - oy) * k, (x - ox + w) * k, (y - oy + h) * k]
        col = colors["A"] if "A" in e["src"] else colors["D"] if "D" in e["src"] else colors["O"]
        dr.rectangle(r, outline=col + (230,), width=max(1, int(2 * px)))
        label = str(i)
        tw = dr.textlength(label, font=font)
        lx, ly = r[0], max(0, r[1] - 14 * px)
        dr.rectangle([lx, ly, lx + tw + 4 * px, ly + 14 * px], fill=col + (235,))
        dr.text((lx + 2 * px, ly), label, fill=(255, 255, 255, 255), font=font)
    im = Image.alpha_composite(im.convert("RGBA"), overlay).convert("RGB")
    if shrink < 1:
        im = im.resize((int(im.width * shrink), int(im.height * shrink)), Image.Resampling.LANCZOS)
    im.save(out)


def main():
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "serve":
        serve(args[0], args[1])
    elif cmd == "detect":
        detect(args[0], args[1], *args[2:5])
    elif cmd == "fuse":
        ocr_path, ax_path, det_path, out = args[:4]
        elements = fuse(ocr_path, ax_path, det_path)
        if os.environ.get("DESK_CAPTION_LATER") == "1" and os.environ.get("DESK_ENABLE_CAPTION") == "1":
            with open(out + ".todo", "w", encoding="utf-8") as f:
                for e in unnamed(elements):
                    f.write(f"{elements.index(e) + 1}\t{','.join(str(int(v)) for v in e['box'])}\n")
        elif len(args) >= 8 and os.environ.get("DESK_ENABLE_CAPTION") == "1":
            name_unnamed(elements, os.environ.get("DESK_SOCK", ""), args[4], float(args[5]), float(args[6]), float(args[7]))
        with open(out, "w", encoding="utf-8") as f:
            for i, e in enumerate(elements, 1):
                cx, cy = center(e["box"])
                src = "".join(s for s in "AODC" if s in e["src"])
                box = ",".join(str(int(v)) for v in e["box"])
                name = e["name"].replace("\t", " ") + (" " + e["state"] if e.get("state") else "")
                f.write(f"{i}\t{int(cx)},{int(cy)}\t{e['kind']}\t{name}\t{src}\t{box}\n")
        if len(args) >= 9:
            draw_marks(elements, args[4], float(args[5]), float(args[6]), float(args[7]), args[8])
            redact_image(args[8], ocr_rows(ocr_path), float(args[5]), float(args[6]), float(args[7]))
    elif cmd == "redact-image":  # IMAGE OCR_TSV OX OY PW: paint over secrets before the agent sees IMAGE
        redact_image(args[0], ocr_rows(args[1]), float(args[2]), float(args[3]), float(args[4]))
    elif cmd == "redact":  # stdin → stdout filter for text the shell prints directly (read/find)
        for line in sys.stdin:
            sys.stdout.write(redact(line))
        sys.stdout.flush()
    elif cmd == "caption":
        caption_later(*args)
    else:
        sys.exit(f"unknown: {cmd}")


if __name__ == "__main__":
    main()
