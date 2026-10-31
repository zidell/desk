#!/usr/bin/env python3
"""Install per-user desk launcher, discoverable skill links and OCR data.

OS packages are diagnosed, never installed with implicit sudo. All generated
runtime, build and model files stay outside the shared skill directory.
"""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request

ROOT=Path(__file__).resolve().parent.parent
CACHE=Path.home()/".cache"/"desk"


def link(source,target):
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.is_symlink() and target.resolve()==source.resolve():return
    if target.exists() or target.is_symlink():raise RuntimeError("기존 경로를 덮어쓰지 않습니다: "+str(target))
    target.symlink_to(source,target_is_directory=source.is_dir())


def install():
    launcher=Path.home()/".local"/"bin"/"desk"
    launcher.parent.mkdir(parents=True,exist_ok=True)
    # The shared Drive copy may lose executable permission. A local executable
    # wrapper calls Python explicitly and follows the canonical shared source.
    import shlex
    text="#!/bin/sh\nexec /usr/bin/python3 "+shlex.quote(str(ROOT/"desk.py"))+ ' "$@"\n'
    if launcher.exists() and launcher.read_text()!=text:raise RuntimeError("기존 desk 실행기를 확인하세요: "+str(launcher))
    launcher.write_text(text);launcher.chmod(0o755)
    for directory in (".codex/skills",".agents/skills"):
        link(ROOT,Path.home()/directory/"desk")
    desktop=Path.home()/".local"/"share"/"applications"/"org.desk.Tool.desktop"
    desktop.parent.mkdir(parents=True,exist_ok=True)
    desktop.write_text('[Desktop Entry]\nType=Application\nName=desk\nComment=Visible desktop control with a stop panel\nExec='+str(launcher)+' help\nIcon=input-mouse\nNoDisplay=true\nTerminal=false\n')
    if shutil.which("update-desktop-database"):subprocess.run(["update-desktop-database",str(desktop.parent)],check=True)
    print("launcher: "+str(launcher))


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
    options=parser.parse_args()
    CACHE.mkdir(parents=True,exist_ok=True,mode=0o700)
    if options.runtime:runtime()
    if options.headers:headers()
    if options.ocr:korean()
    install()
    subprocess.run([sys.executable,str(ROOT/"desk.py"),"doctor"],check=True)


if __name__=="__main__":main()
