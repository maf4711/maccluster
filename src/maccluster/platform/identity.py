"""Self-node matching (pure)."""

from __future__ import annotations

from maccluster.domain.models import HostIdentity, Node
from maccluster.errors import ConfigError


def match_self(nodes: tuple[Node, ...] | list[Node], identity: HostIdentity) -> Node:
    """Return the single node matching hostname and/or hw_uuid.

    Raises ConfigError (exit 2) if zero or multiple matches.
    """
    host_set = {h.lower() for h in identity.hostnames}
    host_set.add(identity.hostname.lower())
    uuid = (identity.hw_uuid or "").strip().lower()

    matched: list[Node] = []
    for node in nodes:
        host_hit = any(h.lower() in host_set for h in node.hostnames)
        uuid_hit = bool(uuid) and node.hw_uuid.strip().lower() == uuid
        if host_hit or uuid_hit:
            matched.append(node)

    if len(matched) == 1:
        return matched[0]

    compared = f"hostnames={sorted(host_set)!r} hw_uuid={identity.hw_uuid!r}"
    if not matched:
        raise ConfigError(
            f"no self node matched ({compared}); update hostnames or hw_uuid in cluster.toml",
            details={"compared": compared, "matches": 0},
        )
    ids = [m.id for m in matched]
    raise ConfigError(
        f"multiple self node matches: {ids} ({compared}); "
        "ensure hostnames/hw_uuid uniquely identify this host",
        details={"compared": compared, "matches": ids},
    )
