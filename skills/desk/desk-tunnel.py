#!/usr/bin/env python3
"""Session-scoped desk RPC over OpenSSH reverse Unix sockets (macOS/Linux)."""
import argparse
import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import socket
import socketserver
import struct
import subprocess
import sys
import tempfile
import threading

if os.name == "nt":
    sys.exit("desk-client: 이 SSH 중계 실행기는 macOS·Linux용입니다")
import fcntl

ROOT = Path(__file__).resolve().parent
LIMIT = 64 * 1024 * 1024
COMMANDS = set("help doctor status begin say end stop handoff see shot look front read find wait-for click-text click-el click dclick rclick drag scroll touch-scroll move key type sequence open windows ask ask-ref ask-report".split())


def receive(sock):
    def exact(size):
        data = bytearray()
        while len(data) < size:
            part = sock.recv(size - len(data))
            if not part:
                raise ConnectionError("desk 터널 연결이 끊겼습니다")
            data.extend(part)
        return data
    size = struct.unpack("!I", exact(4))[0]
    if size > LIMIT:
        raise ValueError("desk 터널 응답 크기 제한 초과")
    return json.loads(exact(size))


def send(sock, data):
    payload = json.dumps(data, ensure_ascii=False).encode()
    if len(payload) > LIMIT:
        raise ValueError("desk 터널 응답 크기 제한 초과")
    sock.sendall(struct.pack("!I", len(payload)) + payload)


