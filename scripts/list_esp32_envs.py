#!/usr/bin/env python3
"""List PlatformIO env names for hardware boards in the firmware submodule.

A board's .ini file lives under firmware/variants/<family>/<board>/*.ini.
Base/shared config files at the top of each chip-family dir (e.g.
variants/esp32/esp32.ini) don't define real board envs, so a plain glob over
the per-board subfiles is enough — no need to hand-maintain a board list.

Usage:
  list_esp32_envs.py <firmware_dir>                 # ESP32 family only (default)
  list_esp32_envs.py <firmware_dir> --all-platforms  # every supported chip family
"""
import glob
import json
import re
import sys

FIRMWARE_DIR = sys.argv[1] if len(sys.argv) > 1 else "firmware"
ALL_PLATFORMS = "--all-platforms" in sys.argv[2:]

ESP32_FAMILIES = ["esp32", "esp32c3", "esp32c6", "esp32p4", "esp32s2", "esp32s3"]
OTHER_FAMILIES = ["nrf52840", "rp2040", "rp2350", "stm32"]
FAMILIES = ESP32_FAMILIES + OTHER_FAMILIES if ALL_PLATFORMS else ESP32_FAMILIES

ENV_RE = re.compile(r"^\[env:([a-zA-Z0-9_.\-]+)\]", re.MULTILINE)

envs = set()
for family in FAMILIES:
    for path in glob.glob(f"{FIRMWARE_DIR}/variants/{family}/*/*.ini"):
        with open(path, encoding="utf-8") as f:
            envs.update(ENV_RE.findall(f.read()))

print(json.dumps(sorted(envs)))
