# Thunderbolt cables for MacCluster

## What we use (this studio)

From live `system_profiler` / `maccluster tb` on the fleet:

| Path | Trained speed | Role |
|------|---------------|------|
| **Mac mini ↔ Mac mini** (receptacle ~1) | **40 Gb/s** | Cluster mesh |
| Mac mini ↔ Studio Display | 20 Gb/s | Display (not mesh) |
| Mac mini ↔ LaCie Rugged SSD | 40 Gb/s | Storage |

**Verdict:** The mini-to-mini hop is a **40 Gb/s** trained link → cable path is **excellent / good enough** for a 2–4 node bridge cluster and home sync.

macOS does **not** expose the retail cable SKU (Apple TB4 Pro Cable vs third-party). Classification uses the **link rate the OS negotiated**.

## What to buy

| Prefer | Avoid |
|--------|--------|
| **Thunderbolt 4** cable, 40 Gb/s certified (short ≤0.8–1 m ideal) | USB-C “charge only” / USB 2 |
| **Thunderbolt 5** cable (works; may train at 40G on TB4 hosts) | Long passive cables that drop to 20G |
| Direct mini ↔ mini | Via Studio Display / cheap hub for mesh |

## Grades in code (`maccluster speedtest` / `tb` / `doctor`)

| Link | Grade | Cluster? |
|------|-------|----------|
| ≥ 40 Gb/s | excellent | yes |
| ≥ 20 Gb/s | good | yes (prefer upgrade) |
| &lt; 20 Gb/s | marginal | investigate |

## Commands

```bash
maccluster tb                 # ports + cable summary
maccluster speedtest          # cable + iperf3 (bridge bind)
maccluster speedtest --cable-only
maccluster doctor             # includes cable finding
```

`sync home` and `remote-install` run a short speedtest **first**.
