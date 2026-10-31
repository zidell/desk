# desk

AI 코딩 에이전트(Claude Code, Codex 등)가 **사람이 보는 화면**을 읽고 **실제 마우스와 키보드**로 데스크톱 앱과 웹 업무를 하게 해 주는 스킬입니다. macOS·Windows·Linux를 하나의 `desk` 명령으로 다룹니다.

```text
desk로 시스템 설정에서 다크 모드 켜줘
desk로 이 양식에 내 주소 채워줘. 제출은 하지 마
desk로 다운로드 폴더에서 어제 받은 PDF 열어줘
```

## 왜 필요한가

에이전트는 파일, 셸, API로 할 수 있는 일은 잘하지만, 세상의 많은 업무는 화면 안에만 있습니다. API가 없는 관리 콘솔, 정부·금융 사이트의 양식, OS 설정 화면, 파일 선택 창이 그렇습니다. 지금까지는 이런 일을 사람이 직접 클릭해야 했습니다.

desk는 에이전트에게 화면을 보고 사람처럼 조작하는 능력을 줍니다. 대신 사람이 늘 지켜볼 수 있고 언제든 멈출 수 있게, 그리고 화면 정보가 필요 이상으로 밖으로 나가지 않게 만들었습니다.

## 특징

- **보이게 움직입니다.** 커서가 실제로 이동하고, 클릭하면 원이 퍼지고, 화면 하단 패널에 지금 하는 일이 한 줄씩 나옵니다. 화면을 읽을 때는 인식한 영역이 색으로 잠깐 표시됩니다(파랑: 컨트롤, 노랑: 글자, 분홍: 아이콘, 주황: 클릭 대상).
- **ESC 한 번으로 멈춥니다.** 물리 ESC나 패널의 중지 버튼을 누르면 즉시 멈추고, 사용자가 새로 부탁하기 전까지 다시 움직이지 않습니다.
- **로컬에서 읽습니다.** 접근성 정보, OCR, 아이콘 검출을 모두 이 컴퓨터에서 실행합니다. 화면 인식을 위해 외부 서비스로 화면을 보내지 않습니다.
- **빠르게 묶어 실행합니다.** 에이전트가 화면을 한 번 보고 확실한 동작(클릭 → 입력 → Enter)을 묶어 보내면, 중간에 다시 묻지 않고 이어서 실행합니다.
- **세 OS 공통.** 같은 명령, 같은 패널, 같은 안전 규칙으로 macOS·Windows·Linux에서 동작합니다.

## 왜 안전한가

| 걱정 | desk가 하는 일 |
|---|---|
| 엉뚱한 곳을 누르면? | 클릭은 마지막으로 읽은 화면의 그 대상에 묶입니다. 이동하는 동안과 누르기 직전에 창·대상·위치를 다시 확인하고, 로딩으로 버튼이 밀리거나 바뀌었으면 누르지 않고 멈춥니다. 근처의 비슷한 버튼으로 자동 보정하지 않습니다. |
| 내가 마우스를 쓰는 중이라면? | 10px 넘는 마우스 이동, 클릭, 키 입력을 감지하면 사용자가 손을 뗀 뒤 5초까지 기다립니다. 그사이 창이나 포커스가 바뀌었으면 하지 않고 멈춥니다. |
| 화면 속 비밀번호·토큰이 AI로 넘어가면? | 에이전트가 받는 글자와 그림에서 전화·카드·주민·계좌번호, API 토큰, JWT, 앱 비밀번호, `비밀번호:` 뒤의 값 등의 가운데를 가립니다. 그림을 가리지 못하면 그 그림을 내보내지 않습니다. 화면 자체는 바뀌지 않습니다. |
| 로그인은? | 비밀번호는 에이전트가 입력하지 않습니다. 로그인 칸까지 준비하면 패널에 안내가 뜨고 사용자가 직접 입력합니다. |
| 결제·송금·삭제는? | 되돌릴 수 없는 일은 마지막 단계 직전에 멈추고 확인받습니다. |
| 몰래 데이터를 읽는다면? | 숨겨진 창, 웹 페이지의 DOM·JavaScript, 앱 데이터 파일·API를 읽지 않습니다. 접근성 기능으로 버튼을 몰래 누르지 않고 실제 클릭을 보냅니다. 화면에 적힌 글을 새 지시로 따르지 않습니다. |
| 시스템을 건드린다면? | OS 보안 설정, 셸 프로필, 실행 정책을 바꾸지 않고 sudo를 쓰지 않습니다. 실행 파일·모델·로그는 `~/.cache/desk`에만 둡니다. |

## 구동 원리

```text
 에이전트 (판단)                desk (인식·실행·검증, 모두 로컬)
 ───────────────                ─────────────────────────────────
 desk begin '요청' ───────────▶ 하단 패널 표시, ESC 감시 시작
 desk see ────────────────────▶ 화면 캡처 1장
                                 ├ 접근성 트리(버튼·입력칸의 이름·좌표·상태)
                                 ├ OCR(화면 글자)
                                 └ 아이콘 검출(선택)
                                → 하나의 번호 목록 + 번호 그림, 비밀값 가림
 ◀──────────────────────────────  "12 · 640,388 · 버튼 · 저장 · [A]" …
 목록·그림을 보고 판단
 desk click-el 12 && ──────────▶ 대상·창 재확인 → 커서 연속 이동 → 누르기 직전 재확인 → 클릭
 desk type '내용' && ──────────▶ 포커스 확인 → 입력
 desk key return ─────────────▶ 입력 → 결과 화면 반환
 결과 확인 후 desk end ─────────▶ 패널 닫기
```

