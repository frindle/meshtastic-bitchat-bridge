"""Reassembly for Bitchat's own BLE-level fragmentation (MessageType.FRAGMENT
= 0x20), used when a packet is too large for one BLE write. A byte-level
relay can't tell a fragment's real type without reassembling it first, so
this has to happen before the relay filter in protocol.py can apply.

Envelope format verified against the official Swift source
(BLEFragmentAssemblyBuffer.swift / BLEFragmentHeader): the FRAGMENT packet's
*payload* is [fragmentID(8, big-endian u64)][index(2, BE)][total(2, BE)]
[originalType(1)][fragment data...], keyed by (outer packet's senderID,
fragmentID). Fragments are concatenated in index order once all `total`
have arrived, yielding the complete original packet's bytes (header,
signature, and padding included) — the same thing `parse()` would have
received directly had it fit in one BLE write.
"""
from __future__ import annotations

import struct
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

MAX_REASSEMBLED_BYTES = 64 * 1024  # generous safety cap, real packets are far smaller
ASSEMBLY_TIMEOUT_S = 30.0
MAX_IN_FLIGHT = 32  # bound memory if many stalled assemblies pile up

Key = Tuple[bytes, int]  # (sender_id, fragment_id)


@dataclass
class _Assembly:
    total: int
    original_type: int
    chunks: Dict[int, bytes] = field(default_factory=dict)
    started_at: float = field(default_factory=time.monotonic)

    @property
    def size(self) -> int:
        return sum(len(c) for c in self.chunks.values())


@dataclass
class Reassembled:
    original_type: int
    data: bytes


class BitchatFragmentAssembler:
    """Feed it outer FRAGMENT packets (sender_id, payload); get back complete
    original packets once all pieces have arrived. Call `expire()`
    periodically to drop stalled assemblies and learn about them (for the
    bridge's drop alert)."""

    def __init__(self):
        self._assemblies: Dict[Key, _Assembly] = {}

    def add(self, sender_id: bytes, fragment_payload: bytes) -> Optional[Reassembled]:
        if len(fragment_payload) < 13:
            return None
        fragment_id = struct.unpack(">Q", fragment_payload[0:8])[0]
        index = struct.unpack(">H", fragment_payload[8:10])[0]
        total = struct.unpack(">H", fragment_payload[10:12])[0]
        original_type = fragment_payload[12]
        chunk = fragment_payload[13:]

        if total == 0 or total > 10_000 or index >= total:
            return None

        key: Key = (sender_id, fragment_id)
        assembly = self._assemblies.get(key)
        if assembly is None:
            if len(self._assemblies) >= MAX_IN_FLIGHT:
                oldest_key = min(self._assemblies, key=lambda k: self._assemblies[k].started_at)
                del self._assemblies[oldest_key]
            assembly = _Assembly(total=total, original_type=original_type)
            self._assemblies[key] = assembly

        if assembly.size + len(chunk) > MAX_REASSEMBLED_BYTES:
            del self._assemblies[key]
            return None

        assembly.chunks[index] = chunk
        if len(assembly.chunks) < assembly.total:
            return None

        del self._assemblies[key]
        reassembled = b"".join(assembly.chunks[i] for i in range(assembly.total))
        return Reassembled(original_type=assembly.original_type, data=reassembled)

    def expire(self) -> list[bytes]:
        """Drop assemblies that have been incomplete for too long. Returns
        the sender_id of each one dropped, so the caller can post a
        message-specific "part of this is missing" notice attributed to
        the actual sender, not just a generic count."""
        now = time.monotonic()
        stale = [k for k, a in self._assemblies.items() if now - a.started_at > ASSEMBLY_TIMEOUT_S]
        for k in stale:
            del self._assemblies[k]
        return [sender_id for sender_id, _fragment_id in stale]
