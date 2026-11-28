# The character device on a TV box

Status: 2026-11-28. **Built, not yet tried on a box.** The software is the same as the Raspberry Pi device's;
this folder holds what a TV box needs instead. The Raspberry Pi device (`device/`) is unchanged and is still
the main device. Start with the checks in "Testing a box" below, and the hardware tests in
`docs/15-hardware-test-plan.md` (T1 to T18).

Two ways to use a box: **on a TV** (below), or as **the desk device**, the Pi build's parts around the box
instead of a Pi: a 7" HDMI screen standing on its side, the ReSpeaker Lite, and the same buttons on a small USB
button board. The desk device is designed in `docs/38-tv-box-desk-device.md` (parts, costs, wiring, power), with
the button board in `button-board/`.

## What it is

A cheap Android TV box with an **Amlogic S905X3** chip (the **X96 Max Plus** first, and the other boxes in
`boards.tsv`), turned into the character device by starting it from a microSD card with Linux on it instead of
Android. The character is on the **TV**, or any HDMI monitor. Everything the Pi device does, the box does:
conversation, memory, characters (2D and 3D), the settings page on your phone, updates, Your day.

How it differs from the Pi, and why:

| | Raspberry Pi device | TV box |
|---|---|---|
| Operating system | Raspberry Pi OS (Debian 13) | Armbian for S905X3 boxes (Debian 13), from the community project [ophub/amlogic-s9xxx-armbian](https://github.com/ophub/amlogic-s9xxx-armbian), with its kernel and boot files |
| Screen | The Touch Display 2, or HDMI; shown with cage | A TV, monitor or small screen over HDMI, at the **screen's own size but never more than 1920x1080** (a 4K picture is too much for the box's Mali-G31 graphics), turned for a screen on its side if asked; shown with Weston's kiosk shell, which can set that (cage cannot) |
| The listen button | A button on the GPIO pins | The **remote's OK button**; or the desk device's **button board**; or a USB button or keyboard (Enter); or the TV's own remote through HDMI-CEC, once turned on |
| Volume, microphone off | The settings page; holding the button | Also the **remote's volume keys**, and its **mute key** for the microphone |
| Sound | The ReSpeaker Lite (USB) | The same, or any USB speaker with a microphone; or the TV's speakers over HDMI |
| Network | Wi-Fi | **Ethernet** (always works); Wi-Fi depends on the box's Wi-Fi chip |
| Switching off | The power button | The **remote's power button** (or the button board's, held a second) switches the box off cleanly; then unplug it, and plug in to start |
| Storage | A microSD card | A card, or the **box's own memory**, put there by one installer card for every unit ("Making many") |
| Power cuts | The system is read-only | **Not yet**: the system is writable (an ext4 journal, and the device saves its files safely, but pull the plug only after switching off) |

## What you need

- **An S905X3 TV box**: an X96 Max Plus with 4 GB of memory (32 or 64 GB of storage). Other S905X3 boxes in
  `boards.tsv` should work the same way. Not suitable: boxes with RK3318, RK3328 or Allwinner H313 chips (their
  graphics are too old), and any box without "S905X3" (or another chip in `boards.tsv`) on its listing.
- **A microSD card**, 16 GB or more (or a USB stick).
- **A TV or HDMI monitor.** A small monitor on its side gives the Pi's tall picture (see "The screen").
- **A USB speaker with a microphone**: the ReSpeaker Lite, as for the Pi, or a USB speakerphone.
- **An Ethernet cable** to your router, for the first start at least.
- The box's **remote**; or a USB button or keyboard; or, for the desk device, the button board (`button-board/`).
- **A toothpick**, to start the box from the card the first time.

For the desk device, the parts list is in `docs/38-tv-box-desk-device.md`: the 7" screen, a two-port USB charger
and its leads, and the button board, besides the box and the ReSpeaker Lite.

## Making the card

**On GitHub** (no Linux computer needed): the repository's *Actions* tab → **Make a TV box image** → *Run
workflow*. Give the software's version (such as 2026.11.28) and choose the box (not sure? the gigabit X96 Max
Plus), the screen (*tv*; or *desk-90* for the desk device's screen on its side, *desk-270* if that comes out
upside down) and whether it is an installer card ("Making many"). About half an hour later the image is on a
draft release, *TV box image &lt;version&gt;*, under Releases: `companion-tvbox-<version>-<box>.img.xz` (with
`-desk-90` and `-installer` in the name when chosen). For testing, you can paste your SSH public key (from your computer's
`~/.ssh/id_ed25519.pub`): that key, and only that key, can then log in to the box as root. Leave it empty for an
image to sell.

**On a Linux computer**: download the community Armbian image for S905X3 boxes (a release of
ophub/amlogic-s9xxx-armbian, the file `Armbian_..._amlogic_s905x3_trixie_..._server_....img.gz`), build the stage
for 64-bit ARM (`stage/README.md`), then:

