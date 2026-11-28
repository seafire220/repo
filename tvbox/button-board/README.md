# The button board (the desk device on a TV box)

A TV box has no pins for buttons, so the desk device's buttons go through a tiny USB board instead:
**the same buttons and switch as the Raspberry Pi build**, wired to a A$7 microcontroller board that the box sees
as a button box. No driver and no settings on the box. The whole desk device is in
`docs/38-tv-box-desk-device.md`.

| Control | What it does on the box | Sent as (Linux key) |
|---|---|---|
| **Listen button** | Press, speak, and the character answers. Held 2 s: the microphone off or on. Held 12 s: forget the Wi-Fi and the settings-page password (as on the Pi) | Play/Pause (KEY_PLAYPAUSE, already one of the box's button keys) |
| **Power button** | **Hold for a second**: the box switches itself off cleanly (the screen goes dark; then it is safe to switch the power off). A quick knock does nothing | Power (KEY_POWER, answered by systemd-logind) |
| **Mute switch** (optional) | While it is on, the microphone is off, and nothing on the settings page can turn it back on (as on the Pi) | Phone Mute, held (KEY_MICMUTE, the Pi's mute key) |

None of these is a keyboard key, so the box does not see a key repeating while the switch is on for hours.
The board's own light is red while the mute switch is on, blue while the listen button is down, and amber while
the power button is held.

## Parts

| Part | Price | Notes |
|---|---|---|
| **Raspberry Pi Pico H** (pins already fitted) | A$8.65 (Core Electronics) | No soldering: the leads push onto its pins |
| or **Waveshare RP2040-Zero** | A$6.60 (Core Electronics) | Smaller; solder six header pins (or the leads) to GP0 to GP5 |
| Two push buttons, a slide switch, jumper leads | as the Pi build | The same parts, `docs/29-hardware-setup.md` |
| A USB cable to the box | | USB-C for the RP2040-Zero, micro-USB for the Pico |

## Wiring

Each control goes on **two pins side by side**: the board reads the first and holds the second at 0 V for it,
so no ground wire is shared and nothing needs splicing. Buttons and switches have no polarity: either lead on
either pin.

| Control | Pins (printed on the board) | Pico H pin numbers |
|---|---|---|
| Listen button | **GP0** and **GP1** | 1 and 2 |
| Power button | **GP2** and **GP3** | 4 and 5 |
| Mute switch | **GP4** and **GP5** (switch closed = microphone off) | 6 and 7 |

## Putting the software on (once per board)

1. Download CircuitPython (free, MIT licence) for the board from circuitpython.org: the *Waveshare RP2040-Zero*
   page or the *Raspberry Pi Pico* page (the `.uf2` file, version 9 or later).
2. Hold the board's **BOOT** button (BOOTSEL on the Pico) while plugging it into a computer. A drive called
   **RPI-RP2** appears: copy the `.uf2` file onto it. The board restarts and a drive called **CIRCUITPY** appears.
3. Copy **`buttons.py`**, **`boot.py`** and **`code.py`** from this folder onto CIRCUITPY (replace the
   `code.py` that is there).
4. Unplug it. From now on it is a button box: plugged into the TV box (or any computer) it shows no drive.

To change the files later, **hold the listen button while plugging the board into a computer**: CIRCUITPY
comes back. To start again from scratch, repeat step 2.

## Checking it

On a computer with Linux, `sudo evtest` lists *Companion Button board Consumer Control*; pressing the listen
button shows KEY_PLAYPAUSE, the switch KEY_MICMUTE (value 1 while on), and holding the power button for a
second KEY_POWER (**careful: that computer may switch itself off**). On the box, the maker's check
(`check-on-box.sh`) lists the board among the buttons, and the support report names it among the input devices.

## How it works

`buttons.py` holds what the board sends: one byte, a bit each for Play/Pause, Power and Phone Mute, in a USB
HID report that Linux's own HID driver turns into those keys. It sends a change at once and the whole state
again every second, so a box that has just started learns where the switch is. `code.py` reads the pins
(debounced, with CircuitPython's `keypad`) and drives the light; `boot.py` names the board and turns off its
drive and serial port. The tests on a computer check the report's layout and the timing:
`core/tests/test_tvbox.py` (ButtonBoardTest).
