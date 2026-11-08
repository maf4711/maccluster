"""Per-root `includes` allowlist, deny list and `docsync roots add-llm` (collected via test_sync_local).

Remote listings run LISTER_PY under /usr/bin/python3 (3.9, like the peers) and go through parse_lines."""
import ast
import hashlib
import io
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from docsync import manifest
from docsync.config import Config, Root, parse_config, render_config
from docsync.session import run_peer
from docsync.transport import LocalTransport, RemoteTransport, TransportError

FAKE_SSH = str(Path(__file__).resolve().parent / "fake_ssh.py")
PY39 = "/usr/bin/python3"
CLAUDE_INCLUDES = ["CLAUDE.md", "settings.json", "projects/*/memory/**"]

# LISTER_PY as released in 1.2.0: a root without includes must list exactly like this.
LISTER_PY_120 = r'''
import os, sys
root = os.path.expanduser(sys.argv[1])
out = sys.stdout
for dirpath, dirnames, filenames in os.walk(root):
    dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))]
    for f in filenames:
        full = os.path.join(dirpath, f)
        if os.path.islink(full) or not os.path.isfile(full):
            continue
        try:
            st = os.lstat(full)
        except OSError:
            continue
        rel = os.path.relpath(full, root)
        size = -1 if getattr(st, "st_flags", 0) & 0x40000000 else st.st_size
        out.write("%d\t%d\t%s\0" % (int(st.st_mtime), size, rel))
'''

# Prelude for the lister: count directory listings inside <root>/sessions (printed to stderr).
COUNTING_PRELUDE = r'''
import os, sys
_calls = []
_scandir, _listdir = os.scandir, os.listdir
def _count(fn):
    def wrapper(path=".", *a, **k):
        if "/sessions" in os.fspath(path):
            _calls.append(os.fspath(path))
        return fn(path, *a, **k)
    return wrapper
os.scandir, os.listdir = _count(_scandir), _count(_listdir)
import atexit
atexit.register(lambda: sys.stderr.write("CALLS=%d\n" % len(_calls)))
'''


def touch(p: Path, text: str = "x", mtime: int | None = None):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    if mtime is not None:
        os.utime(p, (mtime, mtime))


def run_lister(root: Path, includes=(), prelude: str = "", source: str | None = None, icase: bool = False):
    args = ["icase" if icase else "case", *includes] if includes else []
    return subprocess.run([PY39, "-", str(root), *args], input=prelude + (source or manifest.LISTER_PY),
                          text=True, capture_output=True, timeout=120)


