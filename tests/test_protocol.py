"""Round-trip self-check for the bitchat wire protocol. Run: python -m pytest tests/
or just: python tests/test_protocol.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bridge import protocol


def test_alert_round_trip():
    identity = protocol.Identity()
    packet = protocol.build_alert(identity, "hello mesh")

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
    packet = protocol.build_alert(identity, "x")
    assert len(packet) in (256, 512, 1024, 2048) or len(packet) < 256
    assert protocol.parse(packet).payload == b"x"


def test_relay_filter_excludes_public_chat():
    assert protocol.PacketType.MESSAGE in protocol.SKIPPED_TYPES
    assert protocol.PacketType.LEAVE in protocol.SKIPPED_TYPES
    assert protocol.PacketType.MESSAGE not in protocol.RELAYED_TYPES


def test_relay_filter_includes_dms_groups_and_announce():
    assert protocol.PacketType.NOISE_HANDSHAKE in protocol.RELAYED_TYPES
    assert protocol.PacketType.NOISE_ENCRYPTED in protocol.RELAYED_TYPES
    assert protocol.PacketType.ANNOUNCE in protocol.RELAYED_TYPES


if __name__ == "__main__":
    test_alert_round_trip()
    test_announce_round_trip()
    test_short_packet_rejected()
    test_padding_preserves_exact_payload()
    test_relay_filter_excludes_public_chat()
    test_relay_filter_includes_dms_groups_and_announce()
    print("All protocol self-checks passed.")
