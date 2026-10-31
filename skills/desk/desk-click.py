#!/usr/bin/env python3
"""Shared, screen-only evidence for pointer presses. No DOM, application data or input APIs.

Observations are immutable captures exposed by a read command, not internal pre-click captures.
The native backends validate the resulting patch and accessible target again at mouse-down.
"""
import json
import math
import os
from pathlib import Path
import shutil
import sys
import time
import uuid


class StaleClick(Exception):
    pass


def atomic_json(path, value):
    path = Path(path)
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def window_key(window):
    if isinstance(window, dict):
        return [window["hwnd"], window["pid"], window["box"]]
    if not isinstance(window, str):
        return window
    # macOS "frame<TAB>app · title<TAB>pid:windowID": the title is not the window's identity.
    fields = window.split("\t")
    return "\t".join([fields[0], fields[2]]) if len(fields) >= 3 else window


def observe(state, image, region, window, elements=(), bindings=(), epoch=None):
    state = Path(state)
    current_epoch=input_epoch(state)
    if epoch is not None and epoch != current_epoch:
        raise StaleClick("화면을 읽는 중 사용자가 개입했습니다. 다시 읽으세요.")
    image_copy = state / ("observed-" + uuid.uuid4().hex + ".png")
    shutil.copyfile(image, image_copy)
    record = {"image": str(image_copy), "region": region, "window": window_key(window),
              "elements": list(elements), "bindings": list(bindings), "time": time.time(),"inputEpoch":current_epoch}
    atomic_json(state / "observation.json", record)
    for old in state.glob("observed-*.png"):
        if old != image_copy:
            old.unlink(missing_ok=True)
    return record


def invalidate(state):
    (Path(state) / "observation.json").unlink(missing_ok=True)


def input_epoch(state):
    try:
        return (Path(state)/"user-input").read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def contains(box, x, y):
    return box[0] <= x < box[0]+box[2] and box[1] <= y < box[1]+box[3]


def area(box):
    return box[2]*box[3]


def intersect(a, b):
    x,y=max(a[0],b[0]),max(a[1],b[1])
    return [x,y,max(0,min(a[0]+a[2],b[0]+b[2])-x),max(0,min(a[1]+a[3],b[1]+b[3])-y)]


def crop(image, region, box):
    from PIL import Image
    with Image.open(image) as source:
        kx,ky=source.width/region[2],source.height/region[3]
        pixels=(round((box[0]-region[0])*kx),round((box[1]-region[1])*ky),
                round((box[0]+box[2]-region[0])*kx),round((box[1]+box[3]-region[1])*ky))
        result=source.crop(pixels).convert("RGB")
        result.info.update(source.info)
    return result


def save_crop(image, region, box, out):
    result=crop(image,region,box)
    result.save(out,icc_profile=result.info.get("icc_profile"))


def same_pixels(reference, current, current_region, box):
    from PIL import Image, ImageChops
    with Image.open(reference) as source:
        old=source.convert("RGB")
    new=crop(current,current_region,box)
    return old.size==new.size and ImageChops.difference(old,new).getbbox() is None


def same_binding(a, b):
    if a is None or b is None:
        return a is None and b is None
    keys=("id","role","name","box","enabled")
    return all(a.get(k)==b.get(k) for k in keys)


