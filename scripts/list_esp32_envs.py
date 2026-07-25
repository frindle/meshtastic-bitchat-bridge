#!/usr/bin/env python3
"""List PlatformIO env names for ESP32-family boards in the firmware submodule.

An ESP32-family board's .ini file lives under firmware/variants/esp32*/ (the
chip-family directories: esp32, esp32c3, esp32c6, esp32s2, esp32s3, esp32p4).
Base/shared config files at the top of each chip-family dir (e.g.
variants/esp32/esp32.ini) don't define real board envs, so a plain glob over
the per-board subfiles is enough — no need to hand-maintain a board list.
"""
import glob
import json
import re
import sys

FIRMWARE_DIR = sys.argv[1] if len(sys.argv) > 1 else "firmware"

ENV_RE = re.compile(r"^\[env:([a-zA-Z0-9_.\-]+)\]", re.MULTILINE)

envs = set()
for path in glob.glob(f"{FIRMWARE_DIR}/variants/esp32*/*/*.ini"):
    with open(path, encoding="utf-8") as f:
        envs.update(ENV_RE.findall(f.read()))

print(json.dumps(sorted(envs)))
