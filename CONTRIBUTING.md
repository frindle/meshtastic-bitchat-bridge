# Contributing

PRs and issues welcome — this is meant to be a shared tool for extending
Bitchat's range with Meshtastic hardware.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m pytest tests/   # or: python tests/test_protocol.py
```

## Scope

The bridge relays 1:1 DMs, group messages, and peer announcements — **never**
public/broadcast chat, by design (see README's Scope section for why). It
never decrypts anything; Noise-encrypted content is relayed as opaque bytes.

Known gap: Bitchat's own BLE-level packet fragmentation (`bridge/bitchat_fragments.py`)
is reassembled so the relay filter can see the real packet type underneath,
but very unusual fragment patterns (e.g. an assembly that never completes)
just time out and alert rather than being specially handled — if you hit a
real-world case that needs more nuance here, that's a good place to dig in.

## Guidelines

- Keep the bridge a relay, not a Bitchat client — it shouldn't need its own
  UI or persistent chat history.
- Any change to `bridge/protocol.py` should keep/extend the round-trip tests
  in `tests/test_protocol.py`. Same for the fragmentation layers
  (`bridge/bitchat_fragments.py`, `bridge/lora_fragment.py`) and
  `tests/test_fragments.py`.
- If you're adding a new relay path (e.g. a new packet type), note it in
  `docs/protocol-notes.md` so the wire format stays documented in one place,
  and add it to `protocol.RELAYED_TYPES`/`SKIPPED_TYPES` deliberately —
  never relay a new type by default without thinking through whether it
  could carry public/spammable content.