def execute(args, driver=None):
    if not isinstance(args, list) or not args or len(args) > 256 or any(not isinstance(a, str) or "\0" in a or len(a) > 100000 for a in args):
        raise ValueError("desk 명령 인수가 올바르지 않습니다")
    command = args[1] if args[0] == "-q" and len(args) > 1 else args[0]
    if command == "bridge-info":
        return dict(code=0, output=f"대상 클라이언트: {socket.gethostname()} ({sys.platform})\n", images={})
    if command not in COMMANDS:
        raise ValueError("터널에서는 화면 작업 명령만 실행할 수 있습니다")
    env = dict(os.environ, DESK_AUTO_UPDATE="0")
    result = subprocess.run([*(driver or [sys.executable, str(ROOT / "desk.py")]), *args],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    output = result.stdout.decode("utf-8", errors="replace")
    # Only exported, redacted screenshots referenced by desk's output are sent.
    # Never expose raw captures, arbitrary paths or the complete state directory.
    shots_path = (Path(os.environ.get("DESK_STATE", Path.home() / ".cache/desk")) / "shots").absolute()
    shots = shots_path.resolve()
    images = {}
    prefixes = "(?:" + "|".join(re.escape(str(p)) for p in {shots_path, shots}) + ")"
    for match in re.finditer(prefixes + r"/[^\s\x1b\"'<>]+\.png", output):
        path = Path(match.group())
        if path.is_file() and not path.is_symlink() and path.resolve().parent == shots:
            if path.stat().st_size <= 12 * 1024 * 1024:
                images[str(path)] = base64.b64encode(path.read_bytes()).decode()
    return dict(code=result.returncode, output=output, images=images)


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            args = receive(self.request)
            # Concurrent requests must not interleave screen evidence and actions.
            # Refuse a second action; stop may interrupt the currently running one.
            stop = args == ["stop"]
            acquired = False if stop else self.server.action_lock.acquire(blocking=False)
            if not stop and not acquired:
                send(self.request, dict(code=2, output="다른 desk 명령이 실행 중입니다. 완료 후 다시 요청하세요.\n", images={}))
                return
            try:
                reply = execute(args)
            finally:
                if acquired:
                    self.server.action_lock.release()
            send(self.request, reply)
        except (ValueError, OSError, ConnectionError) as exc:
            try:
                send(self.request, dict(code=2, output=f"desk-client: {exc}\n", images={}))
            except OSError:
                pass


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


def route_path(directory):
    key = hashlib.sha256(os.fsencode(str(Path(directory).resolve()))).hexdigest()
    return Path.home() / ".cache/desk-client/routes" / (key + ".json")


@contextmanager
def route_lock(route):
    with route.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def cleanup(route, socket_path):
    import shutil
    route = Path(route)
    with route_lock(route):
        if route.exists() and json.loads(route.read_text()).get("socket") == socket_path:
            route.unlink()
    shutil.rmtree(Path(socket_path).parent, ignore_errors=True)


def current_route():
    for directory in [Path.cwd(), *Path.cwd().parents]:
        candidate = route_path(directory)
        if candidate.is_file():
            return json.loads(candidate.read_text())
    return None


class UnavailableClient(ConnectionError):
    pass


def auto(args):
    # A connected visible client takes priority, even on a server with a monitor.
    if current_route() is not None:
        try:
            return client(args)
        except UnavailableClient:
            pass
    local = ROOT / "desk-local"
    if not local.exists():
        raise ValueError("연결된 클라이언트와 이 기기의 로컬 desk 실행기가 없습니다")
    os.execv(str(local), [str(local), *args])


def client(args):
    route = current_route()
    if route is None:
        raise ValueError("이 프로젝트에 연결된 클라이언트가 없습니다. 클라이언트에서 vs/wh로 다시 접속하세요")
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(600)
        try:
            sock.connect(route["socket"])
        except (FileNotFoundError, ConnectionRefusedError) as exc:
            raise UnavailableClient("클라이언트 연결이 종료됐습니다") from exc
        send(sock, args or ["bridge-info"])
        reply = receive(sock)
    output = reply["output"]
    cache = Path.home() / ".cache/desk-client/shots"
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    for original, data in reply["images"].items():
        local = cache / (hashlib.sha256(original.encode()).hexdigest()[:16] + ".png")
        local.write_bytes(base64.b64decode(data, validate=True))
        local.chmod(0o600)
        output = output.replace(original, str(local))
    for old in sorted(cache.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)[60:]:
        old.unlink(missing_ok=True)
    print(output, end="")
    return reply["code"]


def bootstrap(payload):
    """Runs on the server, with all data read from SSH stdin, never shell text."""
    base = Path.home() / ".local/share/desk-client"
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name, data in payload["files"].items():
        if name not in {"desk-tunnel.py", "SKILL.md"}:
            raise ValueError("잘못된 설치 파일")
        target = base / name
        target.write_bytes(base64.b64decode(data))
    native_skill = base / "native"
    native_skill.mkdir(exist_ok=True)
    (native_skill / "SKILL.md").write_text((base / "SKILL.md").read_text().replace("name: desk-client\n", "name: desk\n", 1))
    launcher = Path.home() / ".local/bin/desk-client"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    text = '#!/bin/sh\nexec python3 "$HOME/.local/share/desk-client/desk-tunnel.py" client "$@"\n'
    if launcher.exists() and launcher.read_text() != text:
        raise ValueError("기존 desk-client 실행기를 덮어쓰지 않습니다")
    launcher.write_text(text)
    launcher.chmod(0o700)
    desk = launcher.with_name("desk")
    dispatcher = '#!/bin/sh\n# desk SSH client dispatcher\nexec python3 "$HOME/.local/share/desk-client/desk-tunnel.py" auto "$@"\n'
    if not desk.exists() or desk.read_bytes() != dispatcher.encode():
        if desk.exists():
            import shutil
            local = base / "desk-local"
            if local.is_symlink() or local.exists():
                local.unlink()
            if desk.is_symlink():
                local.symlink_to(desk.resolve())
            else:
                shutil.copy2(desk, local)
        if desk.is_symlink():
            desk.unlink()
        desk.write_text(dispatcher)
        desk.chmod(0o700)
    for agent in [".codex", ".claude", ".agents"]:
        link = Path.home() / agent / "skills/desk-client"
        link.parent.mkdir(parents=True, exist_ok=True)
        if not link.exists() and not link.is_symlink():
            link.symlink_to(base, target_is_directory=True)
        # Servers without a native desk installation can discover /desk as well.
        native = link.with_name("desk")
        if native.is_symlink() and native.resolve() == base.resolve():
            native.unlink()
        if not native.exists() and not native.is_symlink():
            native.symlink_to(native_skill, target_is_directory=True)
    session = Path(tempfile.mkdtemp(prefix="desk-", dir="/tmp"))
    route = route_path(Path(payload["directory"]).resolve())
    route.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # The most recent connection to a workspace is its visible client.
    value = dict(socket=str(session / "s"))
    temp = route.with_name(route.name + "." + session.name)
    temp.write_text(json.dumps(value))
    temp.chmod(0o600)
    with route_lock(route):
        os.replace(temp, route)
    print(json.dumps(dict(socket=value["socket"], route=str(route))))


def connect(server, directory, check=False, remote_args=None):
    if sys.platform == "win32":
        raise ValueError("이 Unix 소켓 터널 실행기는 macOS·Linux 클라이언트용입니다")
    ssh = ["ssh", "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=3", "-o", "ConnectTimeout=10"]
    source = Path(__file__).read_text()
    skill = (ROOT / "SKILL.md").read_text()
    # A separate skill makes the target explicit without replacing server desk.
    skill = skill.replace("name: desk\n", "name: desk-client\n", 1)
    skill = skill.replace("description: ", "description: SSH 개발서버에서 접속한 클라이언트 화면을 조작할 때 사용한다. ", 1)
    end = skill.index("---", 3) + 3
    note = "\n\n# 화면 대상 자동 선택\n\n아래 명령은 그대로 **`desk`로 호출**한다. 현재 프로젝트에 연결된 클라이언트가 있으면 그 화면을, 없으면 이 기기의 로컬 desk를 사용한다. 먼저 `desk doctor`로 대상 환경을 확인한다. 연결 클라이언트는 `desk bridge-info`로 확인할 수 있다. 그림 경로는 서버에 자동 복사되므로 해당 경로를 연다. 대상이 바뀌면 새 화면을 읽고 판단하며 이전 대상의 좌표·요소 번호를 재사용하지 않는다. ESC 종료 코드 3을 그대로 지킨다.\n"
    skill = skill[:end] + note + skill[end:]
    payload = dict(directory=directory, files={"desk-tunnel.py": base64.b64encode(source.encode()).decode(), "SKILL.md": base64.b64encode(skill.encode()).decode()})
    # Execute the bootstrap function in its own namespace.
    bootstrap_code = "import sys,json,base64; p=json.load(sys.stdin); ns={'__name__':'desk_bootstrap','__file__':'desk-tunnel.py'}; exec(compile(base64.b64decode(p['files']['desk-tunnel.py']),'desk-tunnel.py','exec'),ns); ns['bootstrap'](p)"
    prep = subprocess.run([*ssh, server, "python3 -c " + shlex.quote(bootstrap_code)], input=json.dumps(payload), text=True, capture_output=True)
    if prep.returncode:
        sys.stderr.write(prep.stderr)
        return prep.returncode
    remote = json.loads(prep.stdout)
    with tempfile.TemporaryDirectory(prefix="desk-", dir="/tmp") as tempdir:
        local_socket = str(Path(tempdir) / "s")
        with Server(local_socket, Handler) as rpc:
            os.chmod(local_socket, 0o600)
            rpc.action_lock = threading.Lock()
            thread = threading.Thread(target=rpc.serve_forever, daemon=True)
            thread.start()
            if check or remote_args:
                command = 'cd -- ' + shlex.quote(directory) + ' && "$HOME/.local/bin/desk" ' + shlex.join(remote_args or ["doctor"])
                tty = []
            else:
                command = "exec \"$SHELL\" -lic " + shlex.quote("cd -- " + shlex.quote(directory) + " && z")
                tty = ["-t"]
            try:
                print(f"[desk] 클라이언트 화면 연결: {socket.gethostname()} → {server}", file=sys.stderr)
                result = subprocess.call([*ssh, "-o", "ExitOnForwardFailure=yes", "-R", remote["socket"] + ":" + local_socket, *tty, server, command])
            finally:
                rpc.shutdown()
                # Remove only this session's route; a newer connection may own it.
                cleanup_command = 'python3 "$HOME/.local/share/desk-client/desk-tunnel.py" cleanup ' + shlex.quote(remote["route"]) + " " + shlex.quote(remote["socket"])
                try:
                    cleaned = subprocess.run([*ssh, "-o", "BatchMode=yes", server, cleanup_command], stdout=subprocess.DEVNULL, timeout=15)
                    if cleaned.returncode:
                        print("[desk] 서버의 연결 기록 정리 실패. 터널은 종료됐습니다.", file=sys.stderr)
                except (OSError, subprocess.TimeoutExpired):
                    print("[desk] 서버의 연결 기록 정리 실패. 터널은 종료됐으며 화면 조작은 연결 오류로 거부됩니다.", file=sys.stderr)
    return result


def main():
    if sys.argv[1:2] == ["auto"]:
        return auto(sys.argv[2:])
    if sys.argv[1:2] == ["cleanup"]:
        cleanup(*sys.argv[2:])
        return 0
    if sys.argv[1:2] == ["client"]:
        return client(sys.argv[2:])
    parser = argparse.ArgumentParser(description="SSH 접속 동안 클라이언트 desk를 개발서버에 연결")
    parser.add_argument("server")
    parser.add_argument("directory")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--run", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    return connect(args.server, args.directory, args.check, args.run)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, ConnectionError, subprocess.TimeoutExpired) as exc:
        sys.exit(f"desk-client: {exc}")
