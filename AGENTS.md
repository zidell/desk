# desk 개발 안내

desk 스킬을 고치고 검증하고 배포하는 에이전트가 따를 규칙이다. 사용자 안내는 [README.md](README.md)에 둔다.

## 문서 역할

| 문서 | 읽는 쪽 | 내용 |
| --- | --- | --- |
| [README.md](README.md) | 사람 | 소개, 설치, 권한, 사용법, 중지, 지원 범위, 라이선스 |
| [skills/desk/SKILL.md](skills/desk/SKILL.md) | 스킬을 **실행하는** 에이전트 | 작업 흐름, 명령, 안전 규칙, 종료 코드. 스킬과 함께 배포된다 |
| AGENTS.md (이 파일) | 레포를 **개발하는** 에이전트 | 구조, 설계 불변 조건, 빌드·테스트, 배포 |
| [skills/desk/docs/](skills/desk/docs/) | 둘 다 | OS별 준비·권한·구현 규칙·테스트(`macos.md`·`windows.md`·`linux.md`), OS별 검증 상태(`validation.md`) |

- 실행 지침을 바꾸면 SKILL.md를, 사용자가 알아야 할 설치·동작 변화는 README.md를 함께 고친다.
- 문서는 이 표의 위치에만 둔다. 레포 루트에 docs/를 따로 만들지 않는다.
- README는 사람이 읽는 소개다. 설치 세부와 권한 안내는 에이전트가 `desk doctor`로 점검해 사용자에게 전하므로, README에 OS별 절차를 길게 옮기지 않는다.
- SKILL.md는 스킬을 쓸 때마다 컨텍스트에 들어간다. 짧게 유지하고 개발 사정(이력, 구현 사유)은 docs에 둔다.
- SKILL.md frontmatter의 `description`이 스킬 호출 조건을 정한다. 바꿀 때는 트리거 범위가 넓어지거나 좁아지지 않는지 확인한다.
- `skills/desk/` 밖의 파일(README, AGENTS.md, 매니페스트)은 스킬과 함께 설치되지 않는다. 스킬이 실행 중에 읽어야 하는 내용은 `skills/desk/` 안에 둔다.

## 구조

```text
.claude-plugin/plugin.json        플러그인 매니페스트(버전)
.claude-plugin/marketplace.json   이 레포를 마켓플레이스로 등록
skills/desk/
  desk            macOS 실행부(Bash). Swift helper를 빌드·호출한다
  desk-helper.swift  macOS 캡처·Accessibility·입력·패널
  desk.py         OS 선택 진입점. macOS는 desk로, Windows·Linux는 각 디렉터리로 넘긴다
  desk.cmd        Windows용 desk.py 호출
  desk-ssh        SSH 셸에서 Mac 콘솔 Terminal.app 워커로 중계
  desk-see.py     세 OS 공통: 인식 결과 결합(fusion), 비밀값 가리기(redact, redact_image)
  desk-click.py   세 OS 공통: 클릭 근거 기록과 이동·누르기 직전 대상 검증
  desk-ask.py     영역 질문(ask), 로컬 모델 그림자 답
  widget.json     세 OS 공통 패널·인식 표시·커서 속도 설정
  redact-words.txt  묶음 키 판정용 영어 단어 목록
  windows/        Python 명령부, C# native helper·launcher, 로컬 비전, setup, tests/
  linux/          공통 명령·세션(desk_linux.py), wayland.py·x11.py 어댑터, AT-SPI, KWin 어댑터(kwin/), 패널, 테스트
  linux/atspi_runtime/  앱 전용 AT-SPI 빌드용 업스트림 소스·도구·패치(오프라인 빌드)
```

## 설계 불변 조건

아래는 사용자가 확정한 동작이다. 바꾸려면 먼저 사용자에게 묻는다. SKILL.md·docs에 "(사용자 확정, 날짜)"로 표시된 항목도 같다.

