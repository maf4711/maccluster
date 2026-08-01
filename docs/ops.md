# MacCluster operations

## This fleet (studio-cluster)

| Node | Role | Hostnames | TB IP | HW UUID |
|------|------|-----------|-------|---------|
| node-a | this mini | `CM-CFMQ2D029F` (+ aliases) | `10.42.0.1` | `409C591A-9803-5203-B8C9-E72E73A3EF6E` |
| node-b | peer (mDNS seen) | `CM-KWFVR7JGW3` | `10.42.0.2` | fill on that machine |
| node-c | slot | placeholders | `10.42.0.3` | fill |
| node-d | slot | placeholders | `10.42.0.4` | fill |

Hardware: Apple Silicon Mac mini M4 class, **Thunderbolt 5** ports (up to 120 Gb/s).  
A negotiated **40 Gb/s** Mac↔Mac link usually means a TB4-class cable.

## One-time per member

```bash
cd /path/to/maccluster
./scripts/install-member.sh
# if sudo needed interactively:
sudo maccluster up
maccluster service install
```

Copy the **same** `cluster.toml` to every node (edit UUIDs/hostnames first):

```bash
./scripts/sync-config-from-here.sh a321@peer-host
```

## Daily

```bash
maccluster status
maccluster monitor
maccluster doctor
maccluster topo
maccluster tb
```

## After reboot

Heal service runs best-effort without root. If bridge IP missing:

```bash
sudo maccluster heal
# or
sudo maccluster up
```

## Cables

Plug Thunderbolt cables between minis (mesh/ring as available).  
`maccluster topo` maps Domain UUIDs — it does not tell you how to rewire.

## Uninstall service

```bash
maccluster service uninstall
```
