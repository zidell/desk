// desk-helper: visible input, local OCR and the kill-switch overlay for the `desk` command.
// Built on demand by `desk` (swiftc -O -swift-version 5) into ~/.cache/desk/desk-helper.
// Everything it does is meant to be seen: the pointer glides to its target, text appears one
// character at a time, and the overlay shows the last two lines of desk's action log.
//
//   overlay                  bottom-center panel: the last two lines of the action log (newest below) and
//                            "중지" (click or ESC = kill switch). It never takes the keyboard: the user
//                            steps in by stopping, then asks again
//                            + the focus layer: where the AI looks, drawn over the screen ($STATE/focus)
//   click X Y [right|double] glide there, then click (screen points)
//   move X Y | drag X1 Y1 X2 Y2
//   scroll X Y up|down|left|right N
//   scrollpx X Y up|down|left|right POINTS   one smooth move of that many points (a page)
//   key COMBO                e.g. return, esc, tab, cmd+c, cmd+shift+t, down
//   type TEXT                pasted through the clipboard (works under the Korean input source); the
//                            clipboard is restored afterwards
//   typekeys TEXT            the same as key events carrying the characters (no clipboard)
//   ocr PNG OX OY POINTW [NEEDLE]  local text recognition: "x,y<TAB>text" lines in screen points
//                            (with NEEDLE: matching lines, x,y = the needle's own center)
//   size                     main display size in points ("W H")
//   front                    frontmost app name
//   frontpid                 frontmost app's pid
//   frontpath                frontmost app's bundle path
//   blockers                 what takes the input before the front window: "kind<TAB>name<TAB>x,y,w,h"
//   ocrb PNG OX OY POINTW    OCR lines as "x,y,w,h<TAB>text" boxes (screen points, top-left)
//   ax [SEC]                 front window's on-screen controls: "x,y,w,h<TAB>role<TAB>name<TAB>state"
//   window                   frontmost app's front window: "x,y,w,h<TAB>app · title"
//   grid IN OUT OX OY POINTW STEP SHRINK  screen-coordinate ruler drawn on a screenshot
//   hit X Y                  whether a click there could do anything: pressable|inert|unknown<TAB>role
//   wins                     front app, its window count and front window (changes when a click opens one)

import AppKit
import CoreGraphics
import Foundation
import Vision

let magic: Int64 = 0x6465_736B  // marks events we post, so the user-input watch (ESC, moves) ignores them
// Explicit, session-scoped markers of the local app under input testing. Only
// generated pointer responses are excluded; physical input and all ESC remain watched.
let generatedPointerMarkers = Set((ProcessInfo.processInfo.environment["DESK_GENERATED_POINTER_MARKERS"] ?? "").split(separator: ",").compactMap { Int64($0, radix: 16) })
let stateDir = ProcessInfo.processInfo.environment["DESK_STATE"] ?? (NSHomeDirectory() + "/.cache/desk")
func statePath(_ name: String) -> String { stateDir + "/" + name }

func die(_ message: String) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    exit(2)
}

let source = CGEventSource(stateID: .hidSystemState)

// The app desk acts on ($STATE/target-pid, written by desk for each action). Every event checks it is
// still the one in front: the user may switch apps in the middle of an action (a Return meant for
// Chrome once went to a KakaoTalk chat), and input must never leak into whatever came forward.
var lockedPid: pid_t?
var heldPointerButton: CGMouseButton?
var heldKeyboardRelease: CGEvent?
func releaseHeldKeyboard() {
    guard let event = heldKeyboardRelease else { return }
    heldKeyboardRelease = nil
    event.post(tap: .cghidEventTap)
    // Flush the release before a cancellation exits the short-lived helper.
    usleep(20_000)
}
func releaseHeldPointer() {
    guard let button = heldPointerButton else { return }
    let event = CGEvent(mouseEventSource: source, mouseType: button == .right ? .rightMouseUp : .leftMouseUp,
                        mouseCursorPosition: CGEvent(source: nil)?.location ?? .zero, mouseButton: button)
    event?.setIntegerValueField(.eventSourceUserData, value: magic)
    event?.post(tap: .cghidEventTap)
    heldPointerButton = nil
}
func lockTarget() {
    let saved = (try? String(contentsOfFile: statePath("target-pid"), encoding: .utf8))?
        .trimmingCharacters(in: .whitespacesAndNewlines)
    lockedPid = saved.flatMap { pid_t($0) } ?? targetApp()?.processIdentifier
}
func checkTarget() {
    guard let want = lockedPid else { return }
    for _ in 0..<10 {  // an app just brought forward by desk settles within a tick or two
        if NSWorkspace.shared.frontmostApplication?.processIdentifier == want { return }
        usleep(50_000)
    }
    let now = NSWorkspace.shared.frontmostApplication?.localizedName ?? "?"
    FileHandle.standardError.write("desk: 맨 앞 앱이 '\(now)'(으)로 바뀌어 그 자리에서 멈춤 — 사용자가 다른 앱을 띄웠을 수 있다 (종료 코드 6)\n".data(using: .utf8)!)
    releaseHeldPointer()
    releaseHeldKeyboard()
    exit(6)
}

func post(_ event: CGEvent?) {
    guard let event else { return }
    checkTarget()
    try? FileManager.default.removeItem(atPath: statePath("observation.json"))
    event.setIntegerValueField(.eventSourceUserData, value: magic)
    event.post(tap: .cghidEventTap)
}

func number(_ s: String) -> Double {
    if let v = Double(s) { return v }
    die("숫자가 아님: \(s)")
}

// MARK: mouse (visible: the pointer glides)

func pointer() -> CGPoint { CGEvent(source: nil)?.location ?? .zero }

func mouse(_ type: CGEventType, _ p: CGPoint, _ button: CGMouseButton, clickState: Int64 = 1) {
    if type == .leftMouseDown || type == .rightMouseDown { guardMouseDown(p) }
    let e = CGEvent(mouseEventSource: source, mouseType: type, mouseCursorPosition: p, mouseButton: button)
    e?.setIntegerValueField(.mouseEventClickState, value: clickState)
    if type == .leftMouseUp || type == .rightMouseUp {
        e?.setIntegerValueField(.eventSourceUserData, value: magic)
        e?.post(tap: .cghidEventTap); heldPointerButton = nil; return
    }
    if FileManager.default.fileExists(atPath: statePath("stopped")) { clickAbort("사용자 중지", code: 3) }
    post(e)
    if type == .leftMouseDown || type == .rightMouseDown { heldPointerButton = button }
}

// Constant speed: nearby targets take less time, distant targets take longer.
func glide(to target: CGPoint, dragging: Bool = false) {
    let start = pointer()
    let distance = hypot(target.x - start.x, target.y - start.y)
    if distance < 1 { return }
    let style = WidgetStyle()
    let duration = Double(distance / max(1, style.number("cursorPointsPerSecond")))
    let frame = Double(max(1, style.number("cursorFrameMilliseconds"))) / 1000
    let steps = max(1, Int(ceil(duration / frame)))
    let began = ProcessInfo.processInfo.systemUptime
    for i in 1...steps {
        let deadline = began + duration * Double(i) / Double(steps)
        while ProcessInfo.processInfo.systemUptime < deadline {
            checkTarget()
            if FileManager.default.fileExists(atPath: statePath("stopped")) { clickAbort("사용자 중지", code: 3) }
            usleep(2_000)
        }
        let t = min(1, (ProcessInfo.processInfo.systemUptime-began)/duration)
        let p = CGPoint(x: start.x + (target.x - start.x) * t, y: start.y + (target.y - start.y) * t)
        mouse(dragging ? .leftMouseDragged : .mouseMoved, p, .left)
        if t >= 1 { break }
    }
}

func clickEffect(_ point: CGPoint) {
    // guardedPress publishes after its final capture-hiding token is released.
    if clickHideToken != nil { return }
    let event = "\(UUID().uuidString)\t\(point.x)\t\(point.y)\t\(Date().timeIntervalSince1970)"
    try? event.write(toFile: statePath("click-effect"), atomically: true, encoding: .utf8)
}

func click(_ p: CGPoint, mode: String) {
    glide(to: p)
    switch mode {
    case "right":
        mouse(.rightMouseDown, p, .right)
        mouse(.rightMouseUp, p, .right)
    case "double":
        for n in 1...2 {
            mouse(.leftMouseDown, p, .left, clickState: Int64(n))
            mouse(.leftMouseUp, p, .left, clickState: Int64(n))
            usleep(40_000)
            if n == 1 { rebaseDouble(p) }
        }
    default:
        mouse(.leftMouseDown, p, .left)
        usleep(30_000)
        mouse(.leftMouseUp, p, .left)
    }
    clickEffect(p)
}

func drag(_ a: CGPoint, _ b: CGPoint) {
    glide(to: a)
    mouse(.leftMouseDown, a, .left)
    usleep(60_000)
    glide(to: b, dragging: true)
    mouse(.leftMouseUp, b, .left)
    clickEffect(b)
}

func scroll(_ p: CGPoint, _ direction: String, _ amount: Int32) {
    glide(to: p)
    var dy: Int32 = 0, dx: Int32 = 0
    switch direction {
    case "up": dy = 1
    case "down": dy = -1
    case "left": dx = 1
    case "right": dx = -1
    default: die("방향은 up|down|left|right")
    }
    for _ in 0..<max(amount, 1) {  // one line at a time, so the movement is visible
        post(CGEvent(scrollWheelEvent2Source: source, units: .line, wheelCount: 2, wheel1: dy, wheel2: dx, wheel3: 0))
        usleep(25_000)
    }
}

// A whole distance in points (a page: desk passes 85% of the window), in 10 quick steps so it is seen moving.
func scrollPoints(_ p: CGPoint, _ direction: String, _ points: Int32) {
    glide(to: p)
    let steps: Int32 = 10, step = max(points / steps, 1)
    for _ in 0..<steps {
        let d = direction == "up" || direction == "left" ? step : -step
        let vertical = direction == "up" || direction == "down"
        post(CGEvent(scrollWheelEvent2Source: source, units: .pixel, wheelCount: 2,
            wheel1: vertical ? d : 0, wheel2: vertical ? 0 : d, wheel3: 0))
        usleep(12_000)
    }
}

// MARK: keys

