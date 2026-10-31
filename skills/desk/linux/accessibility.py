"""Read only visible controls of the foreground process. No text/value/action APIs."""
import json
import sys


def collect(window):
    import gi
    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi
    Atspi.set_timeout(350, 500)
    roles = {"push button":"AXButton", "button":"AXButton", "toggle button":"AXToggle", "check box":"AXCheckBox",
        "radio button":"AXRadioButton", "entry":"AXTextField", "password text":"AXTextField",
        "link":"AXLink", "combo box":"AXComboBox", "slider":"AXSlider",
        "menu item":"AXMenuItem", "check menu item":"AXMenuItem", "radio menu item":"AXMenuItem",
        "page tab":"AXTab", "table cell":"AXCell", "spin button":"AXIncrementor"}
    box = window["box"]
    def intersect(a,b):
        x,y=max(a[0],b[0]),max(a[1],b[1])
        return [x,y,max(0,min(a[0]+a[2],b[0]+b[2])-x),max(0,min(a[1]+a[3],b[1]+b[3])-y)]
    desktop = Atspi.get_desktop(0)
    desktop.clear_cache_single()
    result, warnings = [], []
    apps = [desktop.get_child_at_index(i) for i in range(desktop.get_child_count())]
    for app in apps:
        if app.get_process_id() != window["pid"]:
            continue
        app.clear_cache_single()
        # ACTIVE/FOCUSED top-level selection prevents names of background tabs/windows
        # in this process from entering the observation.
        roots=[]
        for i in range(min(app.get_child_count(),100)):
            child=app.get_child_at_index(i)
            child.clear_cache_single()
            states=child.get_state_set()
            popup="menu" in child.get_role_name().lower() and states.contains(Atspi.StateType.SHOWING) and bool(window.get("popups"))
            if states.contains(Atspi.StateType.ACTIVE) or popup: roots.append(child)
        if not roots:
            warnings.append("접근성이 활성 창을 식별하지 못해 OCR만 사용합니다")
            continue
        pending=[]
        for root in roots:
            try:
                rect=root.get_component_iface().get_extents(Atspi.CoordType.SCREEN)
                if min(rect.width,rect.height)<=0:continue
                native=window.get("popups",[]) if "menu" in root.get_role_name().lower() else [window]
                candidates=[b for w in native for b in (w.get("clientBox",w["box"]),w["box"])]
                if not candidates:continue
                target=min(candidates,key=lambda b:abs(b[2]/rect.width-b[3]/rect.height))
                sx,sy=target[2]/rect.width,target[3]/rect.height
                if not (.25<=sx<=4 and .25<=sy<=4 and abs(sx-sy)<.02):
                    warnings.append("접근성 창의 좌표 배율을 검증하지 못해 OCR만 사용합니다");continue
                transform=(target[0]-rect.x*sx,target[1]-rect.y*sy,sx,sy)
                pending.append((root,0,transform,target))
            except Exception: warnings.append("접근성 최상위 창의 좌표를 확인하지 못했습니다")
        count=0
        while pending and count<2500:
            node,depth,transform,visible_box=pending.pop();count+=1
            try:
                node.clear_cache_single()
                states=node.get_state_set()
                if states.contains(Atspi.StateType.DEFUNCT):continue
                visible=states.contains(Atspi.StateType.SHOWING) and states.contains(Atspi.StateType.VISIBLE)
                if visible:
                    role=node.get_role_name().lower(); mapped=roles.get(role)
                    if role=="text" and states.contains(Atspi.StateType.EDITABLE):mapped="AXTextField"
                    if mapped:
                        component=node.get_component_iface()
                        if component:
                            rect=component.get_extents(Atspi.CoordType.SCREEN)
                            ox,oy,sx,sy=transform
                            bounds=[ox+rect.x*sx,oy+rect.y*sy,rect.width*sx,rect.height*sy]
                            clipped=intersect(bounds,visible_box)
                            if min(clipped[2:])>0:
                                flags=[]
                                enabled=states.contains(Atspi.StateType.ENABLED)
                                for flag,label in ((Atspi.StateType.CHECKED,"켬"),(Atspi.StateType.SELECTED,"선택됨"),(Atspi.StateType.EXPANDED,"펼침")):
                                    if states.contains(flag):flags.append(label)
                                if not enabled:flags.append("비활성")
                                result.append({"id":str(window["pid"])+":"+node.path,"role":mapped,
                                    "name":node.get_name() or "", "box":clipped,"enabled":enabled,
                                    "state":" · ".join(flags),"focused":states.contains(Atspi.StateType.FOCUSED)})
                if depth<28 and visible:
                    for i in range(min(node.get_child_count(),500)):
                        child=node.get_child_at_index(i)
                        if child:pending.append((child,depth+1,transform,visible_box))
            except Exception as error:
                if not warnings:warnings.append("접근성 일부 읽기 실패: "+type(error).__name__)
        if pending:warnings.append("접근성 조회 한도에 도달해 일부 컨트롤이 빠졌습니다")
    return {"bindings":result,"warnings":warnings}


def response(window):
    try:
        return collect(window)
    except Exception as error:
        return {"bindings":[],"warnings":["접근성 사용 불가: "+str(error)]}


if __name__ == "__main__":
    import os
    if not os.environ.get("DESK_PRIVATE_ATSPI"):
        from accessibility_worker import runtime_environment
        os.execve(sys.executable,[sys.executable,*sys.argv],runtime_environment())
    import ctypes
    # Refuse an incomplete installation rather than silently querying through
    # the system library and reintroducing the connection leak.
    private=ctypes.CDLL(os.path.join(os.environ["DESK_PRIVATE_ATSPI"],"libatspi.so.0.0.1"),mode=ctypes.RTLD_GLOBAL)
    if ctypes.CDLL("libatspi.so.0").gtools_atspi_runtime_revision() != 1:
        raise RuntimeError("접근성 런타임 버전 불일치")
    if sys.argv[1:] == ["--serve"]:
        for line in sys.stdin:
            request=json.loads(line)
            print(json.dumps({"request_id":request["request_id"],**response(request["window"])},ensure_ascii=False),flush=True)
    else:
        print(json.dumps(response(json.loads(sys.argv[1])),ensure_ascii=False))
