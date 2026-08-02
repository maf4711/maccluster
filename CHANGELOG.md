# Changelog

## 0.1.2 — 2026-08-02

### Fixed / complete
- Peer reachability: ICMP ping with **`-S <self-ip>`** (TB bridge), fallback **TCP:22** when ICMP filtered
- Status shows probe method (`via=tcp:22` / `ping`)
- Example config restored to 4-node template; live 2-node under `examples/studio-live.toml`
- `scripts/remote-install.sh` for peer install over SSH
- Version sync `__version__` / pyproject **0.1.2**

## 0.1.1 — 2026-08-02

### Added
- Live TX/RX rates, pps, errors on `status`/`monitor` (netstat counter deltas)
- `traffic[]` in JSON output; cache under `~/Library/Caches/maccluster/`

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] — 2026-08-01

### Added

- Initial release of MacCluster CLI for Apple Silicon Mac mini Thunderbolt clusters.
- Commands: `tb`, `init`, `config show|validate`, `up`, `heal` [`--loop`], `status`,
  `monitor`, `topo`, `doctor`, `bench`, `service install|uninstall|status`.
- TOML cluster config (`schema_version = 1`) with default subnet `10.42.0.0/24`.
- Shared ensure path for `up` / `heal` (bridge + fixed Self-IP, local only).
- User-domain LaunchAgent for background heal loop.
- Optional `iperf3` bandwidth bench and optional SSH peer probes.
- Plaintext symbols + optional rich monitor; `--json` with `schema_version`.
- Exit codes: 0 ok, 1 error, 2 usage, 3 degraded.
