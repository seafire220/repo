# The desk device on a TV box: the Pi build's parts around a cheaper computer

Status: 2026-11-28. A design, with the software it needs built and tested on a computer (`core/tests/test_tvbox.py`);
**no part has been bought or tried yet.** Prices are a snapshot from Australian shops on this date (sources at the
end); those marked *est.* are my estimates.

The owner asked: "Isn't it possible to connect the TV box to same Pi parts to build the device? My plan is simply to
use the TV box to make it cheaper." **Yes.** The same desk device (a small screen with the character, the ReSpeaker
Lite, a listen button, a power button and a mute switch) can be built around an S905X3 TV box (the X96 Max Plus)
instead of a Raspberry Pi. Most parts carry over; three must change, because a TV box has no screen socket other
than HDMI and no pins for buttons.

## What carries over, and what changes

| Pi build part | On the TV box | Why |
|---|---|---|
| ReSpeaker Lite, speaker, its USB cable | **Unchanged** | USB audio: the box's Linux uses it exactly as the Pi's does |
| Push buttons, slide switch, jumper leads | **Unchanged** | Wired to a small USB board instead of the Pi's pins |
| Raspberry Pi 5 1 GB | **The TV box** | The point of the exercise |
| Touch Display 2 (5") | **A 7" HDMI screen** | The Touch Display 2 connects only to a Pi's DSI socket; the box has only HDMI |
| The Pi's GPIO pins | **A USB button board** (A$7–9) | The box has no pins for buttons |
| microSD card per unit | **None** | Units run from the box's own 32 GB memory, put there by one installer card |
| Official 27 W supply | **A two-port USB charger** | One port for the box's 5 V socket, one for the screen |
| Active Cooler | **None** | The box has its own heatsink and needs no fan |

## Cost

| Part | Pi build | TV box build | Notes |
|---|---|---|---|
| Computer | Pi 5 1 GB, A$78–83 | X96 Max Plus 4 GB/32 GB, **A$62** | Box listings: eBay AU from A$61.99 new, Amazon AU A$115.90, Kogan A$117–124, Big W A$135. Wholesale about US$32–39 |
| Screen | Touch Display 2 5", A$79 | Waveshare 7" HDMI (C), IPS 1024×600, **A$74.60** | Or a generic 7" IPS HDMI screen from AliExpress, about A$30–45 *est.* |
| HDMI cable | — | about A$5 *est.* | If the screen comes without one: a short one |
| ReSpeaker Lite, speaker, USB cable | A$60 | A$60 | Unchanged |
| Buttons and leads | A$9 | A$9 | Unchanged (A$2 more for the optional mute switch, either build) |
| Button board | — | Raspberry Pi Pico H, **A$8.65** | Or a Waveshare RP2040-Zero, A$6.60 (solder six pins) |
| Card | microSD, A$12 | **none** | One installer card (A$12) does every unit |
| Power | Official 27 W, A$24 | Two-port USB charger A$10, USB to 5.5×2.1 mm plug A$6.80 | Or the box's own 5 V 2 A adapter (in its box) and any USB charger for the screen |
| Cooling | Active Cooler, A$9 | none | |
| **Total** | **about A$273** | **about A$236** | **About A$37 (14%) less**, with a screen of the same quality |
| With a generic 7" screen | | **about A$200** *est.* | **About A$73 (27%) less** |

Where the saving comes from: the computer and what it needs (Pi, card, cooler, supply: A$125) against the box,
button board and charger (A$87). The screen saves little, because the Touch Display 2 is unusually cheap for its
quality; a generic screen is the next saving, at the risk of uneven quality between batches. The box's price is
the one to watch: at Amazon AU's A$116 the saving disappears, and bought by the carton from a supplier it falls
further.

What the money buys besides: the box has **4 GB of memory to the Pi's 1 GB** (a 2 GB Pi 5 costs about A$130), room
for heavier 3D avatars and on-device features, and **32 GB of storage built in**. What it costs: a slower
processor (four Cortex-A55 cores to the Pi 5's four Cortex-A76, roughly a third of the speed) and weaker graphics
(Mali-G31 MP2). The 7" screen's 1024×600 picture is less than a third of the pixels of a TV's 1080p, which is the
box's best case for drawing the character smoothly; the on-device voice and speech recognition run on the slower
processor, so replies may take longer than on a Pi 5 (to measure: T15).

## How it fits together

```
                          wall socket
                               |
                 two-port USB charger (5 V, about 17 W)
                    |                            |
       USB to 5.5x2.1 mm plug             micro-USB lead (power only)
                    |                            |
   +----------------+-----------+     +----------+-------------------+
   |  TV box (X96 Max Plus)     |HDMI |  7" screen, standing on its  |
   |                            +-----+  side: a 600x1024 picture    |
   |  USB 3.0 port -- ReSpeaker Lite -- speaker                      |
   |  USB 2.0 port -- button board -- listen button, power button,   |
   |                                   mute switch                   |
   |  Wi-Fi, or Ethernet for the first start                         |
   +----------------------------+                                    |
                                      +------------------------------+
```

### Power

| What | Typical | Loudest moments |
|---|---|---|
| The box, drawing the character | about 3 W *est.* | about 5 W *est.* |
| ReSpeaker Lite and its speaker (through the box's USB) | about 1 W *est.* | about 3.5 W *est.* |
| Button board | 0.1 W | 0.1 W |
| **Through the box's 5 V socket** | **about 4 W (0.8 A)** | **about 8.6 W (1.7 A)** |
| Screen (Waveshare: 2.5 W, at least 500 mA), from the charger's second port | 2.5 W | 2.5 W |

A charger port gives up to 2.4 A, so each side has room; the box's own 5 V 2 A adapter would also do for the
box's side. **Do not power the screen from the box's USB ports**: both are in use, and the box's socket would then
carry everything. The box's power socket is 5.5 × 2.1 mm on most X96 Max Plus boxes; check the plug of its own
adapter before buying the lead (T17 checks for brown-outs at full volume).

### The screen

**Recommended: Waveshare 7inch HDMI LCD (C)** (Core Electronics lists it as "Waveshare 7inch LCD IPS 1024×600";
check that it is the HDMI one). 1024×600, **IPS** (it stays clear seen from the side when it stands on its side: a
cheap TN screen does not), 164.9 × 107.0 × 8 mm, 2.5 W from its micro-USB socket, a backlight switch on the back,
and USB touch (not used yet; it is there for touch reactions later). Waveshare publishes its timing (49 MHz,
1024×600), which the box's HDMI can produce: the Linux driver for the box's display (meson) takes modes at least
400 wide and 480 tall (up to 3840×2160 in today's kernel) at any clock its PLL can make, and 49 MHz is one (the
PLL at 3.92 GHz, within its range). The screen stays 1024×600 on the cable; the turn is made by Weston.

- **Its own size, not 1080p.** The screen accepts a 1080p picture too, and before this change the box sent one
  (fixed at 1920x1080 for TVs), to be squeezed down. Now the box asks the screen for its own size and sends that,
  never more than 1920x1080 (a 4K TV still gets 1080p): `tvbox/bin/companion-tvbox-screen`, run each time the
  screen starts.
- **On its side.** The picture is turned with the image's `--rotate 90` (the workflow's *screen: desk-90*; 270 if
  the screen is mounted the other way up), or `COMPANION_SCREEN_ROTATE` in `/etc/default/companion`.
- **At night** the character sleeps behind a black veil, as on the Pi, but an HDMI screen's backlight stays on:
  Linux can set the Touch Display 2's backlight, not an HDMI screen's. The Waveshare has a backlight switch on its
  back. Two ways to darken it fully are left for when the parts are here (T14): switch the HDMI picture off at
  night so the screen sleeps (a small Weston module: the kiosk shell never does this itself), or use the
  Waveshare's brightness setting over USB (its revision 4.1), which needs its USB lead plugged into the box and
  so a USB hub.
- Alternatives: a **generic 7" IPS 1024×600** screen (about A$30–45 from AliExpress *est.*; choose IPS, and check
  the listing says 1024×600); the **Waveshare 5"** (800×480, A$89.70: dearer than the 7"); the **5.5" AMOLED**
  1080×1920 (A$194.95: too dear).

