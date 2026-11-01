# desk — 데스크톱 제어 플러그인

AI 코딩 에이전트(Claude Code, Codex 등)가 **사람이 보는 화면**을 읽고 **실제 마우스와 키보드**로 데스크톱 앱과 웹 업무를 하게 해 주는 플러그인입니다. 작업 방법과 실행 코드를 담은 `desk` 스킬을 제공하며, macOS·Windows·Linux를 하나의 `desk` 명령으로 다룹니다.

```text
desk로 시스템 설정에서 다크 모드 켜줘
desk로 이 양식에 내 주소 채워줘. 제출은 하지 마
desk로 다운로드 폴더에서 어제 받은 PDF 열어줘
```

## 사용 전 주의

desk에는 여러 안전장치가 있지만, 기본적으로 컴퓨터의 조작을 에이전트에게 맡기는 도구입니다. **에이전트가 수행하는 모든 행위에는 위험이 있으며, 안전장치가 이를 완전히 막아 주지는 못합니다.**

**이 스킬을 자동화 과정에 포함하지 마세요. 컴퓨터 앞에 앉아 에이전트의 동작을 지켜보고 즉시 멈출 수 있을 때에만 실행하세요.**

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
| 밖으로 무엇이 나가나? | 화면 인식은 모두 로컬에서 합니다. 에이전트(와 그 모델 API)가 받는 것은 가린 글자와 그림뿐입니다. 설치할 때 구성 요소를 내려받고, 사용 중에는 최대 한 시간에 한 번 배포 확인·업데이트를 위해 통신합니다. 자동 업데이트는 끌 수 있습니다. |
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

일반 사용자는 사용하는 에이전트에 플러그인을 설치합니다. 이후 새 대화에서 "desk로 … 해줘"라고 요청하면 에이전트가 실행 환경 준비와 점검을 처리합니다. OS 권한 승인과 OS 패키지 설치가 필요하면 그 단계만 안내합니다. 최초 준비에는 Python 등 아래의 OS별 구성 요소가 필요합니다.

**Claude Code**에서 다음 두 명령을 입력합니다.

```text
/plugin marketplace add zidell/desk
/plugin install desk@desk
```

**Codex**에서는 다음 명령으로 설치합니다. 플러그인 관리 명령을 지원하는 최신 Codex가 필요합니다.

```sh
codex plugin marketplace add zidell/desk
codex plugin add desk@desk
```

