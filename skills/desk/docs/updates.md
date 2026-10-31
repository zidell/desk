# 자동 패치

- 자동 패치는 사용자 확정 동작이다(2026-10-31). 이전 알림 전용 구현을 대체한다.
- 세 OS가 `desk-update.py`를 공유한다. `desk.py`는 OS backend import 전에, macOS `desk`는 Bash 본체 실행 전에 updater로 `exec`한다. 패치 후 새 프로세스가 새 코드를 읽는다.
- `begin` 전 한 시간 간격으로 확인한다. 상주 타이머는 없다. `desk update`는 즉시 확인하며 실패·활성 세션이면 종료 코드 1이다. `DESK_AUTO_UPDATE=0`과 기존 `DESK_UPDATE_CHECK=0`은 자동 확인만 끈다.
- 업데이트 상태·설치 경로별 OS 잠금은 `~/.cache/desk/updates/`에 둔다. `DESK_STATE`가 달라도 같은 설치본의 갱신은 직렬화한다. 사용한 상태 폴더를 기록해 다른 상태 폴더에서 살아 있는 패널·세션도 확인한다. Linux는 세션의 OS 잠금으로 확인하며 세션 소켓에 명령을 보내지 않는다.
- 잠금은 begin의 패널 시작이 끝날 때까지 유지한다. `stop`·`end`·일반 명령은 잠금을 기다리지 않는다. 활성 세션 중에는 패치하지 않는다.
- 공식 `VERSION`과 `vX.Y.Z` 태그의 VERSION·plugin.json을 확인한다. git은 공식 origin·main·수정 없음·fast-forward 조건을 모두 확인한 뒤 병합한다. fetch 후 다시 작업트리를 확인하며 강제 reset·stash·rebase를 하지 않는다.
- 실제 스킬 사본은 현재 태그 ZIP과 파일 목록·내용이 같아야 한다. Python 캐시·`.DS_Store`는 비교에서 제외하고 `.cmd`·`.ps1`의 CRLF 차이는 정규화한다. 사본 내부의 심볼릭 링크는 패치 대상에 포함하지 않는다.
- ZIP의 경로 탈출·심볼릭 링크·중복 파일·크기·필수 파일·버전을 검사한다. 같은 파일시스템에서 교체하기 위해 스킬의 형제 임시 폴더에 stage·backup을 둔다. 두 번째 rename 실패 시 원본을 복구하고 임시 폴더를 지운다. 다른 캐시는 소스에 만들지 않는다.
- Claude 플러그인은 기존 설치기가 연결한 고정 marketplace clone을 git으로 패치한다. Windows의 실행 중 `desk.exe` 잠금을 피하기 위해 install·setup·실행기 재빌드는 호출하지 않는다. 이어서 Claude CLI의 설치 목록에서 범위를 확인하고 `plugin update --json --scope …`로 관리 캐시·메타데이터를 갱신한다. 임의 marketplace command를 승인하는 `-y`는 쓰지 않는다. CLI 실패 시에도 성공한 실행 코드 패치는 유지하고 5분 뒤 지침 갱신을 재시도한다. CLI 종료 코드뿐 아니라 설치 목록·캐시의 VERSION·plugin.json도 확인한다. 현재 Claude가 이미 읽은 지침은 재시작 후 바뀐다.
- Codex 플러그인은 별도 `.codex-plugin/plugin.json`과 `.agents/plugins/marketplace.json`으로 배포한다. `requires_local_executor`로 데스크톱 실행을 명시하며, 공개 디렉터리 등록과 GitHub 마켓플레이스 설치를 구분한다. Claude와 같은 `skills/desk`를 쓴다.
- Codex 캐시는 직접 패치하지 않는다. 현재 캐시가 공식 태그와 같은지, `codex plugin list --marketplace … --json`의 설치 항목과 Git 마켓플레이스 원격이 공식 저장소인지 확인한 뒤 `codex plugin marketplace upgrade … --json`·`codex plugin add desk@… --json`을 실행한다. 결과의 플러그인 ID·버전·설치 경로와 새 캐시의 VERSION·Codex 매니페스트를 확인한다. 로컬 마켓플레이스·fork·로컬 수정은 보류한다. 실패하면 기존 버전으로 실행하고 재시도한다.
- Codex 갱신 후 설치기가 만든 스킬 링크 중 이전 버전을 가리키는 링크만 새 캐시로 옮긴다. Windows junction은 로컬에서 교체하고 실행 중인 `desk.exe`는 재빌드하지 않는다. macOS·Linux 실행기도 고정된 `~/.agents/skills/desk` 링크를 거친다. 다른 폴더를 가리키는 링크와 실제 디렉터리는 바꾸지 않는다. 캐시 버전이 달라도 같은 설치의 잠금·확인 주기를 공유한다. 지침은 Codex의 새 대화에서 읽는다.
- Windows Python 진입점은 updater를 `subprocess.call`로 실행해 종료 코드를 보존한다. Windows CRT의 `execv`로 대체하면 부모 프로세스가 0으로 끝나므로 사용하지 않는다. macOS·Linux는 `execv`를 유지한다.
- 다운로드·검증·적용 실패는 현재 코드로 실행하고 5분 뒤 재확인한다. subprocess 출력은 에이전트로 내보내지 않는다. 화면·사용자 데이터는 전송하지 않는다.
- 이전 0.2.1 설치본에는 자동 패치 실행 코드가 없으므로 0.3.0 이상으로 한 번 수동 업데이트해야 한다.

## 재검증

- 화면·입력을 건드리지 않는 공통 회귀: `PYTHONPYCACHEPREFIX=~/.cache/desk/pycache python3 skills/desk/tests/test_update.py -v`.
- 임시 git 원격·태그·worktree, 사본 교체·복원, fork·로컬 수정 보존, 네트워크 실패·재시도, 활성 세션·끄기, 플러그인 CLI 경로, 새 프로세스 재실행·종료 코드를 검사한다.
- 실제 OS에서 실행기→begin 경로와 플러그인 CLI, 사본 교체를 추가 확인한다. 화면 입력 테스트는 AGENTS.md의 별도 동의 규칙을 따른다.
