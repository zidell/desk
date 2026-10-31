# Linux 실행 환경

`desk.py`가 Linux 공통 명령·세션 계층(`linux/desk_linux.py`)을 선택하고,
`XDG_SESSION_TYPE`에 따라 `linux/wayland.py` 또는 `linux/x11.py`를 고른다.
공통 화면 인식 결합과 클릭 근거는 기존 `desk-see.py`·`desk-click.py`를 그대로 사용한다.
macOS Bash/Swift와 Windows Python/C# 실행 경로는 Linux 백엔드에 의존하지 않는다.

## 지원 범위

| 환경 | 화면 | 실제 입력·안전 검사 | 패널 |
|---|---|---|---|
| KDE Wayland | KDE Spectacle의 보이는 현재 모니터 캡처 | 세션 동안만 로드되는 KWin 어댑터 | LayerShellQt, 키보드 포커스 없음 |
| X11 | 보이는 루트 화면 | XTest + XI2 물리 입력 감지 + EWMH 창 검사 | Qt 도구 창 |
| GNOME·그 밖의 Wayland | 아직 통합 지원하지 않음 | 안전 어댑터가 없으면 입력 거부 | 미지원 |

XWayland는 전체 Wayland 데스크톱의 대체 백엔드가 아니다. Wayland에서 X11로 자동 우회하지 않는다.
Linux 공통 계층의 OCR·AT-SPI·로그·관측·클릭 검증은 두 백엔드가 공유한다.
`primary`는 Ctrl이며 키는 현재 적용된 논리 키맵을 기준으로 해석한다.

## 설치

필요한 OS 패키지: Python 3와 PyQt6·PyGObject·Pillow, Tesseract, AT-SPI.
KDE Wayland는 추가로 Spectacle·Klipper, LayerShellQt 런타임과 Wayland shell integration,
C++ 컴파일러·CMake·pkg-config·KWin 개발 SDK·Qt6 개발 패키지를 사용한다.
KWin SDK와 실행 중 KWin의 버전이 맞아야 한다. X11은 Python Xlib를 사용한다.
KDE 기본 캡처는 OpenGL compositor가 필요하며 QPainter 모드는 지원하지 않는다.
시작 시 실제 캡처를 성공시킨 뒤에만 입력을 활성화한다.
OS 패키지 설치에 임의로 sudo를 실행하지 않는다.

```bash
python3 '<skill>/desk.py' install            # 실행기와 스킬 링크만
python3 '<skill>/desk.py' setup [--headers]  # 로컬 런타임·OCR 모델까지 (= setup_linux.py --runtime --ocr)
desk doctor
```

`--runtime`은 `~/.cache/desk/linux`에 system-site-packages venv와 Python Xlib를 준비한다.
`--ocr`은 고정된 upstream 리비전의 한국어·영어 Tesseract 모델을 로컬에 받는다.
`--headers`는 Debian/Ubuntu에서 설치된 LayerShellQt 런타임과 같은 버전의 개발 헤더를
사용자 캐시에 추출한다. 시스템에 개발 헤더가 있으면 그대로 쓴다.
다른 배포판은 해당 배포판의 LayerShellQt 개발 패키지를 설치한다.

실행기는 `~/.local/bin/desk`이며 스킬 폴더 파일이 실행 권한을 잃어도 Python으로 직접 호출한다.
`install`은 Claude Code·Codex·Agents 스킬 폴더의 `desk` 링크를 이 폴더로 맞추고, 다른 곳을 가리키던 링크는 교체한다. 링크가 아닌 파일·폴더는 건드리지 않는다.
스킬 폴더에는 실행 파일·venv·모델·스크린샷·로그를 만들지 않는다.
`DESK_STATE`는 세션 상태와 테스트 결과의 별도 로컬 경로다.
컴파일한 KWin·LayerShellQt 바인딩은 로컬 캐시에 둔다.

## 권한과 첫 실행 점검

macOS 같은 앱별 권한 승인은 없다. `desk doctor`가 필요한 패키지·OCR 언어·접근성 버스·캡처 경로를 보인다.