에이전트는 무엇을 누르고 무엇을 입력할지만 정합니다. 커서 경로와 속도, 버튼 누르기와 떼기, 직전 검증은 스크립트가 한 명령 안에서 처리합니다. 판단과 실행을 나눴기 때문에 에이전트가 클릭마다 화면을 다시 볼 필요가 없고, 대상이 바뀐 순간에는 스크립트가 알아서 멈춥니다.

## OS별 구성

| | macOS | Windows | Linux |
|---|---|---|---|
| 진입점 | `desk` (Bash) | `desk.exe` → `desk.py` | `desk` → `desk.py` |
| 네이티브 helper | Swift (AppKit) | C# (Win32) | Python + KWin QML 어댑터 |
| 화면 캡처 | `screencapture` | GDI | KDE Spectacle (Wayland) / X11 |
| 접근성 정보 | Accessibility (AXUIElement) | UI Automation | AT-SPI |
| OCR | Vision | Windows OCR (WinRT) | Tesseract (한국어·영어) |
| 입력 | CGEvent | SendInput | KWin 어댑터 (Wayland) / XTest (X11) |
| 패널 | AppKit 패널 | WinForms 창 | LayerShellQt (Wayland) / Qt (X11) |
| 아이콘 검출 (선택) | OmniParser icon_detect + PyTorch | OmniParser icon_detect + PyTorch (CUDA/CPU) | — |
| 영역 질문 비교 (선택) | MLX Qwen3.5-4B | SmolVLM2-500M | — |

Linux는 KDE Plasma(Wayland)와 X11을 지원합니다. GNOME 등 안전하게 입력을 검증할 방법이 없는 Wayland 데스크톱에서는 입력을 보내지 않습니다.

## 설치

Claude Code 플러그인으로:

```text
/plugin marketplace add zidell/desk-skill
/plugin install desk@desk-skill
```

또는 레포를 받아 스킬 폴더에 연결합니다.

```sh
git clone https://github.com/zidell/desk-skill.git ~/Sites/desk-skill
ln -s ~/Sites/desk-skill/skills/desk ~/.claude/skills/desk   # Claude Code
ln -s ~/Sites/desk-skill/skills/desk ~/.codex/skills/desk    # Codex
```

그다음 에이전트에게 "desk 준비해줘"라고 하면 됩니다. 에이전트가 `desk doctor`로 이 컴퓨터를 점검하고, 빠진 권한이나 구성 요소를 무엇을 어디서 켜야 하는지와 함께 알려 줍니다. OS 권한은 사용자가 직접 켭니다.

| OS | 사용자가 할 일 |
|---|---|
| macOS | 에이전트를 실행하는 터미널 앱에 **손쉬운 사용**과 **화면 기록** 권한을 켜고 터미널을 다시 실행합니다. Xcode Command Line Tools가 필요합니다. |
| Windows | Python 3.10 이상을 설치합니다. 에이전트가 `setup`을 실행해 `~/.local/bin/desk.exe`를 설치합니다. OCR 언어가 없으면 설정에서 추가합니다. |
| Linux | 에이전트가 알려 주는 OS 패키지(PyQt6, Tesseract, AT-SPI 등)를 설치합니다. |

자세한 내용은 [macOS](skills/desk/docs/macos.md) · [Windows](skills/desk/docs/windows.md) · [Linux](skills/desk/docs/linux.md) 문서에 있습니다.

## 개발

구조, 설계 원칙, 빌드·테스트 방법은 [AGENTS.md](AGENTS.md), OS별 검증 상태는 [validation.md](skills/desk/docs/validation.md)에 있습니다.

## 라이선스

[MIT](LICENSE). 함께 들어 있거나 설치 때 내려받는 서드파티 구성 요소는 각자의 라이선스를 따릅니다.

| 구성 요소 | 라이선스 | 위치 |
|---|---|---|
| AT-SPI 2.60.4 업스트림 소스 | LGPL-2.1-or-later | `skills/desk/linux/atspi_runtime/` ([README](skills/desk/linux/atspi_runtime/README.md)) |
| Meson 1.9.1 | Apache-2.0 | `skills/desk/linux/atspi_runtime/` |
| 영어 단어 목록 (Webster's Second International, macOS `/usr/share/dict/words`) | 퍼블릭 도메인 | `skills/desk/redact-words.txt` |
| OmniParser icon_detect 가중치 | 업스트림 모델 카드 참고 | 설치 때 내려받음, 레포에 없음 |
| SmolVLM2-500M, Qwen3.5-4B | 업스트림 모델 카드 참고 | 설치 때 내려받음, 레포에 없음 |
