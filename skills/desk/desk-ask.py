#!/usr/bin/env python3
"""desk-ask: the narrow questions the AI looked at an image for, asked of a local vision model too.

Shadow period: the AI still looks at the image and records its own
answer (the reference); the local model answers the same question in the background and its answer
is only compared, never used. One JSON record per question in DIR.

  new DIR ID REGION KIND WINDOW QUESTION   record a question (image DIR/ID.png, OCR text DIR/ID.ocr)
  ref DIR ID ANSWER                        the AI's own answer from the image
  work DIR MODEL                           answer every record still without a local answer (one model load)
  report DIR [--list]                      per kind: agreement, local "없음", OCR check, silent-wrong candidates

Check of a local answer, by machine: 글자·숫자 = the answer is in the region's OCR text; "없음" = the
model said the answer is not there; anything else cannot be checked. A silent wrong answer is a local
answer that differs from the reference while the check passed or could not run — the number that
decides whether a kind can go local-first.
"""
import glob
import json
import os
import re
import sys
import time
import uuid
from contextlib import contextmanager

KINDS = ["글자", "숫자", "선택", "색", "창", "그림"]
NONE = "없음"
PROMPT = "{q}\n답이 이 그림에 없으면 '없음'이라고만 쓰세요. 짧게 답만 쓰세요."
MAXPX = 1280  # measured 2026-10-17: region crops at this size, 3-5 s an answer on M1


def path(d, i, ext="json"):
    return os.path.join(d, f"{i}.{ext}")


def load(d, i):
    with open(path(d, i), encoding="utf-8") as f:
        return json.load(f)


def save(d, rec):
    tmp = path(d, rec["id"], uuid.uuid4().hex + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path(d, rec["id"]))


def norm(s):
    s = re.sub(r"[\s,.·:;'\"`()\[\]「」『』]", "", s.lower())
    return re.sub(r"(색|입니다|이에요|예요)$", "", s)


def agreement(local, ref):
    a, b = norm(local), norm(ref)
    if a == b:
        return "일치"
    if a and b and (a in b or b in a):
        return "부분"
    return "불일치"


def check(rec):
    ans = rec["local"]["answer"]
    if norm(ans) == norm(NONE):
        return NONE
    if rec["kind"] in ("글자", "숫자"):
        try:
            with open(path(rec["dir"], rec["id"], "ocr"), encoding="utf-8") as f:
                ocr = norm(f.read())
        except FileNotFoundError:
            return "대조 불가"
        return "통과" if norm(ans) and norm(ans) in ocr else "불일치"
    return "대조 불가"


def new(d, i, region, kind, window, q):
    save(d, {"id": i, "dir": d, "time": time.strftime("%F %T"), "window": window, "region": region,
             "kind": kind, "question": q, "local": None, "check": None, "ref": None, "verdict": None})


@contextmanager
def record_lock(d, i):
    with open(path(d, i, "lock"), "a+b") as lock:
        if sys.platform == "win32":
            import msvcrt
            lock.seek(0); lock.write(b"0"); lock.flush(); lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if sys.platform == "win32":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


def ref(d, i, answer):
    with record_lock(d, i):
        rec = load(d, i)
        rec["ref"] = answer
        save(d, rec)


def read_json_file(filename):
    with open(filename, encoding="utf-8") as source:
        return json.load(source)


def pending(d):
    recs = (read_json_file(p) for p in glob.glob(os.path.join(d, "*.json")))
    return sorted((r for r in recs if r["local"] is None), key=lambda r: r["id"])


def work(d, model_id):
    if sys.platform == "win32":
        return work_windows(d, model_id)
    todo = pending(d)
    if not todo:
        return
    import warnings

    warnings.filterwarnings("ignore")
    os.environ["HF_HUB_OFFLINE"] = "1"
    from mlx_vlm import generate, load as load_model
    from mlx_vlm.prompt_utils import apply_chat_template
    from PIL import Image

    model, proc = load_model(model_id)
    while todo:
        for rec in todo:
            img = path(d, rec["id"], "png")
            small = path(d, rec["id"], "small.png")
            if not os.path.exists(img):
                rec["local"] = {"answer": "", "sec": 0, "model": model_id, "error": "그림 없음"}
            else:
                im = Image.open(img).convert("RGB")
                im.thumbnail((MAXPX, MAXPX))
                im.save(small)
                prompt = apply_chat_template(proc, model.config, PROMPT.format(q=rec["question"]),
                                             num_images=1, enable_thinking=False)
                t = time.time()
                r = generate(model, proc, prompt, image=[small], max_tokens=80, temperature=0.0, verbose=False)
                rec["local"] = {"answer": getattr(r, "text", r).strip(), "sec": round(time.time() - t, 1), "model": model_id}
                os.remove(small)
                rec["check"] = check(rec)
            with record_lock(d, rec["id"]):
                cur = load(d, rec["id"])  # ref may have been written meanwhile
                cur["local"], cur["check"] = rec["local"], rec.get("check")
                save(d, cur)
        # questions asked while this batch ran
        todo = pending(d)