- `accessibility bus: disabled`면 앱이 AT-SPI로 컨트롤 정보를 내보내지 않는다. 데스크톱 설정에서 보조 기술(접근성)을 켜거나
  `gsettings set org.gnome.desktop.interface toolkit-accessibility true` 후 다시 로그인한다. 꺼져 있어도 OCR과 화면 픽셀로 동작한다.
- KDE Wayland 캡처는 KDE가 이미 승인한 Spectacle을 쓰므로 별도 승인 창이 없다. `DESK_CAPTURE=portal`일 때만 OS 공유 창에서 모니터를 승인한다.
- 빠진 OS 패키지는 사용자가 배포판 패키지 관리자로 설치한다. desk는 sudo를 실행하지 않는다.

## KDE Wayland

일반 창 플래그만으로는 숨김/재표시 때 포커스를 빼앗을 수 있어 패널과 인식 표시는
실제 Wayland layer-shell 표면을 사용한다. 하단 패널은 키보드 포커스를 받지 않고,
인식 표시는 마우스 입력도 통과시킨다. 스타일과 커서 속도는 공통 `widget.json`을 읽는다.
캡처할 때 두 표면을 숨겨 인식·픽셀 검증에 섞이지 않게 한다.
팝업 메뉴가 열려 있으면 입력 잡기를 방해하지 않도록 새 인식/클릭 오버레이를 올리지 않고,
반환하는 번호 이미지로만 표시한다. 클릭 영역 픽셀은 커서가 대상에 들어가기 직전에만 비교하고,
커서가 올라간 뒤의 hover 변화(배경·밑줄·툴팁)는 비교하지 않는다(세 OS 공통).
이때 패널도 숨김/재표시하지 않고, 패널이 가린 영역을 캡처에서 마스킹하고 해당 접근성 컨트롤을 제외한다.

KWin 어댑터는 스킬 폴더의 소스로 컴파일한 QML 확장이다. 시스템 플러그인 디렉터리에 설치하거나
kwinrc를 바꾸지 않고 현재 세션에서만 로드·해제한다. 소스·SDK·KWin 버전으로 캐시를 구분하고
동시 빌드는 잠근다. 한 데스크톱에는 Wayland 제어 세션 하나만 허용한다.
어댑터는 보이는 창의 ID·PID·위치와 입력 상태만 조회하고 실제 포인터·키보드 이벤트를 보낸다.
접근성 동작 호출이나 앱 데이터 접근은 하지 않는다.

물리 ESC는 앱에 전달하지 않고 영속 중지한다. 도구가 보내는 `key esc`는 별도로 구분한다.
포인터 이동은 공통 10px 기준을 적용하며 키·클릭·스크롤·터치는 즉시 개입으로 센다.
사용자가 키나 마우스 버튼을 계속 누르고 있으면 입력을 기다린다.
Python 세션의 heartbeat가 3초 동안 끊기면 KWin 어댑터가 중지하고 누른 키와 버튼을 해제한다.
잠금 화면, 다른 창·다른 위치·사용자 입력 세대에서는 새 입력을 거부한다.
버튼을 누르기 직전 실제 포인터의 창도 확인한다.

캡처는 이미 KDE가 승인한 Spectacle 실행 파일을 background/nonotify/new-instance 모드로 사용한다.
사용자 Spectacle 창이나 클립보드를 조작하지 않고 보이는 모니터 이미지만 받는다.
캡처 전용 임의 Python 인터프리터에 화면 권한을 부여하거나 OS의 보안 설정을 낮추지 않는다.
기본 캡처 범위는 커서가 있는 모니터다. `DESK_SCREEN`으로 모니터 이름을 제한하면 커서도
그 모니터에 있어야 한다. 화면 밖 좌표는 입력을 거부한다.

