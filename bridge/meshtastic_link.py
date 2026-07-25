"""Meshtastic (LoRa) side of the bridge: serial connection with auto-reconnect."""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional

import meshtastic.ble_interface
import meshtastic.serial_interface
from pubsub import pub

logger = logging.getLogger("bridge.meshtastic")

RECONNECT_MIN_DELAY = 5
RECONNECT_MAX_DELAY = 20
BRIDGE_TAG = "[Bit]"  # marks messages we originated, so we don't re-relay our own echo

OnText = Callable[[str, str], Awaitable[None]]  # (sender_name, text) -> None


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
        self.on_text: Optional[OnText] = None
        self._interface = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._subscribed = False
        self._stopping = False

    def start(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop
        if not self._try_connect():
            logger.warning("Meshtastic device not found, will keep retrying in the background")
            asyncio.create_task(self._reconnect_loop())

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

    def _sender_name(self, from_id: str) -> str:
        node = self._interface.nodes.get(from_id) if self._interface and self._interface.nodes else None
        return node.get("user", {}).get("longName", from_id) if node else from_id

    def _on_receive(self, packet, interface):
        try:
            text = packet.get("decoded", {}).get("text")
            if not text or text.startswith(BRIDGE_TAG):
                return
            sender = self._sender_name(packet["fromId"])
            logger.info("(LoRa -> bridge) %s: %s", sender, text)
            if self.on_text:
                asyncio.run_coroutine_threadsafe(self.on_text(sender, text), self._loop)
        except Exception:
            logger.debug("Failed to process LoRa packet", exc_info=True)

    def send_text(self, text: str):
        if self._interface is None:
            logger.debug("Meshtastic disconnected, dropping: %s", text)
            return
        try:
            self._interface.sendText(text)
        except Exception:
            logger.warning("Meshtastic send failed, connection likely dead", exc_info=True)
            self._on_connection_lost()

    def stop(self):
        self._stopping = True
        self._close_interface()
