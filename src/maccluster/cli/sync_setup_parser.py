"""`maccluster sync` sub-command tree root and `sync setup` (kept out of parser.py, which is
past its size limit; parser.py adds `home` and `dev` to the returned sub-parsers)."""

from __future__ import annotations


def add_sync_parser(sub):
    """Create `sync`, add `sync setup` and return the sync sub-parsers for home/dev."""
    p_sync = sub.add_parser(
        "sync",
        help="Sync data with peers over TB/SSH (home, ~/Developer, or `setup` of docsync on every node)",
    )
    sync_sub = p_sync.add_subparsers(dest="sync_action", metavar="TARGET")
    p = sync_sub.add_parser(
        "setup",
        help=(
            "Give every cluster node the same docsync set-up: roots documents, desktop, "
            "llm-claude/codex/gemini; peers TB first then .local; one agent com.a321.docsync "
            "(every 15 min, --max-runtime 1800); removes com.maccluster.sync-home; "
            "clones or fast-forwards ~/dotfiles (a dirty clone is left alone)"
        ),
    )
    p.add_argument("--dry-run", action="store_true", help="Print planned changes, write nothing")
    p.add_argument("--node", metavar="NAME", help="Only this node (cluster.toml id or IP)")
    return sync_sub
