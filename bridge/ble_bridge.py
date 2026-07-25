"""Bitchat side of the bridge.

Acts purely as a BLE *central*: it scans for phones already advertising the
Bitchat GATT service (phones run the peripheral role themselves) and connects
out to them. No peripheral/advertising role needed on our end.
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

logger = logging.getLogger("bridge.ble")

SERVICE_UUID = "f47b5e2d-4a9e-4c5a-9b3f-8e1d2c3a4b5c"
CHAR_UUID = "a1b2c3d4-e5f6-4a5b-8c9d-0e1f2a3b4c5d"  # single characteristic, write + notify

SCAN_INTERVAL = 3.0
FAILED_DEVICE_COOLDOWN = 300  # seconds before retrying a device that failed to connect
DEDUPE_SIZE = 100

OnTextMessage = Callable[[str, str], Awaitable[None]]  # (display_name, text) -> None


class BitchatBridge:
    def __init__(self, nickname: str = "MeshBridge"):
        self.identity = protocol.Identity()
        self.nickname = nickname
        self.on_message: Optional[OnTextMessage] = None

        self._clients: Dict[str, BleakClient] = {}
        self._connecting: set[str] = set()
        self._failed_at: Dict[str, float] = {}
        self._nicknames: Dict[str, str] = {}
        self._seen = deque(maxlen=DEDUPE_SIZE)
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

    async def _handle_packet(self, data: bytes):
        pkt = protocol.parse(data)
        if pkt is None:
            return

        dedupe_key = (pkt.sender_id, len(pkt.payload))
        if dedupe_key in self._seen:
            return
        self._seen.append(dedupe_key)

        sender_hex = pkt.sender_id.hex()
        if pkt.packet_type == protocol.PacketType.ANNOUNCE:
            name = protocol.parse_announce_nickname(pkt.payload)
            if name:
                self._nicknames[sender_hex] = name
            return

        if pkt.packet_type != protocol.PacketType.MESSAGE or not pkt.is_broadcast:
            return  # v1 scope: public/broadcast chat only, no encrypted DMs

        text = pkt.payload.decode("utf-8", errors="ignore")
        display_name = self._nicknames.get(sender_hex, sender_hex[-4:])
        logger.info("(BLE -> bridge) %s: %s", display_name, text)
        if self.on_message:
            await self.on_message(display_name, text)

    async def broadcast(self, text: str):
        if self._stopping:
            return
        packet = protocol.build_message(self.identity, text)
        for addr, client in list(self._clients.items()):
            if not client.is_connected:
                continue
            try:
                await client.write_gatt_char(CHAR_UUID, packet, response=True)
            except Exception:
                logger.debug("Broadcast to %s failed", addr, exc_info=True)

    async def stop(self):
        self._stopping = True
        for client in list(self._clients.values()):
            try:
                await client.disconnect()
            except Exception:
                pass
        self._clients.clear()
