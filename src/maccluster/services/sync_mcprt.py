"""MCPRT preflight: merge + commit/push/release, then TestFlight for iOS apps.

Runs before ``maccluster sync dev`` so GitHub (and TestFlight) are up to date
before the TB/Wi-Fi ditto pass copies leftover dirty files (.env, etc.).
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from maccluster.app_factory import AppContext
from maccluster.constants import TIMEOUT_MCPRT_GIT, TIMEOUT_MCPRT_TESTFLIGHT
from maccluster.domain.models import McprtRepoResult, McprtResult
from maccluster.errors import CliError
from maccluster.ports.process import ProcessResult

_SECRET_NAMES = frozenset(
    {
        ".env",
        "credentials.json",
        "secrets.json",
        "secret.json",
    }
)
_SECRET_SUFFIXES = (".pem", ".p8", ".key", ".pfx", ".p12")
_SECRET_BASENAME_RE = re.compile(r"^(?:\.env(?:\..+)?|AuthKey_.+\.p8|id_rsa|id_ed25519|id_ecdsa)$")


def is_secret_rel(rel: str) -> bool:
    """Paths that must never be committed during MCPRT."""
    rel = rel.replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    rel = rel.lstrip("/")
    base = Path(rel).name
    if base in _SECRET_NAMES or rel in _SECRET_NAMES:
        return True
    if _SECRET_BASENAME_RE.match(base):
        return True
    if base.startswith(".env"):
        return True
    lowered = base.lower()
    return any(lowered.endswith(suf) for suf in _SECRET_SUFFIXES)


def looks_like_ios_app(root: Path) -> bool:
    """True if the tree looks like an Xcode/iOS (or nested ios/) app."""
    root = Path(root)
    if not root.is_dir():
        return False
    if any(root.glob("*.xcodeproj")) or any(root.glob("*.xcworkspace")):
        return True
    if (root / "project.yml").is_file():
        return True
    ios = root / "ios"
    if ios.is_dir():
        if any(ios.glob("*.xcodeproj")) or any(ios.glob("*.xcworkspace")):
            return True
        if (ios / "project.yml").is_file():
            return True
    return False


def testflight_ship_script(root: Path) -> Path | None:
    """Prefer a repo-local ship script, else the TestFlight skill."""
    root = Path(root)
    for rel in (
        Path("scripts") / "release-ios.sh",
        Path("scripts") / "ship.sh",
        Path("ios") / "release.sh",
        Path("ios") / "scripts" / "ship.sh",
    ):
        cand = root / rel
        if cand.is_file():
            return cand
    skill = Path.home() / ".grok" / "skills" / "testflight" / "scripts" / "ship.sh"
    if skill.is_file():
        return skill
    return None


def _git_argv(abs_git: str, repo: Path, *args: str) -> list[str]:
    return [abs_git, "-C", str(repo), *args]


def _run(
    ctx: AppContext,
    argv: Sequence[str],
    *,
    timeout: float,
) -> ProcessResult:
    return ctx.runner.run(list(argv), timeout=timeout)


def _origin_nwo(remote_url: str) -> str | None:
    url = remote_url.strip()
    if not url:
        return None
    url = re.sub(r"^ssh://", "", url)
    url = re.sub(r"^git@github\.com:", "", url)
    url = re.sub(r"^https://github\.com/", "", url)
    url = re.sub(r"\.git$", "", url)
    if url.count("/") != 1:
        return None
    return url


def _ship_one(
    ctx: AppContext,
    repo: Path,
    *,
    abs_git: str,
    abs_gh: str | None,
    abs_bash: str | None,
    dry_run: bool,
    testflight: bool,
    git_timeout: float,
    tf_timeout: float,
) -> McprtRepoResult:
    name = repo.name
    st = _run(
        ctx,
        _git_argv(abs_git, repo, "status", "--porcelain"),
        timeout=git_timeout,
    )
    dirty = bool((st.stdout or "").strip())
    committed = merged = pushed = False
    tf_state: str | None = None
    notes: list[str] = []

    if dry_run:
        msg = "would commit+push" if dirty else "would push"
        if testflight and looks_like_ios_app(repo):
            msg += " + testflight"
            tf_state = "skipped"
        return McprtRepoResult(
            name=name,
            ok=True,
            committed=False,
            message=msg,
            testflight=tf_state,
        )

    if dirty:
        _run(ctx, _git_argv(abs_git, repo, "add", "-A"), timeout=git_timeout)
        cached = _run(
            ctx,
            _git_argv(abs_git, repo, "diff", "--cached", "--name-only"),
            timeout=git_timeout,
        )
        staged = [ln.strip() for ln in (cached.stdout or "").splitlines() if ln.strip()]
        keep = [p for p in staged if not is_secret_rel(p)]
        for secret in (p for p in staged if is_secret_rel(p)):
            _run(
                ctx,
                _git_argv(abs_git, repo, "reset", "-q", "HEAD", "--", secret),
                timeout=git_timeout,
            )
            notes.append(f"skip {secret}")
        if keep:
            msg = f"chore(sync): ship local work {date.today().isoformat()}"
            cr = _run(
                ctx,
                _git_argv(abs_git, repo, "commit", "-m", msg),
                timeout=git_timeout,
            )
            if cr.returncode == 0:
                committed = True
                notes.append("committed")
            else:
                err = (cr.stderr or cr.stdout or "commit failed").strip().splitlines()
                return McprtRepoResult(
                    name=name,
                    ok=False,
                    message=err[0][:200] if err else "commit failed",
                )
        else:
            notes.append("nothing to commit (secrets only or empty)")

    br = _run(
        ctx,
        _git_argv(abs_git, repo, "branch", "--show-current"),
        timeout=git_timeout,
    )
    branch = (br.stdout or "").strip() or "main"

    origin = _run(
        ctx,
        _git_argv(abs_git, repo, "remote", "get-url", "origin"),
        timeout=git_timeout,
    )
    if origin.returncode != 0 or not (origin.stdout or "").strip():
        notes.append("no origin")
        return McprtRepoResult(
            name=name,
            ok=True,
            committed=committed,
            message="; ".join(notes) or "no origin",
        )

    nwo = _origin_nwo(origin.stdout or "")
    if abs_gh and nwo and branch not in ("main", "master"):
        listed = _run(
            ctx,
            [
                abs_gh,
                "pr",
                "list",
                "--repo",
                nwo,
                "--head",
                branch,
                "--state",
                "open",
                "--json",
                "number",
            ],
            timeout=git_timeout,
        )
        pr_num: int | None = None
        if listed.returncode == 0 and (listed.stdout or "").strip():
            try:
                rows = json.loads(listed.stdout)
                if isinstance(rows, list) and rows:
                    pr_num = int(rows[0]["number"])
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                pr_num = None
        if pr_num is not None:
            merged_pr = _run(
                ctx,
                [
                    abs_gh,
                    "pr",
                    "merge",
                    str(pr_num),
                    "--repo",
                    nwo,
                    "--squash",
                    "--delete-branch",
                ],
                timeout=git_timeout,
            )
            if merged_pr.returncode == 0:
                notes.append(f"merged pr #{pr_num}")
                prod = "main"
                _run(
                    ctx,
                    _git_argv(abs_git, repo, "fetch", "--prune", "origin"),
                    timeout=git_timeout,
                )
                _run(
                    ctx,
                    _git_argv(abs_git, repo, "checkout", prod),
                    timeout=git_timeout,
                )
                branch = prod
            else:
                notes.append(f"pr #{pr_num} merge failed")

    fetch = _run(
        ctx,
        _git_argv(abs_git, repo, "fetch", "--prune", "origin"),
        timeout=git_timeout,
    )
    if fetch.returncode == 0:
        mg = _run(
            ctx,
            _git_argv(abs_git, repo, "merge", "--no-edit", f"origin/{branch}"),
            timeout=git_timeout,
        )
        if mg.returncode == 0:
            merged = True
            notes.append("merged origin")
        else:
            err = (mg.stderr or mg.stdout or "merge failed").strip().splitlines()
            return McprtRepoResult(
                name=name,
                ok=False,
                committed=committed,
                message=err[0][:200] if err else "merge failed",
            )
    else:
        notes.append("fetch failed")

    push = _run(
        ctx,
        _git_argv(abs_git, repo, "push", "-u", "origin", "HEAD", "--tags"),
        timeout=git_timeout,
    )
    if push.returncode == 0:
        _run(
            ctx,
            _git_argv(abs_git, repo, "push", "--all"),
            timeout=git_timeout,
        )
        pushed = True
        notes.append("pushed")
    else:
        err = (push.stderr or push.stdout or "push failed").strip().splitlines()
        return McprtRepoResult(
            name=name,
            ok=False,
            committed=committed,
            merged=merged,
            message=err[0][:200] if err else "push failed",
        )

    if testflight and looks_like_ios_app(repo) and abs_bash:
        script = testflight_ship_script(repo)
        if script is None:
            tf_state = "skipped"
            notes.append("testflight skipped (no ship.sh)")
        else:
            tf = _run(
                ctx,
                [
                    abs_bash,
                    str(script),
                    "--dir",
                    str(repo),
                    "--internal-group",
                    "intern",
                    "--external-group",
                    "Extern",
                ],
                timeout=tf_timeout,
            )
            if tf.returncode == 0:
                tf_state = "ok"
                notes.append("testflight ok")
            else:
                tf_state = "fail"
                err = (tf.stderr or tf.stdout or "testflight failed").strip().splitlines()
                notes.append(err[0][:160] if err else "testflight failed")
                return McprtRepoResult(
                    name=name,
                    ok=False,
                    committed=committed,
                    merged=merged,
                    pushed=pushed,
                    testflight=tf_state,
                    message="; ".join(notes),
                )
    elif testflight:
        tf_state = "skipped"

    return McprtRepoResult(
        name=name,
        ok=True,
        committed=committed,
        merged=merged,
        pushed=pushed,
        testflight=tf_state,
        message="; ".join(notes) or "ok",
    )


def run_mcprt(
    ctx: AppContext,
    repos: Sequence[Path | str],
    *,
    dry_run: bool = False,
    testflight: bool = True,
    timeout: float = TIMEOUT_MCPRT_GIT,
) -> McprtResult:
    """Ship each repo (cpr), then TestFlight when it looks like an iOS app."""
    paths = [Path(r) for r in repos if r]
    if not paths:
        return McprtResult(repos=(), dry_run=dry_run)

    try:
        abs_git = ctx.runner.resolve("git")
    except CliError as exc:
        raise CliError(f"mcprt needs git: {exc.message}", exit_code=1) from exc
    try:
        abs_gh = ctx.runner.resolve("gh")
    except CliError:
        abs_gh = None
    try:
        abs_bash = ctx.runner.resolve("bash")
    except CliError:
        abs_bash = None

    git_timeout = max(30.0, min(float(timeout), TIMEOUT_MCPRT_GIT))
    tf_timeout = TIMEOUT_MCPRT_TESTFLIGHT
    rows: list[McprtRepoResult] = []
    for repo in paths:
        if not repo.is_dir():
            rows.append(McprtRepoResult(name=repo.name, ok=False, message="not a directory"))
            continue
        rows.append(
            _ship_one(
                ctx,
                repo,
                abs_git=abs_git,
                abs_gh=abs_gh,
                abs_bash=abs_bash,
                dry_run=dry_run,
                testflight=testflight,
                git_timeout=git_timeout,
                tf_timeout=tf_timeout,
            )
        )
    return McprtResult(repos=tuple(rows), dry_run=dry_run)