let keyCodes: [String: CGKeyCode] = [
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11, "q": 12,
    "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23,
    "=": 24, "9": 25, "7": 26, "-": 27, "8": 28, "0": 29, "]": 30, "o": 31, "u": 32, "[": 33, "i": 34,
    "p": 35, "l": 37, "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44, "n": 45, "m": 46,
    ".": 47, "`": 50,
    "return": 36, "enter": 36, "tab": 48, "space": 49, "delete": 51, "backspace": 51, "esc": 53,
    "escape": 53, "forwarddelete": 117, "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
    "left": 123, "right": 124, "down": 125, "up": 126,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98, "f8": 100, "f9": 101,
    "f10": 109, "f11": 103, "f12": 111,
]

func key(_ combo: String, holdMilliseconds: Int = 15) {
    guard (15...2000).contains(holdMilliseconds) else { die("hold 시간은 15~2000ms") }
    // Modifier-only input uses flagsChanged, with the left-device flag as well
    // as the public Command flag. Keep the same target/stop checks as other keys.
    let command = combo.lowercased()
    if ["cmd", "command", "cmd,cmd", "command,command"].contains(command) {
        if holdMilliseconds > 15 { die("길게 누르기는 수정키 없는 단일 키만 지원") }
        let count = command.contains(",") ? 2 : 1
        for tap in 0..<count {
            let down = CGEvent(keyboardEventSource: source, virtualKey: 55, keyDown: true)
            down?.type = .flagsChanged
            down?.flags = CGEventFlags(rawValue: CGEventFlags.maskCommand.rawValue | 0x8) // NX_DEVICELCMDKEYMASK
            post(down)
            usleep(30_000)
            // Always release, even if the front app changes during the tap.
            let up = CGEvent(keyboardEventSource: source, virtualKey: 55, keyDown: false)
            up?.type = .flagsChanged
            up?.flags = []
            up?.setIntegerValueField(.eventSourceUserData, value: magic)
            up?.post(tap: .cghidEventTap)
            if tap + 1 < count { usleep(70_000) }
        }
        return
    }
    var flags: CGEventFlags = []
    var code: CGKeyCode?
    for part in combo.lowercased().split(separator: "+").map(String.init) {
        switch part {
        case "cmd", "command": flags.insert(.maskCommand)
        case "shift": flags.insert(.maskShift)
        case "alt", "opt", "option": flags.insert(.maskAlternate)
        case "ctrl", "control": flags.insert(.maskControl)
        case "fn": flags.insert(.maskSecondaryFn)
        default:
            guard let c = keyCodes[part] else { die("모르는 키: \(part)") }
            code = c
        }
    }
    guard let code else { die("키가 없음: \(combo)") }
    if holdMilliseconds > 15 && !flags.isEmpty { die("길게 누르기는 수정키 없는 단일 키만 지원") }
    let down = CGEvent(keyboardEventSource: source, virtualKey: code, keyDown: true)
    down?.flags = flags
    let up = CGEvent(keyboardEventSource: source, virtualKey: code, keyDown: false)
    up?.flags = flags
    up?.setIntegerValueField(.eventSourceUserData, value: magic)
    heldKeyboardRelease = up
    let previousTerm = signal(SIGTERM) { _ in releaseHeldKeyboard(); exit(3) }
    defer { releaseHeldKeyboard(); signal(SIGTERM, previousTerm) }
    post(down)
    if holdMilliseconds > 15 {
        let initialInput = try? FileManager.default.attributesOfItem(atPath: statePath("user-input"))[.modificationDate] as? Date
        let deadline = ProcessInfo.processInfo.systemUptime + Double(holdMilliseconds) / 1000
        while ProcessInfo.processInfo.systemUptime < deadline {
            if FileManager.default.fileExists(atPath: statePath("stopped")) { releaseHeldKeyboard(); exit(3) }
            checkTarget()
            let currentInput = try? FileManager.default.attributesOfItem(atPath: statePath("user-input"))[.modificationDate] as? Date
            if currentInput != initialInput {
                releaseHeldKeyboard()
                FileHandle.standardError.write("desk: 홀드 중 사용자 입력 감지, 키를 놓고 취소 (종료 코드 6)\n".data(using: .utf8)!)
                exit(6)
            }
            RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.01))
        }
    } else { usleep(15_000) }
    // The release always goes out, like a mouse-up: a target check aborting between down and up
    // left the HID modifier state latched (⌘ stuck), so later clicks became ⌘-clicks (new tabs).
    releaseHeldKeyboard()
    if !flags.isEmpty, let clear = CGEvent(source: source) {
        clear.type = .flagsChanged
        clear.flags = []
        clear.setIntegerValueField(.eventSourceUserData, value: magic)
        clear.post(tap: .cghidEventTap)
    }
    usleep(15_000)
}

// Pasted, so the Korean input source can't turn letters into jamo. The clipboard is put back afterwards.
func type(_ text: String) {
    let board = NSPasteboard.general
    let saved: [NSPasteboardItem] = (board.pasteboardItems ?? []).map { item in
        let copy = NSPasteboardItem()
        for t in item.types {
            if let data = item.data(forType: t) { copy.setData(data, forType: t) }
        }
        return copy
    }
    // One paste for the whole text: an app reads the clipboard when it gets to the ⌘V, sometimes well
    // after it is posted, and a clipboard changed per character then pastes the wrong ones (seen in
    // Chrome's address bar: "naver.com" came out as "aer.commmm"). The restore waits for the same reason.
    board.clearContents()
    board.setString(text, forType: .string)
    key("cmd+v")
    usleep(600_000)
    board.clearContents()
    if !saved.isEmpty { board.writeObjects(saved) }
}

// Key events carrying the characters themselves (no clipboard): for windows that read the clipboard
// late or not at all (e.g. the system open panel, drawn by another process).
func typeKeys(_ text: String) {
    for ch in text {
        let units = Array(String(ch).utf16)
        for down in [true, false] {
            let e = CGEvent(keyboardEventSource: source, virtualKey: 0, keyDown: down)
            units.withUnsafeBufferPointer { e?.keyboardSetUnicodeString(stringLength: units.count, unicodeString: $0.baseAddress) }
            post(e)
        }
        usleep(35_000)
    }
}

// MARK: OCR (local, Vision)

// Prints "x,y<TAB>text" per line, x,y = the line's center in screen points (click target),
// top to bottom. The image covers `pointWidth` points starting at (originX, originY).
// With `needle`, only lines containing it, and x,y is the center of the needle itself (so a short
// label inside a long OCR line is clicked where it is, not at the line's middle).
func ocr(_ path: String, originX: Double, originY: Double, pointWidth: Double, needle: String? = nil,
         boxes: Bool = false) {
    guard let image = NSImage(contentsOfFile: path),
        let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil)
    else { die("이미지를 못 읽음: \(path)") }
    let scale = Double(cg.width) / pointWidth
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["ko-KR", "en-US"]
    request.usesLanguageCorrection = true
    do { try VNImageRequestHandler(cgImage: cg, options: [:]).perform([request]) } catch { die("OCR 실패: \(error)") }
    let w = Double(cg.width), h = Double(cg.height)
    // The desk panel's own text ("찾는 중: …") is not part of the screen being looked at.
    let panel = (try? String(contentsOfFile: statePath("panel-rect"), encoding: .utf8))
        .map { $0.split(separator: ",").compactMap { Double($0.trimmingCharacters(in: .whitespacesAndNewlines)) } }
    var lines: [(x: Int, y: Int, text: String)] = []
    var boxLines: [String] = []
    for obs in request.results ?? [] {
        guard let candidate = obs.topCandidates(1).first else { continue }
        let text = candidate.string
        var box = obs.boundingBox  // normalized, origin bottom-left
        if let needle {
            guard let range = text.range(of: needle) else { continue }
            if let sub = try? candidate.boundingBox(for: range)?.boundingBox { box = sub }
        }
        let x = originX + (box.midX * w) / scale
        let y = originY + ((1 - box.midY) * h) / scale
        if let r = panel, r.count == 4, x >= r[0], x <= r[0] + r[2], y >= r[1], y <= r[1] + r[3] { continue }
        lines.append((Int(x.rounded()), Int(y.rounded()), text))
        if boxes {
            let bx = originX + box.minX * w / scale, by = originY + (1 - box.maxY) * h / scale
            boxLines.append("\(Int(bx)),\(Int(by)),\(Int((box.width * w / scale).rounded())),\(Int((box.height * h / scale).rounded()))\t\(text)")
        }
    }
    // Same visual row (within 6 pt) reads left to right.
    if boxes { boxLines.forEach { print($0) }; return }
    lines.sort { abs($0.y - $1.y) <= 6 ? $0.x < $1.x : $0.y < $1.y }
    for l in lines { print("\(l.x),\(l.y)\t\(l.text)") }
}



// The app the user is working in. desk's own panel (this same executable, run as `overlay`) can be
// "frontmost" for a moment after it launches; then the owner of the topmost normal window counts.
func targetApp() -> NSRunningApplication? {
    let me = Bundle.main.executableURL?.resolvingSymlinksInPath().path
    let front = NSWorkspace.shared.frontmostApplication
    if let front, front.executableURL?.resolvingSymlinksInPath().path != me { return front }
    let list = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID)
        as? [[String: Any]] ?? []
    for w in list {
        guard (w[kCGWindowLayer as String] as? Int) == 0,
            let pid = w[kCGWindowOwnerPID as String] as? Int32,
            let app = NSRunningApplication(processIdentifier: pid),
            app.executableURL?.resolvingSymlinksInPath().path != me
        else { continue }
        return app
    }
    return front
}

// MARK: accessibility (what the front window shows: each on-screen control's kind, name and frame)

// Only elements inside the visible part of the front window are reported (a subtree whose frame
// lies outside it is skipped), and a text field's typed content is never read — the same things a
// person looking at the window sees, with exact frames and kinds.
let interactiveRoles: Set<String> = [
    "AXButton", "AXLink", "AXTextField", "AXTextArea", "AXSearchField", "AXCheckBox", "AXRadioButton",
    "AXPopUpButton", "AXMenuButton", "AXMenuItem", "AXComboBox", "AXSlider", "AXDisclosureTriangle",
    "AXIncrementor", "AXTab", "AXRow", "AXCell", "AXSwitch", "AXToggle", "AXColorWell", "AXDockItem",
]
// Not AXStaticText: Chromium marks every text run inside a clickable area as pressable (hundreds per
// page); the area itself is reported, and the text is OCR's job.
let maybePressable: Set<String> = ["AXGroup", "AXImage", "AXGenericElement", "AXHeading", "AXListItem"]