- **보이는 화면만 읽는다.** DOM·JavaScript, AppleScript 앱 데이터, 앱 DB·파일·API, 숨겨진 창을 읽지 않는다. 접근성 API는 이름·역할·상태·좌표만 읽고 동작 호출(UIA Invoke·ValuePattern, AXPress, AT-SPI Action/Text/Value)은 쓰지 않는다. 실제 포인터·키보드 이벤트를 보낸다.
- **판단은 에이전트, 실행은 스크립트.** 커서 현재 위치 조회, 경로·속도 계산, 연속 이동, 누르기·떼기는 클릭 명령 하나 안에서 처리한다. 행동 묶음 중간에 인식·LLM 호출·고정 대기를 넣지 않는다. 자동 로컬 sLLM 판단을 기본 인식 경로에 넣지 않는다.
- **중지.** 물리 ESC 한 번과 패널 중지 버튼은 영속 중지(종료 코드 3)이며 물리 ESC는 대상 앱에 전달하지 않는다. `begin`으로 스스로 해제하는 경로를 만들지 않는다. 키·버튼은 중지·실패 때도 `finally`에서 뗀다(macOS ⌘ 고착 사례, validation.md 2026-10-28).
- **사용자 개입.** 포인터는 기준점에서 직선 10px(`userPointerThresholdPixels`) 이상일 때만 개입으로 보고, 클릭·스크롤·키·ESC는 즉시 감지한다. 도구 자체 입력은 제외한다.
- **클릭 대상 검증.** 클릭은 마지막으로 읽은 화면의 영역·대상에 묶인다. 대상이 움직이거나 바뀌면 종료 코드 6으로 취소하고 근처 요소로 자동 보정하지 않는다. 클릭 영역 픽셀은 이동 전에 한 번만 비교하고, 커서가 올라간 뒤의 호버 변화는 비교하지 않는다. `--force`는 글자 분류 제한만 푼다.
- **창 판정은 창 ID로.** macOS `pid:창ID`, Windows HWND, Linux 창 id. 제목은 사람이 읽는 용도다.
- **비밀값 가리기.** 에이전트로 나가는 글자·그림의 비밀값은 `desk-see.py`의 공통 함수로 가린다. 그림용 OCR이 실패하면 가리지 못한 그림을 내보내지 않는다. 가린 값을 복원하는 경로를 만들지 않는다.
- **공통 정책은 공통 코드에.** 커서 속도·패널 모양·인식 색은 `widget.json`, 인식 결합·가리기는 `desk-see.py`, 클릭 근거는 `desk-click.py`에 둔다. 한 OS만 다르게 고치지 않는다. 한 OS에서 고치면 나머지 OS도 같이 고치거나 validation.md에 남은 일로 적는다.
- **안전한 거부.** 안전 어댑터가 없는 환경(GNOME 등 Wayland)에서는 입력을 거부한다. Wayland에서 XWayland로 자동 우회하지 않는다.

## 첫 실행 점검

- 각 OS의 `desk doctor`는 빠진 권한·구성 요소마다 `missing`·`disabled`·`hint`·`→`로 고치는 방법을 함께 출력한다. 에이전트는 이를 사용자에게 그대로 전한다(SKILL.md "처음 쓸 때와 권한").
- macOS 권한은 `desk-helper permissions`(TCC는 helper를 실행한 터미널 앱 기준)로 읽는다. `begin`은 빠진 권한을 경고만 하고 기존 동작은 그대로다.
- 점검 항목을 추가할 때도 권한을 대신 켜거나 시스템 설정을 바꾸지 않는다.

## 시스템과 상태

- 소스 폴더에 실행 파일·venv·모델·로그·스크린샷·캐시를 만들지 않는다. 모두 `~/.cache/desk`(`DESK_STATE`)에 둔다. `.gitignore`가 있다고 소스 폴더에 써도 된다는 뜻은 아니다.
- sudo, 셸 프로필, PowerShell 실행 정책, 시스템 라이브러리·서비스, kwinrc, 시스템 플러그인 디렉터리, OS 보안 설정을 바꾸지 않는다. 필요한 OS 패키지는 안내만 한다.
- 모델 다운로드는 설치(setup) 때만 한다. 실행 중에 화면을 외부 추론 API로 보내지 않는다.
- `ask`의 로컬 모델 답은 그림자 모드다. 종류별 30문항 이상·정확도 95% 이상·화면에 없는 답에 90% 이상 "없음"·중앙값 5초 이하를 넘기 전에는 판단 근거로 쓰지 않는다.
- 새 서드파티 소스·가중치를 넣거나 내려받게 하면 라이선스를 확인해 README의 라이선스 절에 적는다. 업스트림 원본을 동봉할 때는 체크섬과 출처를 함께 둔다(`linux/atspi_runtime/` 참고).

