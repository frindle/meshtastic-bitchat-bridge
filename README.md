# meshtastic-bitchat-bridge

Bridges [Bitchat](https://github.com/permissionlesstech/bitchat)'s BLE mesh
with a [Meshtastic](https://meshtastic.org) LoRa mesh, so Bitchat's 1:1 and
group messages can hop onto LoRa and reach a distant Bitchat cluster —
extending Bitchat's normal BLE-only range using cheap long-range radios.

The bridge runs on a Linux box (Raspberry Pi or laptop) with Bluetooth and a
Meshtastic radio, and relays in both directions:

- **Bitchat → Meshtastic**: scans for nearby Bitchat phones over BLE, relays
  private (Noise-encrypted) messages, group messages, and peer
  announcements onto the LoRa mesh as opaque bytes.
- **Meshtastic → Bitchat**: listens for relayed traffic on LoRa, replays it
  verbatim to any connected Bitchat phones over BLE.

It's a relay, not a client — no chat history, no UI, no decryption, just
plumbing. Since 1:1/group content is already end-to-end encrypted between
the real Bitchat clients, the bridge never needs to decrypt or re-sign
anything it relays.

## Scope

**Public/broadcast chat is intentionally never relayed** — a joke or spam
message posted publicly should not be able to flood out across every bridge
on the mesh. Only relayed:

- 1:1 direct messages and group messages (Noise handshake + encrypted
  packets — wire types `0x10`/`0x11`)
- Peer `ANNOUNCE` packets (public keys + nickname) — needed so peers on
  opposite sides of the bridge can discover each other and start a DM in
  the first place. Rate-limited per sender (once per 10 minutes) since the
  nickname field is free text and could otherwise be used to flood the
  mesh the same way a public message would.

Not yet supported: Bitchat's own BLE-level packet fragmentation (very long
DMs that get split before hitting the wire) — the bridge posts a visible
alert rather than silently dropping these. See `CONTRIBUTING.md`.

## Reliability

- **Multi-path delivery comes from Meshtastic itself**: flood routing means
  a packet can travel multiple independent paths simultaneously; Meshtastic
  dedupes by packet ID, so the bridge only needs to worry about total loss
  across every path, not partial loss on one.
- **Resend requests**: the bridge fragments each relayed packet to fit
  LoRa's ~237-byte payload. If a fragment set stalls partway for 10s, the
  bridge asks the original sender to resend (the sender keeps recently-sent
  chunks around for exactly this) before finally giving up at 30s.
- **Visible drop alerts**: if a message still can't be relayed (LoRa
  timeout, disconnected radio, unsupported BLE fragment, oversized
  payload), the bridge posts a plain "⚠️ [Bridge] ..." notice to nearby
  Bitchat phones rather than failing silently.

## Requirements

- A Linux computer with a Bluetooth adapter (Raspberry Pi works well).
- A Meshtastic device, connected either:
  - via USB serial (e.g. `/dev/ttyACM0`) — dedicated/fixed deployment, or
  - via BLE — mobile/wireless deployment, no cable needed.
- Python 3.9+.

## Install

```bash
git clone https://github.com/frindle/meshtastic-bitchat-bridge
cd meshtastic-bitchat-bridge
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### iOS pairing note

To avoid an "Enter PIN to pair" popup when an iPhone running Bitchat connects,
BlueZ needs its `main.conf` (`/etc/bluetooth/main.conf`) `[General]` section set to:

```ini
JustWorksRepairing = never
ControllerMode = le
Privacy = device
```

## Usage

```bash
# Wired Meshtastic radio (default port /dev/ttyACM0)
python -m bridge.main --port /dev/ttyACM0

# Wireless: pair to a Meshtastic radio over BLE instead
python -m bridge.main --ble <device-name-or-address>
```

Open Bitchat on a phone near the bridge — it connects automatically and its
DMs/group messages start flowing over LoRa.

## How it works

The bridge only needs to act as a BLE **central** — Bitchat phones already
run as BLE peripherals advertising the chat service, so the bridge just
connects out to them; it never needs to advertise/host a GATT server itself.
It relays raw packet bytes (never decrypting or rebuilding them), fragmented
to fit Meshtastic's ~237-byte payload over its own `PRIVATE_APP`-port
envelope (distinct from Bitchat's own BLE-level fragmentation), sent via
the official `meshtastic` Python package (serial or BLE).

Full wire-format details (verified against Bitchat's own Swift source) are in
[`docs/protocol-notes.md`](docs/protocol-notes.md).

## Credits

Built from scratch against the documented/verified protocol, informed by
prior art from:
- [GigaProjects/meshtastic-bitchat-bridge](https://github.com/GigaProjects/meshtastic-bitchat-bridge) — same idea, used as an architecture/gotchas reference.
- [kaganisildak/bitchat-python](https://github.com/kaganisildak/bitchat-python) — a Python Bitchat protocol implementation.
- [permissionlesstech/bitchat](https://github.com/permissionlesstech/bitchat) / [bitchat-android](https://github.com/permissionlesstech/bitchat-android) — the canonical protocol source.
- [meshtastic/python](https://github.com/meshtastic/python) — the Meshtastic Python API.

## Changelog

### Unreleased
- Redesigned around private/group relay instead of public broadcast: raw
  packet bytes relayed for DMs, group messages, and rate-limited peer
  announcements; public chat is never relayed, by design.
- Added LoRa-hop fragmentation with stall-triggered resend requests, and
  visible bridge alerts on any relay failure.
- Added Bitchat's own BLE-level fragment reassembly (needed since relay
  decisions are made by packet type, which fragmentation otherwise hides).
- Initial bridge implementation: Bitchat BLE relay (central-only), Meshtastic
  serial/BLE link.
- MIT licensed.
