"""Entry point: wires the Bitchat BLE side to the Meshtastic LoRa side."""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal

from .ble_bridge import BitchatBridge
from .meshtastic_link import MeshtasticLink

logger = logging.getLogger("bridge")


async def run(meshtastic_port: str | None, meshtastic_ble: str | None, nickname: str):
    loop = asyncio.get_running_loop()
    ble = BitchatBridge(nickname=nickname)
    mesh = MeshtasticLink(port=meshtastic_port, ble_address=meshtastic_ble)

    ble.on_message = lambda name, text: mesh_send(mesh, name, text)
    mesh.on_text = lambda name, text: ble.broadcast(f"[Mesh:{name}] {text}")

    mesh.start(loop)
    scan_task = asyncio.create_task(ble.run())

    stop_event = asyncio.Event()
    loop.add_signal_handler(signal.SIGINT, stop_event.set)
    loop.add_signal_handler(signal.SIGTERM, stop_event.set)

    await stop_event.wait()

    logger.info("Shutting down...")
    mesh.stop()
    await ble.stop()
    scan_task.cancel()


async def mesh_send(mesh: MeshtasticLink, name: str, text: str):
    mesh.send_text(f"[Bit:{name}] {text}")


def main():
    parser = argparse.ArgumentParser(description="Meshtastic <-> Bitchat bridge")
    conn = parser.add_mutually_exclusive_group()
    conn.add_argument("--port", help="Meshtastic USB serial port (e.g. /dev/ttyACM0) — wired/dedicated deployment")
    conn.add_argument("--ble", dest="ble_address", help="Meshtastic BLE address/name — mobile/wireless deployment")
    parser.add_argument("--nickname", default="MeshBridge", help="Bitchat nickname to advertise")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    if not args.port and not args.ble_address:
        args.port = "/dev/ttyACM0"  # default to wired for backwards-compatible behavior

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        asyncio.run(run(args.port, args.ble_address, args.nickname))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
