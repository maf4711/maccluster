# Thunderbolt Receptacle → Interface Mapping

MacCluster must map physical Thunderbolt receptacles to network interfaces before
mutating the host (`up` / `heal`). Mapping is **fail-closed**: if the target
interface cannot be determined uniquely, mutate commands exit **2** and do not
guess.

## Defaults

| Config field | Default | Notes |
|---|---|---|
| `bridge_interface` | `bridge0` | Preferred Thunderbolt bridge name on macOS |
| Override | CLI / config | Operator may set an explicit interface if known |

## Known Mac mini layouts (reference)

Layouts are approximate and may drift with macOS / hardware revisions. Always
verify with `maccluster tb` and `ifconfig` on your fleet.

### Apple Silicon Mac mini (typical rear ports)

| Physical position (rear, left→right looking at rear) | Receptacle (profiler) | Typical net iface |
|---|---|---|
| Leftmost TB/USB4 | Receptacle 1 | member of `bridge0` |
| Center TB/USB4 | Receptacle 2 | member of `bridge0` |
| Rightmost TB/USB4 | Receptacle 3 | member of `bridge0` |

On many systems the OS aggregates TB links under **`bridge0`**. MacCluster
prefers the configured `bridge_interface` when it exists and is allowlisted.

## Resolution order (mutate)

1. Config `bridge_interface` if present and valid (`^[A-Za-z][A-Za-z0-9_.-]{0,15}$`).
2. Mapping from live Thunderbolt probe + known layout tables.
3. **Fail closed** (exit 2) if ambiguous or missing — no Wi-Fi / `en0` mutation.

## Operator override

If auto-mapping is wrong for your hardware:

```toml
bridge_interface = "bridge0"   # or another validated TB bridge name
```

Never point this at Wi-Fi (`en0`) or primary Ethernet unless you fully understand
the risk — MacCluster will still refuse non-allowlisted mutation targets beyond
the configured/mapped TB bridge.

## Fail-closed rule (A-039)

- Ambiguous receptacle → interface: **no mutate**.
- Missing interface: **no mutate**.
- Clear message naming what was compared.

## Offline / docs

This document ships with the product. No cloud lookup is performed for mapping.
