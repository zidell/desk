#!/usr/bin/env python3
"""Patch official releases before begin, then load the backend in a fresh process.

Locks and state stay in the local cache. Only copied skills need a temporary
sibling directory for a same-filesystem swap. Plugin caches belong to Claude.
"""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

REPO = "https://github.com/zidell/desk.git"
URL = "https://raw.githubusercontent.com/zidell/desk/main/skills/desk/VERSION"
INTERVAL = 3600
RETRY = 300
LIMIT = 64 * 1024 * 1024


class Deferred(Exception):
    pass


def version(text):
    parts = text.strip().split(".")
    if len(parts) != 3 or not all(p.isascii() and p.isdigit() for p in parts):
        raise ValueError("잘못된 버전 번호")
    return tuple(int(p) for p in parts)


def load(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as f:
        temporary = Path(f.name)
        json.dump(data, f, ensure_ascii=False)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextlib.contextmanager
def lock(path):
    """An OS lock is released on process death; never unlink its inode."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as f:
        f.seek(0, 2)
        if not f.tell():
            f.write(b"0"); f.flush()
        deadline = time.monotonic() + 30
        while True:
            try:
                if sys.platform == "win32":
                    import msvcrt
                    f.seek(0); msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise Deferred("다른 업데이트가 실행 중입니다. 잠시 후 다시 실행하세요")
                time.sleep(.1)
        try:
            yield
        finally:
            if sys.platform == "win32":
                f.seek(0); msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f, fcntl.LOCK_UN)


def active(state):
    """Inspect desk's own panel/session only."""
    try:
        pid = int((state / "overlay.pid").read_text().strip())
        if pid > 0:
            if sys.platform == "win32":
                import ctypes
                handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
                if handle:
                    ctypes.windll.kernel32.CloseHandle(handle)
                    return True
            else:
                os.kill(pid, 0)
                return True
    except (OSError, ValueError):
        pass
    if sys.platform.startswith("linux"):
        try:
            import fcntl
            with (state / "linux-session.lock").open("r") as session:
                try:
                    fcntl.flock(session, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return True
        except OSError:
            pass
    return False


def plugin(root):
    parts = root.parts
    for i in range(len(parts) - 4):
        if parts[i:i+2] == ("plugins", "cache"):
            if plugin_host(root) == "codex":
                return parts[i+2], root
            stable = Path(*parts[:i+1]) / "marketplaces" / parts[i+2] / Path(*parts[i+5:])
            return parts[i+2], stable
        if parts[i:i+2] == ("plugins", "marketplaces"):
            return parts[i+2], root
    return None


def plugin_host(root):
    """Support default and explicitly configured Codex homes, including tests."""
    parts = root.parts
    for i in range(len(parts) - 2):
        if parts[i:i+2] in (("plugins", "cache"), ("plugins", "marketplaces")):
            home = Path(*parts[:i])
            codex = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).resolve()
            if home.resolve() == codex or home.name == ".codex" or (home / "config.toml").is_file():
                return "codex"
            return "claude"
    return None


def run(argv, cwd=None, timeout=20):
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_SSH_COMMAND": "ssh -oBatchMode=yes"}
    return subprocess.run([str(x) for x in argv], cwd=cwd, env=env,
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout, check=True).stdout.strip()


def git(root, *args, timeout=20):
    return run(["git", "-C", root, *args], timeout=timeout)


def official_origin(origin, shorthand=False):
    origin = origin.rstrip("/").removesuffix(".git")
    allowed = {REPO.removesuffix(".git")}
    # Keep installed clones and plugin sources from before the rename working.
    for name in ("desk", "desk-skill"):
        allowed.update(("https://github.com/zidell/" + name,
                        "git@github.com:zidell/" + name,
                        "ssh://git@github.com/zidell/" + name))
        if shorthand:
            allowed.add("zidell/" + name)
    return origin in allowed


def clean_repo(root):
    repo = root.parent.parent
    if not (repo / ".git").exists():
        raise Deferred("공식 git 설치가 아닙니다")
    origin = git(repo, "remote", "get-url", "origin").rstrip("/").removesuffix(".git")
    if not official_origin(origin):
        raise Deferred("fork 또는 다른 원격 저장소는 자동으로 바꾸지 않습니다")
    if git(repo, "branch", "--show-current") != "main" or git(repo, "status", "--porcelain"):
        raise Deferred("로컬 수정 또는 별도 브랜치가 있어 자동 패치를 보류합니다")
    return repo


def git_update(root, latest):
    repo = clean_repo(root)
    # Fetch the official release tag, ignoring local remote fetch rules.
    git(repo, "fetch", "--no-tags", REPO, "refs/tags/v" + latest)
    commit = git(repo, "rev-parse", "FETCH_HEAD^{commit}")
    manifest = json.loads(git(repo, "show", commit + ":.claude-plugin/plugin.json"))
    if git(repo, "show", commit + ":skills/desk/VERSION").strip() != latest or manifest.get("version") != latest:
        raise ValueError("배포 태그의 버전이 일치하지 않습니다")
    try:
        git(repo, "merge-base", "--is-ancestor", "HEAD", commit)
    except subprocess.CalledProcessError:
        raise Deferred("로컬 커밋이 배포본과 달라 자동 패치를 보류합니다") from None
    clean_repo(root)  # a developer may have started editing during network I/O
    git(repo, "-c", "core.hooksPath=" + os.devnull, "merge", "--ff-only", commit, timeout=None)


def download(url):
    deadline = time.monotonic() + 15
    chunks = []
    size = 0
    with urllib.request.urlopen(url, timeout=5) as response:
        while True:
            chunk = response.read(65536)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > LIMIT or time.monotonic() > deadline:
                raise ValueError("배포 다운로드 크기 또는 시간 제한을 넘었습니다")
    return b"".join(chunks)


def release_files(number):
    version(number)
    data = download("https://codeload.github.com/zidell/desk/zip/refs/tags/v" + number)
    files = {}
    manifest = None
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        total = 0
        for item in archive.infolist():
            path = PurePosixPath(item.filename)
            if path.is_absolute() or ".." in path.parts or "\\" in item.filename:
                raise ValueError("배포 파일에 잘못된 경로가 있습니다")
            if path.parts[1:] == (".claude-plugin", "plugin.json"):
                if item.file_size > 65536:
                    raise ValueError("배포 매니페스트 크기 제한을 넘었습니다")
                manifest = json.loads(archive.read(item))
            if len(path.parts) < 4 or path.parts[1:3] != ("skills", "desk") or item.is_dir():
                continue
            if stat.S_ISLNK(item.external_attr >> 16):
                raise ValueError("배포 파일에 심볼릭 링크가 있습니다")
            total += item.file_size
            if total > LIMIT:
                raise ValueError("배포 파일 크기 제한을 넘었습니다")
            relative = Path(*path.parts[3:])
            if relative in files or ":" in str(relative):
                raise ValueError("배포 파일에 중복 또는 잘못된 경로가 있습니다")
            files[relative] = archive.read(item)
    if files.get(Path("VERSION"), b"").decode("ascii").strip() != number or not manifest or manifest.get("version") != number:
        raise ValueError("배포 파일의 버전이 일치하지 않습니다")
    if not all(Path(name) in files for name in ("desk.py", "desk", "desk-update.py", "SKILL.md")):
        raise ValueError("불완전한 배포 파일입니다")
    return files


def normalized(path, data):
    return data.replace(b"\r\n", b"\n") if path.suffix in (".cmd", ".ps1") else data


def matches(root, expected):
    actual = {}
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if "__pycache__" in relative.parts or path.name == ".DS_Store":
            continue
        if path.is_symlink():
            return False
        if path.is_file():
            actual[relative] = normalized(relative, path.read_bytes())
    return actual == {p: normalized(p, data) for p, data in expected.items()}


def copy_update(root, current, latest):
    previous = release_files(current)
    if not matches(root, previous):
        raise Deferred("스킬 사본에 로컬 변경이 있어 자동 패치를 보류합니다")
    incoming = release_files(latest)
    # Same-parent staging is required even when the skill is on another volume.
    directory = Path(tempfile.mkdtemp(prefix=".desk-update-", dir=root.parent))
    stage = directory / "new"
    backup = directory / "old"
    try:
        stage.mkdir()
        for relative, data in incoming.items():
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            if relative.name in ("desk", "desk-ssh", "desk.py"):
                target.chmod(0o755)
        if not matches(root, previous):
            raise Deferred("확인 중 스킬 사본이 바뀌어 자동 패치를 보류합니다")
        try:
            relative_cwd = Path.cwd().relative_to(root)
        except ValueError:
            relative_cwd = None
        if relative_cwd is not None:
            os.chdir(root.parent)
        try:
            root.rename(backup)
            try:
                stage.rename(root)
            except BaseException:
                backup.rename(root)
                raise
        finally:
            if relative_cwd is not None and root.exists():
                destination = root / relative_cwd
                os.chdir(destination if destination.is_dir() else root)
    finally:
        # Never delete the only remaining original if rollback itself failed.
        if root.exists() or not backup.exists():
            shutil.rmtree(directory)


def plugin_metadata(root, latest):
    marketplace, stable = plugin(root)
    executable = shutil.which("claude")
    if executable:
        try:
            entries = json.loads(run([executable, "plugin", "list", "--json"]))
            entries = entries if isinstance(entries, list) else entries.get("plugins", [])
            scopes = {p.get("scope") for p in entries if p.get("id") == "desk@" + marketplace}
            if len(scopes) != 1 or not scopes <= {"user", "project", "local"}:
                raise Deferred("플러그인 설치 범위를 확정하지 못했습니다")
            run([executable, "plugin", "update", "desk@" + marketplace, "--json", "--scope", scopes.pop()])
            installed = json.loads(run([executable, "plugin", "list", "--json"]))
            installed = installed if isinstance(installed, list) else installed.get("plugins", [])
            selected = [p for p in installed if p.get("id") == "desk@" + marketplace]
            if not selected or any(p.get("version") != latest for p in selected):
                raise Deferred("플러그인 캐시가 새 버전을 반영하지 않았습니다")
            for entry in selected:
                cache = Path(entry.get("installPath", ""))
                if (cache / "skills/desk/VERSION").read_text().strip() != latest or load(cache / ".claude-plugin/plugin.json").get("version") != latest:
                    raise Deferred("플러그인 캐시 검증 실패")
            print("desk: 플러그인 지침 갱신은 Claude Code를 다시 시작하면 반영됩니다", flush=True)
            return True
        except (OSError, ValueError, subprocess.SubprocessError, Deferred):
            print("desk: 실행 코드 패치는 완료했습니다. 플러그인 지침 갱신은 다음 확인 때 재시도합니다", flush=True)
    else:
        print("desk: 실행 코드 패치는 완료했습니다. 플러그인 지침 갱신에는 claude 명령이 필요합니다", flush=True)
    return False


def plugin_update(root, latest):
    if plugin_host(root) == "codex":
        return codex_update(root, latest), False
    _, stable = plugin(root)
    # Patch the stable runtime first. CLI failure must not undo a runtime patch
    # or tamper with Claude's managed cache.
    git_update(stable, latest)
    return stable, not plugin_metadata(stable, latest)


def refresh_plugin_links(previous, current):
    """Move only installer-owned links; never rebuild a running Windows launcher."""
    paths = {Path.home() / folder / "desk" for folder in
             (".codex/skills", ".agents/skills", ".claude/skills")}
    for link in paths:
        try:
            original = os.readlink(link)
        except OSError:
            continue
        if link.resolve() != previous.resolve():
            continue

        def create(target):
            if sys.platform == "win32" and not link.is_symlink():
                run(["cmd", "/c", "mklink", "/J", link, target])
            else:
                link.symlink_to(target, target_is_directory=True)

        if sys.platform == "win32" and not link.is_symlink():
            os.rmdir(link)
        else:
            link.unlink()
        try:
            create(current)
        except BaseException:
            create(original)
            raise


def codex_update(root, latest):
    marketplace, _ = plugin(root)
    executable = shutil.which("codex")
    if not executable:
        raise Deferred("Codex 플러그인 갱신에는 codex 명령이 필요합니다")
    if not matches(root, release_files((root / "VERSION").read_text().strip())):
        raise Deferred("Codex 플러그인에 로컬 변경이 있어 자동 패치를 보류합니다")
    entries = json.loads(run([executable, "plugin", "list", "--marketplace", marketplace, "--json"]))
    selected = [p for p in entries.get("installed", []) if p.get("pluginId") == "desk@" + marketplace]
    if len(selected) != 1:
        raise Deferred("Codex 플러그인 설치를 확인하지 못했습니다")
    source = selected[0].get("marketplaceSource", {})
    origin = source.get("source", "").rstrip("/").removesuffix(".git")
    if source.get("sourceType") != "git" or not official_origin(origin, shorthand=True):
        raise Deferred("로컬 마켓플레이스 또는 fork는 자동으로 바꾸지 않습니다")
    run([executable, "plugin", "marketplace", "upgrade", marketplace, "--json"], timeout=60)
    installed = json.loads(run([executable, "plugin", "add", "desk@" + marketplace, "--json"], timeout=60))
    if installed.get("pluginId") != "desk@" + marketplace or version(installed.get("version", "")) < version(latest):
        raise Deferred("Codex 플러그인이 새 버전을 반영하지 않았습니다")
    cache = Path(installed["installedPath"])
    current = cache / "skills" / "desk"
    number = (current / "VERSION").read_text().strip()
    if number != installed["version"] or load(cache / ".codex-plugin/plugin.json").get("version") != number:
        raise Deferred("Codex 플러그인 캐시 검증 실패")
    refresh_plugin_links(root, current)
    print("desk: 플러그인 지침 갱신은 Codex의 새 대화에서 반영됩니다", flush=True)
    return current


def prepare(root, state, record, force=False):
    data = load(record)
    states = {str(state), str(Path.home() / ".cache" / "desk"), *data.get("states", [])}
    states = {p for p in states if p == str(state) or active(Path(p))}
    data["states"] = sorted(states)
    if any(active(Path(p)) for p in states):
        if force:
            print("desk: 제어 세션이 실행 중입니다. desk end 후 업데이트하세요", flush=True)
            data["error"] = "active"
        save(record, data)
        return root
    if not force and (os.environ.get("DESK_AUTO_UPDATE") == "0" or os.environ.get("DESK_UPDATE_CHECK") == "0"):
        save(record, data)
        return root
    if not force and time.time() < data.get("next", 0):
        save(record, data)
        return root
    try:
        current = (root / "VERSION").read_text(encoding="utf-8").strip()
        latest = download(URL).decode("ascii").strip()
        if version(latest) > version(current):
            if plugin(root):
                root, data["plugin_pending"] = plugin_update(root, latest)
            elif (root.parent.parent / ".git").exists():
                git_update(root, latest)
            else:
                copy_update(root, current, latest)
            print("desk: " + current + " → " + latest + " 자동 패치 완료", flush=True)
        elif force:
            print("desk: 현재 버전 " + current + " (새 배포본 없음)", flush=True)
        if plugin(root) and data.get("plugin_pending") and version(latest) <= version(current):
            data["plugin_pending"] = not plugin_metadata(root, current)
        data.update(next=time.time() + (RETRY if data.get("plugin_pending") else INTERVAL), error=None)
    except (OSError, ValueError, subprocess.SubprocessError, zipfile.BadZipFile, Deferred) as error:
        # Never echo subprocess output, which may contain credentials.
        message = str(error) if isinstance(error, Deferred) else "다운로드 또는 적용 실패"
        if not root.is_dir():
            raise Deferred("스킬 경로 복구가 필요합니다. 같은 부모의 .desk-update-*/old에 원본을 보존했습니다") from error
        print("desk: 자동 패치 보류 — " + message + ". 현재 버전으로 실행하며 5분 후 다시 확인합니다", flush=True)
        data.update(next=time.time() + RETRY, error=message)
    save(record, data)
    return root


def bootstrap(root, args):
    state = Path(os.environ.get("DESK_STATE", Path.home() / ".cache" / "desk")).resolve()
    source = plugin(root)
    # Install already connects launchers to the stable marketplace clone. Direct
    # invocation from an old plugin cache uses that same runtime, too.
    if source and (source[1] / "desk.py").is_file():
        root = source[1].resolve()
    identity = root
    if plugin_host(root) == "codex":
        # Versions share the same lock/throttle, even while the host replaces its cache.
        parts = root.parts
        for i in range(len(parts) - 4):
            if parts[i:i+2] == ("plugins", "cache"):
                identity = Path(*parts[:i+4])
                break
    key = hashlib.sha256(str(identity).encode()).hexdigest()[:20]
    directory = Path.home() / ".cache" / "desk" / "updates" / key
    force = (args[1] if args[0] == "-q" and len(args) > 1 else args[0]) == "update"
    with lock(directory / "lock"):
        root = prepare(root, state, directory / "status.json", force)
        if force:
            return 1 if load(directory / "status.json").get("error") else 0
        env = {**os.environ, "DESK_UPDATE_READY": str(root)}
        # Keep the lock until begin has created its panel. Stop/end and ordinary
        # commands never acquire it, so the kill switch is not delayed.
        return subprocess.call([sys.executable, "-X", "utf8", str(root / "desk.py"), *args], env=env)


def main(argv):
    if len(argv) < 3 or argv[0] != "bootstrap":
        print("사용법: desk-update.py bootstrap ROOT begin|update [인수]", file=sys.stderr)
        return 2
    try:
        return bootstrap(Path(argv[1]).resolve(), argv[2:])
    except (OSError, Deferred) as error:
        print("desk: 업데이트 준비 실패 — " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
