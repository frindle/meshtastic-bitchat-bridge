"""Round-trip self-check for the bitchat wire protocol. Run: python -m pytest tests/
or just: python tests/test_protocol.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bridge import protocol


def test_message_round_trip():
    identity = protocol.Identity()
    packet = protocol.build_message(identity, "hello mesh")

    parsed = protocol.parse(packet)
    assert parsed is not None
    assert parsed.packet_type == protocol.PacketType.MESSAGE
    assert parsed.sender_id == identity.sender_id
    assert parsed.is_broadcast
    assert parsed.payload == b"hello mesh"


def test_announce_round_trip():
    identity = protocol.Identity()
    packet = protocol.build_announce(identity, "MeshBridge")

    parsed = protocol.parse(packet)
    assert parsed is not None
    assert parsed.packet_type == protocol.PacketType.ANNOUNCE
    assert protocol.parse_announce_nickname(parsed.payload) == "MeshBridge"


def test_short_packet_rejected():
    assert protocol.parse(b"\x00" * 5) is None


def test_padding_preserves_exact_payload():
    identity = protocol.Identity()
    packet = protocol.build_message(identity, "x")
    assert len(packet) in (256, 512, 1024, 2048) or len(packet) < 256
    assert protocol.parse(packet).payload == b"x"


if __name__ == "__main__":
    test_message_round_trip()
    test_announce_round_trip()
    test_short_packet_rejected()
    test_padding_preserves_exact_payload()
    print("All protocol self-checks passed.")