`DESK_CAPTURE=portal`은 별도 ScreenCast/PipeWire 캡처 경로다. 이 모드에서는 OS 공유 창에서
모니터 하나를 승인해야 하며 복원 토큰은 로컬 상태에만 보관한다. 기본 경로는 아니다.
지원/검증 상태는 아래 검증 기록과 구분한다.

## 인식·명령

Tesseract 한국어·영어 OCR과 보이는 활성 창의 AT-SPI 컨트롤을 같은 화면 근거에 결합한다.
AT-SPI는 이름·역할·상태·사각형만 읽으며 Text/Value 인터페이스나 컨트롤 동작은 호출하지 않는다.
Wayland 툴킷의 창 상대 좌표는 최상위 접근성 창과 KWin의 실제 client/frame 사각형으로 변환한다.
배율을 검증하지 못하면 해당 접근성 결과를 버리고 경고한다. 접근성 도구가 실패해도
OCR·화면 픽셀로 동작하며, 누락된 정보를 읽었다고 간주하지 않는다.

Linux 기본 실행부에는 아이콘 검출기·로컬 비전 모델을 아직 연결하지 않았다.
`ask`는 영역 그림과 질문 기록, `ask-ref`·`ask-report`를 지원하며 모델 답이 없음을 명시한다.
이 기능이 있다는 이유로 Mac/Windows 모델 실행 환경을 Linux에서 재사용하지 않는다.

```bash
desk begin '테스트 요청'
desk say '대상 앱을 확인하는 중'
desk windows
desk open --id '{창 ID}'
# 새 앱은 desktop ID로 실행: desk open org.kde.kwrite.desktop
desk see
desk click-text '입력칸 이름'
desk type '한글 텍스트'
desk key primary+a
desk end
```

`open`은 새 화면 인식까지 반환한다. 읽은 화면·창·포커스가 바뀌면 6으로 멈추고 새로 읽는다.
`-q` 입력 뒤에는 요소/좌표 근거를 무효화하므로 다음 클릭 전 `see`/`shot`이 필요하다.
`type`은 클립보드 선택을 비동기로 제공하며 입력 텍스트를 클립보드에 유지한다.
KDE는 포커스가 없는 패널의 Qt 클립보드 대신 Klipper의 clipboard 서비스에 주어진 텍스트만 쓴다.
기존 클립보드 내용·이력은 조회하지 않는다. 서비스를 사용할 수 없으면 붙여넣기 전에 실패한다.
Linux `type --keys`와 Windows 전용 `sequence`는 지원하지 않는다.
`scroll ... page` 대신 줄 수 또는 해당 앱의 PageDown/PageUp 키를 사용한다.
사용자 중지 뒤에는 새 요청 없이 begin으로 재시작하지 않는다.

## 재검증

화면을 건드리지 않는 공통 근거 검증:

```bash
~/.cache/desk/linux/bin/python '<skill>/linux/smoke.py'
```

보이는 별도 검증 창에서 실제 입력을 실행하는 테스트:

```bash
DESK_STATE=~/.cache/desk/linux-ui-test \
  ~/.cache/desk/linux/bin/python '<skill>/linux/smoke.py' --ui
```

기존 테스트 세션을 재사용할 때만 `--existing`을 붙인다.
X11 회귀 검증은 별도 Xephyr 디스플레이에서 `DISPLAY`·`XDG_SESSION_TYPE=x11`·`QT_QPA_PLATFORM=xcb`를 지정한다.
테스트는 자기 검증 창의 이벤트 로그로 클릭 횟수·입력 값을 확인하며 사용자 앱의 데이터를 읽지 않는다.
실제 사용자 중지(3)가 나오면 더 입력하지 않고 테스트를 끝낸다.
직접 확인한 조건과 아직 확인하지 않은 조건은 [validation.md](validation.md)에 있다.