func axAttr(_ e: AXUIElement, _ name: String) -> AnyObject? {
    var v: CFTypeRef?
    return AXUIElementCopyAttributeValue(e, name as CFString, &v) == .success ? v : nil
}
func axString(_ e: AXUIElement, _ name: String) -> String {
    (axAttr(e, name) as? String)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
}
func axFrame(_ e: AXUIElement) -> CGRect? {
    guard let p = axAttr(e, kAXPositionAttribute), let s = axAttr(e, kAXSizeAttribute) else { return nil }
    var pt = CGPoint.zero, sz = CGSize.zero
    AXValueGetValue(p as! AXValue, .cgPoint, &pt)
    AXValueGetValue(s as! AXValue, .cgSize, &sz)
    return CGRect(origin: pt, size: sz)
}
func axChildren(_ e: AXUIElement) -> [AXUIElement] {
    (axAttr(e, kAXVisibleChildrenAttribute) as? [AXUIElement]) ?? (axAttr(e, kAXChildrenAttribute) as? [AXUIElement]) ?? []
}
func axPressable(_ e: AXUIElement) -> Bool {
    var names: CFArray?
    guard AXUIElementCopyActionNames(e, &names) == .success, let list = names as? [String] else { return false }
    return list.contains(kAXPressAction as String)
}
// Visible text inside an element without a name of its own (e.g. a clickable <div> with a label).
func axInnerText(_ e: AXUIElement, depth: Int = 0) -> String {
    if depth > 3 { return "" }
    var parts: [String] = []
    for c in axChildren(e).prefix(8) {
        let role = axString(c, kAXRoleAttribute)
        if role == "AXStaticText" {
            let t = axString(c, kAXValueAttribute)
            if !t.isEmpty { parts.append(t) }
        } else if !["AXTextField", "AXTextArea"].contains(role) {
            let t = axInnerText(c, depth: depth + 1)
            if !t.isEmpty { parts.append(t) }
        }
        if parts.joined(separator: " ").count > 60 { break }
    }
    return parts.joined(separator: " ")
}

// The state a person sees on a control (a checked box, the highlighted row, a grayed-out button),
// as its own field: "[켬]", "[끔]", "[선택됨]", "[비활성]".
func axState(_ e: AXUIElement, _ role: String) -> String {
    var marks: [String] = []
    switch role {
    case "AXCheckBox", "AXSwitch", "AXToggle":
        if let v = axAttr(e, kAXValueAttribute) as? NSNumber { marks.append(v.intValue == 0 ? "끔" : "켬") }
    case "AXRadioButton", "AXTab":
        if let v = axAttr(e, kAXValueAttribute) as? NSNumber, v.intValue != 0 { marks.append("선택됨") }
    case "AXRow", "AXCell", "AXMenuItem":
        if (axAttr(e, kAXSelectedAttribute) as? Bool) == true { marks.append("선택됨") }
    default: break
    }
    // Menus and disclosure buttons that are open (web pages: aria-expanded). Only "open": Chrome reports
    // nearly every element as not expanded (table cells, plain buttons), so "closed" tells nothing.
    if (axAttr(e, "AXExpanded") as? Bool) == true { marks.append("펼침") }
    if (axAttr(e, kAXEnabledAttribute) as? Bool) == false { marks.append("비활성") }
    return marks.isEmpty ? "" : "[" + marks.joined(separator: ", ") + "]"
}

// "x,y,w,h<TAB>role<TAB>name<TAB>state" per on-screen control of the front window.
func axDump(budget: Double) {
    guard AXIsProcessTrusted() else { die("손쉬운 사용 권한 없음") }
    guard let app = targetApp() else { return }
    let appEl = AXUIElementCreateApplication(app.processIdentifier)
    AXUIElementSetMessagingTimeout(appEl, 1.0)
    // Chromium and Electron build their web-content tree only when asked for.
    AXUIElementSetAttributeValue(appEl, "AXManualAccessibility" as CFString, kCFBooleanTrue)
    let bundle = (app.bundleIdentifier ?? "").lowercased()
    if ["chrome", "chromium", "edgemac", "brave", "vivaldi", "thebrowser", "opera"].contains(where: { bundle.contains($0) }) {
        AXUIElementSetAttributeValue(appEl, "AXEnhancedUserInterface" as CFString, kCFBooleanTrue)
    }
    guard let win = (axAttr(appEl, kAXFocusedWindowAttribute) ?? axAttr(appEl, kAXMainWindowAttribute)) else { return }
    let window = win as! AXUIElement
    guard var visible = axFrame(window) else { return }
    let screen = CGDisplayBounds(CGMainDisplayID())
    visible = visible.intersection(screen)
    let start = Date()
    var queue: [(AXUIElement, Int)] = [(window, 0)]
    var seen = 0
    var out: [String] = []
    // Menus and sheets open on top of the window belong to it too.
    while !queue.isEmpty, seen < 6000, Date().timeIntervalSince(start) < budget {
        let (e, depth) = queue.removeFirst()
        seen += 1
        let role = axString(e, kAXRoleAttribute)
        let frame = axFrame(e)
        if let f = frame, f.width > 0, f.height > 0, !f.intersects(visible), depth > 0 { continue }
        if let f = frame, f.width >= 4, f.height >= 4, depth > 0 {
            let interactive = interactiveRoles.contains(role) || (maybePressable.contains(role) && axPressable(e))
            if interactive {
                var name = axString(e, kAXTitleAttribute)
                if name.isEmpty { name = axString(e, kAXDescriptionAttribute) }
                if name.isEmpty, role == "AXStaticText" { name = axString(e, kAXValueAttribute) }
                if name.isEmpty, ["AXTextField", "AXTextArea", "AXSearchField", "AXComboBox"].contains(role) {
                    name = axString(e, "AXPlaceholderValue")
                }
                if name.isEmpty { name = axInnerText(e) }
                if name.isEmpty { name = axString(e, kAXHelpAttribute) }
                let v = f.intersection(visible)
                let clean = name.replacingOccurrences(of: "\t", with: " ").replacingOccurrences(of: "\n", with: " ")
                out.append("\(Int(v.minX)),\(Int(v.minY)),\(Int(v.width)),\(Int(v.height))\t\(role)\t\(String(clean.prefix(80)))\t\(axState(e, role))")
            }
        }
        for c in axChildren(e) { queue.append((c, depth + 1)) }
    }
    out.forEach { print($0) }
    if !queue.isEmpty { FileHandle.standardError.write("ax: 시간 안에 다 못 훑음 (\(seen)개 확인)\n".data(using: .utf8)!) }
}

// "role<TAB>subrole<TAB>x,y,w,h" of the element that has keyboard focus in the target app.
func focusedElement() -> String {
    guard AXIsProcessTrusted(), let app = targetApp() else { return "" }
    let appEl = AXUIElementCreateApplication(app.processIdentifier)
    AXUIElementSetMessagingTimeout(appEl, 1.0)
    guard let f = axAttr(appEl, kAXFocusedUIElementAttribute) else { return "" }
    let e = f as! AXUIElement
    let r = axFrame(e) ?? .zero
    return "\(axString(e, kAXRoleAttribute))\t\(axString(e, kAXSubroleAttribute))\t\(Int(r.minX)),\(Int(r.minY)),\(Int(r.width)),\(Int(r.height))"
}

// MARK: blockers (what in front takes the input before the window being worked in does)

// One line per thing that blocks: "kind<TAB>name<TAB>x,y,w,h". Kinds: 대화상자 (the app's own dialog or
// sheet), 다른 앱 창 (another process's window over this one: system password or permission prompts),
// 페이지 모달 (a dialog inside a web page). All of these are on screen, in front.
func blockers() {
    guard let app = targetApp() else { return }
    let pid = app.processIdentifier
    var out: [String] = []
    func rect(_ r: CGRect) -> String { "\(Int(r.minX)),\(Int(r.minY)),\(Int(r.width)),\(Int(r.height))" }

    // Another process's window above the app's front window and over it.
    let list = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID)
        as? [[String: Any]] ?? []
    func bounds(_ w: [String: Any]) -> CGRect? {
        guard let b = w[kCGWindowBounds as String] as? [String: Double] else { return nil }
        return CGRect(x: b["X"] ?? 0, y: b["Y"] ?? 0, width: b["Width"] ?? 0, height: b["Height"] ?? 0)
    }
    let passive: Set<String> = ["Window Server", "Dock", "Control Center", "SystemUIServer", "Spotlight",
        "Notification Center", "NotificationCenter", "TextInputMenuAgent", "TextInputSwitcher", "screencapture"]
    // desk's own panel and focus layer (the overlay process) are not in the way: clicks pass the
    // layer, and desk never presses where the panel is.
    let overlayPid = Int32((try? String(contentsOfFile: statePath("overlay.pid"), encoding: .utf8))?
        .trimmingCharacters(in: .whitespacesAndNewlines) ?? "") ?? -1
    // A floating window exactly the size of a whole display is a click-through overlay (Gikey's hint
    // layer, screen annotators), not a prompt: system prompts and dialogs are never display-sized.
    var ids = [CGDirectDisplayID](repeating: 0, count: 16)
    var count: UInt32 = 0
    CGGetActiveDisplayList(16, &ids, &count)
    let displays = ids.prefix(Int(count)).map { CGDisplayBounds($0) }
    func screenOverlay(_ r: CGRect, _ layer: Int) -> Bool {
        layer > 0 && displays.contains { abs($0.minX - r.minX) < 2 && abs($0.minY - r.minY) < 2
            && abs($0.width - r.width) < 2 && abs($0.height - r.height) < 2 }
    }
    if let front = list.firstIndex(where: { ($0[kCGWindowOwnerPID as String] as? Int32) == pid
            && ($0[kCGWindowLayer as String] as? Int) == 0 && (bounds($0)?.width ?? 0) > 80 }),
        let target = bounds(list[front])
    {
        for w in list[..<front] {
            let owner = w[kCGWindowOwnerName as String] as? String ?? ""
            let layer = w[kCGWindowLayer as String] as? Int ?? 0
            guard let opid = w[kCGWindowOwnerPID as String] as? Int32, opid != pid, opid != getpid(), opid != overlayPid,
                !passive.contains(owner), layer < 1000, (w[kCGWindowAlpha as String] as? Double ?? 1) > 0.1,
                let r = bounds(w), r.width > 60, r.height > 40, r.intersects(target), !screenOverlay(r, layer)
            else { continue }
            let title = w[kCGWindowName as String] as? String ?? ""
            out.append("다른 앱 창\t\(owner)\(title.isEmpty ? "" : " · " + title)\t\(rect(r))")
        }
    }

    guard AXIsProcessTrusted() else { out.forEach { print($0) }; return }
    let appEl = AXUIElementCreateApplication(pid)
    AXUIElementSetMessagingTimeout(appEl, 1.0)
    // The app's own dialogs and sheets.
    for w in (axAttr(appEl, kAXWindowsAttribute) as? [AXUIElement]) ?? [] {
        let sub = axString(w, kAXSubroleAttribute)
        if ["AXDialog", "AXSystemDialog"].contains(sub), let f = axFrame(w) {
            out.append("대화상자\t\(axString(w, kAXTitleAttribute))\t\(rect(f))")
        }
        for c in axChildren(w) where axString(c, kAXRoleAttribute) == "AXSheet" {
            if let f = axFrame(c) { out.append("대화상자\t(시트) \(axInnerText(c))\t\(rect(f))") }
        }
    }
    // Modals inside a web page (role=dialog / alertdialog), when on screen.
    if let win = axAttr(appEl, kAXFocusedWindowAttribute), var visible = axFrame(win as! AXUIElement) {
        visible = visible.intersection(CGDisplayBounds(CGMainDisplayID()))
        let start = Date()
        var queue: [AXUIElement] = [win as! AXUIElement]
        var seen = 0
        while !queue.isEmpty, seen < 4000, Date().timeIntervalSince(start) < 1.5 {
            let e = queue.removeFirst()
            seen += 1
            let sub = axString(e, kAXSubroleAttribute)
            if ["AXApplicationDialog", "AXApplicationAlertDialog"].contains(sub), let f = axFrame(e),
                f.width > 80, f.height > 40, f.intersects(visible)
            {
                var name = axString(e, kAXTitleAttribute)
                if name.isEmpty { name = axString(e, kAXDescriptionAttribute) }
                if name.isEmpty { name = axInnerText(e) }
                out.append("페이지 모달\t\(name.replacingOccurrences(of: "\t", with: " "))\t\(rect(f.intersection(visible)))")
                continue
            }
            if let f = axFrame(e), f.width > 0, f.height > 0, !f.intersects(visible) { continue }
            queue.append(contentsOf: axChildren(e))
        }
    }
    out.forEach { print($0) }
}

