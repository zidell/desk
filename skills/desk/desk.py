#!/usr/bin/env python3
"""Single portable entry point; the existing macOS driver remains its native backend."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if sys.platform == "win32":
    state = Path(os.environ.get("DESK_STATE", Path.home() / ".cache" / "desk"))
    runtime = state / "windows" / "Scripts" / "python.exe"
    if not runtime.exists() and "setup" in sys.argv[1:2]:
        subprocess.check_call([sys.executable, "-m", "venv", str(state / "windows")])
    if runtime.exists() and Path(sys.executable).resolve() != runtime.resolve():
        raise SystemExit(subprocess.call([str(runtime), "-X", "utf8", str(__file__), *sys.argv[1:]]))
    sys.pycache_prefix = str(state / "pycache")
    os.environ["PYTHONPYCACHEPREFIX"] = sys.pycache_prefix
    sys.path.insert(0, str(ROOT / "windows"))
    from desk_windows import main
    raise SystemExit(main(sys.argv[1:]))
if sys.platform == "darwin":
    os.execv("/bin/bash", ["bash", str(ROOT / "desk"), *sys.argv[1:]])
if sys.platform.startswith("linux"):
    state = Path(os.environ.get("DESK_STATE", Path.home() / ".cache" / "desk"))
    # Shared system bindings (Qt, GStreamer, AT-SPI) remain available in this venv.
    runtime = Path.home() / ".cache" / "desk" / "linux" / "bin" / "python"
    if runtime.exists() and Path(sys.prefix) != runtime.parent.parent:
        os.execv(str(runtime), [str(runtime), str(__file__), *sys.argv[1:]])
    sys.pycache_prefix = str(state / "pycache")
    os.environ["PYTHONPYCACHEPREFIX"] = sys.pycache_prefix
    sys.path.insert(0, str(ROOT / "linux"))
    from desk_linux import main
    raise SystemExit(main(sys.argv[1:]))
sys.exit("desk: 지원하지 않는 OS입니다: " + sys.platform)
