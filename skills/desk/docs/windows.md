# Windows 실행 환경

Windows 10/11 데스크톱 세션, Python 3.10 이상, .NET Framework 4.x(`csc.exe`)를 쓴다.

## 준비

스킬 폴더에서 실행한다.

```powershell
py -3 -X utf8 .\desk.py setup                       # 기본
py -3 -X utf8 .\desk.py setup --detector --vision   # 아이콘 검출기·비전 모델까지
py -3 -X utf8 .\desk.py install                     # 런타임은 있고 명령·스킬 연결만 복구
```

- 런타임은 `~/.cache/desk/windows` venv, C# helper는 `~/.cache/desk/desk-native.exe`다. 소스가 바뀌면 다시 빌드한다.
- `--detector`는 OmniParser icon_detect_v3 가중치와 PyTorch를, `--vision`은 SmolVLM2-500M과 Transformers를 설치한다. NVIDIA GPU면 CUDA 12.8 wheel, 아니면 CPU wheel을 쓴다.
- 모델은 설치할 때만 내려받는다. 실행 중에는 로컬 파일만 쓰고 화면을 외부 추론 API로 보내지 않는다.
- 설치는 `~/.local/bin`에 `desk.exe`, `desk.cmd`, `desk.ps1`을 만들고 사용자 PATH에 추가한다. 셸 프로필과 실행 정책은 바꾸지 않는다.
- `~/.codex/skills/desk`, `~/.agents/skills/desk`, `~/.claude/skills/desk`는 이 폴더로 가는 junction이다. `desk.cmd`·`desk.exe`는 `~/.codex/skills/desk`를 거쳐 들어온다. 다른 곳을 가리키던 junction은 교체하고, 링크가 아닌 폴더는 건드리지 않는다. 상태는 `doctor`의 `skill_links`.

## 권한과 첫 실행 점검

macOS 같은 앱별 권한 승인은 없다. `desk doctor`가 다음을 알려 준다.

- `ocr_languages`: 설치된 Windows OCR 언어. 한국어나 영어가 없으면 `ocr_hint`대로 **설정 → 시간 및 언어 → 언어 및 지역**에서 그 언어의 광학 문자 인식 기능을 설치한다. 한국어만 있어도 섞인 영문을 인식한다.
- `elevated`: desk가 관리자 권한으로 실행 중인지. 관리자 권한 앱은 일반 권한 프로세스의 입력을 막는다(UIPI). 그런 앱을 다뤄야 하면 에이전트 터미널을 관리자 권한으로 실행한다. UAC 같은 보호된 데스크톱은 OS가 실제로 입력을 막을 때만 사용자에게 필요한 동작을 알린다.
- `cuda`, `detector_weights`, `local_vision`: 선택 구성 요소 상태.

## 실행

- Windows에는 디스플레이 설정을 여는 `desk.cpl`이 있어 bare `desk`가 그쪽으로 해석될 수 있다. `& "$env:USERPROFILE\.local\bin\desk.exe" help`처럼 절대 경로를 쓰고, PATH가 반영된 새 셸에서는 `desk.exe`·`desk.cmd`도 된다.
- `desk.exe`는 Unicode·공백·따옴표를 그대로 전달한다. cmd.exe의 `%`·`!` 확장은 그대로 일어나므로 복잡한 텍스트는 PowerShell에서 호출한다.
- `desk windows`로 보이는 창의 HWND·앱·제목을 보고 `desk open --hwnd 번호`로 특정 창을 앞으로 가져온다. 같은 앱 창이 여럿이면 임의로 고르지 않는다. 에이전트 터미널·호스트 창에는 입력하지 않는다.

## 구현 규칙

- 캡처는 GDI, 좌표는 캡처·UIA·패널·입력 모두 physical pixel/virtual desktop 기준이다.
- UIA는 보이는 foreground 컨트롤의 이름·종류·상태만 읽는다. ValuePattern·TextPattern이나 컨트롤 동작은 호출하지 않는다.
- `open`/`see`는 한 캡처로 OCR·UIA·검출을 병렬 실행하고 완료된 인식 표시를 한 번만 그린다. 검출기는 인증된 named pipe로 모델을 재사용한다.
- native `step` 한 번이 이동·입력·전후 캡처를 처리한다. 입력 도중 helper 프로세스를 반복 실행하지 않는다.
- 입력 전후 캡처를 비교해 입력칸 밖이 바뀌었을 때만 결과 캡처를 한 번 인식한다. 입력칸 안의 타이핑·커서만 바뀌면 결과 그림만 돌려준다.
- `click-text`는 창과 사용자 입력 세대가 같은 기존 인식 결과를 먼저 쓴다.
- 키 포커스는 같은 관측의 Win32 포커스로 판단한다. 다른 시점의 UIA 포커스를 다시 조회하지 않는다.
- SendInput으로 Unicode·키·포인터를 보낸다. 사용자 입력 hook과 창·포커스·위치 이벤트를 감지하고 입력 직전에 다시 검증한다.
- 패널은 포커스를 뺏지 않는다. 인식 표시는 캡처 전에 닫고 확인 응답을 기다리며, 클릭을 가로채지 않는다.
- 패널·검출기·비전 작업자는 호출한 터미널과 분리된 프로세스로 실행한다.
- 자동 로컬 sLLM 설명은 기본 인식 경로에 없다. `ask` 비교는 명시적으로 호출할 때만 쓴다. SmolVLM2는 영어 모델이라 한국어 질문 품질을 Mac MLX 모델과 같다고 보지 않는다.
- 검출 임계값은 Mac과 같은 0.05다. 검출 상자는 클릭 가능성의 추측으로만 쓴다.
- UIA provider가 응답하지 않으면 helper를 종료하고 OCR·검출로 진행한다. 부분 읽기·도구 실패는 출력에 표시한다.

## 테스트

보이는 별도 검증 창과 격리된 상태 디렉터리를 쓴다. 사용자 중지나 예상 못 한 앱 전환이 있으면 더 조작하지 않는다.

```powershell
$py = "$env:USERPROFILE\.cache\desk\windows\Scripts\python.exe"
& $py -X utf8 '<skill>\windows\tests\smoke.py'           # 화면 조작 없음
& $py -X utf8 '<skill>\windows\tests\smoke.py' --widget  # 위젯·인식 표시 구성 요소
& $py -X utf8 '<skill>\windows\tests\smoke.py' --ui      # 실제 입력, 화면을 맡길 수 있을 때만
& $py -X utf8 '<skill>\windows\tests\reflow.py'          # 로딩·리플로우 중 오래된 클릭 방지
```