class _Trees(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.base = base
        self.a, self.b, self.state = base / "A" / ".claude", base / "B" / ".claude", base / "state"
        self.a.mkdir(parents=True)
        self.b.mkdir(parents=True)
        self.now = int(time.time()) - 100

    def tearDown(self):
        self.tmp.cleanup()

    def cfg(self, includes=CLAUDE_INCLUDES, path=None):
        root = Root("llm-claude", str(path or self.a), includes=list(includes) if includes is not None else None)
        return Config(path=str(path or self.a), trash=False).for_root(root)

    def sync(self, cfg, dry=False, b=None, logs=None):
        return run_peer(cfg, "b", LocalTransport(b or self.b), dry=dry, state_dir=self.state,
                        log=(logs.append if logs is not None else (lambda *a: None)), root_name="llm-claude")


    def cli_sync(self):
        from docsync import cli
        text = (f'trash = false\n\n[[roots]]\nname = "llm-claude"\npath = "{self.a}"\n'
                f'includes = {CLAUDE_INCLUDES!r}\n\n[[peers]]\nname = "b"\nhosts = ["{self.b}"]\n').replace("'", '"')
        out = io.StringIO()
        with mock.patch.object(cli, "load_config", lambda: parse_config(text)), \
                mock.patch.object(cli, "STATE_DIR", self.state), redirect_stdout(out):
            rc = cli.main(["sync", "b", "--root", "llm-claude", "--no-notify"])
        return rc, out.getvalue()


class IncludesTests(_Trees):
    def test_allowlist_both_directions(self):
        touch(self.a / "CLAUDE.md", "A-claude", self.now)
        touch(self.a / "notes.txt", "notes", self.now)
        touch(self.a / "sub/CLAUDE.md", "nested", self.now)
        touch(self.a / "projects/p1/memory/MEMORY.md", "mem-a", self.now)
        touch(self.b / "settings.json", "{}", self.now)
        touch(self.b / "projects/a/b/memory/x", "too deep", self.now)
        touch(self.b / "projects/p2/memory/deep/y.md", "mem-b", self.now)
        rep = self.sync(self.cfg())
        self.assertTrue(rep.ok, rep.error)
        self.assertEqual((rep.pushed_files, rep.pulled_files), (2, 2))
        self.assertEqual((self.b / "CLAUDE.md").read_text(), "A-claude")
        self.assertTrue((self.b / "projects/p1/memory/MEMORY.md").exists())
        self.assertEqual((self.a / "settings.json").read_text(), "{}")
        self.assertTrue((self.a / "projects/p2/memory/deep/y.md").exists())
        self.assertFalse((self.b / "notes.txt").exists())
        self.assertFalse((self.b / "sub/CLAUDE.md").exists())
        self.assertFalse((self.a / "projects/a/b/memory/x").exists())
        (self.b / "CLAUDE.md").unlink()  # a deletion inside the allowlist propagates …
        os.unlink(self.a / "notes.txt")  # … one outside it is never seen
        rc, out = self.cli_sync()  # the CLI path (config → api → session) keeps the allowlist
        self.assertEqual(rc, 0, out)
        self.assertIn("[b] denied: 0 file(s)", out)
        self.assertFalse((self.a / "CLAUDE.md").exists())
        self.assertTrue((self.b / "settings.json").exists())
        self.assertTrue((self.b / "projects/a/b/memory/x").exists())
        self.assertTrue((self.a / "sub/CLAUDE.md").exists())
        # the remote lister agrees with the local scan on the same tree
        res = run_lister(self.b, CLAUDE_INCLUDES)
        remote = manifest.parse_lines(res.stdout, [".docsync*"], includes=CLAUDE_INCLUDES)
        self.assertEqual(remote, manifest.scan(self.b, [".docsync*"], includes=CLAUDE_INCLUDES))
        self.assertEqual(sorted(remote), ["projects/p1/memory/MEMORY.md", "projects/p2/memory/deep/y.md", "settings.json"])

    def test_lister_prunes_non_matching_dirs(self):
        sessions = self.a / "sessions"
        sessions.mkdir()
        for i in range(20000):
            (sessions / f"s{i}.jsonl").touch()
        (sessions / "nested").mkdir()
        touch(self.a / "CLAUDE.md")
        touch(self.a / "projects/p/memory/m.md")
        calls = []
        real_scandir, real_listdir = os.scandir, os.listdir

        def count(fn):
            def wrapper(path=".", *a, **k):
                if "/sessions" in os.fspath(path):
                    calls.append(os.fspath(path))
                return fn(path, *a, **k)
            return wrapper
        with mock.patch("os.scandir", count(real_scandir)), mock.patch("os.listdir", count(real_listdir)):
            m = manifest.scan(self.a, [".docsync*"], includes=CLAUDE_INCLUDES)
        self.assertEqual(calls, [])
        self.assertEqual(sorted(m), ["CLAUDE.md", "projects/p/memory/m.md"])
        res = run_lister(self.a, CLAUDE_INCLUDES, prelude=COUNTING_PRELUDE)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("CALLS=0", res.stderr)
        self.assertEqual(len(manifest.parse_lines(res.stdout, [], includes=CLAUDE_INCLUDES)), 2)

    def test_deny_list_wins_over_include(self):
        inc = ["CLAUDE.md", ".credentials.json", "keys/**"]
        touch(self.a / ".credentials.json", "TOKEN", self.now)
        touch(self.a / "CLAUDE.md", "c", self.now)
        denied: set[str] = set()
        local = manifest.scan(self.a, [".docsync*"], includes=inc, denied=denied)
        self.assertEqual(sorted(local), ["CLAUDE.md"])
        self.assertEqual(denied, {".credentials.json"})
        for name in ("auth.json", "oauth_creds.json", "x.pem", "id_rsa", "id_ed25519.pub", ".env"):
            touch(self.b / "keys" / name, "secret", self.now)
        res = run_lister(self.b, inc)
        self.assertEqual(res.returncode, 0, res.stderr)
        sizes = [r.split("\t")[1] for r in res.stdout.split("\0") if r]
        self.assertEqual(sorted(sizes), ["-2"] * 6)  # names only, never a size or content
        rdenied: set[str] = set()
        self.assertEqual(manifest.parse_lines(res.stdout, [], includes=inc, denied=rdenied), {})
        self.assertEqual(len(rdenied), 6)
        forged = f"{self.now}\t5\t.credentials.json\0{self.now}\t1\tCLAUDE.md\0"  # a lister that ignores the deny list
        again: set[str] = set()
        self.assertEqual(sorted(manifest.parse_lines(forged, [], includes=inc, denied=again)), ["CLAUDE.md"])
        self.assertEqual(again, {".credentials.json"})
        # conflict copies of denied names stay denied: local scan, 3.9 lister (-2 marker), parse_lines
        cdir, cinc = self.base / "C", [".credentials.json", "auth.json", ".env", "*.json"]
        cnames = {".credentials.conflict-x-node-a.json", "auth.conflict-x-node-a.json", ".env.conflict-x-node-a"}
        for name in cnames | {"ok.json"}:
            touch(cdir / name, "secret", self.now)
        cden: set[str] = set()
        self.assertEqual(sorted(manifest.scan(cdir, [], includes=cinc, denied=cden)), ["ok.json"])
        self.assertEqual(cden, cnames)
        for icase in (True, False):
            res = run_lister(cdir, cinc, icase=icase)
            recs = {r.split("\t")[2]: r.split("\t")[1] for r in res.stdout.split("\0") if r}
            self.assertEqual(recs, {**{n: "-2" for n in cnames}, "ok.json": "6"}, res.stderr)
        forged = "".join(f"{self.now}\t6\t{n}\0" for n in sorted(cnames | {"ok.json"}))
        cden.clear()
        self.assertEqual(sorted(manifest.parse_lines(forged, [], includes=cinc, denied=cden)), ["ok.json"])
        self.assertEqual(cden, cnames)
        logs: list[str] = []
        (self.base / "empty").mkdir()
        rep = self.sync(self.cfg(inc), dry=True, b=self.base / "empty", logs=logs)
        self.assertTrue(rep.ok, rep.error)
        self.assertEqual(rep.pushed_files, 1)
        self.assertIn("[b] denied: 1 file(s)", logs)
        rep = self.sync(self.cfg(inc))
        self.assertFalse((self.b / ".credentials.json").exists())
        # a root without includes knows no deny list: Keynote decks and certificates still sync
        docs_a, docs_b = self.base / "A" / "Documents", self.base / "B" / "Documents"
        touch(docs_a / "Talk.key", "deck", self.now)
        touch(docs_a / "cert.pem", "cert", self.now)
        docs_b.mkdir(parents=True)
        logs.clear()
        rep = self.sync(self.cfg(None, path=docs_a), b=docs_b, logs=logs)
        self.assertTrue(rep.ok, rep.error)
        self.assertTrue((docs_b / "Talk.key").exists() and (docs_b / "cert.pem").exists())
        self.assertFalse(any("denied" in ln for ln in logs))

    def test_include_symlink_skipped(self):
        target = self.base / "dotfiles" / "skills"
        touch(target / "s.md")
        os.symlink(target, self.a / "skills")
        os.symlink(target / "s.md", self.a / "CLAUDE.md")
        inc = ["skills/**", "skills", "CLAUDE.md"]
        links: set[str] = set()
        self.assertEqual(manifest.scan(self.a, [], includes=inc, links=links), {})
        self.assertEqual(links, {"skills", "CLAUDE.md"})
        res = run_lister(self.a, inc)  # symlinks are reported as -3 markers only (1.3.2), never listed
        self.assertEqual((res.returncode, res.stdout), (0, "0\t-3\tskills\x000\t-3\tCLAUDE.md\x00"))
        links = set()
        self.assertEqual(manifest.parse_lines(res.stdout, [], includes=inc, links=links), {})
        self.assertEqual(links, {"skills", "CLAUDE.md"})

    def test_no_match_no_delete(self):
        touch(self.a / "CLAUDE.md", "a", self.now)
        touch(self.a / "projects/p/memory/m.md", "m", self.now)
        touch(self.b / "history.jsonl", "h", self.now)
        touch(self.b / "sub/CLAUDE.md", "s", self.now)
        cfg = self.cfg(["settings.json"])  # matches nothing on either side
        for _ in range(2):
            rep = self.sync(cfg)
            self.assertTrue(rep.ok, rep.error)
            self.assertEqual((rep.deleted_local, rep.deleted_remote, rep.pushed_files, rep.pulled_files), (0, 0, 0, 0))
        self.assertTrue((self.a / "CLAUDE.md").exists() and (self.b / "history.jsonl").exists())
        self.assertFalse((self.a / "history.jsonl").exists())
        # case_insensitive (APFS): A/claude.md is the same file as B/CLAUDE.md, so it is listed too and
        # the clash becomes a conflict copy (as in roots without includes), never a silent overwrite
        a2, b2 = self.base / "A2", self.base / "B2"
        touch(a2 / "claude.md", "mine", self.now - 50)
        touch(b2 / "CLAUDE.md", "theirs", self.now)
        self.assertIn("\tclaude.md\0", run_lister(a2, ["CLAUDE.md"], icase=True).stdout)
        self.assertEqual(run_lister(a2, ["CLAUDE.md"]).stdout, "")  # case-sensitive peer mode
        rep = run_peer(self.cfg(["CLAUDE.md"], path=a2), "b2", LocalTransport(b2), state_dir=self.state,
                       log=lambda *a: None, root_name="llm-claude")
        self.assertTrue(rep.ok, rep.error)
        self.assertEqual(len(rep.conflicts), 1)
        self.assertIn("mine", [f.read_text() for f in a2.iterdir() if f.is_file()])
        # conflict copies are tracked like their original: deleting one on A reaches B
        a3, b3, copy = self.base / "A3", self.base / "B3", "CLAUDE.conflict-20261108T120000-node-a.md"
        touch(a3 / "CLAUDE.md", "c", self.now)
        touch(a3 / copy, "loser", self.now)
        b3.mkdir()
        self.assertIn("\t" + copy + "\0", run_lister(a3, CLAUDE_INCLUDES, icase=True).stdout)
        cfg3 = self.cfg(path=a3)

        def sync3():
            return run_peer(cfg3, "b3", LocalTransport(b3), state_dir=self.state, log=lambda *a: None,
                            root_name="llm-claude")
        self.assertEqual(sync3().pushed_files, 2)
        self.assertTrue((b3 / copy).exists())
        (a3 / copy).unlink()
        rep = sync3()
        self.assertTrue(rep.ok, rep.error)
        self.assertEqual(rep.deleted_remote, 1)
        self.assertFalse((b3 / copy).exists())

    def test_pattern_quoted_for_peer(self):
        touch(self.b / "projects/real/memory/m.md", "m", self.now)
        cwd = self.base / "cwd"
        touch(cwd / "projects/zz/memory/zz")  # what sh would expand an unquoted pattern to
        old = os.getcwd()
        os.chdir(cwd)
        self.addCleanup(os.chdir, old)
        t = RemoteTransport("peer", str(self.b))
        t.ssh_cmd = [sys.executable, FAKE_SSH, "--state", str(self.state)]
        sent = []
        real = t.ssh
        t.ssh = lambda cmd, **kw: (sent.append(cmd), real(cmd, **kw))[1]
        m = t.manifest([".docsync*"], set(), includes=["projects/*/memory/**"])
        self.assertEqual(sorted(m), ["projects/real/memory/m.md"])
        self.assertTrue(sent[0].endswith(" 'projects/*/memory/**'"), sent[0])

    def test_root_without_includes_unchanged(self):
        for rel in ("a.txt", "sub/b.txt", "sub/deep/c.pem", ".env", "Talk.key", "sessions/x.jsonl"):
            touch(self.a / rel, rel, self.now)
        os.symlink(self.a / "sub", self.a / "link")
        self.assertIsNone(Root("documents", "~/Documents").includes)
        old, new = run_lister(self.a, source=LISTER_PY_120), run_lister(self.a)
        self.assertEqual((old.returncode, new.returncode), (0, 0), new.stderr)
        marker = "0\t-3\tlink\0"  # 1.3.2: the symlink is reported (name only); all else byte-identical
        self.assertIn(marker, new.stdout)
        self.assertEqual(old.stdout, new.stdout.replace(marker, ""))
        ex = [".docsync*", ".DS_Store"]
        self.assertEqual(manifest.scan(self.a, ex), manifest.parse_lines(old.stdout, ex))
        self.assertEqual(len(manifest.scan(self.a, ex)), 6)
        ast.parse(manifest.LISTER_PY, feature_version=(3, 9))
        ast.parse(manifest.FILTER_PY, feature_version=(3, 9))
        cfg = Config(path=str(self.a)).for_root(Root("documents", str(self.a)))
        self.assertIsNone(cfg.includes)
        seen = {}

        class Spy(LocalTransport):
            def manifest(self, excludes, dataless=None):  # the 1.2.0 transport interface
                seen["called"] = True
                return super().manifest(excludes, dataless)
        rep = run_peer(cfg, "b", Spy(self.b), dry=True, state_dir=self.state, log=lambda *a: None)
        self.assertTrue(rep.ok and seen, rep.error)


class IncludesConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.file = self.dir / "config.toml"

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *argv):
        from docsync import cli
        out = io.StringIO()
        with mock.patch.object(cli, "CONFIG_FILE", self.file), redirect_stdout(out):
            rc = cli.main(list(argv))
        return rc, out.getvalue()

    def test_add_llm_roots_idempotent(self):
        original = ('# my docsync config — keep this comment\npath = "~/Documents"\n\n'
                    '[[peers]]\nname = "node-a"  # the Studio\nhosts = ["10.0.0.2"]\n')
        self.file.write_text(original)
        os.chmod(self.file, 0o600)
        rc, out = self.run_cli("roots", "add-llm")
        self.assertEqual(self.file.stat().st_mode & 0o777, 0o600)
        self.assertEqual(rc, 0, out)
        text = self.file.read_text()
        self.assertTrue(text.startswith(original), "existing bytes must stay untouched")
        cfg = parse_config(text)
        roots = {r.name: r for r in cfg.effective_roots()}
        self.assertEqual(sorted(roots), ["documents", "llm-claude", "llm-codex", "llm-gemini"])
        self.assertEqual(roots["llm-claude"].path, "~/.claude")
        self.assertEqual(roots["llm-claude"].includes,
                         ["CLAUDE.md", "settings.json", "MEMORY.md", "RTK.md", "projects/*/memory/**"])
        self.assertEqual(roots["llm-codex"].includes, ["config.toml", "AGENTS.md", "rules/**"])
        self.assertEqual(roots["llm-gemini"].includes, ["settings.json", "GEMINI.md"])
        self.assertIsNone(roots["documents"].includes)
        digest = hashlib.sha256(self.file.read_bytes()).hexdigest()
        rc, out = self.run_cli("roots", "add-llm")
        self.assertEqual(rc, 0)
        self.assertIn("llm roots already present", out)
        self.assertEqual(hashlib.sha256(self.file.read_bytes()).hexdigest(), digest)
        again = parse_config(render_config(cfg))  # what `docsync init --force`/save_config writes
        self.assertEqual({r.name: r.includes for r in again.effective_roots()},
                         {r.name: r.includes for r in cfg.effective_roots()})

    def test_includes_validation(self):
        with self.assertRaises(ValueError) as cm:
            parse_config('[[roots]]\nname = "llm"\npath = "~/.claude"\nincludes = ["ok", "[z-a]"]\n')
        self.assertIn("root 'llm': bad include pattern '[z-a]': ", str(cm.exception))
        for bad in ("[]", '"CLAUDE.md"', "[1, 2]", '["ok", ""]'):
            with self.assertRaises(ValueError) as cm:
                parse_config(f'[[roots]]\nname = "llm"\npath = "~/.claude"\nincludes = {bad}\n')
            self.assertIn("root 'llm': includes must be a non-empty list of strings", str(cm.exception))
        peer = self.dir / "peer"
        touch(peer / "CLAUDE.md")
        t = RemoteTransport("peer", str(peer))
        t.ssh_cmd = [sys.executable, FAKE_SSH, "--state", str(self.dir / "state")]
        sent = []
        real = t.ssh
        t.ssh = lambda cmd, **kw: (sent.append(cmd), real(cmd, **kw))[1]
        with mock.patch.dict(os.environ, {"PATH": "/bin"}):  # /bin has sh but no python3
            with self.assertRaises(TransportError) as cm:
                t.manifest([], set(), includes=["CLAUDE.md"])
        self.assertIn("includes need python3 on the peer", str(cm.exception))
        self.assertEqual(len(sent), 1)
        self.assertFalse(any("find ." in c for c in sent), sent)  # LISTER_SH never ran
        self.assertEqual(sorted(t.manifest([], set(), includes=["CLAUDE.md"])), ["CLAUDE.md"])

    def test_agent_skips_includes_roots_in_watch(self):
        from docsync import cli
        self.file.write_text('path = "~/Documents"\n\n[[roots]]\nname = "llm-claude"\npath = "~/.claude"\n'
                             'includes = ["CLAUDE.md"]\n')
        with mock.patch.object(cli, "load_config", lambda: parse_config(self.file.read_text())), \
                mock.patch.object(cli.agent, "install", return_value="ok") as install:
            rc, _ = self.run_cli("install-agent")
        self.assertEqual(rc, 0)
        interval, watch = install.call_args.args
        self.assertEqual(interval, 15)
        self.assertEqual(watch, [str(Path("~/Documents").expanduser())])
        self.assertNotIn(str(Path("~/.claude").expanduser()), watch)


