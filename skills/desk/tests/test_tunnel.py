"""No desktop input: real subprocess argv, socket framing and screenshot export."""
import base64
import importlib.util
import json
import os
from pathlib import Path
import socket
import struct
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("tunnel", Path(__file__).resolve().parents[1] / "desk-tunnel.py")
tunnel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tunnel)


class TunnelTests(unittest.TestCase):
    def test_argv_and_exit_code_preserved(self):
        args = ["type", "한글 ' 공백 ; $(false) `false` \\"]
        reply = tunnel.execute(args, [sys.executable, "-c", "import sys,json; print(json.dumps(sys.argv[1:])); sys.exit(3)"])
        self.assertEqual(json.loads(reply["output"]), args)
        self.assertEqual(reply["code"], 3)

    def test_reject_non_desk_commands(self):
        for args in (["sh", "-c", "true"], ["install"], ["update"], [], ["type", 123]):
            with self.subTest(args=args), self.assertRaises(ValueError):
                tunnel.execute(args)

    def test_only_exported_shots_are_sent(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, DESK_STATE=folder):
            root = Path(folder)
            shots = root / "shots"
            shots.mkdir()
            good = shots / "see.png"
            good.write_bytes(b"redacted image")
            raw = root / "raw.png"
            raw.write_bytes(b"private raw image")
            (shots / "unreferenced.png").write_bytes(b"not in output")
            (shots / "symlink.png").symlink_to(raw)
            output = "\n".join(str(p) for p in (good, raw, shots / "symlink.png"))
            reply = tunnel.execute(["see"], [sys.executable, "-c", "import sys; print(sys.argv[1])", output])
            self.assertEqual(reply["images"], {str(good): base64.b64encode(b"redacted image").decode()})

    def test_fragmented_frame(self):
        left, right = socket.socketpair()
        payload = json.dumps(["type", "한글"]).encode()
        wire = struct.pack("!I", len(payload)) + payload
        def write():
            with right:
                for value in wire:
                    right.sendall(bytes([value]))
        thread = threading.Thread(target=write)
        thread.start()
        with left:
            self.assertEqual(tunnel.receive(left), ["type", "한글"])
        thread.join()

    def test_oversized_frame(self):
        left, right = socket.socketpair()
        with left, right:
            right.sendall(struct.pack("!I", tunnel.LIMIT + 1))
            with self.assertRaises(ValueError):
                tunnel.receive(left)

    def test_client_parent_route_and_image_path(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            project = home / "project"
            project.mkdir()
            child = project / "subfolder"
            child.mkdir()
            with patch.object(Path, "home", return_value=home), patch.object(Path, "cwd", return_value=child):
                route = tunnel.route_path(project)
                route.parent.mkdir(parents=True)
                sockpath = str(home / "rpc")
                route.write_text(json.dumps(dict(socket=sockpath)))
                with socket.socket(socket.AF_UNIX) as listener:
                    listener.bind(sockpath)
                    listener.listen()
                    def respond():
                        connection, _ = listener.accept()
                        with connection:
                            self.assertEqual(tunnel.receive(connection), ["see"])
                            tunnel.send(connection, dict(code=6, output="/client/shots/a.png\n", images={"/client/shots/a.png": base64.b64encode(b"redacted").decode()}))
                    thread = threading.Thread(target=respond)
                    thread.start()
                    from io import StringIO
                    with patch("sys.stdout", new_callable=StringIO) as output:
                        self.assertEqual(tunnel.client(["see"]), 6)
                        local = Path(output.getvalue().strip())
                        self.assertTrue(local.is_relative_to(home))
                        self.assertEqual(local.read_bytes(), b"redacted")
                        self.assertEqual(local.stat().st_mode & 0o777, 0o600)
                    thread.join()

    def test_busy_actions_refused_but_stop_allowed(self):
        with tempfile.TemporaryDirectory() as folder:
            with tunnel.Server(str(Path(folder) / "rpc"), tunnel.Handler) as server:
                server.action_lock = threading.Lock()
                server.action_lock.acquire()
                thread = threading.Thread(target=server.serve_forever)
                thread.start()
                try:
                    with socket.socket(socket.AF_UNIX) as sock:
                        sock.connect(server.server_address)
                        tunnel.send(sock, ["click", "1", "1"])
                        self.assertEqual(tunnel.receive(sock)["code"], 2)
                    with patch.object(tunnel, "execute", return_value=dict(code=3, output="stopped", images={})) as execute:
                        with socket.socket(socket.AF_UNIX) as sock:
                            sock.connect(server.server_address)
                            tunnel.send(sock, ["stop"])
                            self.assertEqual(tunnel.receive(sock)["code"], 3)
                        execute.assert_called_once_with(["stop"])
                finally:
                    server.shutdown()
                    thread.join()

    def test_reconnect_cleanup_preserves_new_route_and_native_skill(self):
        from io import StringIO
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            project = home / "project"
            project.mkdir()
            native = home / ".codex/skills/desk"
            native.mkdir(parents=True)
            (native / "SKILL.md").write_text("existing native skill")
            original = home / ".local/bin/desk"
            original.parent.mkdir(parents=True)
            original.write_text("#!/bin/sh\necho native\n")
            original.chmod(0o700)
            sessions = [home / "s1", home / "s2"]
            for session in sessions:
                session.mkdir(mode=0o700)
            payload = dict(directory=str(project), files={
                "desk-tunnel.py": base64.b64encode(b"source").decode(),
                "SKILL.md": base64.b64encode(b"---\nname: desk-client\n---\n").decode()})
            with patch.object(Path, "home", return_value=home), patch.object(tunnel.tempfile, "mkdtemp", side_effect=[str(s) for s in sessions]), patch("sys.stdout", new_callable=StringIO):
                tunnel.bootstrap(payload)
                tunnel.bootstrap(payload)
                self.assertIn("auto", original.read_text())
                self.assertEqual((home / ".local/share/desk-client/desk-local").read_text(), "#!/bin/sh\necho native\n")
                route = tunnel.route_path(project)
                tunnel.cleanup(str(route), str(sessions[0] / "s"))
                self.assertEqual(json.loads(route.read_text())["socket"], str(sessions[1] / "s"))
                self.assertFalse(sessions[0].exists())
                self.assertEqual((native / "SKILL.md").read_text(), "existing native skill")
                self.assertIn("name: desk\n", (home / ".claude/skills/desk/SKILL.md").read_text())
                tunnel.cleanup(str(route), str(sessions[1] / "s"))
                self.assertFalse(route.exists())

    def test_auto_uses_client_even_with_server_screen(self):
        with patch.object(tunnel, "current_route", return_value={"socket": "connected"}), patch.object(tunnel, "client", return_value=3) as client, patch.dict(os.environ, {"DISPLAY": ":0"}, clear=True):
            self.assertEqual(tunnel.auto(["see"]), 3)
            client.assert_called_once_with(["see"])

    def test_auto_ssh_without_route_uses_native_driver(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            native = root / "desk-local"
            native.write_text("native")
            with patch.object(tunnel, "ROOT", root), patch.object(tunnel, "current_route", return_value=None), patch.dict(os.environ, {"SSH_CONNECTION": "test connection", "DISPLAY": ":0"}, clear=True), patch.object(os, "execv") as execute:
                tunnel.auto(["doctor"])
                execute.assert_called_once_with(str(native), [str(native), "doctor"])

    def test_auto_dead_route_uses_native_but_never_replays_sent_commands(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "desk-local").write_text("native")
            with patch.object(tunnel, "ROOT", root), patch.object(tunnel, "current_route", return_value={"socket": "old"}), patch.object(os, "execv") as execute:
                with patch.object(tunnel, "client", side_effect=tunnel.UnavailableClient()):
                    tunnel.auto(["doctor"])
                    execute.assert_called_once()
                execute.reset_mock()
                with patch.object(tunnel, "client", side_effect=ConnectionError("response lost")):
                    with self.assertRaises(ConnectionError):
                        tunnel.auto(["click", "1", "1"])
                    execute.assert_not_called()

    def test_auto_local_without_ssh_preserves_native_driver(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            native = root / "desk-local"
            native.write_text("native")
            with patch.object(tunnel, "ROOT", root), patch.object(tunnel, "current_route", return_value=None), patch.dict(os.environ, {}, clear=True), patch.object(os, "execv") as execute:
                tunnel.auto(["doctor"])
                execute.assert_called_once_with(str(native), [str(native), "doctor"])


if __name__ == "__main__":
    unittest.main()
