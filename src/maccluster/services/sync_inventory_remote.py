"""Remote side of the home sync: the peer-side walk shipped over SSH.

The peer runs a self-contained script (``_REMOTE_INVENTORY_PY``) under its own
``/usr/bin/python3``; this module ships it, runs it, cleans it up, and decides
whether the listing it got back covers the whole tree. ``complete=False`` means
"missing from this list" does not mean "missing on the peer".
"""

from __future__ import annotations

import os
from pathlib import Path

from maccluster.app_factory import AppContext
from maccluster.services.sync_inventory import FileMeta, parse_inventory_text
from maccluster.services.sync_ssh import _scp_argv, _ssh_argv

# Remote inventory: argv home excludes_file → lines relpath\\tmtime_ns\\tsize
_REMOTE_INVENTORY_PY = 'import fnmatch, json, os, signal, stat, subprocess, sys, time\n\n# Unbuffered inventory lines (SSH non-TTY otherwise loses stdout on kill/timeout)\ntry:\n    sys.stdout.reconfigure(line_buffering=True)\nexcept Exception:\n    pass\ntry:\n    sys.stderr.reconfigure(line_buffering=True)\nexcept Exception:\n    pass\n\nroot, ex_path = sys.argv[1], sys.argv[2]\nincludes = [x.strip().strip("/") for x in sys.argv[3:] if x.strip()]\nex = open(ex_path, encoding="utf-8").read().splitlines() if os.path.isfile(ex_path) else []\nPREF = ("Developer", "Downloads", ".ssh", ".config", "Desktop", "Documents")\nincludes.sort(key=lambda x: PREF.index(x.split("/")[0]) if x.split("/")[0] in PREF else 99)\nt0 = time.time()\nMAX_SEC = float(os.environ.get("MACCLUSTER_INV_MAX_SEC", "900"))\nDIR_SEC = float(os.environ.get("MACCLUSTER_INV_DIR_SEC", "6"))\nSKIP_NAMES = {\n    "imessage_export", "node_modules", ".git", "DerivedData",\n    "__pycache__", ".venv", "venv", ".Trash", "Library",\n}\nDOTDIRS = os.environ.get("MACCLUSTER_INV_DOTDIRS", "").strip().lower() in ("1", "true", "yes")\n# Per-directory child processes cost one interpreter start per folder: measured\n# 167 files/s, so a 4.4M-file tree needs 7h and always trips MAX_SEC. Only cloud\n# providers (iCloud/FileProvider) can wedge scandir, so the guard is opt-in.\nSAFE_SCANDIR = os.environ.get("MACCLUSTER_INV_SAFE_SCANDIR", "").strip().lower() in ("1", "true", "yes")\nif DOTDIRS:\n    SKIP_NAMES.discard(".git")\nUF_DATALESS = 0x40000000\nn_emitted = 0\n\n\ndef excl(rel):\n    rel = rel.replace("\\\\", "/").lstrip("./")\n    parts = rel.split("/")\n    for pat in ex:\n        if not pat:\n            continue\n        p = pat.replace("\\\\", "/")\n        if p.endswith("/"):\n            b = p.rstrip("/")\n            if rel == b or rel.startswith(b + "/"):\n                return True\n            if b.startswith("**/") and (b[3:] in parts or any(fnmatch.fnmatch(x, b[3:]) for x in parts)):\n                return True\n        elif p.startswith("**/"):\n            rest = p[3:]\n            if any(x == rest or fnmatch.fnmatch(x, rest) for x in parts):\n                return True\n            if fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(os.path.basename(rel), rest):\n                return True\n        else:\n            if fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(os.path.basename(rel), p):\n                return True\n            b = p.rstrip("/")\n            if rel == b or rel.startswith(b + "/"):\n                return True\n    return False\n\n\ndef safe_scandir(path):\n    """List dir. Fast in-process scandir; killable child only when asked."""\n    if not SAFE_SCANDIR:\n        try:\n            out = []\n            for e in os.scandir(path):\n                try:\n                    out.append([e.name, e.path, e.is_dir(follow_symlinks=False), e.is_file(follow_symlinks=False)])\n                except OSError:\n                    pass\n            return out\n        except OSError:\n            return None\n    code = (\n        "import os,json,sys\\n"\n        "p=sys.argv[1]\\n"\n        "o=[]\\n"\n        "try:\\n"\n        "  for e in os.scandir(p):\\n"\n        "    try:\\n"\n        "      o.append([e.name,e.path,e.is_dir(follow_symlinks=False),e.is_file(follow_symlinks=False)])\\n"\n        "    except OSError:\\n"\n        "      pass\\n"\n        "except Exception:\\n"\n        "  sys.exit(2)\\n"\n        "print(json.dumps(o))\\n"\n    )\n    try:\n        r = subprocess.run(\n            [sys.executable, "-c", code, path],\n            capture_output=True,\n            text=True,\n            timeout=DIR_SEC,\n        )\n    except subprocess.TimeoutExpired:\n        print("# skip-hang %s" % path, file=sys.stderr, flush=True)\n        return None\n    if r.returncode != 0:\n        return None\n    try:\n        return json.loads(r.stdout or "[]")\n    except Exception:\n        return None\n\n\ndef emit_file(home, path):\n    global n_emitted\n    try:\n        st = os.lstat(path)\n    except OSError:\n        return False\n    if not (stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode)):\n        return False\n    if getattr(st, "st_flags", 0) & UF_DATALESS:\n        return False\n    rel = os.path.relpath(path, home).replace("\\\\", "/")\n    if excl(rel):\n        return False\n    sys.stdout.write("%s\\t%d\\t%d\\n" % (rel, st.st_mtime_ns, st.st_size))\n    n_emitted += 1\n    if n_emitted % 200 == 0:\n        sys.stdout.flush()\n    return True\n\n\ndef walk_safe(home, start):\n    n = 0\n    stack = [start]\n    while stack:\n        if time.time() - t0 > MAX_SEC:\n            print("# inventory time budget", file=sys.stderr, flush=True)\n            break\n        cur = stack.pop()\n        entries = safe_scandir(cur)\n        if entries is None:\n            try:\n                label = os.path.relpath(cur, home)\n            except Exception:\n                label = cur\n            print("# skip-hang %s" % label, file=sys.stderr, flush=True)\n            continue\n        for name, path, is_dir, is_file in entries:\n            if time.time() - t0 > MAX_SEC:\n                break\n            if name in SKIP_NAMES:\n                continue\n            if name == ".DS_Store":\n                continue\n            # skip heavy/hidden dirs except .ssh / .config (DOTDIRS walks .git/.github)\n            if (not DOTDIRS) and name.startswith(".") and name not in (".ssh", ".config"):\n                if is_dir:\n                    continue\n            if is_dir:\n                rel = os.path.relpath(path, home).replace("\\\\", "/")\n                if excl(rel) or excl(rel + "/"):\n                    continue\n                stack.append(path)\n            elif is_file or True:\n                if emit_file(home, path):\n                    n += 1\n                    if n % 20000 == 0:\n                        print("# listed %d" % n, file=sys.stderr, flush=True)\n    return n\n\n\nwalk_roots = []\nif includes:\n    for inc in includes:\n        if not inc or ".." in inc.split("/"):\n            continue\n        p0 = os.path.join(root, inc)\n        if not os.path.lexists(p0):\n            continue\n        base = inc.split("/")[0]\n        if base in ("Documents", "Desktop") and "/" not in inc.rstrip("/"):\n            kids = safe_scandir(p0)\n            if kids is None:\n                print("# skip-hang %s" % inc, file=sys.stderr, flush=True)\n                continue\n            for name, path, is_dir, is_file in kids:\n                if name in SKIP_NAMES or name == ".DS_Store":\n                    continue\n                if is_dir:\n                    walk_roots.append((path, "%s/%s" % (inc.rstrip("/"), name)))\n                elif is_file:\n                    emit_file(root, path)\n        else:\n            walk_roots.append((p0, inc))\nelse:\n    walk_roots.append((root, ""))\n\nn = 0\nfor walk_root, label in walk_roots:\n    if time.time() - t0 > MAX_SEC:\n        break\n    print("# walk %s" % label, file=sys.stderr, flush=True)\n    n += walk_safe(root, walk_root)\n\nsys.stdout.flush()\nprint("# inventory done n=%d sec=%d" % (n_emitted, int(time.time() - t0)), file=sys.stderr, flush=True)\nsys.exit(0)\n'


