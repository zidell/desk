#!/usr/bin/env python3
"""Install per-user desk launcher, discoverable skill links and OCR data.

OS packages are diagnosed, never installed with implicit sudo. All generated
runtime, build and model files stay outside the shared skill directory.
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request

ROOT=Path(__file__).resolve().parent.parent
CACHE=Path.home()/".cache"/"desk"


def link(source,target):
    """Point target at source. An existing link elsewhere (an older clone, a synced folder) is replaced;
    a real file or directory is never touched."""
    target.parent.mkdir(parents=True,exist_ok=True)
    # Already this folder: a link to it, or the folder itself (npx skills installs a real copy there).
    if target.exists() and target.resolve()==source.resolve():return
    if target.is_symlink():
        print("교체: "+str(target)+" → "+str(source)+" (이전: "+str(target.readlink())+")")
        target.unlink()
    elif target.exists():raise RuntimeError("링크가 아닌 기존 경로라 덮어쓰지 않습니다: "+str(target))
    target.symlink_to(source,target_is_directory=source.is_dir())


SKILL_DIRS=(".claude/skills",".codex/skills",".agents/skills")


def skill_links():
    """Skill folders this install should own; a Claude Code plugin install already provides the skill to Claude."""
    plugin=Path.home()/".claude"/"plugins" in ROOT.parents
    return [Path.home()/d/"desk" for d in SKILL_DIRS if not (plugin and d==".claude/skills")]


def install():
    launcher=Path.home()/".local"/"bin"/"desk"
    launcher.parent.mkdir(parents=True,exist_ok=True)
    # A copied or synced skill folder may lose executable permission. The wrapper calls Python explicitly.
    import shlex
    text="#!/bin/sh\nexec /usr/bin/python3 "+shlex.quote(str(ROOT/"desk.py"))+ ' "$@"\n'
    if launcher.is_symlink() or launcher.exists():
        old=launcher.read_text(errors="replace") if launcher.is_file() else ""
        # Only an earlier desk launcher (another clone, a moved folder) is replaced.
        if old!=text and not (old.startswith("#!/bin/sh\nexec ") and "desk.py" in old):
            raise RuntimeError("desk 실행기가 아닌 기존 파일이라 덮어쓰지 않습니다: "+str(launcher))
        if old!=text:print("교체: "+str(launcher))
        launcher.unlink()
    launcher.write_text(text);launcher.chmod(0o755)
    for target in skill_links():
        link(ROOT,target)
    desktop=Path.home()/".local"/"share"/"applications"/"org.desk.Tool.desktop"
    desktop.parent.mkdir(parents=True,exist_ok=True)
    desktop.write_text('[Desktop Entry]\nType=Application\nName=desk\nComment=Visible desktop control with a stop panel\nExec='+str(launcher)+' help\nIcon=input-mouse\nNoDisplay=true\nTerminal=false\n')
    if shutil.which("update-desktop-database"):subprocess.run(["update-desktop-database",str(desktop.parent)],check=True)
    print("launcher: "+str(launcher))
    if str(launcher.parent) not in os.environ.get("PATH","").split(os.pathsep):
        print("PATH에 "+str(launcher.parent)+"가 없습니다. 셸 설정에 추가하거나 "+str(launcher)+"로 실행하세요")


def install_status():
    """doctor lines: is the launcher and every skill link pointing at this folder?"""
    launcher=Path.home()/".local"/"bin"/"desk"
    lines=[]
    ok=launcher.is_file() and str(ROOT/"desk.py") in launcher.read_text(errors="replace")
    lines.append("launcher: "+(str(launcher) if ok else "missing or another folder → desk.py install"))
    for target in skill_links():
        good=target.resolve()==ROOT.resolve() if target.exists() else False
        lines.append("skill link "+str(target)+": "+("ok" if good else "missing or another folder → desk.py install"))
    return lines


def runtime():
    target=CACHE/"linux"
    subprocess.run([sys.executable,"-m","venv","--without-pip","--system-site-packages",str(target)],check=True)
    python=target/"bin"/"python"
    if subprocess.run([str(python),"-m","pip","--version"],capture_output=True).returncode:
        bootstrap=CACHE/"get-pip.py"
        urllib.request.urlretrieve("https://bootstrap.pypa.io/get-pip.py",bootstrap)
        subprocess.run([str(python),str(bootstrap)],check=True)
    subprocess.run([str(python),"-m","pip","install","python-xlib"],check=True)


def korean():
    directory=CACHE/"tessdata";directory.mkdir(parents=True,exist_ok=True)
    for lang in ("kor","eng"):
        target=directory/(lang+".traineddata")
        if target.exists():continue
        # Pin the exact upstream model revision; no runtime downloads.
        url="https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0/"+lang+".traineddata"
        temporary=target.with_suffix(".tmp")
        urllib.request.urlretrieve(url,temporary);temporary.replace(target)
        print(lang+" OCR installed")


def headers():
    if Path("/usr/include/LayerShellQt/window.h").exists():return
    version=subprocess.check_output(["dpkg-query","-W","-f=${Version}","liblayershellqtinterface6"],text=True).strip()
    directory=CACHE/"header-packages";directory.mkdir(parents=True,exist_ok=True)
    subprocess.run(["apt-get","download","liblayershellqtinterface-dev="+version],cwd=directory,check=True)
    packages=list(directory.glob("liblayershellqtinterface-dev*.deb"))
    if not packages:raise RuntimeError("LayerShellQt 개발 헤더 패키지를 받지 못했습니다")
    # Per-user extraction; no root install or system configuration changes.
    subprocess.run(["dpkg-deb","-x",str(max(packages,key=lambda p:p.stat().st_mtime)),str(CACHE/"headers")],check=True)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--runtime",action="store_true",help="prepare local Python environment")
    parser.add_argument("--ocr",action="store_true",help="download local Korean/English OCR models")
    parser.add_argument("--headers",action="store_true",help="extract matching LayerShellQt headers locally (Debian/Ubuntu)")
    parser.add_argument("--install-only",action="store_true",help="only the launcher and skill links")
    options=parser.parse_args()
    if options.install_only:
        install();return
    CACHE.mkdir(parents=True,exist_ok=True,mode=0o700)
    if options.runtime:runtime()
    if options.headers:headers()
    if options.ocr:korean()
    install()
    subprocess.run([sys.executable,str(ROOT/"desk.py"),"doctor"],check=True)


if __name__=="__main__":
    try:main()
    except RuntimeError as e:sys.exit("desk: "+str(e))
