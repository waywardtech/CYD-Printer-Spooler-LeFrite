# CYD_LE_SPOOLER_Arduino

The Arduino sketch for the CYD side of LE SPOOLER. Build instructions are in
[`../README.md`](../README.md).

| File | Purpose |
|---|---|
| `CYD_LE_SPOOLER_Arduino.ino` | Paper renderer, print mechanism, sounds, polling, SD, touch, animations, serial |
| `config.example.h` | Settings template. Copy it to `spooler_config.h` (git-ignored) and set Wi-Fi, server URL and pins. |

## Overstrike on a real screen
Glyphs are drawn with a transparent background, so the screen really does overstrike.
`e`, backspace, `'` stamps an apostrophe over the `e` and you see an é. That's true to
the hardware: a daisywheel never "knew" about é either.