## 빌드

각 OS 로컬에서 빌드·검증한다. 다른 OS의 코드는 고칠 수 있지만, 그 OS에서 실행해 보기 전에는 검증했다고 하지 않는다.

- macOS: `desk`가 `desk-helper.swift`가 바이너리보다 새로우면 `swiftc -O`로 `~/.cache/desk/desk-helper`를 다시 빌드한다. 빌드 로그는 `~/.cache/desk/build.log`.
- Windows: `desk_windows.py`가 .NET Framework `csc.exe`로 `~/.cache/desk/desk-native.exe`를 빌드한다. 준비는 `py -3 -X utf8 .\desk.py setup [--detector] [--vision]`, 연결 복구는 `install`.
- Linux: `linux/setup_linux.py --runtime --ocr --headers`. KWin 어댑터와 AT-SPI 런타임은 캐시에 빌드되며 소스·SDK·KWin 버전으로 캐시를 구분한다.

## 테스트

실제 입력 테스트는 사용자의 마우스·키보드를 가져간다. 사람이 쓰는 기기에서는 실행 전에 묻고, 테스트 전용 창과 격리된 `DESK_STATE`를 쓴다. 테스트 중 종료 코드 3(사용자 중지)이 나오면 더 입력하지 말고 끝낸다.

| OS | 화면 조작 없음 | 실제 입력 |
| --- | --- | --- |
| Linux | `~/.cache/desk/linux/bin/python skills/desk/linux/smoke.py`, `test_accessibility_worker.py`, `test_resources.py`, `test_stop_panel.py` | `DESK_STATE=~/.cache/desk/linux-ui-test … smoke.py --ui`. `guard_smoke.py`는 중첩 KWin 테스트 데스크톱에서만 |
| Windows | — | `windows/tests/smoke.py`, `windows/tests/reflow.py` (별도 검증 창) |
| macOS | Swift 빌드 성공 | 자동 테스트 없음. 테스트 페이지·앱에서 직접 확인 |

Python만 고쳤을 때 최소한 `python3 -m py_compile`로 문법을 확인한다. 자세한 실행 조건은 각 OS 문서의 재검증 절에 있다.

## 검증 기록

- [docs/validation.md](skills/desk/docs/validation.md)는 날짜별 이력이 아니라 OS별 현재 검증 상태다. 이력은 커밋 메시지에 남긴다.
- 동작을 바꾸면 "재검증이 필요한 변경" 표에 OS별로 **직접 실행**, **빌드만**, **문법 검사만**, **코드만**을 적고, 그 OS에서 확인하면 해당 OS 절로 옮긴다. 확인하지 않은 조건을 확인했다고 쓰지 않는다.
- 검증 기록과 테스트 픽스처에 실제 비밀번호·토큰·개인정보를 쓰지 않는다. 가짜 값을 쓴다.

## 파일 형식과 문체

- `desk`, `desk-ssh` 같은 Bash 파일은 LF여야 한다. CRLF면 macOS에서 바로 문법 오류가 난다. `.gitattributes`가 LF로 맞추고 `.cmd`·`.ps1`만 CRLF다.
- `desk`, `desk-ssh`, `desk.py`는 실행 권한을 유지한다.
- SKILL.md·docs는 한국어 평서문("~한다"), 개조식으로 쓴다. README는 존댓말("~합니다")로 쓴다. 코드 주석은 그 파일의 기존 언어와 밀도를 따른다.
- 사용자에게 보이는 메시지(패널, 오류)는 한국어로 쓴다.

## 배포

- 사용자에게 보이는 변경을 내보낼 때 `.claude-plugin/plugin.json`의 `version`을 올린다(SemVer).
- 커밋·태그·푸시는 사용자가 요청할 때만 한다.