```bash
sudo ./tvbox/build-image.sh Armbian_26.11.0_amlogic_s905x3_trixie_6.12.58_server_2026.11.20.img.gz companion-tvbox.img --board x96-max-plus
```

Add `--rotate 90` for the desk device's screen on its side, and `--installer` for an installer card (and the same
to `check-image.sh` below).

```bash
sudo ./tvbox/check-image.sh companion-tvbox.img --board x96-max-plus
```

Write the `.img.xz` (or `.img`) to the microSD card with **balenaEtcher** or **Raspberry Pi Imager** (*Use
custom*, and no customisation settings).

## Starting the box from the card

The box's own system (Android) stays on its internal memory, untouched; the card is started instead.

1. Unplug the box, and put the card in its card slot. Connect the TV (HDMI), the Ethernet cable and the USB
   speaker.
2. **The toothpick**: inside the round AV socket (the one for old TVs' audio and video) there is a small
   button. Press it with a toothpick and keep it pressed while you plug in the power. Let go when the box's logo
   or text appears (five to ten seconds). From now on the box starts from a card whenever one is in it.
3. The first start takes a few minutes (the card's system grows to fill it). Then the TV shows the setup
   screen, with the settings page's address.
4. On your phone, on the same network, open that address (or http://companion.local), and set up as for the
   Pi: `docs/28-user-guide.md`.

If the TV shows Android again, the toothpick step did not take: try again, holding a little longer.

## Using it

- **The remote's OK button** is the listen button: press, speak, and it answers. Holding it does what holding
  the Pi's button does: two seconds turns the microphone off or on, twelve seconds forgets the Wi-Fi and the
  settings-page password.
- The remote's **volume keys** change the volume (shown on the screen); its **mute key** turns the microphone
  off or on.
- A **USB button** (one that types Enter), or a keyboard's **Enter**, works as the listen button too.
- **The button board** (the desk device): its listen button works as above; its mute switch turns the microphone
  off while it is on, as the Pi's does; **hold its power button for a second** to switch the box off cleanly (a
  knock does nothing). `button-board/README.md`.
- **The TV's own remote**, through HDMI-CEC: with TVs that allow it, its OK button can be the listen button. It
  is off to begin with, because some TVs switch to the box's input when it announces itself: turn it on with
  `COMPANION_CEC=on` in `/etc/default/companion` (a maker's job for now: "Logging in", below).
- **Switching off**: press the remote's power button, wait for the picture to go, then unplug. To start again,
  plug it back in.

## The screen

The picture is the screen's own size, but never more than 1920x1080: a TV gets 1920x1080 (a 4K TV enlarges it), a
7" screen its own 1024x600, which is a third of the work and so smoother. It is worked out each time the screen
starts (`bin/companion-tvbox-screen`; `check-on-box.sh` shows the size chosen). If the TV cuts off the edges (the
captions at the bottom, the clock at the top), set the TV's picture size to *Just Scan*, *Screen Fit* or *1:1*.

For a tall picture like the Pi's, stand the screen on its side and turn the picture: an image built with
`--rotate 90` (*desk-90*) is already turned; on a box made otherwise, `COMPANION_SCREEN_ROTATE=90` in
`/etc/default/companion` (270 if the picture is upside down). A size of your own: `COMPANION_SCREEN_MODE=1280x720`
there, or `mode=1280x720` in `/etc/companion/weston.ini`.

At night the character sleeps behind a black veil, but an HDMI screen's backlight stays on (Linux can set the
Pi's own screen's backlight, not an HDMI screen's): `docs/38-tv-box-desk-device.md`, "The screen".

## Sound

A USB speaker with a microphone (the ReSpeaker Lite) is found by itself, as on the Pi. Without one, the device
speaks through the TV (HDMI): the box's own sound card is set to send its sound there at each start
(`bin/companion-tvbox-sound`). To use the TV's speakers with a USB microphone, set
`COMPANION_ALSA_PLAYBACK=plughw:CARD=X96MAX` in `/etc/default/companion` (the card's name is in `aplay -l`).

## Making many: the installer card

Running from a card is fine; the Pi runs from a card too. But each unit can instead run from the box's own memory
(32 GB on the X96 Max Plus), with no card to buy, and **one installer card does every unit**:

1. Make an installer card (*installer* ticked on GitHub, or `--installer`), for the box's model and screen.
2. Put it in a box and start the box from it with the toothpick, as above. The screen says what it is doing: it
   copies the device onto the box's own memory, **which erases Android**, and switches the box off when it has
   finished (about ten minutes).
3. Take the card out and plug the box in again: it starts from its own memory, at the setup screen. The card goes
   in the next box.

Each unit is its own: nothing that identifies the card is copied, and each makes its own hotspot password (its
owner sets the settings-page password). Put the card in a box that already has the device and it says so and
changes nothing; if the copy fails, the screen says so and nothing starts. It uses the community project's own
installer (`armbian-install`), answered with the box's number in its list (`boards.tsv`), and keeps the box's own
boot loader (`bin/companion-tvbox-install`).

Erasing Android is also the safest choice: cheap Android boxes have been sold with malware already on them (the
FBI's BADBOX 2.0 warning, `docs/37-android-tv-box.md`). On one box by hand, with a maker's image (an SSH key): log
in and run `armbian-install`, choosing the box's model from its list (the X96 Max Plus with gigabit Ethernet is
502).

## If something does not work

| What you see | What to do |
|---|---|
| The TV shows Android | Start from the card again with the toothpick (above) |
| No picture at all, or it starts again and again | The card was not written properly (write it again), or the box is not the one chosen: open the card's **BOOT** drive on any computer, and in `uEnv.txt` change the `FDT=` line to the right file from `boards.tsv` |
| No network over Ethernet (the socket's light stays off) | The X96 Max Plus comes with gigabit or 100 Mbit Ethernet: change `FDT=` in `uEnv.txt` to the other one (`meson-sm1-x96-max-plus-100m.dtb` or `meson-sm1-x96-max-plus.dtb`) |
| No Wi-Fi networks to join | The box's Wi-Fi chip may have no Linux driver: use Ethernet |
| No sound | Use a USB speaker with a microphone; for the TV's speakers, see "Sound" |
| The remote does nothing | Remotes differ: use a USB button or keyboard (Enter), and send the support report (below) |
| The picture is on its side or upside down | `COMPANION_SCREEN_ROTATE` (90 or 270) in `/etc/default/companion`, or an image built for the other way |
| The button board does nothing | `button-board/README.md`, "Checking it"; the support report lists *Companion Button board* among the input devices when the box sees it |
| The character moves slowly | Choose a lighter drawing for 3D avatars (the character's *3D* settings); note the frame rate (Animation tab) |

**The support report** (settings page → *Device* → *Help* → *Download a support report*) has what helps: the
box, its sound cards, its input devices (the remote among them), its network devices, the screen's size and
the frame rate, and the logs. Nothing private is in it.

## Testing a box

The checks to run on a new box, in order, are T1 to T10 in `docs/15-hardware-test-plan.md`. With a maker's image,
`sudo /usr/local/lib/companion-tvbox/check-on-box.sh` prints the box's graphics, screen, sound, buttons and network in
one go.

## Logging in (makers only)

An image to sell has no way in: root's password is locked (the community images come with `root` / `1234`) and
SSH is off. An image built with an SSH public key (above) lets that key in as root, and nothing else:
`ssh root@companion.local`. Never sell a unit with a key in it.

## Before selling

This is a test variant. Before selling TV box units, as for the Pi (`docs/33-finishing.md`), and also:

- **Power-cut protection** for the box (a read-only system and a data partition, as `device/build-image.sh`
  does for the Pi).
- **The source code**: of the image's Debian packages (as the Pi image's release has), and of the box's Linux
  kernel and boot loader, which come from the community project and its kernel repositories
  (github.com/ophub/kernel, built from unifreq's kernel sources): the GPL requires offering them with the
  device.
- **Which boxes**: boxes sold under one name change inside from batch to batch. Test every batch (T1 to T10),
  and prefer a supplier who keeps the board the same.
- **The remote**: label it (OK is the listen button), or ship a USB button.
- **The desk device**: the tests T11 to T18, and its open questions (`docs/38-tv-box-desk-device.md`).
- **The button board**: a board shipped with CircuitPython on it carries CircuitPython's MIT licence notice
  (`docs/34-licences.md`).

## What is in this folder

| File | What it does |
|---|---|
| `install.sh` | Installs the device's software on a box running Armbian: `device/install.sh` (shared with the Pi, unchanged), then the box's own parts |
| `build-image.sh`, `check-image.sh` | Make, then check, a card image from the community Armbian image |
| `boards.tsv` | The S905X3 boxes, and the device-tree file that describes each to Linux |
| `config/weston.ini` | The screen: its own size up to 1920x1080, never blanking, the character full-screen |
| `config/companion-stage-tvbox.conf` | Starts the screen with Weston instead of cage, its size and turn worked out first (a drop-in that updates leave alone) |
| `config/companion-tvbox.default` | The button, remote keys, screen and sound settings, added to `/etc/default/companion` |
| `config/*.service`, `bin/` | The screen's size and turn (`companion-tvbox-screen`); the box's sound to HDMI at each start; the TV's own remote through HDMI-CEC; the character's program under Weston; the installer card (`companion-tvbox-install`) |
| `button-board/` | The desk device's button board: its files (CircuitPython) and how to make one |
| `check-on-box.sh` | On a box: what works |
| `.github/workflows/make-tvbox-image.yml` | Makes the image on GitHub |
