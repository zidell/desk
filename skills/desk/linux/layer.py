"""Small binding to the installed LayerShellQt library, keeping panel focus out of apps."""
import ctypes
import ctypes.util
import fcntl
import hashlib
from pathlib import Path
import subprocess


def load():
    source=Path(__file__).with_suffix(".cpp")
    includes=[Path("/usr/include/LayerShellQt"),Path.home()/".cache/desk/headers/usr/include/LayerShellQt"]
    include=next((p for p in includes if (p/"window.h").exists()),None)
    if include is None:raise RuntimeError("Wayland 패널에 liblayershellqtinterface-dev 헤더가 필요합니다. docs/linux.md 참고")
    version=subprocess.check_output(["pkg-config","--modversion","Qt6Core"])
    digest=hashlib.sha256(source.read_bytes()+(include/"window.h").read_bytes()+version).hexdigest()[:16]
    directory=Path.home()/".cache"/"desk"/"layer";directory.mkdir(parents=True,exist_ok=True)
    library=directory/(digest+".so")
    with (directory/(digest+".lock")).open("w") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if not library.exists():
            import shlex
            flags=shlex.split(subprocess.check_output(["pkg-config","--cflags","--libs","Qt6Core","Qt6Gui"],text=True))
            subprocess.run(["c++","-shared","-fPIC","-O2",str(source),"-I"+str(include),*flags,
                            "-l:"+ctypes.util.find_library("LayerShellQtInterface"),"-o",str(library)],check=True,timeout=60)
    binding=ctypes.CDLL(str(library));binding.desk_layer.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_int]
    binding.desk_layer.restype=None
    return binding


def configure(binding,widget,overlay=False,margin=0):
    from PyQt6 import sip
    widget.winId()
    binding.desk_layer(int(sip.unwrapinstance(widget.windowHandle())),int(overlay),margin)