def work_windows(d, model_id):
    """Same shadow records as macOS, with offline PyTorch CUDA/CPU inference."""
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent / "windows"))
    from local_vision import load_vision
    from PIL import Image
    os.environ["HF_HUB_OFFLINE"] = "1"
    answer = load_vision(model_id)
    idle_until = time.monotonic() + 30
    while time.monotonic() < idle_until:
        if (Path(d).parent / "stopped").exists():
            return
        todo = pending(d)
        if not todo:
            time.sleep(.2)
            continue
        for rec in todo:
            started = time.monotonic()
            try:
                with Image.open(path(d, rec["id"], "png")) as source:
                    image = source.convert("RGB")
                image.thumbnail((MAXPX, MAXPX))
                text = answer(image, rec["question"] + "\nAnswer briefly. If it is not visible, say '없음'.")
                result = {"answer": text, "sec": round(time.monotonic()-started, 1), "model": model_id}
            except Exception as e:
                result = {"answer": "", "sec": round(time.monotonic()-started, 1), "model": model_id, "error": str(e)}
            with record_lock(d, rec["id"]):
                current = load(d, rec["id"])
                current["local"] = result
                current["check"] = check(current) if not result.get("error") else "대조 불가"
                save(d, current)
        idle_until = time.monotonic() + 30


def report(d, listing):
    recs = [read_json_file(p) for p in sorted(glob.glob(os.path.join(d, "*.json")))]
    done = [r for r in recs if r["local"] and r["ref"] and not r["local"].get("error")]
    print(f"질문 {len(recs)}개 · 로컬·참조 답 다 있는 것 {len(done)}개 "
          f"(로컬 대기 {sum(1 for r in recs if not r['local'])}, 참조 답 없음 {sum(1 for r in recs if not r['ref'])})")
    print("종류\tn\t일치\t부분\t불일치\t로컬 없음\t대조 통과/불일치/불가\t조용한 오답 후보\t로컬 초(중앙)")
    for k in KINDS:
        rs = [r for r in done if r["kind"] == k]
        if not rs:
            continue
        ag = [agreement(r["local"]["answer"], r["ref"]) for r in rs]
        ch = [r["check"] for r in rs]
        silent = sum(1 for a, c in zip(ag, ch) if a != "일치" and c in ("통과", "대조 불가"))
        secs = sorted(r["local"]["sec"] for r in rs)
        print(f"{k}\t{len(rs)}\t{ag.count('일치')}\t{ag.count('부분')}\t{ag.count('불일치')}\t{ch.count(NONE)}\t"
              f"{ch.count('통과')}/{ch.count('불일치')}/{ch.count('대조 불가')}\t{silent}\t{secs[len(secs) // 2]}")
    if listing:
        print("\n일치하지 않은 것 (판정: verdict = local | ref | both | neither 를 레코드에 적는다)")
        for r in done:
            a = agreement(r["local"]["answer"], r["ref"])
            if a != "일치":
                print(f"{r['id']}\t{r['kind']}\t{a}\t{r['check']}\t{r['verdict'] or '-'}\tQ {r['question']}\t"
                      f"로컬 {r['local']['answer']!r}\t참조 {r['ref']!r}\t{path(d, r['id'], 'png')}")


def main():
    cmd, a = sys.argv[1], sys.argv[2:]
    if cmd == "new":
        new(*a[:5], " ".join(a[5:]))
    elif cmd == "ref":
        ref(a[0], a[1], " ".join(a[2:]))
    elif cmd == "work":
        work(a[0], a[1])
    elif cmd == "report":
        report(a[0], "--list" in a)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