def prepare(state, point, window, requested_box=None):
    state=Path(state)
    try:
        observed=json.loads((state/"observation.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise StaleClick("마지막으로 읽은 화면 근거가 없습니다. desk see/shot/read로 다시 확인하세요.")
    if observed["window"]!=window_key(window):
        raise StaleClick("클릭 근거의 창이 현재 창과 다릅니다.")
    if observed.get("inputEpoch")!=input_epoch(state):
        raise StaleClick("화면을 읽은 뒤 사용자가 개입했습니다. 다시 읽으세요.")
    x,y=point
    if not contains(observed["region"],x,y):
        raise StaleClick("마지막으로 읽은 영역 밖의 좌표입니다.")
    candidates=[e["box"] for e in observed["elements"] if contains(e["box"],x,y)]
    selected=requested_box or (min(candidates,key=area) if candidates else [x-96,y-48,192,96])
    # Keep a little surrounding context; bound large link/card crops to the actual point.
    box=[math.floor(selected[0]-8),math.floor(selected[1]-8),math.ceil(selected[2]+16),math.ceil(selected[3]+16)]
    box=intersect(box,[x-256,y-128,512,256])
    box=intersect(box,observed["region"])
    if not contains(box,x,y) or min(box[2:])<=0:
        raise StaleClick("클릭 대상 영역을 확인할 수 없습니다.")
    patch=state/"click-observed.png"
    save_crop(observed["image"],observed["region"],box,patch)
    bindings=[b for b in observed["bindings"] if b.get("enabled", "비활성" not in b.get("state","")) and contains(b["box"],x,y) and b.get("role")!="Text"]
    known=min(bindings,key=lambda b:area(b["box"])) if bindings else None
    if known:
        known={**known,"enabled":known.get("enabled","비활성" not in known.get("state",""))}
    if not candidates and not requested_box and known is None:
        from PIL import Image
        with Image.open(patch) as im:
            if all(lo==hi for lo,hi in im.convert("RGB").getextrema()):
                raise StaleClick("비어 있는 영역이라 대상을 식별할 수 없습니다. 이름/요소 번호로 선택하세요.")
    return {"point":list(point),"box":box,"pixels":str(patch),"observedBinding":known,"window":window_key(window),"inputEpoch":input_epoch(state)}


def verify_initial(token, image, region, binding):
    known=token.get("observedBinding")
    # Enumeration and point hit-testing may expose different AX/UIA nodes for
    # the same visible control. Unchanged screen evidence is sufficient before
    # movement; retain the point binding for all subsequent mouse-down checks.
    if same_pixels(token["pixels"],image,region,token["box"]):
        return
    if known is not None and not same_binding(known,binding):
        raise StaleClick("읽은 뒤 대상의 정체·위치가 바뀌었습니다. 다시 읽으세요.")
    if known is not None and binding is not None and same_binding(known,binding):
        return  # same native control and exact frame; caret/hover paint can legitimately differ
    if binding and binding.get("caret"):
        from PIL import Image, ImageChops, ImageDraw
        old=Image.open(token["pixels"]).convert("RGB")
        new=crop(image,region,token["box"])
        if old.size==new.size:
            caret=intersect(binding["caret"],token["box"])
            if min(caret[2:])>0:
                sx,sy=old.width/token["box"][2],old.height/token["box"][3]
                rect=(math.floor((caret[0]-token["box"][0])*sx),math.floor((caret[1]-token["box"][1])*sy),
                      math.ceil((caret[0]+caret[2]-token["box"][0])*sx)-1,math.ceil((caret[1]+caret[3]-token["box"][1])*sy)-1)
                ImageDraw.Draw(old).rectangle(rect,fill="black");ImageDraw.Draw(new).rectangle(rect,fill="black")
                if ImageChops.difference(old,new).getbbox() is None:return
    raise StaleClick("읽은 뒤 클릭 대상 화면이 바뀌었습니다(로딩·리플로우 가능). 클릭하지 않고 멈춥니다.")


def finalize(state, token, image, region, binding, current_binding, settled=False):
    if token["inputEpoch"] != input_epoch(state):
        raise StaleClick("클릭 준비 중 사용자가 개입해 클릭하지 않습니다.")
    if not same_binding(binding,current_binding):
        raise StaleClick("클릭 예고 중 대상의 정체·위치가 바뀌었습니다. 다시 읽어야 합니다.")
    # settled: pixels were verified before movement and the arrival image carries hover
    # feedback, so comparing it with the pre-hover observation would be wrong.
    if binding is None and not settled and not same_pixels(token["pixels"],image,region,token["box"]):
        raise StaleClick("클릭 예고 중 대상 모양이 바뀌어 클릭하지 않습니다.")
    out=Path(state)/"click-final.png"
    save_crop(image,region,token["box"],out)
    guard={"point":token["point"],"box":token["box"],"pixels":str(out),"binding":current_binding,"window":token["window"],"inputEpoch":token["inputEpoch"]}
    atomic_json(Path(state)/"click-guard.json",guard)
    return guard


def main(args):
    command,state=args[:2]
    if command=="observe":
        image,region,window=args[2:5]
        elements=[]
        if len(args)>5 and Path(args[5]).exists():
            for line in Path(args[5]).read_text(encoding="utf-8").splitlines():
                cols=line.split("\t")
                if len(cols)>=6:
                    elements.append({"box":[float(v) for v in cols[5].split(",")]})
        observe(state,image,[int(v) for v in region.split(",")],window,elements,epoch=os.environ.get("DESK_CAPTURE_INPUT"))
    elif command=="invalidate":invalidate(state)
    elif command=="prepare":
        point=[int(v) for v in args[2:4]]
        requested=[float(v) for v in args[5].split(",")] if len(args)>5 else None
        atomic_json(Path(state)/"click-plan.json",prepare(state,point,args[4],requested))
    elif command=="initial":
        token=json.loads((Path(state)/"click-plan.json").read_text(encoding="utf-8"))
        binding=json.loads(Path(args[4]).read_text(encoding="utf-8"))
        verify_initial(token,args[2],[int(v) for v in args[3].split(",")],binding)
        token["binding"]=binding
        atomic_json(Path(state)/"click-plan.json",token)
    elif command=="finalize":
        token=json.loads((Path(state)/"click-plan.json").read_text(encoding="utf-8"))
        binding=json.loads(Path(args[4]).read_text(encoding="utf-8"))
        current=json.loads(Path(args[5]).read_text(encoding="utf-8"))
        finalize(state,token,args[2],[int(v) for v in args[3].split(",")],binding,current)
    else:raise ValueError("unknown click evidence command")


if __name__=="__main__":
    try:main(sys.argv[1:])
    except StaleClick as e:
        print("desk: "+str(e)+" (종료 코드 6)",file=sys.stderr)
        sys.exit(6)
