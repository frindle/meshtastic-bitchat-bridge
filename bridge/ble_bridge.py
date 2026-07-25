"""Bitchat side of the bridge.

Acts purely as a BLE *central*: it scans for phones already advertising the
Bitchat GATT service (phones run the peripheral role themselves) and connects
out to them. No peripheral/advertising role needed on our end.

Relays 1:1 DMs, group messages, and Noise handshakes as opaque bytes — never
decrypted, never rebuilt, since that content is already end-to-end encrypted
between the real Bitchat clients. ANNOUNCE (public keys + nickname) is also
relayed, verbatim, so peers on the far side of the bridge can discover this
side's peers and start a DM — but rate-limited per sender, since its nickname
field is free text and relaying it unrestricted would let it be used to
flood the whole mesh the same way a public message would.

Public broadcast chat (MESSAGE, LEAVE) is intentionally never relayed.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Awaitable, Callable, Dict, Optional

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice

from . import protocol
from .bitchat_fragments import BitchatFragmentAssembler

logger = logging.getLogger("bridge.ble")

SERVICE_UUID = "f47b5e2d-4a9e-4c5a-9b3f-8e1d2c3a4b5c"
CHAR_UUID = "a1b2c3d4-e5f6-4a5b-8c9d-0e1f2a3b4c5d"  # single characteristic, write + notify

SCAN_INTERVAL = 3.0
FAILED_DEVICE_COOLDOWN = 300  # seconds before retrying a device that failed to connect
DEDUPE_SIZE = 100
ANNOUNCE_RELAY_COOLDOWN_S = 10 * 60  # per-sender: caps announce-nickname flooding
FRAGMENT_EXPIRE_INTERVAL_S = 5.0

OnRelayPacket = Callable[[bytes], Awaitable[None]]  # raw bitchat packet bytes -> None


class BitchatBridge:
    def __init__(self, nickname: str = "MeshBridge"):
        self.identity = protocol.Identity()
        self.nickname = nickname
        self.on_relay_packet: Optional[OnRelayPacket] = None

        self._clients: Dict[str, BleakClient] = {}
        self._connecting: set[str] = set()
        self._failed_at: Dict[str, float] = {}
        self._seen = deque(maxlen=DEDUPE_SIZE)
        self._announce_relayed_at: Dict[bytes, float] = {}
        self._nicknames: Dict[bytes, str] = {}  # sender_id -> last-known nickname, for alert attribution only
        self._fragments = BitchatFragmentAssembler()
        self._last_fragment_expire = time.monotonic()
        self._stopping = False

    async def run(self):
        logger.info("Scanning for Bitchat peers (identity %s)...", self.identity.sender_id.hex())
        while not self._stopping:
            try:
                found = await BleakScanner.discover(timeout=SCAN_INTERVAL, return_adv=True)
                for device, adv in found.values():
                    if SERVICE_UUID not in [u.lower() for u in adv.service_uuids]:
                        continue
                    self._maybe_connect(device)
            except Exception:
                logger.debug("Scan pass failed", exc_info=True)
                await asyncio.sleep(1.0)

            if time.monotonic() - self._last_fragment_expire > FRAGMENT_EXPIRE_INTERVAL_S:
                dropped_senders = self._fragments.expire()
                self._last_fragment_expire = time.monotonic()
                for sender_id in dropped_senders:
                    await self.alert(f"message from {self._display_name(sender_id)} is incomplete (part of it was lost)")

    def _maybe_connect(self, device: BLEDevice):
        addr = device.address
        if addr in self._clients or addr in self._connecting:
            return
        failed_at = self._failed_at.get(addr)
        if failed_at and time.time() - failed_at < FAILED_DEVICE_COOLDOWN:
            return
        self._failed_at.pop(addr, None)
        self._connecting.add(addr)
        asyncio.create_task(self._connect(device))

    async def _connect(self, device: BLEDevice, retries: int = 3):
        addr = device.address
        for attempt in range(retries):
            client = BleakClient(addr, timeout=10.0, disconnected_callback=self._on_disconnect)
            try:
                await client.connect()
                await client.start_notify(CHAR_UUID, self._make_notify_handler(addr))
                await client.write_gatt_char(
                    CHAR_UUID, protocol.build_announce(self.identity, self.nickname), response=True
                )
                self._clients[addr] = client
                logger.info("Connected to Bitchat peer %s", addr)
                return
            except Exception as exc:
                logger.debug("Connect attempt %d/%d to %s failed: %s", attempt + 1, retries, addr, exc)
                try:
                    if client.is_connected:
                        await client.disconnect()
                except Exception:
                    pass
                if attempt < retries - 1:
                    await asyncio.sleep(2.0 * (2**attempt))
        self._failed_at[addr] = time.time()
        self._connecting.discard(addr)

    def _on_disconnect(self, client: BleakClient):
        self._clients.pop(client.address, None)
        self._connecting.discard(client.address)
        logger.info("Bitchat peer %s disconnected", client.address)

    def _make_notify_handler(self, addr: str):
        def handler(_handle: int, data: bytearray):
            self._connecting.discard(addr)
            asyncio.create_task(self._handle_packet(bytes(data)))

        return handler

    def _display_name(self, sender_id: bytes) -> str:
        return self._nicknames.get(sender_id, sender_id.hex()[-4:])

    async def _handle_packet(self, data: bytes):
        pkt = protocol.parse(data)
        if pkt is None:
            return

        dedupe_key = (pkt.sender_id, len(pkt.payload))
        if dedupe_key in self._seen:
            return
        self._seen.append(dedupe_key)

        if pkt.packet_type == protocol.PacketType.ANNOUNCE:
            name = protocol.parse_announce_nickname(pkt.payload)
            if name:
                self._nicknames[pkt.sender_id] = name  # kept for alert attribution, not the relay decision

        if pkt.packet_type == protocol.PacketType.FRAGMENT:
            reassembled = self._fragments.add(pkt.sender_id, pkt.payload)
            if reassembled is None:
                return  # still waiting on more fragments (or malformed — silently ignored)
            await self._route(pkt.sender_id, reassembled.original_type, reassembled.data)
            return

        await self._route(pkt.sender_id, pkt.packet_type, data)

    async def _route(self, sender_id: bytes, packet_type: int, raw: bytes):
        if packet_type in protocol.SKIPPED_TYPES:
            return  # public broadcast chat — never relayed, by design

        if packet_type in protocol.UNSUPPORTED_TYPES:
            await self.alert(f"message from {self._display_name(sender_id)} can't be relayed yet (unsupported fragment format)")
            return

        if packet_type not in protocol.RELAYED_TYPES:
            return  # unrecognized type — ignore rather than relay something unvetted

        if packet_type == protocol.PacketType.ANNOUNCE:
            last = self._announce_relayed_at.get(sender_id)
            if last and time.monotonic() - last < ANNOUNCE_RELAY_COOLDOWN_S:
                return  # rate-limited: caps nickname-field flooding, see module docstring
            self._announce_relayed_at[sender_id] = time.monotonic()

        if self.on_relay_packet:
            await self.on_relay_packet(raw)

    async def alert(self, text: str):
        logger.warning("Bridge alert: %s", text)
        packet = protocol.build_alert(self.identity, f"⚠️ [Bridge] {text}")
        for client in list(self._clients.values()):
            if not client.is_connected:
                continue
            try:
                await client.write_gatt_char(CHAR_UUID, packet, response=True)
            except Exception:
                logger.debug("Alert write failed", exc_info=True)

    async def replay(self, raw_packet: bytes):
        """Write a raw (already-encrypted, already-signed) packet received
        from the LoRa side back out to every connected phone verbatim —
        same flood semantics as real Bitchat peers use: everyone hears it,
        only the intended recipient(s) can decrypt it."""
        if self._stopping:
            return
        for addr, client in list(self._clients.items()):
            if not client.is_connected:
                continue
            try:
                await client.write_gatt_char(CHAR_UUID, raw_packet, response=True)
            except Exception:
                logger.debug("Replay to %s failed", addr, exc_info=True)

    async def stop(self):
        self._stopping = True
        for client in list(self._clients.values()):
            try:
                await client.disconnect()
            except Exception:
                pass
        self._clients.clear()