def _remote_inventory(
    ctx: AppContext,
    abs_ssh: str,
    abs_scp: str,
    ssh_target: str,
    remote_home: str,
    excludes: tuple[str, ...],
    *,
    timeout: float,
    work: Path,
    bind_ip: str | None = None,
    includes: tuple[str, ...] = (),
    include_dotdirs: bool = False,
    safe_scandir: bool = False,
) -> tuple[dict[str, FileMeta] | None, str, bool]:
    """Returns (inventory, note, complete).

    ``complete`` is False when the walk stopped early — time budget or a hung
    directory. Callers must not read "missing from the inventory" as "missing
    on the peer" in that case.
    """
    script = work / "remote_inv.py"
    script.write_text(_REMOTE_INVENTORY_PY, encoding="utf-8")
    excl_file = work / "excludes.txt"
    excl_file.write_text("\n".join(excludes) + "\n", encoding="utf-8")
    remote_script = f"/tmp/maccluster-inv-{os.getpid()}.py"
    remote_excl = f"/tmp/maccluster-excl-{os.getpid()}.txt"

    for local, remote in ((script, remote_script), (excl_file, remote_excl)):
        scp = ctx.runner.run(
            _scp_argv(abs_scp, str(local), f"{ssh_target}:{remote}", bind_ip=bind_ip),
            timeout=min(timeout, 60.0),
        )
        if scp.returncode != 0:
            return None, (scp.stderr or f"scp {local.name} failed")[:300], False

    # PYTHONUNBUFFERED so inventory lines are not stuck in libc buffers if SSH dies
    env_pairs = ["env", "PYTHONUNBUFFERED=1"]
    if include_dotdirs:
        env_pairs.append("MACCLUSTER_INV_DOTDIRS=1")
    if safe_scandir:
        # Home can reach iCloud/FileProvider paths where scandir wedges; pay the
        # per-directory child process there. ~/Developer never does, so `dev`
        # keeps the fast in-process walk.
        env_pairs.append("MACCLUSTER_INV_SAFE_SCANDIR=1")
    r = ctx.runner.run(
        _ssh_argv(
            abs_ssh,
            ssh_target,
            *env_pairs,
            "/usr/bin/python3",
            "-u",
            remote_script,
            remote_home,
            remote_excl,
            *[inc.rstrip("/") for inc in includes if inc.strip()],
            bind_ip=bind_ip,
        ),
        timeout=timeout,
    )
    ctx.runner.run(
        _ssh_argv(
            abs_ssh,
            ssh_target,
            "/bin/rm",
            "-f",
            remote_script,
            remote_excl,
            bind_ip=bind_ip,
        ),
        timeout=30.0,
    )
    inv = parse_inventory_text(r.stdout or "")
    err = (r.stderr or "").strip()
    # Accept partial inventory on timeout/kill if we listed any files
    if inv:
        if r.returncode != 0:
            return inv, f"partial inventory ({len(inv)} files); {err[:120]}", False
        if "# inventory time budget" in err or "# skip-hang" in err:
            return inv, f"partial inventory ({len(inv)} files); {err[:120]}", False
        return inv, "", True
    # Empty stdout: if remote walk started (stderr markers), soft-empty so push can proceed
    hangish = any(
        m in err
        for m in (
            "# walk",
            "# skip-hang",
            "# inventory",
            "# listed",
            "inventory time budget",
        )
    )
    if r.returncode != 0 or hangish:
        if hangish or r.returncode in (124, -9, -15, 255):
            return {}, f"empty/partial inventory (peer hang or timeout); {err[:160]}", False
        return None, (err or r.stdout or "remote inventory failed")[:300], False
    return inv, "", True