Codex 데스크톱의 플러그인 목록에서도 추가한 `desk` 소스를 선택해 설치할 수 있습니다. 설치·갱신 뒤 새 대화에서 사용합니다. 이 레포에는 Claude용 `.claude-plugin`과 Codex용 `.codex-plugin` 매니페스트가 있고, 두 플러그인이 같은 `skills/desk`를 제공합니다. Codex 카탈로그 규격은 [공식 문서](https://developers.openai.com/plugins/build/plugins)를 따릅니다. 이 방식은 GitHub 마켓플레이스에서 설치하며, 공개 플러그인 디렉터리에 등록되었다는 뜻은 아닙니다.

저장소와 마켓플레이스 이름은 0.4.1부터 `desk-skill`에서 `desk`로 바뀌었습니다. 이전 플러그인 설치를 새 이름으로 옮기려면 기존 `desk@desk-skill`을 제거한 뒤 위 설치 명령으로 `desk@desk`를 설치합니다. 이전 마켓플레이스 등록은 새 설치를 확인한 뒤 제거합니다. 기존 git 설치 폴더는 옮길 필요가 없으며, 자동 패치는 이전 원격 주소도 허용합니다.

### 개발용·직접 설치

소스를 수정하며 쓰거나 스킬만 직접 설치할 때는 아래 절차를 따릅니다. 일반 플러그인 사용자는 직접 실행할 필요가 없습니다.

### 에이전트용 설치 절차

1. 레포를 받습니다. 위치는 바꿔도 되며, 이후 명령의 경로만 맞추면 됩니다.
   ```sh
   git clone https://github.com/zidell/desk.git ~/.local/share/desk                         # macOS·Linux
   git clone https://github.com/zidell/desk.git "$env:USERPROFILE\.local\share\desk"     # Windows PowerShell
   ```
2. 실행 명령과 스킬 링크를 설치합니다. `~/.local/bin/desk`(Windows는 `desk.exe`)를 만들고, `~/.claude/skills/desk`·`~/.codex/skills/desk`·`~/.agents/skills/desk`가 이 폴더를 가리키게 합니다. 다른 곳을 가리키던 링크는 교체하고, 링크가 아닌 파일·폴더는 건드리지 않습니다.
   ```sh
   python3 ~/.local/share/desk/skills/desk/desk.py install                       # macOS·Linux
   py -3 -X utf8 "$env:USERPROFILE\.local\share\desk\skills\desk\desk.py" setup   # Windows (pip 패키지 설치 포함)
   ```
3. `desk doctor`를 실행하고, `missing`·`disabled`·`→`로 표시된 항목을 사용자에게 안내합니다. OS 권한과 OS 패키지는 사용자가 직접 켜거나 설치합니다.
4. 이후에는 작업 시작 시 자동으로 새 배포본을 패치합니다. 즉시 확인하려면 `desk update`(Windows는 `desk.exe update`)를 실행합니다. 폴더를 옮겼다면 2단계를 다시 실행합니다.

스킬만 설치하는 방법도 지원합니다. 받은 스킬 폴더에서 에이전트가 2단계를 한 번 실행합니다.

- `npx skills add zidell/desk -g`: 스킬 폴더는 `~/.agents/skills/desk`입니다.

### 업데이트

desk는 작업을 시작하기 전(`begin`), 최대 한 시간에 한 번 GitHub의 버전을 확인하고 새 배포 태그의 코드를 자동으로 패치합니다. 패치 뒤 새 코드로 작업을 시작하며, 제어 세션이 실행 중이면 갱신을 미룹니다. 사용하지 않는 동안 별도 상주 프로그램을 실행하지는 않습니다.

- git 설치는 공식 저장소의 수정 없는 `main` 브랜치만 fast-forward합니다. 로컬 수정·별도 커밋·fork는 덮어쓰지 않고 이유를 알려 줍니다.
- `npx skills` 사본은 현재 공식 배포본과 같은지 확인한 뒤 같은 경로에 교체합니다. 교체 실패 시 원래 사본을 복원합니다.
- Claude 플러그인은 실행기가 연결된 마켓플레이스 clone을 먼저 패치합니다. `claude` 명령으로 플러그인 지침도 갱신하며, CLI가 없거나 실패해도 실행 코드 패치는 유지합니다. 새 지침은 Claude Code를 재시작하면 반영됩니다.
- Codex 플러그인은 `codex plugin marketplace upgrade`와 `codex plugin add`로 관리 캐시를 갱신하고, 실행기에서 쓰는 링크를 새 버전으로 연결합니다. 캐시 파일을 직접 덮어쓰지 않습니다. 로컬 개발용 마켓플레이스·fork·수정한 사본은 자동 갱신하지 않습니다. 새 지침은 Codex의 새 대화에서 반영됩니다.
- 다운로드·적용 실패 시 현재 버전으로 실행하고 5분 뒤 다음 `begin`에서 다시 확인합니다. `desk update`로 즉시 재시도할 수도 있습니다.
- 자동 확인·패치를 끄려면 `DESK_AUTO_UPDATE=0`을 설정합니다. 기존 `DESK_UPDATE_CHECK=0`도 지원합니다. 직접 실행한 `desk update`는 이 설정과 무관하게 확인합니다.

**0.2.1을 포함한 이전 설치본에는 자동 패치 코드가 없습니다.** 이번 0.3.0 이상으로 한 번 업데이트해야 이후부터 자동 패치됩니다. git 설치는 `git pull --ff-only`, 스킬 사본은 `npx skills update -g`, Claude 플러그인은 `/plugin`의 업데이트를 사용합니다. 변경 내용은 [Releases](https://github.com/zidell/desk/releases)에 있습니다.

Codex 플러그인 자동 패치는 **0.4.0부터** 지원합니다.

### 사용자가 할 일

| OS | 할 일 |
|---|---|
| macOS | 에이전트를 실행하는 터미널 앱에 **손쉬운 사용**과 **화면 기록** 권한을 켜고 터미널을 다시 실행합니다(`desk permissions --request`로 승인 창을 띄울 수 있습니다). Xcode Command Line Tools가 필요합니다. |
| Windows | Python 3.10 이상을 설치합니다. OCR 언어가 없으면 `doctor` 안내대로 설정에서 추가합니다. |
| Linux | `doctor`가 알려 주는 OS 패키지(PyQt6, Tesseract, AT-SPI 등)를 설치합니다. |

자세한 내용은 [macOS](skills/desk/docs/macos.md) · [Windows](skills/desk/docs/windows.md) · [Linux](skills/desk/docs/linux.md) 문서에 있습니다.

## 기여

버그 제보와 제안은 [Issues](https://github.com/zidell/desk/issues)에 남겨 주세요. 코드를 고쳤다면 레포를 fork해서 PR을 보내 주세요. 에이전트에게 "이 수정으로 PR 보내줘"라고 하면 fork와 PR 생성까지 해 줍니다.

자기 기기에 맞게 고쳐 쓰려면 받은 clone을 직접 고치기보다 fork해서 쓰는 편이 낫습니다. clone을 고친 채로 두면 업데이트(`git pull`)가 충돌할 수 있습니다.

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
