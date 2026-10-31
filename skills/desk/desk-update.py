#!/usr/bin/env python3
"""desk-update: an occasional, non-blocking check for a newer desk release (macOS, Windows, Linux).

`begin` prints the result of the previous check and, at most once a week, starts a detached process that
fetches the published VERSION file. Nothing about the screen, the user or this machine is sent. desk never
updates itself; the notice says how to update for the way it was installed. DESK_UPDATE_CHECK=0 turns it off.

  notice STATE ROOT   one line if the last check found a newer version; refresh in the background when stale
  fetch STATE         (background) store the latest published version in STATE/update.json
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

URL = "https://raw.githubusercontent.com/zidell/desk-skill/main/skills/desk/VERSION"
INTERVAL = 7 * 24 * 3600


def version(text):
    parts = text.strip().split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise ValueError(text)
    return tuple(int(p) for p in parts)


def load(cache):
    try:
        return json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save(cache, data):
    tmp = cache.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, cache)


def how_to_update(root):
    repo = root.parent.parent
    if (repo / ".git").exists():
        return "git -C " + str(repo) + " pull"
    if "plugins" in root.parts:
        return "Claude Code에서 /plugin → desk 업데이트 후 desk doctor"
    return "npx skills update -g"


def notice(state, root):
    if os.environ.get("DESK_UPDATE_CHECK") == "0":
        return
    cache = state / "update.json"
    data = load(cache)
    try:
        current = (root / "VERSION").read_text(encoding="utf-8").strip()
        latest = data.get("latest")
        if latest and version(latest) > version(current):
            print("desk " + latest + " 업데이트가 있습니다(현재 " + current + "). 작업을 마친 뒤 사용자에게 알리세요: " + how_to_update(root))
    except (OSError, ValueError):
        pass
    if time.time() - data.get("checked", 0) < INTERVAL:
        return
    # Mark first so that concurrent begins start one fetch, and a failed fetch waits a week too.
    save(cache, {**data, "checked": time.time()})
    flags = {"creationflags": 0x00000008 | 0x08000000} if sys.platform == "win32" else {"start_new_session": True}
    subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "fetch", str(state)],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **flags)


def fetch(state):
    cache = state / "update.json"
    with urllib.request.urlopen(URL, timeout=10) as response:
        latest = response.read(32).decode("ascii").strip()
    version(latest)
    save(cache, {**load(cache), "latest": latest, "checked": time.time()})


def main(argv):
    try:
        if argv[:1] == ["notice"] and len(argv) == 3:
            notice(Path(argv[1]), Path(argv[2]))
        elif argv[:1] == ["fetch"] and len(argv) == 2:
            fetch(Path(argv[1]))
        else:
            print("\n".join(__doc__.strip().splitlines()[-2:]), file=sys.stderr)
            return 2
    except (OSError, ValueError):
        return 1  # an update check never breaks desk
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
