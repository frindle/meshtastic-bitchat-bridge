# Contributing

PRs and issues welcome — this is meant to be a shared tool for extending
Bitchat's range with Meshtastic hardware.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m pytest tests/   # or: python tests/test_protocol.py
```

## Scope of v1

The bridge currently relays only **public/broadcast** Bitchat chat messages
and nicknames — it does not decrypt Noise-encrypted private DMs, group
messages, or files. See `docs/protocol-notes.md` for the full wire format;
adding private-message support means implementing the Noise XX handshake and
holding per-peer session state, which is a natural next contribution.

## Guidelines

- Keep the bridge a relay, not a Bitchat client — it shouldn't need its own
  UI or persistent chat history.
- Any change to `bridge/protocol.py` should keep/extend the round-trip tests
  in `tests/test_protocol.py`.
- If you're adding a new relay path (e.g. a new packet type), note it in
  `docs/protocol-notes.md` so the wire format stays documented in one place.