// MARK: front window (what the user sees in front: its frame and title bar text)

// "x,y,w,h<TAB>app · title" of the frontmost app's front window, in screen points; app only if none.
// What a click at a point would land on, asked of the front app itself (desk's own panel and focus
// layer are other windows): "pressable<TAB>role", "inert<TAB>role" (text, an image or a heading with
// nothing pressable around it: a click there does nothing), or "unknown<TAB>role" (apps that draw
// their own UI or web canvases tell nothing).
let pressRoles: Set<String> = ["AXButton", "AXLink", "AXCheckBox", "AXRadioButton", "AXPopUpButton",
    "AXMenuButton", "AXMenuItem", "AXMenuBarItem", "AXTab", "AXTextField", "AXTextArea", "AXSearchField",
    "AXComboBox", "AXCell", "AXRow", "AXSlider", "AXIncrementor", "AXDisclosureTriangle", "AXSwitch",
    "AXToggle", "AXDockItem", "AXColorWell", "AXSegmentedControl"]
func hit(_ p: CGPoint) -> String {
    guard AXIsProcessTrusted(), let app = targetApp() else { return "unknown\t" }
    let appEl = AXUIElementCreateApplication(app.processIdentifier)
    AXUIElementSetMessagingTimeout(appEl, 1.0)
    var found: AXUIElement?
    guard AXUIElementCopyElementAtPosition(appEl, Float(p.x), Float(p.y), &found) == .success, let first = found
    else { return "unknown\t" }
    let role = axString(first, kAXRoleAttribute)
    var cur: AXUIElement? = first
    for _ in 0..<5 {
        guard let c = cur else { break }
        let r = axString(c, kAXRoleAttribute)
        if pressRoles.contains(r) || axPressable(c) { return "pressable\t\(r)" }
        if r == "AXWindow" || r == "AXWebArea" || r == "AXApplication" { break }
        cur = axAttr(c, kAXParentAttribute).map { $0 as! AXUIElement }
    }
    return (["AXStaticText", "AXImage", "AXHeading"].contains(role) ? "inert\t" : "unknown\t") + role
}

// Which app is in front, its front window and how many windows it shows (menus and popovers
// included): a click that opens or closes one changes this even where the front window's pixels don't.
func windowsSignature() -> String {
    guard let app = targetApp() else { return "" }
    let list = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID)
        as? [[String: Any]] ?? []
    let count = list.filter { ($0[kCGWindowOwnerPID as String] as? Int32) == app.processIdentifier }.count
    return "\(app.processIdentifier) \(count) " + frontWindow()
}

@_silgen_name("_AXUIElementGetWindow")
func _AXUIElementGetWindow(_ element: AXUIElement, _ id: UnsafeMutablePointer<CGWindowID>) -> AXError

// "x,y,w,h<TAB>app · title<TAB>pid:windowID". The title is for people to read; which window it is
// comes from the frame and the window ID (frontWindowKey). Web apps rewrite their title on their own
// (Gmail flashes "… 메시지를 보냄" for an unread chat), and that is not another window.
func frontWindow() -> String {
    guard let app = targetApp() else { return "" }
    let name = app.localizedName ?? ""
    let pid = app.processIdentifier
    // The app's own main window when it says (Chrome's address-bar dropdown is a separate, untitled
    // window above it that comes and goes); otherwise the frontmost sizable window on screen.
    if AXIsProcessTrusted() {
        let appEl = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(appEl, 1.0)
        for attr in [kAXMainWindowAttribute, kAXFocusedWindowAttribute] {
            if let w = axAttr(appEl, attr), CFGetTypeID(w) == AXUIElementGetTypeID() {
                let win = w as! AXUIElement
                if let f = axFrame(win), f.width > 80, f.height > 80 {
                    let title = axString(win, kAXTitleAttribute)
                    var wid: CGWindowID = 0
                    _ = _AXUIElementGetWindow(win, &wid)
                    return "\(Int(f.minX)),\(Int(f.minY)),\(Int(f.width)),\(Int(f.height))\t\(title.isEmpty ? name : name + " · " + title)\t\(pid):\(wid)"
                }
            }
        }
    }
    let list = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID)
        as? [[String: Any]] ?? []
    for w in list {  // front to back
        guard (w[kCGWindowOwnerPID as String] as? Int32) == pid,
            (w[kCGWindowLayer as String] as? Int) == 0,
            let b = w[kCGWindowBounds as String] as? [String: Double],
            let x = b["X"], let y = b["Y"], let width = b["Width"], let height = b["Height"],
            width > 80, height > 80
        else { continue }
        let title = (w[kCGWindowName as String] as? String) ?? ""
        let wid = (w[kCGWindowNumber as String] as? Int) ?? 0
        return "\(Int(x)),\(Int(y)),\(Int(width)),\(Int(height))\t\(title.isEmpty ? name : name + " · " + title)\t\(pid):\(wid)"
    }
    return "\t\(name)\t\(pid):0"
}
// The window's identity without its title: frame and pid:windowID.
func windowKey(_ window: String) -> String {
    let f = window.components(separatedBy: "\t")
    return f.count >= 3 ? f[0] + "\t" + f[2] : window
}

// MARK: grid (screen-coordinate ruler drawn on a screenshot, so a point is read off the image)

// Draws faint lines every `step` points with their screen coordinates at the top and left edges.
// `shrink` is how much the image will be scaled down afterwards, so labels stay readable.
func grid(_ input: String, _ output: String, originX: Double, originY: Double, pointWidth: Double,
          step: Double, shrink: Double) {
    guard let image = NSImage(contentsOfFile: input),
        let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil)
    else { die("이미지를 못 읽음: \(input)") }
    let w = cg.width, h = cg.height
    let scale = Double(w) / pointWidth  // pixels per point
    guard let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: 0,
        space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)
    else { die("그리기 실패") }
    ctx.draw(cg, in: CGRect(x: 0, y: 0, width: w, height: h))
    let ns = NSGraphicsContext(cgContext: ctx, flipped: false)
    NSGraphicsContext.current = ns
    let px = 1 / shrink  // one pixel after shrinking
    let font = NSFont.monospacedDigitSystemFont(ofSize: 12 * px, weight: .semibold)
    let attrs: [NSAttributedString.Key: Any] = [.font: font, .foregroundColor: NSColor.white]
    let line = NSColor(calibratedRed: 1, green: 0.1, blue: 0.6, alpha: 0.35)
    func label(_ s: String, _ x: Double, _ yTop: Double) {
        let str = NSAttributedString(string: s, attributes: attrs)
        let size = str.size()
        let r = NSRect(x: x, y: Double(h) - yTop - size.height - 2 * px, width: size.width + 4 * px, height: size.height + 2 * px)
        NSColor(calibratedRed: 0.75, green: 0, blue: 0.4, alpha: 0.85).setFill()
        r.fill()
        str.draw(at: NSPoint(x: r.minX + 2 * px, y: r.minY + px))
    }
    var gx = (originX / step).rounded(.up) * step
    while gx < originX + pointWidth {
        let x = (gx - originX) * scale
        line.setFill()
        NSRect(x: x, y: 0, width: px, height: Double(h)).fill()
        label("\(Int(gx))", x + 2 * px, 0)
        gx += step
    }
    let pointHeight = Double(h) / scale
    var gy = (originY / step).rounded(.up) * step
    while gy < originY + pointHeight {
        let yTop = (gy - originY) * scale
        line.setFill()
        NSRect(x: 0, y: Double(h) - yTop - px, width: Double(w), height: px).fill()
        label("\(Int(gy))", 0, yTop + 2 * px)
        gy += step
    }
    NSGraphicsContext.current = nil
    guard let out = ctx.makeImage(),
        let data = NSBitmapImageRep(cgImage: out).representation(using: .png, properties: [:])
    else { die("저장 실패") }
    do { try data.write(to: URL(fileURLWithPath: output)) } catch { die("저장 실패: \(error)") }
}

// MARK: focus layer (where the AI is looking: what it read, what it is about to press)

