"""ASCII sketches for `docsync tb`: which receptacle holds what, and wrong-vs-right cabling."""

from __future__ import annotations

import re
import subprocess

from docsync.thunderbolt import TBPort, TBState, parse_profiler_text

# model prefix -> (label, number of Thunderbolt ports on the back/side, number of USB-C-only ports)
# Mac mini M4 / M4 Pro: 3× Thunderbolt on the back, 2× USB-C (USB 3, no Thunderbolt) on the front.
LAYOUTS: list[tuple[str, str, int, int, str]] = [
    ("Mac16,10", "Mac mini (M4)", 3, 2, "front ports are USB-C only — no Thunderbolt, no bridge"),
    ("Mac16,11", "Mac mini (M4 Pro)", 3, 2, "front ports are USB-C only — no Thunderbolt, no bridge"),
    ("Mac14,3", "Mac mini (M2)", 2, 0, ""),
    ("Mac14,12", "Mac mini (M2 Pro)", 4, 0, ""),
    ("Mac16,9", "Mac Studio (M4 Max)", 4, 2, "front ports are USB-C only — use the back"),
    ("Mac15,14", "Mac Studio (M3 Ultra)", 6, 0, ""),
    ("Mac17,", "MacBook Pro (M5)", 3, 0, ""),
    ("Mac16,", "MacBook Pro (M4)", 3, 0, ""),
    ("Mac15,", "MacBook Pro (M3)", 3, 0, ""),
]


def layout(model: str) -> tuple[str, int, int, str]:
    for prefix, label, tb, usb, note in LAYOUTS:
        if model.startswith(prefix):
            return label, tb, usb, note
    return model or "Mac", 0, 0, ""


def local_model() -> str:
    try:
        return subprocess.run(["sysctl", "-n", "hw.model"], text=True, capture_output=True, timeout=5).stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""


REMOTE_CMD = ("sysctl -n hw.model; echo '=== TB'; system_profiler SPThunderboltDataType 2>/dev/null; "
              "echo '=== BRIDGE'; ifconfig bridge0 2>/dev/null | grep -E 'inet |status'")


def parse_remote(out: str) -> tuple[str, TBState]:
    """Parse REMOTE_CMD output into (model, TBState)."""
    model, _, rest = out.partition("=== TB")
    tb_text, _, bridge = rest.partition("=== BRIDGE")
    st = TBState(ports=parse_profiler_text(tb_text))
    st.interfaces = ["bridge0"] if "bridge0" in bridge or "inet " in bridge else []
    m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", bridge)
    if m and "inactive" not in bridge:
        st.active_ifaces["bridge0"] = [m.group(1)]
    return model.strip(), st


def port_cell(p: TBPort) -> str:
    if p.connected:
        dev = p.peer_label
        return f"TB{p.receptacle}: {dev} {p.speed_gbps} Gb/s" if p.speed_gbps else f"TB{p.receptacle}: {dev}"
    return f"TB{p.receptacle}: free"


def sketch_mac(name: str, model: str, st: TBState, highlight_free: bool = False) -> str:
    label, n_tb, n_usb, note = layout(model)
    ports = sorted(st.ports, key=lambda p: (p.receptacle or "9"))
    cells = [port_cell(p) for p in ports]
    if highlight_free:
        cells = [c + " ◀ plug here" if c.endswith("free") else c for c in cells]
    tb_line = "  ".join(f"[{c}]" for c in cells) if cells else f"[{n_tb}× Thunderbolt]"
    side = "back " if n_usb else "ports"
    lines = [f"   {name}  —  {label} ({model or '?'})",
             f"     {side}: {tb_line}"]
    if n_usb:
        lines.append(f"     front: " + "  ".join("(USB-C ✗ no TB)" for _ in range(n_usb)) + f"   ← {note}")
    ip = ", ".join(a for addrs in st.active_ifaces.values() for a in addrs)
    lines.append(f"     bridge0: {ip if ip else 'no IPv4 (no Thunderbolt link)'}")
    return "\n".join(lines)


def sketch_wrong_right(local_name: str, peer_name: str, peer_model: str) -> str:
    _, _, n_usb, _ = layout(peer_model)
    front = f"{peer_name} FRONT port" if n_usb else f"a USB-only port / cable"
    return "\n".join([
        "   ✗ wrong                                              ✓ right",
        f"   {local_name:<10} ──cable──▶ {front:<26}   {local_name:<10} ──cable──▶ {peer_name} BACK port (TB1-3)",
        "              USB-C only: no Thunderbolt, no bridge0,             Thunderbolt 5: bridge0 gets 10.42.0.x,",
        "              system_profiler: 'No device connected'              docsync syncs 'via Thunderbolt NN Gb/s'",
        "   also check: cable must be a Thunderbolt/USB4 cable (⚡ logo), not a charge-only USB-C cable;",
        "               the peer must be awake; a Thunderbolt display on the same port is fine (daisy chain).",
    ])