### The buttons: the button board

A Raspberry Pi Pico H (or a smaller RP2040-Zero) running a few lines of CircuitPython, which the box sees as a
button box: no driver and no settings (`tvbox/button-board/README.md`). The same buttons and switch as the Pi
build, each on two pins side by side (one read, one held at 0 V), so the leads push on and nothing is spliced.

| Control | Pins | What it does | Linux sees |
|---|---|---|---|
| Listen button | GP0, GP1 | Press, speak, it answers; held 2 s, the microphone off or on; held 12 s, forget the Wi-Fi and the password (as the Pi) | Play/Pause (KEY_PLAYPAUSE): already a button key on the box |
| Power button | GP2, GP3 | **Held a second**: the box switches itself off cleanly. A knock does nothing | Power (KEY_POWER): systemd-logind switches the box off |
| Mute switch (optional) | GP4, GP5 | On: the microphone is off and nothing can turn it on (as the Pi) | Phone Mute, held (KEY_MICMUTE: the Pi's own mute key) |

None of these is a keyboard key, so Linux does not repeat them while held (a switch can be on for days), and the
board sends its whole state every second, so a box that has just started learns where the switch is. The box's
remote still works beside it (OK to listen, the volume keys, mute).

### Switching on and off

- **Off**: hold the power button for a second. The box shuts down cleanly and the screen goes dark; then switch
  off at the wall (or with a switch on the box's power lead).
- **On**: switch on at the wall: the box starts when power comes.

This is how the Pi 5 build works too (its power button only shuts down; it starts with the Pi's own button or the
power). One button for both on and off would need a power-latch circuit beside the button board: an idea for later,
not designed.

### No card per unit: the installer card

A unit runs from the box's own memory. One **installer card** (the workflow's *installer* box; `build-image.sh
--installer`) does every unit:

1. Put the installer card in the box and start it with the toothpick (as in `tvbox/README.md`).
2. The screen says what it is doing; about ten minutes later the box switches itself off. Android on it is gone.
3. Take the card out and switch on: the unit starts from its own memory, at the setup screen.

It uses the community project's own installer (`armbian-install`, answered with the box's number from
`tvbox/boards.tsv`), keeps the box's boot loader, and makes each copy a unit of its own (no identity carried over
from the card; each unit makes its own hotspot password and its owner the settings-page password, as before). It
refuses to run twice on a box that already has the device, and stops with the reason on the screen if anything
fails. Erasing Android is also the safer state for a box: cheap boxes have been sold with malware on them
(`37-android-tv-box.md`).