// `desk` writes $STATE/focus ("mode<TAB>label<TAB>seconds", then "x,y,w,h<TAB>kind<TAB>text" lines in
// screen points, top-left origin) and this draws it over the screen, letting clicks through:
//   scan    what a look read, box by box from the top (A = blue: the app's own controls, O = yellow:
//           text, D = pink: found by the detector only), under a sweeping line; kind "frame" = the window
//   target  the focus frame sliding to what is about to be pressed (kind "target"), the other
//           candidates dashed (kind "cand")
//   region  the area looked at closely;  found  rings on found text (kind "point");  miss / note  a label
// Nothing of it may end up in a screenshot: desk puts a file in $STATE/hiding before each capture and
// waits until $STATE/focus-visible is gone; the drawing is then dropped, not shown again.
struct FocusItem {
    var rect: CGRect
    var kind: String
    var text: String
    var delay: Double = 0
}

func easeInOut(_ t: Double) -> Double {
    let t = min(max(t, 0), 1)
    return t < 0.5 ? 2 * t * t : 1 - pow(-2 * t + 2, 2) / 2
}

final class FocusView: NSView {
    override var isFlipped: Bool { true }
    var mode = ""
    var label = ""
    var items: [FocusItem] = []
    var frameRect: CGRect?
    var target: CGRect?
    var from: CGRect?
    var start = Date()
    var hold: Double = 2.5
    let fade: Double = 0.35
    let sweep: Double = 0.7
    let slide: Double = 0.28

    var elapsed: Double { Date().timeIntervalSince(start) }
    var done: Bool { mode.isEmpty || elapsed > hold + fade }

    func alpha(appearAt delay: Double = 0) -> CGFloat {
        let t = elapsed
        let fadeIn = min(max((t - delay) / 0.12, 0), 1)
        let fadeOut = min(max((hold + fade - t) / fade, 0), 1)
        return CGFloat(fadeIn * fadeOut)
    }

    static let style = WidgetStyle()
    static let blue = style.color("scanControl")
    static let yellow = style.color("scanText")
    static let pink = style.color("scanDetection")
    static let accent = style.color("target")
    static let red = style.color("miss")
    static let cyan = style.color("scanLine")

    override func draw(_ dirtyRect: NSRect) {
        guard !done else { return }
        let a = alpha()
        switch mode {
        case "scan":
            if let f = frameRect, elapsed < sweep + 0.25 {  // the reading line
                let y = f.minY + f.height * easeInOut(elapsed / sweep)
                let line = NSRect(x: f.minX, y: y - 1, width: f.width, height: 2)
                FocusView.cyan.withAlphaComponent(0.7 * CGFloat(min(1, (sweep + 0.25 - elapsed) / 0.25))).setFill()
                line.fill()
            }
            for item in items where item.kind != "frame" {
                let ia = alpha(appearAt: item.delay)
                if ia <= 0 { continue }
                let r = item.rect.insetBy(dx: -2, dy: -2)
                let path = NSBezierPath(roundedRect: r, xRadius: 4, yRadius: 4)
                switch item.kind {
                case "O":
                    FocusView.yellow.withAlphaComponent(0.22 * ia).setFill()
                    path.fill()
                case "A":
                    FocusView.blue.withAlphaComponent(0.8 * ia).setStroke()
                    path.lineWidth = 1.5
                    path.stroke()
                default:
                    FocusView.pink.withAlphaComponent(0.75 * ia).setStroke()
                    path.lineWidth = 1.5
                    path.stroke()
                }
            }
            if let f = frameRect, !label.isEmpty { pill(label, near: CGRect(x: f.midX - 1, y: f.minY + 8, width: 2, height: 0), color: FocusView.blue, alpha: a, below: true) }
        case "target":
            for item in items where item.kind == "cand" {
                let path = NSBezierPath(roundedRect: item.rect.insetBy(dx: -3, dy: -3), xRadius: 5, yRadius: 5)
                path.setLineDash([5, 4], count: 2, phase: 0)
                path.lineWidth = 1.5
                NSColor.white.withAlphaComponent(0.7 * a).setStroke()
                path.stroke()
            }
            guard let t = target else {  // several candidates, none chosen
                if let c = items.first(where: { $0.kind == "cand" }), !label.isEmpty {
                    pill(label, near: c.rect.insetBy(dx: -3, dy: -3), color: FocusView.accent, alpha: a, below: false)
                }
                break
            }
            let p = easeInOut(elapsed / slide)
            var r = t
            if let f = from {
                r = CGRect(x: f.minX + (t.minX - f.minX) * p, y: f.minY + (t.minY - f.minY) * p,
                    width: f.width + (t.width - f.width) * p, height: f.height + (t.height - f.height) * p)
            }
            r = r.insetBy(dx: -5, dy: -5)
            let pulse = elapsed > slide ? 0.75 + 0.25 * cos((elapsed - slide) * 6) : 1
            let path = NSBezierPath(roundedRect: r, xRadius: 7, yRadius: 7)
            path.lineWidth = 3
            FocusView.accent.withAlphaComponent(a * CGFloat(pulse)).setStroke()
            NSGraphicsContext.saveGraphicsState()
            let glow = NSShadow()
            glow.shadowColor = FocusView.accent.withAlphaComponent(0.9 * a)
            glow.shadowBlurRadius = 10
            glow.set()
            path.stroke()
            NSGraphicsContext.restoreGraphicsState()
            if p >= 1, !label.isEmpty { pill(label, near: r, color: FocusView.accent, alpha: a, below: false) }
        case "region":
            for item in items {
                let path = NSBezierPath(roundedRect: item.rect.insetBy(dx: 2, dy: 2), xRadius: 6, yRadius: 6)
                path.setLineDash([8, 5], count: 2, phase: CGFloat(elapsed * 30))
                path.lineWidth = 2.5
                FocusView.cyan.withAlphaComponent(a).setStroke()
                path.stroke()
                if !label.isEmpty { pill(label, near: item.rect.insetBy(dx: 2, dy: 2), color: FocusView.cyan, alpha: a, below: false) }
            }
        case "found":
            for item in items where item.kind == "point" {
                let c = CGPoint(x: item.rect.midX, y: item.rect.midY)
                let grow = CGFloat(easeInOut(min(elapsed / 0.4, 1)))
                let rad = 10 + 12 * grow
                let ring = NSBezierPath(ovalIn: CGRect(x: c.x - rad, y: c.y - rad, width: rad * 2, height: rad * 2))
                ring.lineWidth = 3
                FocusView.yellow.withAlphaComponent(a).setStroke()
                ring.stroke()
                FocusView.yellow.withAlphaComponent(0.18 * a).setFill()
                ring.fill()
            }
            if let first = items.first(where: { $0.kind == "point" }), !label.isEmpty {
                pill(label, near: first.rect.insetBy(dx: -22, dy: -22), color: FocusView.yellow, alpha: a, below: false)
            }
        case "ripple":
            let progress = min(1, elapsed / (Double(FocusView.style.number("clickRippleMilliseconds")) / 1000))
            guard progress < 1, let point = items.first else { break }
            let radius = CGFloat(5 + Double(FocusView.style.number("clickRippleRadius")) * progress)
            let ring = NSBezierPath(ovalIn: CGRect(x: point.rect.midX-radius, y: point.rect.midY-radius, width: radius*2, height: radius*2))
            FocusView.accent.withAlphaComponent(CGFloat(0.25*(1-progress))).setFill(); ring.fill()
            FocusView.accent.withAlphaComponent(CGFloat(1-progress)).setStroke(); ring.lineWidth=3; ring.stroke()
        default:  // miss, note
            let place = frameRect ?? bounds
            pill(label, near: CGRect(x: place.midX - 1, y: place.minY + 8, width: 2, height: 0),
                color: mode == "miss" ? FocusView.red : FocusView.blue, alpha: a, below: true)
        }
    }

    // A rounded label beside a rect: above it (below when there is no room, or when asked).
    func pill(_ text: String, near r: CGRect, color: NSColor, alpha a: CGFloat, below: Bool) {
        guard a > 0 else { return }
        let dark = color == FocusView.yellow || color == FocusView.cyan
        let attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 13, weight: .semibold),
            .foregroundColor: (dark ? NSColor.black : NSColor.white).withAlphaComponent(a),
        ]
        let str = NSAttributedString(string: text.count > 50 ? String(text.prefix(49)) + "…" : text, attributes: attrs)
        let size = str.size()
        let w = size.width + 16, h = size.height + 6
        var x = r.minX
        x = min(max(x, 4), bounds.width - w - 4)
        var y = below ? r.maxY + 6 : r.minY - h - 6
        if y < 4 { y = r.maxY + 6 }
        if y + h > bounds.height - 4 { y = r.minY - h - 6 }
        let box = NSRect(x: x, y: y, width: w, height: h)
        color.withAlphaComponent(0.92 * a).setFill()
        NSBezierPath(roundedRect: box, xRadius: h / 2, yRadius: h / 2).fill()
        str.draw(at: NSPoint(x: box.minX + 8, y: box.minY + 3))
    }
}

final class FocusLayer {
    var window: NSWindow!
    var view: FocusView!
    var timer: Timer?
    var lastStamp = ""
    var lastClick = ""
    var lastTarget: CGRect?
    var shown = 0  // counts show()s, so a late unlink from an earlier drop() leaves a newer mark alone

    func setUp() {
        let screen = NSScreen.screens.first!
        window = NSWindow(contentRect: screen.frame, styleMask: [.borderless], backing: .buffered, defer: false)
        window.level = NSWindow.Level(rawValue: NSWindow.Level.screenSaver.rawValue - 1)  // under the panel
        window.ignoresMouseEvents = true
        window.isOpaque = false
        window.backgroundColor = .clear
        window.hasShadow = false
        window.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
        view = FocusView(frame: NSRect(origin: .zero, size: screen.frame.size))
        window.contentView = view
        try? FileManager.default.createDirectory(atPath: statePath("hiding"), withIntermediateDirectories: true)
        unlink(statePath("focus-visible"))
        lastClick = readState("click-effect")
    }

    // A capture is waiting (stale tokens of a killed desk are cleared after 10 s).
    func hidingRequested() -> Bool {
        let dir = statePath("hiding"), fm = FileManager.default
        var any = false
        for name in (try? fm.contentsOfDirectory(atPath: dir)) ?? [] {
            let path = dir + "/" + name
            if let date = (try? fm.attributesOfItem(atPath: path))?[.modificationDate] as? Date,
                Date().timeIntervalSince(date) > 10
            {
                unlink(path)
            } else {
                any = true
            }
        }
        return any
    }

