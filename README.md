# meshtastic-bitchat-bridge

Bridges [Bitchat](https://github.com/permissionlesstech/bitchat)'s BLE mesh
with a [Meshtastic](https://meshtastic.org) LoRa mesh, so Bitchat messages can
hop onto LoRa and reach a distant Bitchat cluster — extending Bitchat's normal
BLE-only range using cheap long-range radios.

The bridge runs on a Linux box (Raspberry Pi or laptop) with Bluetooth and a
Meshtastic radio, and relays public chat messages in both directions:

- **Bitchat → Meshtastic**: scans for nearby Bitchat phones over BLE, forwards
  any public chat message it hears onto the LoRa mesh.
- **Meshtastic → Bitchat**: listens for LoRa text traffic, rebroadcasts it to
  any connected Bitchat phones over BLE.

It's a relay, not a client — no chat history, no UI, just plumbing.

## Scope (v1)

Only **public/broadcast** Bitchat messages and nicknames are relayed.
Noise-encrypted private DMs and group messages are intentionally out of
scope for now — see `CONTRIBUTING.md` if you want to take that on.

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
public messages start flowing over LoRa.

## How it works

The bridge only needs to act as a BLE **central** — Bitchat phones already
run as BLE peripherals advertising the chat service, so the bridge just
connects out to them; it never needs to advertise/host a GATT server itself.
On the Meshtastic side it uses the official `meshtastic` Python package's
local device API (serial or BLE) and relays plain text, letting Meshtastic
handle its own LoRa fragmentation.

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
- Initial bridge implementation: Bitchat BLE relay (central-only), Meshtastic
  serial/BLE link, public-message relay in both directions.
- MIT licensed.