class IncludesToolsTests(_Trees):
    """`docsync conflicts` and `docsync status` filter an includes root like sync does."""

    def cli(self, *argv, includes=CLAUDE_INCLUDES):
        from docsync import cli
        text = f'path = "{self.a}"\n'  # without includes: the legacy single root, as before
        if includes is not None:  # `path` is the "documents" root: keep it out of the includes root
            text = f'path = "{self.base / "docs"}"\n'
            text += f'\n[[roots]]\nname = "llm-claude"\npath = "{self.a}"\nincludes = {list(includes)!r}\n'.replace("'", '"')
        out = io.StringIO()
        with mock.patch.object(cli, "load_config", lambda: parse_config(text)), \
                mock.patch.object(cli, "cmd_peers", lambda a: 0), redirect_stdout(out):
            rc = cli.main(list(argv))
        return rc, out.getvalue()

    def conflict_tree(self):
        for rel in ("CLAUDE.conflict-x-node-a.md", "sessions/foo.conflict-x-node-a.json",
                    ".credentials.conflict-x-node-a.json", "sessions/deep/s.jsonl"):
            touch(self.a / rel, rel, self.now)

    def test_conflicts_respects_includes(self):
        self.conflict_tree()
        calls = []
        real_scandir, real_listdir = os.scandir, os.listdir

        def counting(fn):
            def wrapper(path=".", *args, **kw):
                if "/sessions" in os.fspath(path):
                    calls.append(os.fspath(path))
                return fn(path, *args, **kw)
            return wrapper
        with mock.patch.object(os, "scandir", counting(real_scandir)), \
                mock.patch.object(os, "listdir", counting(real_listdir)):
            rc, out = self.cli("conflicts")
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [])
        self.assertIn("CLAUDE.conflict-x-node-a.md", out)
        self.assertNotIn("sessions/", out)
        self.assertNotIn(".credentials", out)
        self.assertIn("1 conflict copies", out)

    def test_status_respects_includes(self):
        touch(self.a / "CLAUDE.md", "12345", self.now)
        touch(self.a / "projects/p/memory/m.md", "123", self.now)
        touch(self.a / "sessions/big.jsonl", "x" * 5000, self.now)
        touch(self.a / ".credentials.json", "secret", self.now)
        touch(self.a / "auth.json", "s", self.now)
        rc, out = self.cli("status")
        self.assertEqual(rc, 0)
        from docsync.report import fmt_bytes
        self.assertIn(f"{self.a}: 2 files, {fmt_bytes(8)}\n", out)

    def test_tools_unchanged_without_includes(self):
        self.conflict_tree()
        touch(self.a / "CLAUDE.md", "12345", self.now)
        from docsync.report import fmt_bytes
        cfg = parse_config(f'path = "{self.a}"\n')
        m = manifest.scan(cfg.local_root, cfg.excludes)
        rc, out = self.cli("status", includes=None)
        self.assertEqual(rc, 0)
        self.assertEqual(out, f"{cfg.local_root}: {len(m):,} files, {fmt_bytes(sum(v[1] for v in m.values()))}\n")
        self.assertIn("5 files", out)
        rc, out = self.cli("conflicts", includes=None)
        hits = sorted(p for p in manifest.scan(self.a, [".docsync*"]) if ".conflict-" in p)
        self.assertEqual(out, "".join(f"   {h}\n" for h in hits) + f"{len(hits)} conflict copies — review, then "
                         "delete the loser; the deletion propagates on the next sync\n")
        self.assertEqual(len(hits), 3)


if __name__ == "__main__":
    unittest.main()
