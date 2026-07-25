"""Bitchat binary wire protocol: packet build/parse, signing, padding.

Format verified against the official Swift source (permissionlesstech/bitchat)
and cross-checked against the Kotlin Android port. See ../docs/protocol-notes.md.
"""
from __future__ import annotations

import hashlib
import struct
import time
from dataclasses import dataclass, field
from typing import Optional

import lz4.block
import nacl.signing

VERSION = 0x01
HEADER_SIZE = 14  # ver(1) type(1) ttl(1) timestamp(8) flags(1) payloadLen(2)
DEFAULT_TTL = 7
BROADCAST_ID = b"\xff" * 8
PAD_BLOCK_SIZES = (256, 512, 1024, 2048)


class PacketType:
    ANNOUNCE = 0x01
    MESSAGE = 0x02
    LEAVE = 0x03


class Flag:
    HAS_RECIPIENT = 0x01
    HAS_SIGNATURE = 0x02
    IS_COMPRESSED = 0x04


def pad(data: bytes) -> bytes:
    """Pad to the next bitchat privacy block size (PKCS#7-style byte value),
    matching BinaryProtocol's padding. No-ops if the +16 encryption-overhead
    margin doesn't fit any block or padding would exceed 255 bytes."""
    for size in PAD_BLOCK_SIZES:
        if len(data) + 16 <= size:
            needed = size - len(data)
            if needed == 0 or needed > 255:
                return data
            return data + bytes([needed]) * needed
    return data


@dataclass
class Identity:
    """A bitchat peer identity: Ed25519 signing key + derived 8-byte sender ID."""

    signing_key: nacl.signing.SigningKey = field(default_factory=nacl.signing.SigningKey.generate)

    @property
    def public_key(self) -> bytes:
        return bytes(self.signing_key.verify_key)

    @property
    def sender_id(self) -> bytes:
        # Bitchat derives the sender ID from the signing (Ed25519) public key.
        return hashlib.sha256(self.public_key).digest()[:8]


def _header(packet_type: int, ttl: int, timestamp_ms: int, flags: int, payload_len: int) -> bytes:
    return struct.pack(">BBBQBH", VERSION, packet_type, ttl, timestamp_ms, flags, payload_len)


def _build_signed(identity: Identity, packet_type: int, payload: bytes, recipient_id: Optional[bytes]) -> bytes:
    """The signature covers the packet as it would be encoded with TTL=0 and no
    signature flag, then padded — that's what the app verifies against, so we
    have to reconstruct that exact canonical form before signing."""
    timestamp_ms = int(time.time() * 1000)
    flags = Flag.HAS_SIGNATURE | (Flag.HAS_RECIPIENT if recipient_id else 0)
    body = identity.sender_id + (recipient_id or b"") + payload

    canonical = _header(packet_type, 0, timestamp_ms, flags & ~Flag.HAS_SIGNATURE, len(payload)) + body
    signature = identity.signing_key.sign(pad(canonical)).signature

    final = _header(packet_type, DEFAULT_TTL, timestamp_ms, flags, len(payload)) + body + signature
    return pad(final)


def build_announce(identity: Identity, nickname: str) -> bytes:
    name = nickname.encode("utf-8")[:255]
    payload = bytes([0x01, len(name)]) + name  # TLV tag 0x01 = nickname
    return _build_signed(identity, PacketType.ANNOUNCE, payload, recipient_id=BROADCAST_ID)


def build_message(identity: Identity, text: str) -> bytes:
    return _build_signed(identity, PacketType.MESSAGE, text.encode("utf-8"), recipient_id=BROADCAST_ID)


@dataclass
class ParsedPacket:
    packet_type: int
    sender_id: bytes
    recipient_id: Optional[bytes]
    payload: bytes

    @property
    def is_broadcast(self) -> bool:
        return self.recipient_id is None or self.recipient_id == BROADCAST_ID


def parse(data: bytes) -> Optional[ParsedPacket]:
    if len(data) < HEADER_SIZE:
        return None

    packet_type = data[1]
    flags = data[11]
    payload_len = struct.unpack(">H", data[12:14])[0]
    has_recipient = bool(flags & Flag.HAS_RECIPIENT)
    is_compressed = bool(flags & Flag.IS_COMPRESSED)

    offset = HEADER_SIZE
    sender_id = bytes(data[offset : offset + 8])
    offset += 8

    recipient_id = None
    if has_recipient:
        recipient_id = bytes(data[offset : offset + 8])
        offset += 8

    if offset + payload_len > len(data):
        return None
    raw_payload = bytes(data[offset : offset + payload_len])

    if is_compressed:
        try:
            raw_payload = lz4.block.decompress(raw_payload[2:], uncompressed_size=65536)
        except Exception:
            return None

    return ParsedPacket(packet_type=packet_type, sender_id=sender_id, recipient_id=recipient_id, payload=raw_payload)


def parse_announce_nickname(payload: bytes) -> Optional[str]:
    """Pull the nickname (TLV tag 0x01) out of an ANNOUNCE payload."""
    i = 0
    while i + 2 <= len(payload):
        tag, length = payload[i], payload[i + 1]
        i += 2
        if i + length > len(payload):
            break
        if tag == 0x01:
            return payload[i : i + length].decode("utf-8", errors="ignore")
        i += length
    return None
