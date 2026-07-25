# Regional duty-cycle limits and `override_duty_cycle`

This is documentation only — there is no code here, and nothing in this
repo modifies or bypasses anything. It explains an option that already
ships in stock Meshtastic firmware, so that a decision about whether to use
it is made deliberately and with full information, by someone with the
standing to make that call for their own equipment.

## What the limit is

Regions like EU868 have a legally mandated duty-cycle limit (ETSI EN 300
220) on how much of the time a radio may transmit. Meshtastic enforces this
in firmware — see `firmware/src/airtime.cpp`
(`AirTime::isTxAllowedAirUtil()`) and `firmware/src/mesh/Router.cpp`, both
of which check a config field before allowing a transmission:

```cpp
if (!config.lora.override_duty_cycle && effectiveDutyCycle < 100) {
    // ...enforce the limit...
}
```

The effective duty-cycle percentage per region is computed in
`firmware/src/mesh/RadioInterface.cpp` (`getEffectiveDutyCycle()`).

## The existing override — not something we built

Meshtastic already exposes a config field for this:
`config.lora.override_duty_cycle` (defaults to `false`). It's defined in
`firmware/protobufs/meshtastic/config.proto`, with this exact comment from
the Meshtastic project itself:

> If true, duty cycle limits will be exceeded and thus you're possibly not
> following the local regulations if you're not a HAM.
> Has no effect if the duty cycle of the used region is 100%.

**Read that precisely**: this isn't a general "emergency" exemption. It's
built for licensed amateur radio (HAM) operators, who in many countries
operate under a *separate* set of spectrum rules from the unlicensed
ISM-band duty-cycle limit that applies to everyone else. Setting this flag
without holding an applicable license, and without that license's rules
actually permitting the transmission you're about to make, means operating
outside what's legally permitted in that region — full stop, regardless of
how urgent the situation feels.

## If you ever have a genuine, specific legal basis to use it

Step by step, using the official Meshtastic tooling — no firmware
modification needed, since this is already a stock config field:

1. Connect to the device (USB serial or BLE) with the official
   [`meshtastic` Python CLI](https://python.meshtastic.org) or the
   Meshtastic mobile app.
2. Check the current setting first:
   ```bash
   meshtastic --port /dev/ttyACM0 --get lora
   ```
3. Set the override:
   ```bash
   meshtastic --port /dev/ttyACM0 --set lora.override_duty_cycle true
   ```
   (Or via the mobile app: LoRa config screen → advanced settings.)
4. To revert:
   ```bash
   meshtastic --port /dev/ttyACM0 --set lora.override_duty_cycle false
   ```

That's it — the whole "how" is four commands against stock firmware. The
part that actually matters is step zero, which isn't technical: confirming,
for your specific situation and jurisdiction, that you have the legal
standing to do this (e.g. an amateur radio license whose rules cover the
transmission you're making) — and making that determination is yours to
make, not something this project verifies or grants.