    // Called by the panel's 0.1 s tick: picks up a new $STATE/focus.
    func poll() {
        let click = readState("click-effect")
        if click != lastClick {
            lastClick = click
            let fields = click.components(separatedBy: "\t")
            if !hidingRequested(), fields.count == 4, let x = Double(fields[1]), let y = Double(fields[2]),
               let time = Double(fields[3]), Date().timeIntervalSince1970-time < 1 {
                let hold = Double(FocusView.style.number("clickRippleMilliseconds"))/1000
                load("ripple\t\t\(hold)\n\(x),\(y),0,0\tpoint\t\n")
                return
            }
        }
        let path = statePath("focus")
        guard let attrs = try? FileManager.default.attributesOfItem(atPath: path) else { return }
        let stamp = "\(attrs[.systemFileNumber] ?? 0)-\((attrs[.modificationDate] as? Date)?.timeIntervalSince1970 ?? 0)"
        if stamp == lastStamp { return }
        lastStamp = stamp
        guard let text = try? String(contentsOfFile: path, encoding: .utf8) else { return }
        load(text)
    }

    func load(_ text: String) {
        var lines = text.split(separator: "\n", omittingEmptySubsequences: true).map(String.init)
        guard !lines.isEmpty else { return }
        let head = lines.removeFirst().components(separatedBy: "\t")
        var items: [FocusItem] = []
        for line in lines {
            let f = line.components(separatedBy: "\t")
            let n = f[0].split(separator: ",").compactMap { Double($0) }
            guard n.count == 4 else { continue }
            items.append(FocusItem(rect: CGRect(x: n[0], y: n[1], width: n[2], height: n[3]),
                kind: f.count > 1 ? f[1] : "", text: f.count > 2 ? f[2] : ""))
        }
        // Text is read from the top down: each box appears as the sweeping line passes it.
        let shown = items.filter { $0.kind != "frame" }
        let top = shown.map(\.rect.minY).min() ?? 0, bottom = shown.map(\.rect.maxY).max() ?? 1
        for i in items.indices {
            items[i].delay = view.sweep * (items[i].rect.minY - top) / max(bottom - top, 1)
        }
        let mode = head[0]
        let primary = NSScreen.screens.first!
        var screen = primary
        if mode == "ripple", let point = items.first {
            let appKitPoint = NSPoint(x: point.rect.midX, y: primary.frame.maxY-point.rect.midY)
            screen = NSScreen.screens.first(where: { $0.frame.contains(appKitPoint) }) ?? primary
            for i in items.indices {
                items[i].rect = items[i].rect.offsetBy(dx: -screen.frame.minX, dy: -(primary.frame.maxY-screen.frame.maxY))
            }
        }
        window.setFrame(screen.frame, display: false)
        view.frame = NSRect(origin: .zero, size: screen.frame.size)
        view.from = nil
        if mode == "target" {
            let t = items.first(where: { $0.kind == "target" })?.rect
            // The frame slides from where the last one was, or from the pointer.
            if let last = lastTarget, view.mode == "target", !view.done {
                view.from = last
            } else {
                let p = pointer()
                view.from = CGRect(x: p.x - 30, y: p.y - 30, width: 60, height: 60)
            }
            view.target = t
            lastTarget = t
        }
        view.mode = mode
        view.label = head.count > 1 ? head[1] : ""
        view.hold = head.count > 2 ? (Double(head[2]) ?? 2.5) : 2.5
        view.items = items
        view.frameRect = items.first(where: { $0.kind == "frame" })?.rect
        view.start = Date()
        show()
    }

    func show() {
        // Visible first, then a last look for a capture: one of the two sides always sees the other.
        shown += 1
        try? "".write(toFile: statePath("focus-visible"), atomically: false, encoding: .utf8)
        if hidingRequested() { drop(); return }
        window.orderFrontRegardless()
        view.needsDisplay = true
        if timer == nil {
            timer = Timer.scheduledTimer(withTimeInterval: 1.0 / 60, repeats: true) { [weak self] _ in self?.frame() }
        }
    }

    func frame() {
        if hidingRequested() || view.done { drop(); return }
        view.needsDisplay = true
    }

    // Off the screen at once; the mark that keeps captures waiting goes a frame later, once the
    // window server has drawn the screen without it.
    func drop() {
        timer?.invalidate()
        timer = nil
        view.mode = ""
        lastTarget = nil
        window.orderOut(nil)
        let n = shown
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.04) { [weak self] in
            if self?.shown == n { unlink(statePath("focus-visible")) }
        }
    }
}

// MARK: overlay (what the AI is doing, a line to talk to it, and the kill switch)

final class ClickView: NSView {
    var onClick: (() -> Void)?
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    // The label inside took the clicks on its text, so "중지" did nothing where people click it.
    override func hitTest(_ point: NSPoint) -> NSView? { frame.contains(point) ? self : nil }
    override func mouseDown(with event: NSEvent) { onClick?() }
    override func resetCursorRects() { addCursorRect(bounds, cursor: .pointingHand) }
}

struct WidgetStyle {
    let values: [String: Any]
    init() {
        guard let file = ProcessInfo.processInfo.environment["DESK_WIDGET"],
            let data = try? Data(contentsOf: URL(fileURLWithPath: file)),
            let config = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        else { die("공통 widget.json 경로를 읽지 못했습니다. desk 실행기를 사용하세요.") }
        values = config
    }
    func number(_ key: String) -> CGFloat { CGFloat((values[key] as! NSNumber).doubleValue) }
    func text(_ key: String) -> String { values[key] as! String }
    func color(_ key: String) -> NSColor {
        let hex = UInt32(text(key).dropFirst(), radix: 16)!
        return NSColor(srgbRed: CGFloat((hex >> 16) & 255) / 255,
                       green: CGFloat((hex >> 8) & 255) / 255, blue: CGFloat(hex & 255) / 255, alpha: 1)
    }
}

final class Overlay: NSObject {
    static let shared = Overlay()
    var panel: NSPanel!
    var olderLabel: NSTextField!
    var newerLabel: NSTextField!
    var stopLabel: NSTextField!
    var tap: CFMachPort?
    var stopped = false
    let focus = FocusLayer()
    let idleLimit: TimeInterval = 600

    func label(_ size: CGFloat, _ weight: NSFont.Weight, _ color: NSColor, _ frame: NSRect) -> NSTextField {
        let l = NSTextField(labelWithString: "")
        l.font = .systemFont(ofSize: size, weight: weight)
        l.textColor = color
        l.lineBreakMode = .byTruncatingTail
        l.frame = frame
        return l
    }

    func run() {
        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)
        try? "\(getpid())".write(toFile: statePath("overlay.pid"), atomically: true, encoding: .utf8)
        atexit {
            for f in ["overlay.pid", "panel-rect", "focus-visible", "focus"] { unlink(statePath(f)) }
        }
        signal(SIGTERM) { _ in exit(0) }

        let style = WidgetStyle()
        let width = min(style.number("width"), NSScreen.screens.first!.visibleFrame.width - 24)
        let height = style.number("height")
        let screen = NSScreen.screens.first!.visibleFrame
        panel = NSPanel(
            contentRect: NSRect(x: screen.midX - width / 2, y: screen.minY + style.number("bottomMargin"), width: width, height: height),
            styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        panel.level = .screenSaver
        panel.isFloatingPanel = true
        panel.hidesOnDeactivate = false
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
        panel.backgroundColor = .clear
        panel.isOpaque = false
        panel.hasShadow = true
        let full = NSScreen.screens.first!.frame  // panel rect in screen points, top-left origin, for OCR
        let f = panel.frame
        try? "\(Int(f.minX)),\(Int(full.maxY - f.maxY)),\(Int(f.width)),\(Int(f.height))".write(
            toFile: statePath("panel-rect"), atomically: true, encoding: .utf8)

        let view = NSView(frame: NSRect(x: 0, y: 0, width: width, height: height))
        view.wantsLayer = true
        view.layer?.backgroundColor = style.color("background").withAlphaComponent(0.92).cgColor
        view.layer?.cornerRadius = style.number("cornerRadius")
        // A red edge, the stop button's red: the panel reads as "AI in control" at a glance.
        view.layer?.borderWidth = style.number("borderWidth")
        view.layer?.borderColor = style.color("accent").cgColor

        let textW = width - 16 - 136
        // A log, not a status: each new line comes in below and pushes the one before up.
        olderLabel = label(style.number("olderFontSize"), .regular, style.color("muted"), NSRect(x: 16, y: 36, width: textW, height: 16))
        newerLabel = label(style.number("newerFontSize"), .semibold, style.color("foreground"), NSRect(x: 16, y: 12, width: textW, height: 20))
        view.addSubview(olderLabel)
        view.addSubview(newerLabel)

        let stop = ClickView(frame: NSRect(x: width - style.number("stopWidth") - style.number("stopInset"), y: style.number("stopTop"), width: style.number("stopWidth"), height: height - 2 * style.number("stopTop")))
        stop.wantsLayer = true
        stop.layer?.backgroundColor = style.color("accent").cgColor
        stop.layer?.cornerRadius = style.number("stopRadius")
        stop.onClick = { [weak self] in self?.stop(reason: "중지 버튼") }
        stopLabel = NSTextField(wrappingLabelWithString: "")
        stopLabel.font = .systemFont(ofSize: style.number("stopFontSize"), weight: .semibold)
        stopLabel.textColor = style.color("foreground")
        stopLabel.alignment = .center
        stopLabel.frame = NSRect(x: 4, y: (stop.frame.height - 20) / 2, width: stop.frame.width - 8, height: 20)
        stop.addSubview(stopLabel)
        view.addSubview(stop)
        panel.contentView = view

        focus.setUp()
        installTap()
        stopLabel.stringValue = tap == nil ? "중지(클릭)" : style.text("stopLabel")
        panel.orderFrontRegardless()

        tick()
        Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { [weak self] _ in self?.tick() }
        app.run()
    }

    // MARK: kill switch

    // The user's own input (no `magic` mark): ESC once stops; any key, pointer move, click or scroll is
    // noted in $STATE/user-input, and desk holds its next action until the user has been still 5 s.
    var pointerAnchor = CGEvent(source: nil)?.location ?? .zero
    let pointerThreshold = WidgetStyle().number("userPointerThresholdPixels")
    var lastInputMark: TimeInterval = 0
    func userInput() {
        let now = Date().timeIntervalSince1970
        lastInputMark = now
        try? UUID().uuidString.write(toFile: statePath("user-input"), atomically: true, encoding: .utf8)
    }

