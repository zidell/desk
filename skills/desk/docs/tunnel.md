# SSH 개발서버에서 접속 클라이언트의 desk 실행

사용자가 중계 구성을 명시적으로 요청했을 때 설치한다. 일반 화면 작업에서 연결이 없다는 이유만으로 만들지 않는다.

## 연결

macOS·Linux의 **로컬 GUI 터미널**에서 실행한다. 클라이언트에 desk와 Python 3, OpenSSH가 필요하고, 서버에는 Python 3와 Unix 소켓 역방향 전달을 허용하는 SSH가 필요하다. 서버의 OS는 Linux·macOS 모두 가능하다. Windows 클라이언트는 이 실행기의 지원 대상이 아니다.

```sh
python3 ~/.codex/skills/desk/desk-tunnel.py user@server /absolute/project/path
```

접속 후 해당 프로젝트에서 기존 `z` 실행기를 시작한다. Zellij를 쓰지 않는 서버에서는 `connect()`의 실행 커맨드를 해당 환경에 맞게 통합해야 한다. `--check`는 터널을 열어 클라이언트의 doctor만 실행한 뒤 닫는다. `--run bridge-info`처럼 한 desk 명령을 실행할 수도 있다.

서버에 `~/.local/bin/desk-client`와 에이전트별 `desk-client` 스킬 링크를 설치한다. 서버에 desk 스킬이 없으면 desk 이름으로도 제공하고, 기존 desk 설치는 보존한다. 실행 중인 에이전트는 새 스킬 목록을 읽도록 새 대화를 연다. 기존 대화에서도 절대 경로 `~/.local/bin/desk-client`를 직접 호출할 수 있다.

```sh
desk-client bridge-info
desk-client doctor
desk-client begin '클라이언트 화면 작업'
desk-client say '현재 화면 확인'
desk-client see
# 반환한 서버 그림 경로를 열어 확인한 다음 조작한다.
desk-client end
```

## 수명과 대상

- 역방향 SSH 전달은 접속 동안만 열린다. 클라이언트에 SSH 서버·키·TCP 포트를 추가하지 않는다. 양쪽 소켓은 사용자 전용 임시 디렉터리에 두며 클라이언트 소켓은 0600이다.
- 프로젝트의 절대 경로와 상위 디렉터리로 연결 기록을 찾는다. 기존 Zellij 패널도 환경변수 갱신 없이 재접속한 클라이언트를 찾는다. 같은 프로젝트에는 마지막으로 연결한 클라이언트를 사용한다. 다른 프로젝트는 각각 연결한다. 연결 기록이 없거나 터널이 끊기면 거부하고 서버 화면으로 대체하지 않는다.
- 기존 Zellij 세션의 패널은 붙은 원래 프로젝트 아래에서 명령을 실행한다. 다른 프로젝트로 이동하면 그 프로젝트에 연결된 클라이언트를 찾는다.
- 허용한 desk 명령의 인수만 JSON으로 전달하고 셸로 해석하지 않는다. 설치·자동 갱신·임의 셸 실행 명령을 제공하지 않는다. 연결 중에는 desk 자동 갱신을 끈다.
- 가려서 출력한 `shots/*.png` 중 desk 출력에 명시된 파일만 서버로 복사한다. 원본 캡처나 임의 파일을 읽는 RPC는 없다. 서버 그림 캐시는 최근 60개를 보관한다. 원격 모델 API로 가는 정보 범위는 일반 desk와 같다.
- 명령을 동시에 섞어 실행하지 않는다. 동시에 들어온 조작은 거부하고 `stop`은 진행 중 명령과 별도로 받을 수 있다. ESC·중지 패널·기존 종료 코드·화면 대상 검증은 클라이언트의 desk가 유지한다.
- SSH 단절 시 터널은 사라진다. 이미 클라이언트에서 시작한 단일 명령은 끝날 수 있으므로 그동안도 로컬 ESC로 중지한다. 끊겼다는 이유로 같은 동작을 자동 재시도하지 않는다.

## 검증

화면 조작 없는 회귀는 `python3 skills/desk/tests/test_tunnel.py -v`로 실행한다. OS별 실제 확인 범위는 [validation.md](validation.md)에 적는다.
