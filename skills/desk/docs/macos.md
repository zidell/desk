# macOS 실행 환경

`desk`(Bash)와 `desk-helper.swift`가 macOS 실행부다. `desk.py`를 실행해도 같은 Bash 실행부로 넘어간다.

## 준비

- `python3 <스킬 폴더>/desk.py install`(또는 `desk install`)이 `~/.local/bin/desk`·`desk-ssh` 실행기와 Claude Code·Codex·Agents 스킬 링크를 만든다. `~/.local/bin`이 PATH에 없으면 알려 준다. 셸 프로필은 바꾸지 않는다.
- Swift helper는 처음 실행할 때와 소스가 바뀌었을 때 `~/.cache/desk/desk-helper`로 빌드된다. Xcode Command Line Tools(`swiftc`)가 필요하다.
- `begin`은 클릭 근거를 비교할 Pillow를 준비한다. `~/.cache/desk/omni` 환경이 있으면 쓰고, 없으면 `~/.cache/desk/evidence` venv를 만든다. 준비에 실패하면 클릭을 허용하지 않는다.

## 권한

권한은 helper가 아니라 에이전트를 실행한 **터미널 앱**(Terminal, iTerm2, Ghostty, VS Code 등)에 붙는다.

| 권한 | 없으면 |
|---|---|
| 손쉬운 사용 | 접근성 읽기, 클릭·입력, ESC 중지 감지가 막힌다. 패널의 중지 버튼은 동작한다 |
| 화면 및 시스템 오디오 녹음(화면 기록) | 화면 캡처가 실패한다 |

- `desk permissions`로 상태를 보고, `desk permissions --request`로 시스템 승인 창을 띄운다. 한 번 거부하면 macOS는 승인 창을 다시 띄우지 않으므로 시스템 설정에서 직접 켠다.
- 켠 뒤에는 터미널 앱을 완전히 종료하고 다시 실행해야 적용된다.
- `begin`은 빠진 권한을 경고한다. `doctor`도 권한 상태를 함께 보인다.
- 켜져 있는데도 실패하면 `tccutil reset Accessibility <번들ID>`로 초기화하고 다시 승인받는다.

## 선택 구성 요소

아이콘 검출기:

```bash
/opt/homebrew/bin/python3.12 -m venv ~/.cache/desk/omni
~/.cache/desk/omni/bin/pip install torch torchvision pillow numpy huggingface_hub
~/.cache/desk/omni/bin/python -I -c "from huggingface_hub import hf_hub_download as d; d('microsoft/OmniParser-v2.0','icon_detect_v3/model.pt',revision='refs/pr/37',local_dir='$HOME/.cache/desk/omni/weights')"
```

`desk ask` 비교용 MLX 비전 모델(없으면 비교만 빠진다):

```bash
/opt/homebrew/bin/python3.12 -m venv ~/.cache/desk/llm
~/.cache/desk/llm/bin/pip install mlx-vlm
~/.cache/desk/llm/bin/python -I -c "from huggingface_hub import snapshot_download as d; d('mlx-community/Qwen3.5-4B-4bit')"
```

## 구현 규칙

- 커서 이동은 `widget.json`의 속도와 8ms 프레임을 쓴다. 거리와 무관한 고정 이동 시간이나 클릭 전 대기는 없다.
- 클릭이 끝나면 커서 중심에서 원이 퍼진다. 드래그는 놓는 위치에, 보조 모니터에서는 그 화면에 표시한다.
- 같은 창인지는 프레임과 `pid:창ID`(AX 창의 `_AXUIElementGetWindow`, 없으면 CGWindow 번호)로 판단한다. 제목은 사람이 읽는 용도다(`desk`의 `win_key`, `desk-click.py`의 `window_key`, helper의 `windowKey`). 웹 앱이 제목을 스스로 바꿔도 같은 창으로 보고, 같은 프레임에 겹친 다른 창은 창 ID로 구분한다.
- Chrome 웹 접근성은 같은 좌표에서 버튼, 페이지 전체 AXGroup, 없음 사이를 오간다. `holdClick`은 같은 조상 줄의 요소를 같은 대상으로 보고 가장 좁은 것을 유지하며, 그 요소의 정체·위치·활성 상태를 확인한다.
- 키를 뗄 때와 수정키 해제(flagsChanged)는 대상 검사 없이 항상 보낸다. 검사에서 중단돼도 ⌘ 같은 수정키가 눌린 채 남지 않게 하기 위해서다.

## SSH로 접속한 셸에서 쓸 때

SSH 셸은 콘솔 GUI 세션 밖이라 화면 권한을 받을 수 없다(`화면 캡처 실패`, `could not create image from rect`). 권한을 더 줘도 해결되지 않는다.

- `SSH_CONNECTION`이 있거나 위 오류가 나면 `desk` 대신 `desk-ssh run '<desk 명령들>' [초]`로 실행한다. 콘솔에 로그인된 Terminal.app에 상주 워커를 띄워 GUI 세션 안에서 같은 명령을 실행하고, 출력과 종료 코드를 그대로 돌려준다. 워커 안에 `desk` 함수가 정의돼 있어 명령 묶음은 평소처럼 쓴다.
  ```bash
  desk-ssh run "desk begin '요청 요약' && desk say '하는 일' && desk open 'Google Chrome'" 120
  ```
- `desk install`이 `~/.local/bin/desk-ssh`를 만든다. 없으면 `bash <스킬 폴더>/desk-ssh`로 부른다.
- 워커는 첫 `run`에서 자동으로 뜬다. 작업이 끝나면(중지·실패로 끝나도) `desk end` 뒤 `desk-ssh stop`까지 한다. `stop`은 워커가 띄운 Terminal 창만 닫는다. 상태는 `desk-ssh status`, 파일은 `~/.cache/desk/ssh/`.
- 조건: 그 Mac 콘솔에 사용자가 로그인해 있고, Terminal.app에 화면 기록·손쉬운 사용이 켜져 있어야 한다.
- 조작은 그 Mac의 모니터에서 일어난다. 사용자가 그 화면을 보고 있는지(유니버설 컨트롤, 화면 공유, 옆 모니터) 확인하고 진행한다. 유니버설 컨트롤로 커서가 넘어와 있으면 사용자 개입으로 감지돼 종료 코드 6이 날 수 있다.

## 줄바꿈

`desk`와 `desk-ssh`는 LF여야 한다. CRLF가 되면 `$'in\r'` 문법 오류로 바로 멈춘다. 레포의 `.gitattributes`가 LF로 맞춘다.