    func installTap() {
        let types: [CGEventType] = [.keyDown, .keyUp, .mouseMoved, .leftMouseDown, .rightMouseDown, .otherMouseDown,
                                    .leftMouseDragged, .rightMouseDragged, .scrollWheel]
        let mask = types.reduce(CGEventMask(0)) { $0 | (1 << $1.rawValue) }
        let callback: CGEventTapCallBack = { _, type, event, _ in
            let me = Overlay.shared
            if type == .tapDisabledByTimeout || type == .tapDisabledByUserInput {
                if let t = me.tap { CGEvent.tapEnable(tap: t, enable: true) }
            } else if event.getIntegerValueField(.eventSourceUserData) == magic {
                me.pointerAnchor = event.location
            } else if [.mouseMoved, .leftMouseDown, .rightMouseDown, .otherMouseDown, .leftMouseDragged, .rightMouseDragged, .scrollWheel].contains(type) && generatedPointerMarkers.contains(event.getIntegerValueField(.eventSourceUserData)) {
                me.pointerAnchor = event.location
            } else if type == .mouseMoved {
                let p = event.location
                let dx = p.x - me.pointerAnchor.x, dy = p.y - me.pointerAnchor.y
                if dx * dx + dy * dy >= me.pointerThreshold * me.pointerThreshold {
                    me.pointerAnchor = p
                    me.userInput()
                }
            } else {
                if (type == .keyDown || type == .keyUp), event.getIntegerValueField(.keyboardEventKeycode) == 53 {
                    if type == .keyDown, event.getIntegerValueField(.keyboardEventAutorepeat) == 0 { me.escPressed() }
                    return nil  // Stop is consumed; it must not also cancel something in the target app.
                }
                me.pointerAnchor = event.location
                me.userInput()
            }
            return Unmanaged.passUnretained(event)
        }
        tap = CGEvent.tapCreate(
            tap: .cgSessionEventTap, place: .headInsertEventTap, options: .defaultTap,
            eventsOfInterest: mask, callback: callback, userInfo: nil)
        guard let tap else {
            try? "".write(toFile: statePath("esc-unavailable"), atomically: true, encoding: .utf8)
            return
        }
        unlink(statePath("esc-unavailable"))
        let src = CFMachPortCreateRunLoopSource(nil, tap, 0)
        CFRunLoopAddSource(CFRunLoopGetMain(), src, .commonModes)
        CGEvent.tapEnable(tap: tap, enable: true)
    }

    func escPressed() {
        stop(reason: "ESC")
    }

    // MARK: status

    // The session's last N log lines (since "── begin"), without the date: "2026-10-13 12:38:05 ".
    func recentLog(_ n: Int) -> [String] {
        guard let h = FileHandle(forReadingAtPath: statePath("actions.log")) else { return [] }
        defer { try? h.close() }
        let size = h.seekToEndOfFile()
        h.seek(toFileOffset: size > 8192 ? size - 8192 : 0)
        var lines = String(decoding: h.readDataToEndOfFile(), as: UTF8.self).split(separator: "\n").map(String.init)
        if let i = lines.lastIndex(where: { $0.contains("── begin") }) { lines = Array(lines[i...]) }
        var shown: [String] = []
        for line in lines {
            var t = line.count > 20 ? String(line.dropFirst(20)) : line
            if t.hasPrefix("설명: ") { t = "AI ▸ " + t.dropFirst(4) }
            if t.hasPrefix("── begin") { t = "▶ 시작" + (t.count > 9 ? ": " + t.dropFirst(9) : "") }
            // The same line again ("화면 보는 중 · Google Chrome" twice) does not push the one before out.
            if shown.last != t { shown.append(t) }
        }
        return Array(shown.suffix(n))
    }

    func tick() {
        if stopped { return }
        let fm = FileManager.default
        if fm.fileExists(atPath: statePath("stopped")) { showStopped(); return }

        focus.poll()
        let lines = recentLog(2)
        olderLabel.stringValue = lines.count > 1 ? lines[0] : ""
        newerLabel.stringValue = lines.last ?? ""

        if let attrs = try? fm.attributesOfItem(atPath: statePath("last")),
            let date = attrs[.modificationDate] as? Date,
            Date().timeIntervalSince(date) > idleLimit
        {
            exit(0)
        }
    }

    func stop(reason: String) {
        if stopped { return }
        try? "\(reason) \(Date())".write(toFile: statePath("stopped"), atomically: true, encoding: .utf8)
        let running = statePath("running")
        for name in (try? FileManager.default.contentsOfDirectory(atPath: running)) ?? [] {
            if let pid = Int32(name) { kill(pid, SIGTERM) }
        }
        showStopped()
    }

    func showStopped() {
        stopped = true
        focus.drop()
        olderLabel.stringValue = recentLog(1).first ?? ""
        newerLabel.stringValue = "⏹ 중지됨 · AI 제어를 막았습니다"
        stopLabel.stringValue = "중지됨"
        DispatchQueue.main.asyncAfter(deadline: .now() + 2.5) { exit(0) }
    }
}

// A press is one native transaction: retain the AX object through preview and mouse-down.
// Screen pixels remain mandatory for canvas/unknown controls; no AX action is invoked.
var clickPlan: [String: Any]?
var clickElement: AXUIElement?
var clickBinding: [String: Any]?
var clickHideToken: String?

