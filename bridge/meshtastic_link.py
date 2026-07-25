"""Meshtastic (LoRa) side of the bridge: serial/BLE connection with
auto-reconnect, relaying raw Bitchat packet bytes as PRIVATE_APP data
(fragmented to fit Meshtastic's ~237-byte payload ceiling — see
lora_fragment.py), never as human-readable text.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable, Optional

import meshtastic.ble_interface
import meshtastic.serial_interface
from meshtastic.protobuf import portnums_pb2
from pubsub import pub

from .lora_fragment import LoRaFragmentAssembler, SentMessageCache, encode, encode_resend_request, is_resend_request

logger = logging.getLogger("bridge.meshtastic")

RECONNECT_MIN_DELAY = 5
RECONNECT_MAX_DELAY = 20
EXPIRE_INTERVAL_S = 5.0

OnRelayPacket = Callable[[bytes], Awaitable[None]]
OnAlert = Callable[[str], Awaitable[None]]


class MeshtasticLink:
    """Connects to a Meshtastic node either over USB serial (dedicated/wired
    deployment) or over BLE (mobile deployment — pair to a radio wirelessly,
    e.g. one carried in a backpack, no cable needed)."""

    def __init__(
        self,
        port: Optional[str] = None,
        ble_address: Optional[str] = None,
        fallback_ports: tuple[str, ...] = ("/dev/ttyUSB0",),
    ):
        if bool(port) == bool(ble_address):
            raise ValueError("Provide exactly one of port (serial) or ble_address (BLE)")
        self.port = port
        self.ble_address = ble_address
        self._fallback_ports = fallback_ports
        self.on_relay_packet: Optional[OnRelayPacket] = None
        self.on_alert: Optional[OnAlert] = None
        self._interface = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._subscribed = False
        self._stopping = False
        self._assembler = LoRaFragmentAssembler()
        self._sent_cache = SentMessageCache()
        self._last_expire = time.monotonic()

    def start(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop
        if not self._try_connect():
            logger.warning("Meshtastic device not found, will keep retrying in the background")
            asyncio.create_task(self._reconnect_loop())
        asyncio.create_task(self._expire_loop())

    def _try_connect(self) -> bool:
        if self.ble_address:
            candidates = [("ble", self.ble_address)]
        else:
            candidates = [("serial", p) for p in (self.port, *self._fallback_ports)]

        for kind, target in candidates:
            try:
                if kind == "ble":
                    self._interface = meshtastic.ble_interface.BLEInterface(target)
                else:
                    self._interface = meshtastic.serial_interface.SerialInterface(target)
                if not self._subscribed:
                    pub.subscribe(self._on_receive, "meshtastic.receive")
                    pub.subscribe(self._on_connection_lost, "meshtastic.connection.lost")
                    self._subscribed = True
                node = self._interface.getMyNodeInfo().get("user", {})
                logger.info("Meshtastic connected via %s %s (node: %s)", kind, target, node.get("longName", "unknown"))
                return True
            except Exception as exc:
                logger.debug("Meshtastic connect failed via %s %s: %s", kind, target, exc)
        return False

    def _on_connection_lost(self, interface=None):
        logger.warning("Meshtastic connection lost, reconnecting...")
        self._close_interface()
        asyncio.run_coroutine_threadsafe(self._reconnect_loop(), self._loop)

    def _close_interface(self):
        if self._interface:
            try:
                self._interface.close()
            except Exception:
                pass
            self._interface = None

    async def _reconnect_loop(self):
        delay = RECONNECT_MIN_DELAY
        while not self._stopping and self._interface is None:
            await asyncio.sleep(delay)
            if self._stopping:
                return
            if self._try_connect():
                logger.info("Meshtastic reconnected")
                return
            delay = min(delay * 2, RECONNECT_MAX_DELAY)

    async def _expire_loop(self):
        while not self._stopping:
            await asyncio.sleep(EXPIRE_INTERVAL_S)
            self._sent_cache.expire()

            for from_node_id, msg_id in self._assembler.due_for_resend_request():
                logger.info("Stalled assembly from %s (msg %d) — requesting resend", from_node_id, msg_id)
                self._send_raw(encode_resend_request(msg_id), destination_id=from_node_id)

            dropped = self._assembler.expire()
            if dropped and self.on_alert:
                await self.on_alert(f"{dropped} message(s) from the mesh couldn't be relayed (LoRa loss/timeout)")

    def _on_receive(self, packet, interface):
        try:
            decoded = packet.get("decoded", {})
            if decoded.get("portnum") != "PRIVATE_APP":
                return
            envelope = decoded.get("payload")
            if not envelope:
                return
            envelope = bytes(envelope)
            from_node_id = packet["fromId"]

            requested_msg_id = is_resend_request(envelope)
            if requested_msg_id is not None:
                cached = self._sent_cache.get(requested_msg_id)
                if cached:
                    logger.info("Resend requested by %s for msg %d — resending", from_node_id, requested_msg_id)
                    self._send_raw_chunks(cached, destination_id=from_node_id)
                return

            reassembled = self._assembler.add(from_node_id, envelope)
            if reassembled is None:
                return
            logger.info("(LoRa -> bridge) relaying %d bytes from %s", len(reassembled), from_node_id)
            if self.on_relay_packet:
                asyncio.run_coroutine_threadsafe(self.on_relay_packet(reassembled), self._loop)
        except Exception:
            logger.debug("Failed to process LoRa packet", exc_info=True)

    def send_packet(self, raw: bytes):
        if self._interface is None:
            logger.debug("Meshtastic disconnected, dropping %d-byte packet", len(raw))
            if self.on_alert:
                asyncio.run_coroutine_threadsafe(
                    self.on_alert("a message couldn't be relayed (Meshtastic disconnected)"), self._loop
                )
            return

        encoded = encode(raw)
        if encoded is None:
            logger.warning("Packet too large to fragment for LoRa (%d bytes)", len(raw))
            if self.on_alert:
                asyncio.run_coroutine_threadsafe(
                    self.on_alert("a message couldn't be relayed (too large for LoRa)"), self._loop
                )
            return

        msg_id, chunks = encoded
        self._sent_cache.remember(msg_id, chunks)
        self._send_raw_chunks(chunks)

    def _send_raw_chunks(self, chunks: list[bytes], destination_id=None):
        for chunk in chunks:
            self._send_raw(chunk, destination_id=destination_id)

    def _send_raw(self, data: bytes, destination_id=None):
        if self._interface is None:
            return
        try:
            kwargs = {"portNum": portnums_pb2.PortNum.PRIVATE_APP}
            if destination_id is not None:
                kwargs["destinationId"] = destination_id
            self._interface.sendData(data, **kwargs)
        except Exception:
            logger.warning("Meshtastic send failed, connection likely dead", exc_info=True)
            self._on_connection_lost()

    def stop(self):
        self._stopping = True
        self._close_interface()
