# Protocol notes

Findings from initial protocol research (2026-07-25), verified against live
source where noted.

## Bitchat (`permissionlesstech/bitchat`, Swift, verified via GitHub fetch)

### Wire format
`BinaryProtocol.swift` + `BitchatPacket.swift`, all big-endian:

```
version(1) type(1) ttl(1) timestamp(8,u64) flags(1) payloadLen(2 v1 / 4 v2)
senderID(8) [recipientID(8) if flags&0x01] [route: count(1)+hops(8 each), v2+ if flags&0x08]
[origSize(2/4) if flags&0x04 compressed] payload(var) [signature(64) if flags&0x02]
```

- v1 header = 14 bytes, v2 = 16 bytes (bigger length field).
- Flags: `0x01` hasRecipient, `0x02` hasSignature, `0x04` isCompressed (zlib),
  `0x08` hasRoute (v2+), `0x10` isRSR.
- Whole packet gets PKCS#7-style padding to 256/512/1024/2048 blocks
  (`MessagePadding`).
- Max packet 65,535 bytes (v1) / larger (v2). BLE MTU target ~512 bytes;
  larger payloads fragmented via `MessageType.fragment = 0x20`.

### Message types (`MessageType.swift`)
`announce=0x01, message=0x02, leave=0x03, courierEnvelope=0x04,
noiseHandshake=0x10, noiseEncrypted=0x11, fragment=0x20, requestSync=0x21,
fileTransfer=0x22, boardPost=0x23, prekeyBundle=0x24, groupMessage=0x25,
ping=0x26, pong=0x27, nostrCarrier=0x28, voiceFrame=0x29`

Everything private (chat, receipts, delivery acks) rides inside
`noiseEncrypted`, distinguished only after decryption by a `NoisePayloadType`
byte (0x01 privateMessage, 0x02 readReceipt, 0x03 delivered, 0x06/0x07 group,
0x08 voice, 0x10/0x11 verify, 0x12 vouch) — by design, for traffic-analysis
resistance.

Public `announce`/`message`/`leave` payloads are TLV-encoded
(`Protocols/Packets.swift`), e.g. `AnnouncementPacket`: TLV type(1)+len(1)+value
— nickname=0x01, noisePublicKey=0x02, signingPublicKey=0x03,
directNeighbors=0x04, capabilities=0x05, bridgeGeohash=0x06.

### BLE transport (`Services/BLE/BLEService.swift`)
- One GATT service UUID: `F47B5E2D-4A9E-4C5A-9B3F-8E1D2C3A4B5A` (testnet) /
  `...5C` (mainnet).
- One characteristic UUID (write + notify):
  `A1B2C3D4-E5F6-4A5B-8C9D-0E1F2A3B4C5D`.
- Every device is simultaneously central and peripheral — classic flood/relay
  mesh, no fixed roles.

### Noise usage
XX pattern for interactive peer handshakes (mutual auth + forward secrecy,
ephemeral Curve25519). X pattern for one-shot store-and-forward "courier"
envelopes. Static keys are Curve25519; a separate Ed25519 signing key is
advertised in the announce packet.

### Gaps
No non-Swift reference implementation found in-repo (Android port is a
separate community repo, not yet checked). No `PROTOCOL.md` — format lives
entirely in source, cross-checked against code comments.

## Meshtastic

- Library: `pip install meshtastic`. Connect via `SerialInterface()` (USB) or
  `TCPInterface(hostname)`.
- Send: `interface.sendText(msg)` for plain text, or
  `interface.sendData(payload, portNum=...)` for arbitrary bytes. Both
  thread-safe.
- Receive: pubsub — `from pubsub import pub; pub.subscribe(onReceive,
  "meshtastic.receive")`; callback fires on a background thread with a packet
  dict.
- LoRa payload ceiling ~237 bytes per packet; Meshtastic handles its own
  fragmentation for longer sends.
- Channels are pre-shared-key symmetric encryption groups — a different trust
  model than Noise's per-peer keys. The bridge rides on top of whatever
  channel the Pi's radio is configured for; it doesn't change Meshtastic's
  channel crypto.
- Local device link (serial/TCP, wrapping `FromRadio`/`ToRadio` protobufs) is
  the right integration point for a bridge daemon — not MQTT (that's for
  internet-connected gateway nodes).
- **PortNum**: use `PRIVATE_APP` (256) for bridged Bitchat payloads via
  `sendData(..., portNum=PRIVATE_APP)`. Ports 0-63 are core-only, 64-127
  require PR registration, 256-511 is open for private/custom apps with no
  registration needed — confirmed via `portnums.proto`.

## Architecture implication

Bitchat packets (up to ~512 bytes typical, larger when fragmented) don't fit
in one ~237-byte Meshtastic payload. The bridge needs its own
fragmentation/reassembly layer wrapping each Bitchat packet for the LoRa hop
(distinct from Bitchat's own BLE-side fragmentation) — e.g. a small envelope
header (message ID, fragment index/count) around chunks of the raw Bitchat
packet bytes, reassembled on the far side before being replayed onto BLE
verbatim. This lets the bridge stay a dumb relay — it doesn't need to decrypt
Noise payloads, just move opaque Bitchat packets across the LoRa hop.
