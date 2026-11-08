"""ICloudCheckTests: `docsync icloud-check` and the "covered by iCloud" fallback of api.sync. Collected
through test_sync_local, which re-exports the class; no test_ prefix, so discovery does not run it twice.

iCloud state comes from the test hook DOCSYNC_TEST_ICLOUD: one JSON file per node with
{"node": name, "dd": {root: bool}, "pending": {root: [relpath, ...]}, "brctl": text}. Peers are second
trees on this Mac reached through tests/fake_ssh.py; their `docsync` is a wrapper on PATH that points
DOCSYNC_TEST_ICLOUD at the peer's own JSON (in its state dir) and runs this checkout's docsync."""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from docsync import api
from docsync.config import Config, Peer, Root

ROOT = Path(__file__).resolve().parent.parent
FAKE_SSH = str(Path(__file__).resolve().parent / "fake_ssh.py")
ROOTS = ("documents", "desktop")
CLEAN_BRCTL = "1 containers matching 'com.apple.CloudDocs'\n<c{1}m.a{3}e.C{7}s[1] foreground {client:idle}>\n"
DOWN_BRCTL = CLEAN_BRCTL + """    Client Truth Unclean Items:
    --------------------------
    Under /Documents/Business
        r:2718782 i:<3ED20624> al:1 up:idle st{n:"rechnung.pdf" doc}
        > downloader{[content:bu80i last-error:<CKError 0x7a: "Network Unavailable"> failed attempts:1 last:9.10h ago next:ready]}
"""
STUCK_BRCTL = DOWN_BRCTL + """    Under /Documents/Steuer
        r:1 i:<4DB0FBA3> al:1 up:idle st{n:"beleg.pdf" doc}
        > uploader{[last-error:<CKError 0x1: "Network Unavailable"> failed attempts:3 last:9.10h ago next:ready]}
        r:2 i:<19253F0B> al:1 up:idle st{n:"neu.pdf" doc}
        > uploader{[last-error:<CKError 0x2: "Network Unavailable"> failed attempts:1 last:3.0m ago next:ready]}
"""
# The fake peer's docsync, installed only in $HOME/.local/bin: the probe must find it by extending PATH.
WRAPPER = """#!/bin/sh
if [ -e "$DOCSYNC_STATE_DIR/old" ]; then
  echo "usage: docsync [-h] [--version] {{init,peers,status,sync}} ..." >&2
  echo "docsync: error: argument cmd: invalid choice: 'icloud-check' (choose from 'init', 'sync')" >&2
  exit 2
fi
DOCSYNC_TEST_ICLOUD="$DOCSYNC_STATE_DIR/icloud-test.json" PYTHONPATH={root} exec {python} -m docsync "$@"
"""


def touch(p: Path, text: str = "x", age: float = 3600) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    t = time.time() - age
    os.utime(p, (t, t))


class ICloudCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.dir = {n: base / n for n in ("node-c", "node-a", "node-b")}
        for d in self.dir.values():
            for sub in ("Documents", "Desktop", "state"):
                (d / sub).mkdir(parents=True)
        self.home = base / "home"
        bindir = self.home / ".local" / "bin"
        bindir.mkdir(parents=True)
        (bindir / "docsync").write_text(WRAPPER.format(root=ROOT, python=sys.executable))
        (bindir / "docsync").chmod(0o755)
        env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(self.home),  # like a non-interactive ssh
               "DOCSYNC_TEST_ICLOUD": str(self.dir["node-c"] / "state" / "icloud-test.json")}
        self.env = mock.patch.dict(os.environ, env)
        self.env.start()
        for n in self.dir:
            self.icloud(n, documents=True, desktop=True)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def icloud(self, node: str, brctl: str = CLEAN_BRCTL, pending: dict | None = None, version="1.3.0", **dd: bool) -> None:
        data = {"node": node, "dd": dd, "pending": pending or {}, "brctl": brctl, "version": version}
        (self.dir[node] / "state" / "icloud-test.json").write_text(json.dumps(data))

    def tree(self, node: str, root: str = "documents") -> Path:
        return self.dir[node] / ("Documents" if root == "documents" else "Desktop")

    @property
    def state(self) -> Path:
        return self.dir["node-c"] / "state"

    def check(self, peers=("node-a", "node-b"), unreachable=()):
        from docsync import icloud
        c = self.dir["node-c"]
        cfg = Config(path=str(c / "Documents"), trash=False,
                     roots=[Root("documents", str(c / "Documents")), Root("desktop", str(c / "Desktop"))],
                     peers=[Peer(n, [f"fake-{n}"]) for n in peers])
        specs = [api.PeerSpec(n, None if n in unreachable else f"fake-{n}", hosts_tried=[f"fake-{n}"],
                              paths={r: str(self.tree(n, r)) for r in ROOTS}) for n in peers]
        rep = icloud.check(cfg, specs, state_dir=self.state,
                           ssh_cmd=lambda name: [sys.executable, FAKE_SSH, "--state", str(self.dir[name] / "state")])
        return rep, rep.render()

    def test_mixed_dd_state_exits_1(self):
        from docsync import cli
        a = cli.build_parser().parse_args(["icloud-check", "--local", "--json", "--root", "documents"])
        self.assertEqual((a.local, a.json, a.root), (True, True, ["documents"]))
        self.icloud("node-a", documents=True, desktop=False)  # the 2026-11-08 state
        self.icloud("node-b", documents=False, desktop=True)
        rep, out = self.check()
        self.assertEqual(rep.exit_code, 1, out)
        self.assertIn("node-c  documents  dd: on", out)
        self.assertIn("node-c  desktop  dd: on", out)
        self.assertIn("node-a  desktop  dd: off", out)
        self.assertIn("node-b  documents  dd: off", out)
        self.assertNotIn("node-a  documents  dd: off", out)
        self.assertIn("enable Desktop & Documents on node-a, node-b", out)

    def test_pending_upload_exits_1(self):
        touch(self.tree("node-c") / "old.txt", age=20 * 60)
        touch(self.tree("node-c") / "new.txt", age=5 * 60)
        rep, out = self.check(peers=())
        self.assertEqual(rep.exit_code, 0, out)  # all uploaded, brctl clean
        self.assertIn("node-c  documents  dd: on  not uploaded: 0  stuck: up 0 / down 0", out)
        self.icloud("node-c", documents=True, desktop=True, pending={"documents": ["old.txt", "new.txt"]})
        rep, out = self.check(peers=())
        self.assertEqual(rep.exit_code, 1, out)
        self.assertIn("node-c  documents  dd: on  not uploaded: 1", out)  # the 5 min old one is not counted
        self.icloud("node-c", documents=True, desktop=True, brctl=STUCK_BRCTL)  # 1 old + 1 recent upload, 1 download
        rep, out = self.check(peers=())
        self.assertEqual(rep.exit_code, 1, out)
        self.assertIn("node-c  documents  dd: on  not uploaded: 0  stuck: up 1 / down 1", out)
        self.assertEqual((rep.rows[0].stuck, rep.rows[0].stuck_down), (1, 1))
        self.assertEqual({k: rep.to_dict()["nodes"][0][k] for k in ("stuck", "stuck_down")}, {"stuck": 1, "stuck_down": 1})
        self.icloud("node-c", documents=True, desktop=True, brctl=DOWN_BRCTL)  # an evicted file failed to download
        rep, out = self.check(peers=())
        self.assertEqual(rep.exit_code, 0, out)
        self.assertIn("node-c  documents  dd: on  not uploaded: 0  stuck: up 0 / down 1", out)
        self.assertIn("node-c  desktop  dd: on  not uploaded: 0  stuck: up 0 / down 0", out)
        self.icloud("node-c", documents=True, desktop=True, brctl="brctl: some new format nobody parses\n")
        rep, out = self.check(peers=())
        self.assertEqual(rep.exit_code, 1, out)
        self.assertIn("stuck: unknown", out)

    def test_parity_lists_missing_file(self):
        touch(self.tree("node-b") / "Steuer/only-b.txt")
        rep, out = self.check(peers=("node-b",))
        self.assertEqual(rep.exit_code, 1, out)
        self.assertIn("Steuer/only-b.txt  missing on node-c", out)
        self.assertEqual(rep.to_dict()["parity"]["documents"],
                         [{"path": "Steuer/only-b.txt", "node": "node-c", "problem": "missing"}])
        shutil.rmtree(self.tree("node-b") / "Steuer")
        for node in ("node-c", "node-b"):
            touch(self.tree(node, "desktop") / "a.pdf", "same", age=600)
            touch(self.tree(node, "desktop") / "a 2.pdf", "same", age=600)
        rep, out = self.check(peers=("node-b",))
        self.assertEqual(rep.exit_code, 1, out)
        self.assertIn("a 2.pdf  icloud duplicate on node-c", out)
        os.unlink(self.tree("node-c", "desktop") / "a 2.pdf")
        os.unlink(self.tree("node-b", "desktop") / "a 2.pdf")
        touch(self.tree("node-b", "desktop") / "a.pdf", "longer text", age=600)
        rep, out = self.check(peers=("node-b",))
        self.assertEqual(rep.exit_code, 1, out)
        self.assertIn("a.pdf  differs on node-b", out)

    def write_state(self, node: str, age: float, ok: bool = True) -> None:
        rec = {"checked_at": time.time() - age, "node": node, "roots": {"documents": {"ok": ok, "dd": True}}}
        (self.state / f"icloud-{node}.json").write_text(json.dumps(rec))

    def sync_unreachable(self):
        return api.sync(api.SyncRequest(roots=[api.RootSpec("documents", str(self.tree("node-c")))],
                                        peers=[api.PeerSpec("node-b", None, hosts_tried=["10.42.0.2"])],
                                        state_dir=self.state, safetynet=False, trash=False))

    def test_unreachable_covered_by_icloud(self):
        self.write_state("node-c", 2 * 3600)
        self.write_state("node-b", 2 * 3600)
        res = self.sync_unreachable()
        self.assertEqual(res.exit_code, 0, res.report.render())
        self.assertIn("unreachable — covered by iCloud", res.report.render())
        self.assertEqual(res.to_dict()["peers"][0]["error"], "unreachable — covered by iCloud")
        self.write_state("node-b", 2 * 3600, ok=False)
        self.assertEqual(self.sync_unreachable().exit_code, 2)
        for corrupt in ('{"checked_at": %d, "roots": {"documents": true}}' % time.time(),
                        '{"checked_at": "x", "roots": []}', '[1, 2]', '{not json'):
            (self.state / "icloud-node-b.json").write_text(corrupt)
            self.assertEqual(self.sync_unreachable().exit_code, 2, corrupt)  # fails closed, no crash
        self.write_state("node-b", 30 * 3600)
        res = self.sync_unreachable()
        self.assertEqual(res.exit_code, 2, res.report.render())
        self.assertNotIn("covered by iCloud", res.report.render())

    def test_unreachable_during_check(self):
        from docsync import icloud
        self.write_state("node-b", 2 * 3600)  # an older passing check must not survive this one
        rep, out = self.check(unreachable=("node-b",))
        self.assertEqual(rep.exit_code, 1, out)
        self.assertIn("node-b  documents  unreachable", out)
        self.assertIn("node-a  documents  dd: on  not uploaded: 0  stuck: up 0 / down 0", out)
        rec = json.loads((self.state / "icloud-node-b.json").read_text())
        self.assertFalse(any(r["ok"] for r in rec["roots"].values()), rec)
        self.assertFalse(icloud.covered(self.state, "node-c", "node-b", "documents"))
        self.assertTrue(icloud.covered(self.state, "node-c", "node-a", "documents"))

    def test_dd_off_everywhere(self):
        for n in self.dir:
            self.icloud(n, documents=False, desktop=False)
        touch(self.tree("node-a") / "only-a.txt")  # docsync syncs it; iCloud parity is not checked
        rep, out = self.check(peers=("node-a",))
        self.assertEqual(rep.exit_code, 0, out)
        self.assertIn("documents: dd off on every node — docsync only", out)
        self.assertIn("node-a  desktop  dd: off", out)
        self.assertNotIn("missing", out)

    def test_peer_probe_via_ssh(self):
        touch(self.tree("node-a") / "x.txt", age=7200)
        self.icloud("node-a", documents=True, desktop=True, pending={"documents": ["x.txt"]})
        rep, out = self.check(peers=("node-a",))
        self.assertIn("node-a  documents  dd: on  not uploaded: 1  stuck: up 0 / down 0", out)  # computed on the peer
        self.assertIn("x.txt  missing on node-c", out)
        os.unlink(self.tree("node-a") / "x.txt")
        self.icloud("node-a", documents=True, desktop=True)
        rep, out = self.check(peers=("node-a",))
        self.assertEqual(rep.exit_code, 0, out)
        self.assertIn("iCloud: complete", out)
        nodes = {(r["node"], r["root"]): r for r in rep.to_dict()["nodes"]}
        self.assertEqual(nodes[("node-a", "desktop")]["dd"], True)
        for version in ("1.2.9", None):  # the command exists but the reported version is too low / missing
            self.icloud("node-a", documents=True, desktop=True, version=version)
            rep, out = self.check(peers=("node-a",))
            self.assertEqual(rep.exit_code, 1, out)
            self.assertIn("node-a  documents  docsync too old on node-a (needs 1.3.0)", out)
        self.icloud("node-a", documents=True, desktop=True)
        (self.dir["node-b"] / "state" / "old").touch()  # docsync 1.2.0 on node-b: no icloud-check
        rep, out = self.check()
        self.assertEqual(rep.exit_code, 1, out)
        self.assertIn("docsync too old on node-b", out)
        self.assertIn("node-a  documents  dd: on", out)

    # The real probes, without the DOCSYNC_TEST_ICLOUD hook.

    def test_real_upload_helper(self):
        from docsync import icloud
        del os.environ["DOCSYNC_TEST_ICLOUD"]
        stub = self.home / "helper"  # stands in for the Swift helper: one old file, one excluded, one recent
        recent = int(time.time())
        stub.write_text(f"#!/bin/sh\nprintf '100\\ta.txt\\000200\\tsub/.DS_Store\\000{recent}\\tnew.txt\\000'\n")
        stub.chmod(0o755)
        with mock.patch.object(icloud, "_helper", return_value=stub):
            self.assertEqual(icloud.not_uploaded("documents", self.tree("node-c"), [".DS_Store"]), 1)
        stub.write_text("#!/bin/sh\nexit 3\n")
        with mock.patch.object(icloud, "_helper", return_value=stub):
            self.assertIsNone(icloud.not_uploaded("documents", self.tree("node-c"), []))

    def test_real_dd_xattr(self):
        import subprocess
        from docsync import icloud
        del os.environ["DOCSYNC_TEST_ICLOUD"]
        on, off = self.tree("node-c"), self.tree("node-c", "desktop")
        subprocess.run(["/usr/bin/xattr", "-w", icloud.DOMAIN_XATTR, "x", str(on)], check=True)
        self.assertTrue(icloud.dd_on("documents", on))
        self.assertFalse(icloud.dd_on("desktop", off))

    def test_real_brctl(self):
        from docsync import icloud
        del os.environ["DOCSYNC_TEST_ICLOUD"]
        stubdir = self.home / "stubs"
        stubdir.mkdir()
        (self.home / "brctl.out").write_text(STUCK_BRCTL)
        (stubdir / "brctl").write_text(f'#!/bin/sh\n[ "$1 $2" = "status com.apple.CloudDocs" ] || exit 9\ncat {self.home}/brctl.out\n')
        (stubdir / "brctl").chmod(0o755)
        os.environ["PATH"] = f"{stubdir}:{os.environ['PATH']}"
        self.assertEqual(icloud.stuck_in(icloud.stuck_folders(icloud.brctl_status()), self.tree("node-c")), (1, 1))
        (stubdir / "brctl").write_text("#!/bin/sh\nexit 1\n")
        self.assertIsNone(icloud.brctl_status())
        self.assertEqual(icloud.stuck_in(icloud.stuck_folders(None), self.tree("node-c")), (None, None))