중지 컴포넌트의 회귀 검증은 `linux/guard_smoke.py`다. 별도 D-Bus 세션의 KWin을
명시적인 `--x11-display`·`--socket`으로 띄운 테스트 데스크톱에서만 실행된다.
부모 테스트 컨트롤러가 X11 `desk`로 5px 이동 → 10px 이동 → ESC를 보내며,
중첩 KWin에는 이 입력이 도구 자체 입력과 구분되는 실제 장치 이벤트로 들어온다.
`DESK_GUARD_TEST_STATE`의 `ready`·`stage`·`armed`·`small-pass`·`large-pass` 파일로
순서를 맞춘다. `stage=arm`, 이어서 이동 검증 후 `stage=expect-esc`를 사용한다.
heartbeat 만료와 누른 Shift 해제까지 `result.json`으로 검증한다.
실제 데스크톱을 이 컴포넌트 테스트에 직접 연결하지 않는다.

### 명시적 터미널 테스트와 연타

터미널은 기본 입력 대상에서 거부한다. 사용자가 터미널 조작을 요청했을 때만
`desk windows`에서 확인한 특정 창을 `desk open --terminal --id '창 ID'`로 지정할 수 있다.
허용은 그 창 ID에만 적용되며 중지·사용자 개입·창 검증은 유지한다.
`desk key --repeat 2 ctrl`은 한 검증된 동작 안에서 80ms 간격으로 두 번 탭한다.
반복 횟수는 1–5이고 단일 키 명령의 기본 동작은 그대로다.

### 조합키와 함께 한 프레임 커서 이동

`desk move --modifiers super+alt X Y` 및 `super+ctrl`은 KWin 창 이동·크기 조절을 검증하는 짧은 커서 이동이다. 현재 관측한 창과 입력 epoch를 확인하고, 지정한 modifier를 누른 동안 원점과 끝점으로 실제 포인터 이벤트를 보낸 뒤 `finally`에서 키를 해제한다. 이동 거리는 설정된 한 프레임 거리 이하이고 화면 안으로 제한한다. 창 기하 변화만 허용하며 ID·PID·사용자 입력 epoch가 바뀌면 취소한다. 일반 move·click·drag의 기존 검증은 유지한다.

### 접근성 연결 재사용

Linux 세션은 접근성 조회용 프로세스 하나를 유지한다. 화면 인식·클릭 대상·포커스 검사마다 새 프로세스를 만들지 않으며, 각 조회에서 접근성 캐시를 비워 최신 상태를 확인한다. 입력 전 창·대상·사용자 중지 검사는 유지한다. 응답 시간 초과나 연결 오류 때만 작업 프로세스를 정리하고, 반복 실패 시 5초부터 최대 60초까지 복구 간격을 늘린다. 복구 대기 중에는 접근성 미제공 경고를 반환하며 기존 픽셀·OCR 안전 검사 경로를 따른다. 세션 종료 시 작업 프로세스도 종료한다.

데스크톱을 조작하지 않는 연결 재사용·실패 복구 테스트:

```bash
~/.cache/desk/linux/bin/python '<skill>/linux/test_accessibility_worker.py'
```

### 장기 실행 자원 관리

Linux 접근성 작업은 앱 전용 AT-SPI 클라이언트를 사용하며, 시스템 라이브러리로 조용히 대체하지 않도록 런타임 표식을 검사한다. 최초 빌드에 C 컴파일러·pkg-config·ninja·patch·patchelf와 D-Bus·GLib/GIO·libxml2·X11/XTest/XInput/XRes 개발 헤더가 필요하다. 원본 소스·빌드 도구·체크섬·패치는 linux/atspi_runtime에 포함되어 네트워크 없이 캐시에 빌드한다. 시스템 서비스나 권한은 변경하지 않는다.

접근성 경고는 중복을 제거하고 최대 32개를 유지한다. 인식 애니메이션은 숨김·정적 클릭 표시 상태에서 정지한다. 생성 캡처는 24시간·512MiB 기준으로 정리하며 현재 관측과 최근 작업 프레임 3종은 보호한다. actions.log는 8MiB마다 한 개 백업으로 교체한다.

추가 비조작 자원 회귀 검사는 `linux/test_resources.py`, 패널 중지 버튼은 `linux/test_stop_panel.py`로 실행한다.
