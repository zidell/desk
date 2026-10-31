"""Idempotent Windows install; binaries, environments and models stay off the shared Drive."""
import os
from pathlib import Path
import subprocess
import sys
import winreg

ROOT=Path(__file__).resolve().parent.parent
STATE=Path(os.environ.get("DESK_STATE",Path.home()/".cache"/"desk"))
BASE=["pillow","psutil","winrt-runtime","winrt-Windows.Foundation","winrt-Windows.Foundation.Collections",
      "winrt-Windows.Globalization","winrt-Windows.Media.Ocr","winrt-Windows.Graphics.Imaging",
      "winrt-Windows.Storage","winrt-Windows.Storage.Streams","huggingface_hub>=0.34,<1"]


def call(*argv):
    subprocess.run([str(a) for a in argv],check=True)


def is_link(path):
    """A symlink or an NTFS junction (os.readlink reads both)."""
    try:return path.is_symlink() or bool(os.readlink(path))
    except (OSError,ValueError):return False


def stable_root():
    """Claude Code installs a plugin in plugins/cache/<marketplace>/<plugin>/<version>/, a new folder on every update.
    Its marketplace clone in plugins/marketplaces/<marketplace>/ keeps one path and is updated with it."""
    parts=ROOT.parts
    for i in range(len(parts)-4):
        if parts[i]=="plugins" and parts[i+1]=="cache":
            stable=Path(*parts[:i+1])/"marketplaces"/parts[i+2]/Path(*parts[i+5:])
            if (stable/"SKILL.md").exists():return stable
    return ROOT


def is_plugin():
    parts=ROOT.parts
    return any(parts[i]=="plugins" and parts[i+1] in ("cache","marketplaces") for i in range(len(parts)-1))


def skill_links():
    # ~/.codex/skills/desk comes first: desk.cmd and desk.exe enter through it.
    dirs=[(".codex","skills"),(".agents","skills")]+([] if is_plugin() else [(".claude","skills")])
    return [Path.home().joinpath(*d)/"desk" for d in dirs]


def install_status():
    """doctor fields: does every skill link point at this folder?"""
    root=stable_root()
    return {str(link):("ok" if link.exists() and link.resolve()==root.resolve() else "missing or another folder → desk.py install")
            for link in skill_links()}


def install_commands(py):
    bin_dir=Path.home()/".local"/"bin"
    bin_dir.mkdir(parents=True,exist_ok=True)
    # The ASCII junction path avoids cmd.exe decoding a UTF-8 batch file as the legacy code page.
    entry=Path.home()/".codex"/"skills"/"desk"/"desk.py"
    command='@echo off\r\npy -3 -X utf8 "%USERPROFILE%\\.codex\\skills\\desk\\desk.py" %*\r\nexit /b %errorlevel%\r\n'
    (bin_dir/"desk.cmd").write_bytes(command.encode("utf-8"))
    # Scripts in a user directory are discoverable; the full path is also usable in -NoProfile shells.
    ps='& "'+str(py)+'" -X utf8 "'+str(entry)+'" @args\nexit $LASTEXITCODE\n'
    (bin_dir/"desk.ps1").write_text(ps,encoding="utf-8-sig")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,"Environment") as key:
        try:old,_=winreg.QueryValueEx(key,"Path")
        except FileNotFoundError:old=""
        entries=[e for e in old.split(";") if e and e.casefold()!=str(bin_dir).casefold()]
        winreg.SetValueEx(key,"Path",0,winreg.REG_EXPAND_SZ,str(bin_dir)+";"+";".join(entries))
    # A native launcher preserves Unicode arguments without requiring script policy changes.
    import json
    framework=Path(os.environ["WINDIR"])/"Microsoft.NET"/"Framework64"/"v4.0.30319"
    call(framework/"csc.exe","/nologo","/target:exe","/platform:x64","/r:System.Web.Extensions.dll",
         "/out:"+str(bin_dir/"desk.exe"),ROOT/"windows"/"DeskLauncher.cs")
    (bin_dir/"desk-launch.json").write_text(json.dumps({"python":str(py),"entry":str(entry)}),encoding="utf-8")
    import ctypes
    docs_buffer=ctypes.create_unicode_buffer(32768)
    if ctypes.windll.shell32.SHGetFolderPathW(None,5,None,0,docs_buffer)!=0:
        raise RuntimeError("Windows 문서 폴더 경로를 조회하지 못했습니다")
    docs=Path(docs_buffer.value)
    # Remove only our earlier development registration; preserve all other profile content.
    import re
    for docs_dir in {docs,Path.home()/"Documents"}:
        for folder in ("WindowsPowerShell","PowerShell"):
            profile=docs_dir/folder/"profile.ps1"
            if not profile.exists():continue
            prior=profile.read_text(encoding="utf-8-sig")
            clean=re.sub(r"\n# desk: shared macOS/Windows computer control\nfunction global:desk \{[^\n]*\}\n?", "\n", prior)
            if clean!=prior:
                if clean.strip():profile.write_text(clean,encoding="utf-8-sig")
                else:profile.unlink()
    root=stable_root()
    for link in skill_links():
        link.parent.mkdir(parents=True,exist_ok=True)
        # Already this folder: a junction to it, or the folder itself (npx skills installs a real copy there).
        if link.exists() and link.resolve()==root.resolve():continue
        if is_link(link):
            # A junction or symlink to an older clone or synced folder; removing it leaves the target intact.
            print("교체: "+str(link)+" → "+str(root)+" (이전: "+str(link.resolve())+")")
            os.rmdir(link)
        elif link.exists():
            raise RuntimeError("링크가 아닌 기존 경로라 덮어쓰지 않습니다: "+str(link))
        call("cmd","/c","mklink","/J",str(link),str(root))
    result=ctypes.c_size_t()
    ctypes.windll.user32.SendMessageTimeoutW(0xFFFF,0x1A,0,"Environment",2,3000,ctypes.byref(result))
    print("설치: "+str(bin_dir/"desk.cmd"))
    print("Windows: desk.exe 또는 desk.cmd (desk.cpl 충돌을 피하려면 확장자/절대 경로 명시)")


def setup(args,install=False):
    py=STATE/"windows"/"Scripts"/"python.exe"
    if not py.exists():
        call(sys.executable,"-m","venv",STATE/"windows")
    if not install:
        call(py,"-m","pip","install",*BASE)
        if "--detector" in args or "--vision" in args:
            cuda=bool(__import__("shutil").which("nvidia-smi"))
            call(py,"-m","pip","install","torch","torchvision","--index-url",
                 "https://download.pytorch.org/whl/cu128" if cuda else "https://download.pytorch.org/whl/cpu")
        if "--detector" in args:
            from huggingface_hub import hf_hub_download
            hf_hub_download("microsoft/OmniParser-v2.0","icon_detect_v3/model.pt",revision="refs/pr/37",local_dir=str(STATE/"omni"/"weights"))
        if "--vision" in args:
            call(py,"-m","pip","install","transformers>=4.50,<5","accelerate","num2words")
            from huggingface_hub import snapshot_download
            import json
            model="HuggingFaceTB/SmolVLM2-500M-Video-Instruct"
            snapshot_download(model,ignore_patterns=["*.onnx","*.bin","*.gguf"])
            (STATE/"vision.json").write_text(json.dumps({"model":model,"mode":"shadow","language":"English"}),encoding="utf-8")
    install_commands(py)
    print("Windows desk 준비 완료. desk.cmd doctor로 확인하세요.")
