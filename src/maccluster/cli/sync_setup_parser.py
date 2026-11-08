"""`maccluster sync setup` arguments (kept out of parser.py, which is past its size limit)."""

from __future__ import annotations


def add_sync_setup_parser(sync_sub) -> None:
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
