#!/usr/bin/env python3
"""Build an app-private AT-SPI runtime; never install system services or files."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import zipfile


def build(output, bridge=False):
    root=Path(__file__).resolve().parent
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    sums=json.loads((root/'checksums.json').read_text())
    for name,expected in sums.items():
        if hashlib.sha256((root/name).read_bytes()).hexdigest()!=expected:
            raise RuntimeError('AT-SPI vendored input checksum mismatch: '+name)
    packages=['dbus-1','glib-2.0','gobject-2.0','gio-2.0']+(['atk','gmodule-2.0','libxml-2.0'] if bridge else [])
    versions=subprocess.check_output(['pkg-config','--modversion',*packages])
    key=hashlib.sha256((root/'build.py').read_bytes()+(root/'transport.patch').read_bytes()+versions+json.dumps(sums,sort_keys=True).encode()+os.uname().machine.encode()+os.environ.get('CC','cc').encode()+str(bridge).encode()).hexdigest()
    with (output/'.build.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        stamp=output/'.stamp'
        libraries=['libatspi.so.0.0.1']+(['libatk-bridge-2.0.so.0.0.0'] if bridge else [])
        if stamp.exists() and stamp.read_text()==key and all((output/n).exists() for n in libraries):return output
        # Each destination has its own source/build directory. No global loader
        # configuration, login settings, or D-Bus service registration is changed.
        work=output/'.build'
        if work.exists():shutil.rmtree(work)
        work.mkdir()
        tools=work/'tools'
        with zipfile.ZipFile(root/'meson-1.9.1-py3-none-any.whl') as z:z.extractall(tools)
        with tarfile.open(root/'at-spi2-core-2.60.4.tar.xz') as t:t.extractall(work,filter='data')
        source=work/'at-spi2-core-2.60.4'
        with (root/'transport.patch').open('rb') as patch:
            subprocess.run(['patch','--batch','--forward','-p1'],stdin=patch,cwd=source,stdout=subprocess.DEVNULL,check=True)
        env={**os.environ,'PYTHONPATH':str(tools)}
        meson=[sys.executable,'-m','mesonbuild.mesonmain']
        log=output/'build.log'
        with log.open('w') as f:
            subprocess.run([*meson,'setup',str(work/'build'),str(source),'--buildtype=release','-Dintrospection=disabled','-Dgtk2_atk_adaptor=false','-Ddefault_bus=dbus-daemon','-Duse_systemd=false'],env=env,stdout=f,stderr=subprocess.STDOUT,check=True)
            targets=['atspi/libatspi.so.0.0.1']+(['atk-adaptor/libatk-bridge-2.0.so.0.0.0'] if bridge else [])
            subprocess.run(['ninja','-C',str(work/'build'),'-j2',*targets],env=env,stdout=f,stderr=subprocess.STDOUT,check=True)
        for name in libraries:
            src=work/'build'/('atk-adaptor' if 'bridge' in name else 'atspi')/name
            temp=output/(name+'.new');shutil.copy2(src,temp)
            subprocess.run(['patchelf','--remove-rpath',str(temp)],check=True)
            os.replace(temp,output/name)
            for alias in (name.split('.so.')[0]+'.so.0',name.split('.so.')[0]+'.so'):
                link=output/alias;link.unlink(missing_ok=True);link.symlink_to(name)
        shutil.copy2(root/'transport.patch',output/'transport.patch')
        shutil.copy2(root/'README.md',output/'README.md')
        # Ship the LGPL source and the exact build inputs with the private DSO.
        for name in sums:shutil.copy2(root/name,output/name)
        for name in ['build.py','checksums.json']:shutil.copy2(root/name,output/name)
        stamp.write_text(key)
        return output


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('output',type=Path);p.add_argument('--bridge',action='store_true')
    a=p.parse_args();print(build(a.output,a.bridge))