### Network

The gigabit X96 Max Plus has a Realtek RTL8822CS Wi-Fi chip, which Linux supports (the rtw88 driver, since 6.4);
boxes sold under the same name change inside from batch to batch, so check each batch (T4, T18). Ethernet always
works, for the first start if need be.

### Mounting

- The screen on its side: 107 mm wide, 165 mm tall, 8 mm thick.
- The box (about 10 cm square and 2 cm thick: measure yours) behind the screen or lying in the base, with its vents
  clear: it has no fan.
- The ReSpeaker Lite with its microphones facing the room, near the screen's top or bottom edge; the speaker facing
  forward; the buttons on top; the mute switch on the side.
- For the first prototype: a tablet stand, or a simple printed stand. A designed case waits for measured parts.

## Software made for it

- `core/companion/__main__.py`: on the box, the button board's mute switch is found and read like the Pi's
  (KEY_MICMUTE); while any device holds it the microphone stays off, and a second device (a headset's mute button)
  cannot undo it; `COMPANION_MUTE_KEY` for a switch that sends another key.
- `tvbox/button-board/`: the board's files (`buttons.py` holds what it sends and when; `code.py` reads the pins;
  `boot.py` names it and hides its drive and serial port) and its guide.
- `tvbox/bin/companion-tvbox-screen`: the picture at the screen's own size up to 1080p, turned on request, run by
  the screen service before Weston starts.
- `tvbox/bin/companion-tvbox-install` and its service: the installer card.
- `build-image.sh --rotate` and `--installer`, the same in the workflow (*screen*, *installer*), and checks for
  both in `check-image.sh`; `check-on-box.sh` shows the screen's modes and the button board.

