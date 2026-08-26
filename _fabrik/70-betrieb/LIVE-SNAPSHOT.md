# Live snapshot 2026-08-02T10:45:34Z

## version
maccluster 0.1.3

## doctor
doctor worst=warn
  [ok     ] config: config valid — studio-cluster
  [ok     ] self: self=node-a — ip=10.42.0.1
  [ok     ] thunderbolt: 5 port(s), 3 connected — system_profiler
  [ok     ] bridge: bridge0 up=True — addrs=['10.42.0.1']
  [warn   ] peers: 1 peer(s) unreachable — node-b
  [info   ] iperf3: iperf3 available

## status
cluster: studio-cluster  overall=degraded  ts=2026-08-02T10:45:40.775768+00:00
bridge: bridge0 exists=True up=True addrs=10.42.0.1
* node-a       10.42.0.1       [UP] up      [LINK] connected
  node-b       10.42.0.2       [DOWN] down    [??] unknown via=ping
traffic Δ1.6s:
  bridge0     RX      0 b/s (   0 pps)  TX    206 b/s (   1 pps)  err in/out 0/0 (+0/+0)
  en2         RX      0 b/s (   0 pps)  TX      0 b/s (   0 pps)  err in/out 0/0 (+0/+0)
  en3         RX      0 b/s (   0 pps)  TX      0 b/s (   0 pps)  err in/out 0/0 (+0/+0)
  en4         RX      0 b/s (   0 pps)  TX      0 b/s (   0 pps)  err in/out 0/0 (+0/+0)

## service
label: com.maccluster.heal
installed: True
running: True
plist: /Users/a321/Library/LaunchAgents/com.maccluster.heal.plist
interval_seconds: -
detail: running

## tb (head)
Thunderbolt (system_profiler) — Mac mini
  receptacle 3: [LINK] connected cap=USB4/TB speed=20 Gb/s iface=- peer=Mac mini
    domain=3D589BC7-F32A-4054-BE1A-F8253AD4FE51
  receptacle ?: [LINK] connected cap=USB4/TB speed=20 Gb/s iface=- peer=Studio Display
  receptacle 2: [LINK] connected cap=USB4/TB speed=40 Gb/s iface=- peer=Mac mini
    domain=E5C15E80-C140-4B6D-89CC-8A9846961AC7
  receptacle ?: [NO-LINK] unconnected cap=USB4/TB speed=40 Gb/s iface=- peer=no peer
  receptacle 1: [NO-LINK] unconnected cap=USB4/TB speed=120 Gb/s iface=- peer=no peer
    domain=2877CCA3-0C34-4652-B0B9-A00BE06237EE
