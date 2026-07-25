"""Our own fragmentation for the LoRa hop — distinct from Bitchat's BLE-level
fragmentation (bitchat_fragments.py). A relayed Bitchat packet can be up to
~2KB (its own padding block sizes), far bigger than Meshtastic's ~237-byte
LoRa payload ceiling, so it needs to be split for this specific hop and
reassembled on the far side back into the exact original bytes before being
replayed onto BLE.

Envelope (4 bytes overhead, entirely our own design — not part of the
Bitchat protocol): [msgID(2, BE)][index(1)][count(1)][chunk...]

count == 0 is a reserved sentinel marking a *resend request* control packet
rather than a data chunk (a real message always has count >= 1), carrying no
chunk data: [msgID(2, BE)][0x00][0x00]. If an assembly stalls partway (some
but not all chunks arrived) for longer than STALL_THRESHOLD_S, the receiver
sends one of these back to whoever sent the fragments, asking them to
resend — giving loss on one LoRa path a real second chance via retransmission,
on top of the multi-path redundancy flood-routing already provides, before
the assembly is finally given up on at ASSEMBLY_TIMEOUT_S.
"""
from __future__ import annotations

import random
import struct
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

ENVELOPE_SIZE = 4
MESHTASTIC_PAYLOAD_MAX = 237
CHUNK_SIZE = MESHTASTIC_PAYLOAD_MAX - ENVELOPE_SIZE
MAX_CHUNKS = 255  # count is one byte, and count == 0 is reserved (see module docstring)
STALL_THRESHOLD_S = 10.0  # incomplete for this long -> ask the sender to resend, once
ASSEMBLY_TIMEOUT_S = 30.0  # still incomplete after this -> give up, alert
MAX_IN_FLIGHT = 16
SEND_CACHE_TTL_S = 60.0  # how long a sender keeps a message available to resend on request

Key = Tuple[str, int]  # (from_node_id, msg_id)


def encode(data: bytes) -> Optional[Tuple[int, List[bytes]]]:
    """Split `data` into LoRa-sized envelope chunks under a fresh msg_id.
    Returns None if it's too large to fit even at the max chunk count
    (caller should alert/drop)."""
    chunks = [data[i : i + CHUNK_SIZE] for i in range(0, len(data), CHUNK_SIZE)] or [b""]
    if len(chunks) > MAX_CHUNKS:
        return None
    msg_id = random.randint(0, 0xFFFF)
    count = len(chunks)
    return msg_id, [struct.pack(">HBB", msg_id, index, count) + chunk for index, chunk in enumerate(chunks)]


def encode_resend_request(msg_id: int) -> bytes:
    return struct.pack(">HBB", msg_id, 0, 0)


def is_resend_request(envelope: bytes) -> Optional[int]:
    """Returns the requested msg_id if `envelope` is a resend-request control
    packet, else None."""
    if len(envelope) != ENVELOPE_SIZE:
        return None
    msg_id, index, count = struct.unpack(">HBB", envelope)
    return msg_id if count == 0 and index == 0 else None


@dataclass
class _Assembly:
    total: int
    chunks: Dict[int, bytes] = field(default_factory=dict)
    started_at: float = field(default_factory=time.monotonic)
    resend_requested: bool = False


class LoRaFragmentAssembler:
    def __init__(self):
        self._assemblies: Dict[Key, _Assembly] = {}

    def add(self, from_node_id: str, envelope: bytes) -> Optional[bytes]:
        if len(envelope) < ENVELOPE_SIZE:
            return None
        msg_id, index, count = struct.unpack(">HBB", envelope[:ENVELOPE_SIZE])
        chunk = envelope[ENVELOPE_SIZE:]
        if count == 0 or index >= count:
            return None  # resend requests are handled separately by the caller (is_resend_request)

        key: Key = (from_node_id, msg_id)
        assembly = self._assemblies.get(key)
        if assembly is None:
            if len(self._assemblies) >= MAX_IN_FLIGHT:
                oldest = min(self._assemblies, key=lambda k: self._assemblies[k].started_at)
                del self._assemblies[oldest]
            assembly = _Assembly(total=count)
            self._assemblies[key] = assembly

        assembly.chunks[index] = chunk
        if len(assembly.chunks) < assembly.total:
            return None

        del self._assemblies[key]
        return b"".join(assembly.chunks[i] for i in range(assembly.total))

    def due_for_resend_request(self) -> List[Key]:
        """Incomplete assemblies stalled past STALL_THRESHOLD_S that haven't
        already had a resend requested. Marks them as requested."""
        now = time.monotonic()
        due = []
        for key, assembly in self._assemblies.items():
            if assembly.resend_requested:
                continue
            if now - assembly.started_at > STALL_THRESHOLD_S:
                assembly.resend_requested = True
                due.append(key)
        return due

    def expire(self) -> List[Key]:
        now = time.monotonic()
        stale = [k for k, a in self._assemblies.items() if now - a.started_at > ASSEMBLY_TIMEOUT_S]
        for k in stale:
            del self._assemblies[k]
        return stale


@dataclass
class _SentMessage:
    chunks: List[bytes]
    sent_at: float = field(default_factory=time.monotonic)


class SentMessageCache:
    """Lets a sender answer a resend request: keeps recently-sent chunk sets
    around briefly so they can be replayed verbatim on request."""

    def __init__(self):
        self._sent: Dict[int, _SentMessage] = {}

    def remember(self, msg_id: int, chunks: List[bytes]):
        self._sent[msg_id] = _SentMessage(chunks=chunks)

    def get(self, msg_id: int) -> Optional[List[bytes]]:
        entry = self._sent.get(msg_id)
        return entry.chunks if entry else None

    def expire(self):
        now = time.monotonic()
        stale = [k for k, e in self._sent.items() if now - e.sent_at > SEND_CACHE_TTL_S]
        for k in stale:
            del self._sent[k]
