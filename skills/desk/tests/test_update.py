"""No UI input or external network: exercise real git repos and staged releases."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

SOURCE = Path(__file__).resolve().parents[1]
sys.pycache_prefix = str(Path.home() / ".cache" / "desk" / "pycache")
spec = importlib.util.spec_from_file_location("desk_update", SOURCE / "desk-update.py")
update = importlib.util.module_from_spec(spec)
spec.loader.exec_module(update)
REAL_ACTIVE = update.active


def tree(number):
    return {Path("VERSION"): (number + "\n").encode(), Path("SKILL.md"): b"skill\n",
            Path("desk"): b"#!/bin/bash\n", Path("desk-update.py"): b"# updater\n",
            Path("desk.py"): b'import os,sys\nprint("backend",os.environ.pop("DESK_UPDATE_READY",None),*sys.argv[1:])\nraise SystemExit(6)\n',
            Path("desk.cmd"): b"@echo off\r\n"}


def put(root, files):
    for path, data in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def archive(number, extra=None):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as f:
        for path, content in tree(number).items():
            f.writestr("repo/skills/desk/" + path.as_posix(), content)
        f.writestr("repo/.claude-plugin/plugin.json", json.dumps({"version": number}))
        for name, content in (extra or {}).items():
            f.writestr(name, content)
    return data.getvalue()


class UpdateTests(unittest.TestCase):
    def setUp(self):
        base = Path.home() / ".cache" / "desk" / "update-tests"
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "skill"
        put(self.root, tree("1.0.0"))
        self.state = self.base / "state"
        self.record = self.base / "status.json"
        self.output = io.StringIO()
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {"DESK_AUTO_UPDATE": "1", "DESK_UPDATE_CHECK": "1"}).start()
        patch.object(update, "active", return_value=False).start()

    def releases(self, url):
        if url == update.URL:
            return b"1.0.1\n"
        return archive("1.0.0" if "v1.0.0" in url else "1.0.1")

    def prepare(self, **kw):
        with contextlib.redirect_stdout(self.output):
            return update.prepare(self.root, self.state, self.record, **kw)

    def test_copied_skill_patches_same_path_and_preserves_link(self):
        link = self.base / "link"
        link.symlink_to(self.root, target_is_directory=True)
        with patch.object(update, "download", side_effect=self.releases):
            self.prepare()
        self.assertEqual((link / "VERSION").read_text().strip(), "1.0.1")
        self.assertTrue(os.access(link / "desk", os.X_OK))
        self.assertEqual(list(self.base.glob(".desk-update-*")), [])

    def test_modified_copy_is_not_overwritten(self):
        (self.root / "SKILL.md").write_text("my changes")
        with patch.object(update, "download", side_effect=self.releases):
            self.prepare()
        self.assertEqual((self.root / "SKILL.md").read_text(), "my changes")
        self.assertTrue(update.load(self.record)["error"])

    def test_swap_failure_restores_original(self):
        original = Path.rename
        def fail_stage(path, target):
            if path.name == "new":
                raise PermissionError("fixture: locked directory")
            return original(path, target)
        with patch.object(update, "download", side_effect=self.releases), patch.object(Path, "rename", fail_stage):
            self.prepare()
        self.assertEqual((self.root / "VERSION").read_text().strip(), "1.0.0")
        self.assertTrue(update.matches(self.root, tree("1.0.0")))

    def test_network_failure_retries_in_five_minutes(self):
        with patch.object(update, "download", side_effect=OSError("offline")):
            self.prepare()
        self.assertEqual((self.root / "VERSION").read_text().strip(), "1.0.0")
        self.assertAlmostEqual(update.load(self.record)["next"] - update.time.time(), update.RETRY, delta=2)

    def test_swap_from_inside_skill_reopens_current_directory(self):
        cwd = Path.cwd()
        try:
            os.chdir(self.root)
            with patch.object(update, "download", side_effect=self.releases):
                self.prepare()
            self.assertEqual(Path.cwd(), self.root)
            self.assertEqual((Path.cwd() / "VERSION").read_text().strip(), "1.0.1")
        finally:
            os.chdir(cwd)

    def test_failed_rollback_keeps_backup_instead_of_deleting_original(self):
        original = Path.rename
        def fail_new_and_restore(path, target):
            if path.name in ("new", "old"):
                raise PermissionError("fixture: locked destination")
            return original(path, target)
        with patch.object(update, "download", side_effect=self.releases), patch.object(Path, "rename", fail_new_and_restore):
            with self.assertRaises(update.Deferred): self.prepare()
        backups = list(self.base.glob(".desk-update-*/old/VERSION"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text().strip(), "1.0.0")

    def test_success_is_throttled_and_registers_other_state(self):
        with patch.object(update, "download", side_effect=self.releases) as fetch:
            self.prepare()
            other = self.base / "other-state"
            with contextlib.redirect_stdout(self.output):
                update.prepare(self.root, other, self.record)
            self.assertEqual(fetch.call_count, 3)  # VERSION + two archives, never a second check
            self.assertIn(str(other), update.load(self.record)["states"])

    def test_active_session_and_disable_do_not_access_network(self):
        for disabled in (False, True):
            with self.subTest(disabled=disabled), patch.object(update, "download") as fetch:
                if disabled:
                    with patch.dict(os.environ, {"DESK_AUTO_UPDATE": "0"}): self.prepare()
                else:
                    with patch.object(update, "active", return_value=True): self.prepare()
                fetch.assert_not_called()

    def test_old_opt_out_is_respected(self):
        with patch.dict(os.environ, {"DESK_UPDATE_CHECK": "0"}), patch.object(update, "download") as fetch:
            self.prepare()
            fetch.assert_not_called()

    def test_archive_rejects_traversal_symlink_and_wrong_version(self):
        link = zipfile.ZipInfo("repo/skills/desk/escape")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        fixtures = [archive("1.0.1", {"repo/skills/desk/../../escape": b"bad"}), archive("9.9.9")]
        data = io.BytesIO(archive("1.0.1"))
        with zipfile.ZipFile(data, "a") as f: f.writestr(link, b"/tmp/escape")
        fixtures.append(data.getvalue())
        for fixture in fixtures:
            with self.subTest(), patch.object(update, "download", return_value=fixture):
                with self.assertRaises(ValueError): update.release_files("1.0.1")

    def make_repo(self):
        remote = self.base / "upstream.git"
        work = self.base / "repo"
        subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
        subprocess.run(["git", "init", "-b", "main", str(work)], check=True, capture_output=True)
        for name, value in (("user.email", "fixture@example.invalid"), ("user.name", "Fixture")):
            update.git(work, "config", name, value)
        put(work / "skills/desk", tree("1.0.0"))
        put(work, {Path(".claude-plugin/plugin.json"): b'{"version":"1.0.0"}'})
        update.git(work, "add", "."); update.git(work, "commit", "-m", "old")
        old = update.git(work, "rev-parse", "HEAD")
        put(work / "skills/desk", tree("1.0.1"))
        (work / ".claude-plugin/plugin.json").write_text('{"version":"1.0.1"}')
        update.git(work, "add", "."); update.git(work, "commit", "-m", "new")
        update.git(work, "tag", "v1.0.1")
        update.git(work, "remote", "add", "origin", str(remote))
        update.git(work, "push", "origin", "main", "--tags")
        update.git(work, "reset", "--hard", old)
        return work, remote, old

    def test_git_fast_forward_from_real_tag(self):
        repo, remote, _ = self.make_repo()
        with patch.object(update, "REPO", str(remote)):
            update.git_update(repo / "skills/desk", "1.0.1")
        self.assertEqual((repo / "skills/desk/VERSION").read_text().strip(), "1.0.1")

    def test_git_dirty_fork_branch_and_local_commit_are_preserved(self):
        repo, remote, old = self.make_repo()
        for kind in ("dirty", "untracked", "fork", "branch", "ahead", "detached"):
            with self.subTest(kind=kind), patch.object(update, "REPO", str(remote)):
                update.git(repo, "checkout", "main")
                update.git(repo, "reset", "--hard", old)
                update.git(repo, "clean", "-fd")
                update.git(repo, "remote", "set-url", "origin", str(remote))
                if kind == "dirty": (repo / "skills/desk/SKILL.md").write_text("edited")
                if kind == "untracked": (repo / "mine.txt").write_text("keep")
                if kind == "fork": update.git(repo, "remote", "set-url", "origin", "https://example.invalid/fork.git")
                if kind == "branch": update.git(repo, "checkout", "-B", "feature")
                if kind == "detached": update.git(repo, "checkout", "--detach")
                if kind == "ahead":
                    (repo / "mine.txt").write_text("keep")
                    update.git(repo, "add", "."); update.git(repo, "commit", "-m", "local")
                before = update.git(repo, "rev-parse", "HEAD")
                with self.assertRaises(update.Deferred): update.git_update(repo / "skills/desk", "1.0.1")
                self.assertEqual(update.git(repo, "rev-parse", "HEAD"), before)

    def test_git_worktree_dotgit_file(self):
        repo, remote, old = self.make_repo()
        update.git(repo, "checkout", "--detach")
        target = self.base / "worktree"
        update.git(repo, "worktree", "add", str(target), "main")
        self.assertTrue((target / ".git").is_file())
        with patch.object(update, "REPO", str(remote)):
            update.git_update(target / "skills/desk", "1.0.1")
        self.assertEqual((target / "skills/desk/VERSION").read_text().strip(), "1.0.1")

    def test_plugin_runtime_patched_even_if_cli_metadata_update_fails(self):
        root = self.base / ".claude/plugins/marketplaces/desk-skill/skills/desk"
        with patch.object(update, "git_update") as apply, patch.object(update.shutil, "which", return_value="claude"), patch.object(update, "run", side_effect=OSError("CLI unavailable")):
            with contextlib.redirect_stdout(self.output): result = update.plugin_update(root, "1.0.1")
        apply.assert_called_once_with(root, "1.0.1")
        self.assertEqual(result, (root, True))
        self.assertIn("실행 코드 패치는 완료", self.output.getvalue())

    def test_plugin_scope_and_no_install_or_yes_flag(self):
        root = self.base / ".claude/plugins/marketplaces/desk-skill/skills/desk"
        listing = json.dumps([{"id": "desk@desk-skill", "scope": "user"}])
        cache = self.base / "plugin-cache"
        put(cache, {Path("skills/desk/VERSION"): b"1.0.1", Path(".claude-plugin/plugin.json"): b'{"version":"1.0.1"}'})
        installed = json.dumps([{"id": "desk@desk-skill", "scope": "user", "version": "1.0.1", "installPath": str(cache)}])
        with patch.object(update, "git_update"), patch.object(update.shutil, "which", return_value="claude"), patch.object(update, "run", side_effect=[listing, "{}", installed]) as cli:
            with contextlib.redirect_stdout(self.output): update.plugin_update(root, "1.0.1")
        args = cli.call_args_list[1].args[0]
        self.assertEqual(args[-2:], ["--scope", "user"])
        self.assertNotIn("-y", args)
        self.assertNotIn("install", args)

    def test_real_live_panel_pid_defers_without_reading_screen(self):
        self.state.mkdir()
        (self.state / "overlay.pid").write_text(str(os.getpid()))
        self.assertTrue(REAL_ACTIVE(self.state))
        with patch.object(update, "active", REAL_ACTIVE), patch.object(update, "download") as fetch:
            self.prepare()
        fetch.assert_not_called()

    def test_two_processes_share_one_update_and_check(self):
        # Separate processes use the real OS lock and shared throttle record.
        fixture = self.base / "releases"
        fixture.mkdir()
        (fixture / "1.0.0.zip").write_bytes(archive("1.0.0"))
        (fixture / "1.0.1.zip").write_bytes(archive("1.0.1"))
        script = self.base / "driver.py"
        script.write_text('''import importlib.util,os,pathlib,sys,time
source,root,fixture=sys.argv[1:]
spec=importlib.util.spec_from_file_location("updater",source)
u=importlib.util.module_from_spec(spec);spec.loader.exec_module(u)
def fetch(url):
    if url==u.URL:
        with open(pathlib.Path(fixture)/"checks","a") as f:f.write("check\\n")
        time.sleep(.2)
        return b"1.0.1"
    return (pathlib.Path(fixture)/("1.0.0.zip" if "v1.0.0" in url else "1.0.1.zip")).read_bytes()
u.download=fetch;u.active=lambda state:False;u.subprocess.call=lambda *a,**k:0
raise SystemExit(u.bootstrap(pathlib.Path(root),["begin","fixture"]))
''')
        env = {**os.environ, "HOME": str(self.base), "DESK_STATE": str(self.state)}
        argv = [sys.executable, str(script), str(SOURCE / "desk-update.py"), str(self.root), str(fixture)]
        children = [subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
        for child in children:
            out, err = child.communicate(timeout=10)
            self.assertEqual(child.returncode, 0, (out, err))
        self.assertEqual((fixture / "checks").read_text().splitlines(), ["check"])
        self.assertEqual((self.root / "VERSION").read_text().strip(), "1.0.1")

    def test_mac_bash_exits_before_updater_patches_source(self):
        if sys.platform != "darwin": self.skipTest("macOS Bash entry")
        root = self.base / "bash-entry"
        root.mkdir()
        shutil.copy(SOURCE / "desk", root / "desk")
        (root / "desk-update.py").write_text('import sys\nprint("updater-before-bash-body",*sys.argv[1:])\nraise SystemExit(6)\n')
        result = subprocess.run(["bash", str(root / "desk"), "begin", "테스트"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 6)
        self.assertIn("updater-before-bash-body", result.stdout)

    def test_failed_plugin_metadata_is_retried_when_runtime_already_current(self):
        self.root = self.base / ".claude/plugins/marketplaces/desk-skill/skills/desk"
        put(self.root, tree("1.0.1"))
        update.save(self.record, {"plugin_pending": True, "next": 0})
        with patch.object(update, "download", return_value=b"1.0.1"), patch.object(update, "plugin_metadata", return_value=True) as sync, patch.object(update, "git_update") as apply:
            self.prepare()
        sync.assert_called_once_with(self.root, "1.0.1")
        apply.assert_not_called()
        self.assertFalse(update.load(self.record)["plugin_pending"])

    def test_bootstrap_reloads_updated_backend_and_preserves_exit(self):
        with patch.object(update, "download", side_effect=self.releases), patch.object(Path, "home", return_value=self.base), patch.dict(os.environ, {"DESK_STATE": str(self.state)}):
            with contextlib.redirect_stdout(self.output): result = update.bootstrap(self.root, ["begin", "한글 공백"])
        self.assertEqual(result, 6)
        self.assertEqual((self.root / "VERSION").read_text().strip(), "1.0.1")

    def test_entry_disable_does_not_recurse_or_load_backend_before_update(self):
        root = self.base / "entry"
        root.mkdir()
        shutil.copy(SOURCE / "desk.py", root / "desk.py")
        (root / "desk-update.py").write_text('import sys\nprint("updater-first",*sys.argv[1:])\nraise SystemExit(6)\n')
        result = subprocess.run([sys.executable, str(root / "desk.py"), "begin", "테스트"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 6)
        self.assertIn("updater-first bootstrap", result.stdout)


if __name__ == "__main__":
    unittest.main()