## What only the parts can tell (`15-hardware-test-plan.md`, T11 to T18)

The screen's own 1024×600 from the box; the frame rate on it (T12); the button board's three controls and the
clean power-off (T13); the backlight at night (T14); reply times with on-device voice (T15); the installer card on a
real box (T16); power at full volume (T17); Wi-Fi on the box's chip (T18).

## Sources (2026-11-28)

- X96 Max Plus 4 GB/32 GB: [eBay AU](https://www.ebay.com.au/itm/314241296955), [eBay AU](https://www.ebay.com.au/itm/293639623991),
  [Amazon AU](https://www.amazon.com.au/X96-Max-Plus-Amlogic-Android/dp/B0B971RVWM),
  [Kogan](https://www.kogan.com/au/buy/dsport-x96-max-plus-4gb32gb-amlogic-s905x3-android-90-8k-video-decode-tv-box-24g58g-wifi-bluetooth-1000mbps-lan-usb30-youtube-netflix-google-play-black-geek20893/),
  [Big W](https://www.bigw.com.au/product/x96-max-plus-4gb-32gb-amlogic-s905x3-android-9-0-8k-video-decode-tv-box-2-4g-5-8g-wifi-bluetooth-1000mbps-lan-usb3-0-youtube-netflix-google-play-black/p/9903515855),
  [Alibaba buying guide](https://electronics.alibaba.com/buyingguides/x96-plus-tv-box-guide-what-you-actually-need-to-know);
  its power socket: [DroiX](https://droix.net/product/5v-2a-5-5mm-2-1mm-power-adapter-for-droix-imxqpro-mini-android-box-htpc-t95z/).
- Screens: [Waveshare 7" IPS, Core Electronics](https://core-electronics.com.au/waveshare-7inch-lcd-ips-1024-600.html),
  [Waveshare's 7inch HDMI LCD (C) wiki](https://www.waveshare.com/wiki/7inch_HDMI_LCD_(C)) (size, power, timing,
  backlight), [52Pi 7"](https://core-electronics.com.au/52pi-7-inch-display-hdmi-for-pi-and-windows.html) (A$94.95),
  [Waveshare 5"](https://core-electronics.com.au/waveshare-5inch-lcd-800x480.html),
  [Waveshare 5.5" AMOLED](https://core-electronics.com.au/waveshare-5-5inch-amoled-1080x1920.html),
  [Jaycar 7"](https://jaycar.com.au/p/XC9026) (A$179), [Notebookcheck on cheap 7" screens](https://www.notebookcheck.net/Cheap-7-inch-HDMI-monitor-reviewed-Perfect-10-display-for-Raspberry-Pi-5.1315770.0.html).
- Button board: [Raspberry Pi Pico H](https://core-electronics.com.au/catalog/product/view/sku/CE07589),
  [RP2040-Zero](https://core-electronics.com.au/rp2040-zero-a-pico-like-mcu-board-based-on-raspberry-pi-mcu-rp2040-mini-ver-1.html);
  how Linux names the board's keys: [hid-input.c](https://github.com/torvalds/linux/blob/master/drivers/hid/hid-input.c)
  (Telephony 0x2F is KEY_MICMUTE, Consumer 0x30 KEY_POWER, 0xCD KEY_PLAYPAUSE; only keyboard keys repeat);
  [systemd's power-switch rule](https://github.com/systemd/systemd/blob/v257/rules.d/70-power-switch.rules) (every
  device with keys is watched for the power key).
- Power: [USB to 2.1 mm plug lead](https://core-electronics.com.au/usb-to-2-1mm-male-barrel-jack-cable-22awg-1-meter.html),
  [two-port USB charger](https://www.officeworks.com.au/shop/officeworks/p/keji-usb-a-17w-dual-wall-charger-black-keji2awcbk).
- The installer: ophub/amlogic-s9xxx-armbian's `armbian-install` and its list of boxes (`model_database.conf`);
  Weston 13's source (only the desktop shell switches the screen off when idle, never the kiosk shell).
