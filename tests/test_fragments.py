"""Self-checks for both fragmentation layers: Bitchat's own BLE-level
fragments (bitchat_fragments.py) and our LoRa-hop envelope (lora_fragment.py),
including the resend-request path.
"""
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bridge import lora_fragment
from bridge.bitchat_fragments import BitchatFragmentAssembler


def _bitchat_fragment_payload(fragment_id: int, index: int, total: int, original_type: int, chunk: bytes) -> bytes:
    return struct.pack(">QHHB", fragment_id, index, total, original_type) + chunk


def test_bitchat_fragment_reassembly():
    assembler = BitchatFragmentAssembler()
    sender = b"\x01" * 8
    data = b"hello from a fragmented noise-encrypted message"
    mid = len(data) // 2
    part_a, part_b = data[:mid], data[mid:]

    assert assembler.add(sender, _bitchat_fragment_payload(42, 0, 2, 0x11, part_a)) is None
    result = assembler.add(sender, _bitchat_fragment_payload(42, 1, 2, 0x11, part_b))
    assert result is not None
    assert result.original_type == 0x11
    assert result.data == data


def test_bitchat_fragment_out_of_order():
    assembler = BitchatFragmentAssembler()
    sender = b"\x02" * 8
    chunks = [b"AAA", b"BBB", b"CCC"]
    for index in (2, 0, 1):
        result = assembler.add(sender, _bitchat_fragment_payload(7, index, 3, 0x02, chunks[index]))
    assert result is not None
    assert result.data == b"".join(chunks)


def test_lora_encode_decode_round_trip():
    data = b"x" * 500  # bigger than one chunk, forces multiple fragments
    msg_id, chunks = lora_fragment.encode(data)
    assert len(chunks) > 1

    assembler = lora_fragment.LoRaFragmentAssembler()
    reassembled = None
    for chunk in chunks:
        reassembled = assembler.add("!aaaa", chunk)
    assert reassembled == data


def test_lora_resend_request_roundtrip():
    msg_id = 1234
    control = lora_fragment.encode_resend_request(msg_id)
    assert lora_fragment.is_resend_request(control) == msg_id

    # A real data chunk (count >= 1) must never be misread as a control packet
    _, chunks = lora_fragment.encode(b"short")
    assert lora_fragment.is_resend_request(chunks[0]) is None


def test_lora_stalled_assembly_requests_resend_once():
    assembler = lora_fragment.LoRaFragmentAssembler()
    _, chunks = lora_fragment.encode(b"y" * 500)
    assembler.add("!bbbb", chunks[0])  # only the first of several chunks — leaves it incomplete

    key = ("!bbbb", struct.unpack(">H", chunks[0][:2])[0])
    assembler._assemblies[key].started_at = time.monotonic() - lora_fragment.STALL_THRESHOLD_S - 1

    due = assembler.due_for_resend_request()
    assert due == [key]
    assert assembler.due_for_resend_request() == []  # not requested twice


def test_sent_message_cache():
    cache = lora_fragment.SentMessageCache()
    cache.remember(99, [b"a", b"b"])
    assert cache.get(99) == [b"a", b"b"]
    assert cache.get(100) is None


if __name__ == "__main__":
    test_bitchat_fragment_reassembly()
    test_bitchat_fragment_out_of_order()
    test_lora_encode_decode_round_trip()
    test_lora_resend_request_roundtrip()
    test_lora_stalled_assembly_requests_resend_once()
    test_sent_message_cache()
    print("All fragment self-checks passed.")