func readState(_ name: String) -> String {
    (try? String(contentsOfFile: statePath(name), encoding: .utf8)) ?? ""
}
func clickAbort(_ message: String, code: Int32 = 6) -> Never {
    releaseHeldPointer()
    if let token = clickHideToken { unlink(token) }
    FileHandle.standardError.write(("desk: " + message + "\n").data(using: .utf8)!)
    exit(code)
}
func pointElement(_ p: CGPoint) -> AXUIElement? {
    guard AXIsProcessTrusted(), let app = targetApp() else { return nil }
    let root = AXUIElementCreateApplication(app.processIdentifier)
    AXUIElementSetMessagingTimeout(root, 0.3)
    var found: AXUIElement?
    guard AXUIElementCopyElementAtPosition(root, Float(p.x), Float(p.y), &found) == .success else { return nil }
    var current = found
    for _ in 0..<10 {
        guard let element = current else { break }
        let role = axString(element, kAXRoleAttribute)
        if (pressRoles.contains(role) || axPressable(element)), let frame = axFrame(element), frame.contains(p) { return element }
        if ["AXWindow", "AXWebArea", "AXApplication"].contains(role) { break }
        current = axAttr(element, kAXParentAttribute).map { $0 as! AXUIElement }
    }
    return nil
}
func bindingOf(_ element: AXUIElement?) -> [String: Any]? {
    guard let element = element, let frame = axFrame(element) else { return nil }
    var name = axString(element, kAXTitleAttribute)
    if name.isEmpty { name = axString(element, kAXDescriptionAttribute) }
    var result: [String: Any] = ["id": axString(element, "AXIdentifier"), "role": axString(element, kAXRoleAttribute),
            "name": name, "box": [Int(frame.minX), Int(frame.minY), Int(frame.width), Int(frame.height)],
            "enabled": (axAttr(element, kAXEnabledAttribute) as? NSNumber)?.boolValue ?? true]
    if let caret = caretFrame(element) { result["caret"] = [Int(caret.minX), Int(caret.minY), Int(ceil(caret.width)), Int(ceil(caret.height))] }
    return result
}
func sameBinding(_ a: [String: Any]?, _ b: [String: Any]?) -> Bool {
    if a == nil || b == nil { return a == nil && b == nil }
    var left = a!, right = b!
    left.removeValue(forKey: "caret"); right.removeValue(forKey: "caret")
    return NSDictionary(dictionary: left).isEqual(to: right)
}
func caretFrame(_ element: AXUIElement) -> CGRect? {
    guard ["AXTextField", "AXTextArea", "AXSearchField", "AXComboBox"].contains(axString(element, kAXRoleAttribute)),
          let selected = axAttr(element, kAXSelectedTextRangeAttribute), CFGetTypeID(selected) == AXValueGetTypeID() else { return nil }
    var range = CFRange(location: 0, length: 0)
    guard AXValueGetValue(selected as! AXValue, .cfRange, &range), range.length == 0 else { return nil }
    var value: CFTypeRef?
    guard AXUIElementCopyParameterizedAttributeValue(element, kAXBoundsForRangeParameterizedAttribute as CFString, selected, &value) == .success,
          let value = value, CFGetTypeID(value) == AXValueGetTypeID() else { return nil }
    var rect = CGRect.zero
    guard AXValueGetValue(value as! AXValue, .cgRect, &rect), let bounds = axFrame(element), bounds.contains(rect) else { return nil }
    return rect
}
func writeClickJSON(_ value: Any, _ name: String) {
    guard let data = try? JSONSerialization.data(withJSONObject: value, options: [.fragmentsAllowed]) else { clickAbort("클릭 근거 직렬화 실패") }
    do { try data.write(to: URL(fileURLWithPath: statePath(name)), options: .atomic) }
    catch { clickAbort("클릭 근거 저장 실패") }
}
func holdClick(_ p: CGPoint) {
    guard let plan = clickPlan else { clickAbort("클릭 근거 없음. desk 실행기를 사용하세요.") }
    if FileManager.default.fileExists(atPath: statePath("stopped")) { clickAbort("사용자 중지", code: 3) }
    if readState("user-input") != (plan["inputEpoch"] as? String ?? "") { clickAbort("클릭 준비 중 사용자 개입") }
    checkTarget()
    if windowKey(frontWindow()) != windowKey(plan["window"] as? String ?? "") { clickAbort("클릭 준비 중 창 변경") }
    let current = pointElement(p)
    // Chrome's web AX hit test is unstable at one point: it flips between the real button and
    // the page-wide AXGroup (or nothing) as the pointer arrives. Elements on one ancestor chain
    // at the same point are the same target at different depths, not a reflow. Keep the
    // narrowest one, and check that element's own identity/box/enabled state directly.
    if let current = current, let binding = bindingOf(current), (binding["enabled"] as? Bool) == true,
       let frame = axFrame(current), frame.contains(p),
       clickElement == nil || (!CFEqual(clickElement!, current) && isDescendant(current, of: clickElement!)) {
        clickElement = current; clickBinding = binding
        return
    }
    if let retained = clickElement, current == nil || (!CFEqual(retained, current!) && isDescendant(retained, of: current!)) {
        guard sameBinding(clickBinding, bindingOf(retained)), (clickBinding?["enabled"] as? Bool) == true,
              let frame = axFrame(retained), frame.contains(p) else { clickAbort("클릭 대상의 정체·위치 변경. 다시 읽으세요.") }
        return
    }
    if let retained = clickElement {
        guard let current = current, CFEqual(retained, current), sameBinding(clickBinding, bindingOf(current)),
              (bindingOf(current)?["enabled"] as? Bool) == true else { clickAbort("클릭 대상의 정체·위치 변경. 다시 읽으세요.") }
    } else if current != nil { clickAbort("클릭 대상이 교체됨") }
}
func isDescendant(_ element: AXUIElement, of ancestor: AXUIElement) -> Bool {
    var current = axAttr(element, kAXParentAttribute).map { $0 as! AXUIElement }
    for _ in 0..<40 {
        guard let node = current else { return false }
        if CFEqual(node, ancestor) { return true }
        current = axAttr(node, kAXParentAttribute).map { $0 as! AXUIElement }
    }
    return false
}
func hideClickFocus() {
    let folder = statePath("hiding")
    try? FileManager.default.createDirectory(atPath: folder, withIntermediateDirectories: true)
    let token = folder + "/click-" + UUID().uuidString
    try? Data().write(to: URL(fileURLWithPath: token))
    clickHideToken = token
    for _ in 0..<30 {
        if !FileManager.default.fileExists(atPath: statePath("focus-visible")) { return }
        usleep(20_000)
    }
    clickAbort("표시 레이어를 숨기지 못해 클릭 취소")
}
func showClickFocus() {
    if let token = clickHideToken { unlink(token) }
    clickHideToken = nil
}
func clickCapture(_ file: String, _ box: [Int]) {
    let task = Process()
    task.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
    task.arguments = ["-x", "-R", box.map(String.init).joined(separator: ","), file]
    do { try task.run(); task.waitUntilExit() } catch { clickAbort("클릭 전 화면 캡처 실패") }
    if task.terminationStatus != 0 { clickAbort("클릭 전 화면 캡처 실패") }
}
func clickPixels(_ file: String) -> Data? {
    guard let data = try? Data(contentsOf: URL(fileURLWithPath: file)),
          let rep = NSBitmapImageRep(data: data), let image = rep.cgImage else { return nil }
    let w = image.width, h = image.height
    var bytes = Data(count: w * h * 4)
    let drawn: Bool = bytes.withUnsafeMutableBytes { raw in
        guard let context = CGContext(data: raw.baseAddress, width: w, height: h, bitsPerComponent: 8,
            bytesPerRow: w * 4, space: CGColorSpace(name: CGColorSpace.sRGB)!,
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return false }
        context.draw(image, in: CGRect(x: 0, y: 0, width: w, height: h)); return true
    }
    return drawn ? bytes : nil
}
func equalClickPixels(_ a: String, _ b: String, _ box: [Int]) -> Bool {
    guard var left = clickPixels(a), var right = clickPixels(b), left.count == right.count else { return false }
    if left == right { return true }
    // Use the accessibility API's actual insertion caret, never a guessed text-field mask.
    guard let element = clickElement, let caret = caretFrame(element),
          let data = try? Data(contentsOf: URL(fileURLWithPath: b)),
          let image = NSBitmapImageRep(data: data) else { return false }
    let sx = Double(image.pixelsWide) / Double(box[2]), sy = Double(image.pixelsHigh) / Double(box[3])
    let x0 = max(0, Int(floor((caret.minX - Double(box[0])) * sx)))
    let x1 = min(image.pixelsWide, Int(ceil((caret.maxX - Double(box[0])) * sx)))
    let y0 = max(0, Int(floor((caret.minY - Double(box[1])) * sy)))
    let y1 = min(image.pixelsHigh, Int(ceil((caret.maxY - Double(box[1])) * sy)))
    if x0 >= x1 || y0 >= y1 { return false }
    // CGContext pixel rows have a bottom-left origin.
    for y in y0..<y1 { for x in x0..<x1 {
        let i = ((image.pixelsHigh - 1 - y) * image.pixelsWide + x) * 4
        for channel in 0..<4 { left[i + channel] = 0; right[i + channel] = 0 }
    } }
    return left == right
}
func guardMouseDown(_ p: CGPoint) {
    holdClick(p)
    guard let plan = clickPlan, let point = plan["point"] as? [Int], point == [Int(p.x), Int(p.y)],
          plan["box"] is [Int] else { clickAbort("클릭 최종 근거 없음") }
    // Pixels are not compared under the pointer: hover tint, underline and tooltips
    // legitimately repaint the target there. Reflow is caught before movement
    // (guardedPress) and by the retained identity/window/user-input checks (holdClick).
}
func rebaseDouble(_ p: CGPoint) {
    holdClick(p)
}
func guardedPress(_ p: CGPoint, _ mode: String, _ delay: Double, _ label: String, _ end: CGPoint?) {
    guard let data = try? Data(contentsOf: URL(fileURLWithPath: statePath("click-plan.json"))),
          let plan = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
          let box = plan["box"] as? [Int], let original = plan["pixels"] as? String else { clickAbort("클릭 계획 없음") }
    clickPlan = plan
    clickElement = pointElement(p); clickBinding = bindingOf(clickElement)
    // Rebind the point target before movement. The unchanged pixel patch below
    // validates the public observation; holdClick retains this AX identity
    // throughout movement and mouse-down, without comparing unlike AX nodes.
    holdClick(p)
    hideClickFocus()
    let initial = statePath("click-native-initial.png")
    clickCapture(initial, box)
    guard equalClickPixels(original, initial, box) else { clickAbort("읽은 뒤 화면 변경. 클릭 취소") }
    showClickFocus()
    // No pixel check once the pointer starts moving: hover feedback anywhere along the
    // path (row tint, underline, tooltip) is not a reflow. The unchanged pre-move patch
    // above and holdClick's retained identity/window/user-input checks guard the press.
    glide(to: p)
    hideClickFocus()
    defer { showClickFocus(); clickEffect(end ?? p) }
    holdClick(p)
    try? windowsSignature().write(toFile: statePath("pre-sig"), atomically: true, encoding: .utf8)
    let region = frontWindow().components(separatedBy: "\t")[0].split(separator: ",").compactMap { Int($0) }
    if region.count == 4 { clickCapture(statePath("pre.png"), region) }
    if let end = end { drag(p, end) } else { click(p, mode: mode) }
}

// MARK: main

var args = Array(CommandLine.arguments.dropFirst())
guard let command = args.first else { die("usage: desk-helper overlay|click|move|drag|scroll|key|type|ocr|size|front") }
args.removeFirst()
func arg(_ i: Int) -> String {
    if i < args.count { return args[i] }
    die("인수가 모자람: \(command)")
}

switch command {
case "click-bind":
    let binding = bindingOf(pointElement(CGPoint(x: number(arg(0)), y: number(arg(1)))))
    let result: Any = binding.map { $0 as Any } ?? NSNull()
    writeClickJSON(result, "click-binding-output.json")
    print(readState("click-binding-output.json"))
case "guarded-press":
    lockTarget()
    let p = CGPoint(x: number(arg(0)), y: number(arg(1)))
    let end = arg(2) == "drag" ? CGPoint(x: number(arg(5)), y: number(arg(6))) : nil
    guardedPress(p, arg(2), number(arg(3)), arg(4), end)
case "overlay":
    Overlay.shared.run()
case "click", "move", "drag", "scroll", "scrollpx", "key", "type", "typekeys":
    lockTarget()
    switch command {
    case "click":
        click(CGPoint(x: number(arg(0)), y: number(arg(1))), mode: args.count > 2 ? args[2] : "left")
    case "move":
        glide(to: CGPoint(x: number(arg(0)), y: number(arg(1))))
    case "drag":
        drag(CGPoint(x: number(arg(0)), y: number(arg(1))), CGPoint(x: number(arg(2)), y: number(arg(3))))
    case "scrollpx":
        scrollPoints(CGPoint(x: number(arg(0)), y: number(arg(1))), arg(2), Int32(number(arg(3))))
    case "scroll":
        scroll(CGPoint(x: number(arg(0)), y: number(arg(1))), arg(2), Int32(args.count > 3 ? number(args[3]) : 5))
    case "key":
        key(arg(0), holdMilliseconds: args.count > 1 ? Int(number(args[1])) : 15)
    case "type":
        type(arg(0))
    case "typekeys":
        typeKeys(arg(0))
    default:
        break
    }
case "ocr":
    ocr(arg(0), originX: number(arg(1)), originY: number(arg(2)), pointWidth: number(arg(3)),
        needle: args.count > 4 ? args[4] : nil)
case "window":
    print(frontWindow())
case "focused":
    print(focusedElement())
case "ocrb":
    ocr(arg(0), originX: number(arg(1)), originY: number(arg(2)), pointWidth: number(arg(3)), boxes: true)
case "ax":
    axDump(budget: args.isEmpty ? 4 : number(args[0]))
case "grid":
    grid(arg(0), arg(1), originX: number(arg(2)), originY: number(arg(3)), pointWidth: number(arg(4)),
         step: number(arg(5)), shrink: number(arg(6)))
case "size":
    let b = CGDisplayBounds(CGMainDisplayID())
    print("\(Int(b.width)) \(Int(b.height))")
case "front":
    print(targetApp()?.localizedName ?? "")
case "hit":
    print(hit(CGPoint(x: number(arg(0)), y: number(arg(1)))))
case "wins":
    print(windowsSignature())
case "blockers":
    blockers()
case "frontpath":
    print(targetApp()?.bundleURL?.path ?? "")
case "frontpid":
    print(targetApp()?.processIdentifier ?? 0)
case "permissions":
    // TCC attributes this helper to the terminal app that launched it, so this is the terminal's grant.
    // --request shows the system prompts once; macOS does not show them again after a denial.
    if args.first == "--request" {
        _ = AXIsProcessTrustedWithOptions([kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary)
        _ = CGRequestScreenCaptureAccess()
    }
    print("accessibility\t\(AXIsProcessTrusted() ? "granted" : "missing")")
    print("screen-recording\t\(CGPreflightScreenCaptureAccess() ? "granted" : "missing")")
default:
    die("모르는 명령: \(command)")
}
