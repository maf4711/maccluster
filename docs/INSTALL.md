# How to install MacCluster

**Need:** macOS on Apple Silicon · Python 3.11+ · (recommended) [pipx](https://pipx.pypa.io/)

---

## 1) Install (one line)

```bash
curl -fsSL https://raw.githubusercontent.com/maf4711/maccluster/main/install.sh | bash
```

Put `~/.local/bin` on your PATH if needed:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

Check:

```bash
maccluster --version
maccluster --help
```

### Alternatives

```bash
# pipx from Git (latest)
pipx install "git+https://github.com/maf4711/maccluster.git"

# pinned release
pipx install "git+https://github.com/maf4711/maccluster.git@v0.1.1"

# ZIP
curl -fsSL https://github.com/maf4711/maccluster/archive/refs/heads/main.zip -o maccluster.zip
unzip maccluster.zip && cd maccluster-main && ./install.sh
```

Upgrade later:

```bash
curl -fsSL https://raw.githubusercontent.com/maf4711/maccluster/main/install.sh | bash
# or: pipx reinstall maccluster
```

---

## 2) First setup (each Mac mini)

```bash
maccluster init
# edit ~/.config/maccluster/cluster.toml
# — same file on all nodes: hostnames, hw_uuid, IPs 10.42.0.1–.4

maccluster config validate
sudo maccluster up          # sets bridge + Self-IP (admin password)
maccluster service install  # background heal --loop
```

Optional (bandwidth bench):

```bash
brew install iperf3
```

---

## 3) Daily use

```bash
maccluster tb           # Thunderbolt ports / speeds
maccluster status       # nodes + link + TX/RX rates (2nd run shows rates)
maccluster monitor      # live refresh
maccluster topo         # cable map
maccluster doctor       # diagnostics
```

---

## Links

| | |
|---|---|
| Repo | https://github.com/maf4711/maccluster |
| Install script (raw) | https://raw.githubusercontent.com/maf4711/maccluster/main/install.sh |
| ZIP (main) | https://github.com/maf4711/maccluster/archive/refs/heads/main.zip |
| Ops (fleet) | [ops.md](ops.md) |

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `maccluster: command not found` | `export PATH="$HOME/.local/bin:$PATH"` |
| Wrong/old version | `hash -r` · use `~/.local/bin/maccluster --version` · reinstall with install.sh |
| `sudo maccluster up` fails | Run in Terminal with admin rights; or `open scripts/up-with-admin.command` |
| Peers DOWN | Plug TB cables · same `cluster.toml` on all nodes · `maccluster doctor` |
| Rates show `n/a` | Run `status` twice (Δ ≥ ~0.5 s) or use `monitor` |

---

## Remote install (peer Mac mini)

On **this** Mac (node-a), after TB peer has IP `10.42.0.2` and Remote Login enabled:

```bash
# one-time: allow SSH key (enter peer password once)
ssh-copy-id -i ~/.ssh/id_ed25519.pub a321@10.42.0.2

# install + copy config + up + service
cd /path/to/maccluster
./scripts/remote-install.sh a321@10.42.0.2 --copy-config
```

Or on the peer itself:

```bash
curl -fsSL https://raw.githubusercontent.com/maf4711/maccluster/main/install.sh | bash
# place same cluster.toml, then:
sudo maccluster up
maccluster service install
```
