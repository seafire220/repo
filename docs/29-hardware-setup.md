# Setting up the first device, once the parts arrive

Status: 2026-11-19. For the owner, building the first prototype. Written from
the design, the install scripts and tests under ARM emulation; **not yet
followed on real hardware**. Steps marked *check* depend on the hardware:
note what you find and it goes back into this guide.

It brings together `05-hardware-and-bom.md` (parts), `22-board-options.md`
(which board), `13-build-and-install.md` (installing), `18-out-of-the-box.md`
(the ready-made card image) and `15-hardware-test-plan.md` (tests).

A cheaper build around an S905X3 TV box instead of the Pi, with most of these
same parts (a 7" HDMI screen instead of the Touch Display 2, and the buttons on
a small USB board): `38-tv-box-desk-device.md`.

## 1. What to buy

The board recommendation changed after the parts list was written: a
**Raspberry Pi 5 1GB** now costs less than the Pi 4 2GB it was planned
around, and is faster (`22-board-options.md`). A Pi 4 works the same way;
differences are noted below.

| Part | Notes | About |
|---|---|---|
| Raspberry Pi 5, 1 GB | Or a Pi 4 (1 or 2 GB) | A$78–83 |
| Raspberry Pi Touch Display 2, 5" | The 7" one (A$106) uses the same software; it needs a different settings line (step 4) | A$79 |
| Seeed **ReSpeaker Lite** (the USB board, not the kit with an ESP32 on it) | Two microphones with echo cancellation, and a speaker amplifier | A$46 |
| Speaker, 4 Ω, 5 W, about 50 mm | With a lead that fits the ReSpeaker Lite's speaker socket, or bare wires | A$9 |
| USB cable for the ReSpeaker Lite | USB-C to USB-A, if one is not in its box | A$5 |
| microSD card, 32 GB, A1 rated | A name brand | A$12 |
| Power supply | **Pi 5: the official 27 W USB-C supply** (with a 3 A supply the Pi 5 limits what its USB ports may draw, and the speaker amplifier is powered over USB). Pi 4: the official 15 W supply | A$24 (Pi 5), A$17 (Pi 4) |
| Cooling | Pi 5: the Active Cooler. Pi 4: stick-on heatsinks | A$9, A$5 |
| Two momentary push buttons, with leads | Listen and power. Leads with female push-on ends fit the Pi's pins without soldering | A$6 |
| Four female-to-female jumper leads | If the buttons' leads do not end in push-on sockets | A$3 |

About **A$270** with the Pi 5 and the 5" screen. Core Electronics stocks the
Pi, the screen and the supplies; Seeed, Core Electronics or Pakronics the
ReSpeaker Lite; Jaycar or AliExpress the speaker and buttons.

**You will also need:** a computer with Raspberry Pi Imager, a microSD card
reader, and for the first unit a way to type commands to the Pi over the
network (PowerShell on Windows and Terminal on a Mac both have `ssh`).
Handy but optional: a micro-HDMI to HDMI lead (Pi 5 and Pi 4) and a USB
keyboard, to see the Pi's own messages on a TV if the screen stays black.

## 2. Unpack and check

- The Touch Display 2 box: the screen, its power lead (two wires to the
  Pi's pins), screws and stand-offs, and ribbon cables. *Check*: there
  should be one ribbon for the Pi 5 (narrow plugs at both ends) and one for
  older Pis. Note what came in the box.
- The ReSpeaker Lite: note how its speaker socket looks, and whether a USB
  cable came with it.

## 3. Put it together

Do everything with the power unplugged. The pins named below are on the
Pi's 40-pin header; pin 1 is the corner pin nearest the microSD end of the
board, marked with a square pad underneath, and pins 1, 3, 5... run along the
inside row, 2, 4, 6... along the edge.

| Pin | Use | Pin | Use |
|---|---|---|---|
| 1 | — | **2** | **5 V: screen power (red)** |
| 3 | — | **4** | (5 V, spare) |
| **5** | **GPIO3: power button** | **6** | **Ground: screen power (black)** |
| 7 | — | 8 | — |
| **9** | **Ground: listen button** | 10 | — |
| **11** | **GPIO17: listen button** | 12 | — |
| 13 | (GPIO27: mute switch, if fitted) | **14** | **Ground: power button** |
| 15 | — | 16 | — |
| 17 | — | 18 | — |
| 19 | — | 20 | (Ground: mute switch, if fitted) |

1. **Cooling.** Fit the Active Cooler (Pi 5: it clips into two holes and
   plugs into the fan socket) or stick on the heatsinks (Pi 4).
2. **Screen.** Fix the Pi to the back of the screen with the stand-offs.
   Connect the ribbon: on a Pi 5 into one of the two narrow connectors
   marked CAM/DISP (*check* which one the settings line in step 4 expects:
   note which one you used); on a Pi 4 into the connector marked DISPLAY.
   Lift the connector's latch, slide the ribbon in fully with its contacts
   facing the right way (the box shows which), press the latch down.
3. **Screen power.** The screen's two-wire lead: red to pin 2, black to
   pin 6.
4. **Listen button**: one lead to pin 11, the other to pin 9. Either way
   round; buttons have no polarity.
5. **Power button**: pin 5 and pin 14. Pressing it shuts the Pi down
   cleanly. On a Pi 4 pressing it again starts it. **On a Pi 5** it only
   shuts down: start it again with the Pi 5's own power button (on the
   board's edge) or by unplugging and plugging in the power.
6. **Sound.** The speaker into the ReSpeaker Lite's speaker socket; the
   ReSpeaker Lite to a USB port on the Pi (on a Pi 4, a black USB 2.0 port).
7. **Card and power** come in steps 4 and 5.

No resistors are needed: the software switches on the Pi's own pull-ups for
both buttons.

**A mute switch (optional).** A slide switch between pin 13 (GPIO27) and pin 20 (ground), closed for
"microphone off". Then remove the `#` in front of the `gpio-key,gpio=27` line in the boot settings
(`/boot/firmware/config.txt`, or `device/config/boot-config.txt` before building an image). The
crossed-out microphone on the screen follows the switch, and while it is on nothing can turn the
microphone back on.

## 4. Put the software on the card

There are two ways. **For this first unit use the first one**: it keeps a
login, so you can see what is going on and fix the screen setting if
needed. The ready-made image (the second way) is how units for sale are
made; it has no login at all, by design.

### First unit: install by hand

1. **What you need from the repository.** On your computer, download the
   repository from GitHub (*Code → Download ZIP*) and the **stage program
   built for the Pi** (`stage`, about 5 MB; built from the same code,
   2026-11-19). Ask Claude for the stage program: it can attach it to a
   release of the private repository, or you can build it on a Linux PC
   (`13-build-and-install.md`, step 4). Without it, the installer builds it
   on the Pi, which takes a long time and may run out of memory on 1 GB.
2. **Write the card** with Raspberry Pi Imager:
   - Device: your Pi. Operating system: *Raspberry Pi OS (other) →
     Raspberry Pi OS Lite (64-bit)*. Storage: the microSD card.
   - In Imager's customisation steps (*Edit settings* in older versions):
     host name **companion**; a user name and password of your choice; your
     home Wi-Fi and country; your time zone; and remote access by SSH with
     password login.
   - Write the card, put it in the Pi, plug in the power.
3. **Log in from your computer** after a minute or two:

   ```bash
   ssh YOUR-USER@companion.local
   ```

   (If `companion.local` is not found, look up the Pi's address in your
   router's list of devices and use that.)
4. **Copy the files across.** In a second window on your computer, from the
   folder where they were downloaded:

   ```bash
   scp repo-main.zip stage YOUR-USER@companion.local:
   ```

5. **Install**, on the Pi:

   ```bash
   sudo apt install -y unzip && unzip -q repo-main.zip && mv repo-main repo
   ```

   ```bash
   mkdir -p repo/stage/target/aarch64-unknown-linux-gnu/release && mv stage repo/stage/target/aarch64-unknown-linux-gnu/release/ && chmod +x repo/stage/target/aarch64-unknown-linux-gnu/release/stage
   ```

   ```bash
   cd ~/repo && sudo ./device/install.sh
   ```

   It installs everything (10–20 minutes, mostly downloads) and adds the
   button and screen lines to `/boot/firmware/config.txt`.
6. **Check the screen line** before restarting. For the **7"** screen,
   change `5inch` to `7inch`:

   ```bash
   sudo nano /boot/firmware/config.txt
   ```

   The line is `dtoverlay=vc4-kms-dsi-ili9881-5inch`, near the end between
   the *Character device* markers. *Check*: on a Pi 5, the Raspberry Pi
   documentation for Touch Display 2 says whether the line needs `,dsi0` or
   `,dsi1` added for the connector you used. Save (Ctrl+O, Enter) and leave
   (Ctrl+X).
7. **Restart:**

   ```bash
   sudo reboot
   ```

The screen should show *Hello!* with the device's address (it already has
your Wi-Fi from Imager). Carry on at step 5.

### Units for sale (and the easiest first card): the ready-made image

One image, flashed to every card, with everything on it: Raspberry Pi OS, the device's software, its
models and voices. Nothing secret is in it (`18-out-of-the-box.md`). Its system partition is read-only,
and a third partition holds what the device saves (`33-finishing.md`).

**Get the image (no Linux computer needed).** On GitHub, in this repository:
1. **Actions → Make a device image → Run workflow.** Give a version, such as `2026.11.21` (updates you
   publish later must be newer), and leave the release as **draft**. It takes about half an hour, and
   about as long again to add the operating system's source code.
2. **Releases → Device image 2026.11.21** (a draft, seen only by you): download
   `companion-<version>.img.xz` (about 1 GB) and its `.sha256`. Keep it as `.img.xz`: Imager reads it
   as it is.
3. Before selling devices with this image, **publish** the release (**Edit → Publish release**; leave
   *Set as the latest release* unticked, so Character Studio's links keep working). The device's
   notice sends owners to it for the source code of the open-source software, which the GPL requires us
   to offer, so keep it for at least three years after the last device with that image is sold
   (`34-licences.md`).

(On a Linux computer, `device/build-image.sh` makes the same image; `--help` says how.)

**Write it to the card**, on a Mac or PC:
1. Install **Raspberry Pi Imager** from raspberrypi.com/software, and put the microSD card in the
   computer (a USB card reader if it has no slot).
2. **Choose Device**: Raspberry Pi 4 (or 5).
3. **Choose OS**: scroll to the bottom, **Use custom**, and pick `companion-<version>.img.xz`.
4. **Choose Storage**: the card. Check it is the card, by its size: everything on it is erased.
5. **Next.** When Imager asks *"Would you like to apply OS customisation settings?"*, choose **No**. The
   device sets itself up from its own screen; customisation would change that.
6. **Yes** to erase the card. Imager writes and then checks it: about ten minutes. Eject the card.

**Start it.** Put the card in the Pi (contacts towards the board), connect everything (step 3), and
plug in the power. The first start takes a little longer (the data partition grows to fill the card).
After about a minute the screen shows *Set up Wi-Fi*, as a buyer sees it (`28-user-guide.md`). Carry on
at step 5, then the tests in `15-hardware-test-plan.md`.

Every card for sale gets the same image. After that, the devices update themselves from your update
repository (`32-updates.md`); you never flash them again.

## 5. First start and first checks

1. **Settings page.** On your phone or computer (on the same Wi-Fi), open the
   address on the screen or `http://companion.local`, and choose a
   settings-page password.
2. **A language-model key.** Under **AI services**, choose a service and
   model, paste your key, **Save and test**. The voice and speech
   recognition start as the free on-device ones.
3. **A character.** Under **Characters**, add a `.char` file (any of the
   test characters, or one made with Character Studio,
   `14-making-characters.md`).
4. **Talk.** Press the listen button and say hello.

Then work through the tests in `15-hardware-test-plan.md`: the eight
bring-up tests (power and screen, boot time, Wi-Fi setup, the settings page,
both buttons, the 12-second reset, sound out, sound in), then the character
and conversation tests. Each has a pass mark.

## 6. What to send back

These decide the next software changes. Copy the output into a message to
Claude, or into the test plan's table.

| What | How |
|---|---|
| Boot time | A stopwatch from power on to the character |
| Frames per second | `journalctl -u companion-stage -n 20`: the heartbeats show `fps` (aim: 30 or more) |
| Memory | `free -m` while talking |
| Under-voltage | `journalctl -k \| grep -i voltage` (should print nothing) |
| The screen line that worked | `grep dsi /boot/firmware/config.txt` |
| The sound card's name | `aplay -l` and `arecord -l` |
| Echo cancellation | Turn on *Let me interrupt it by speaking* (Device): does it ever answer itself? |
| Reply speed | Time from the end of your sentence to its first sound, per AI service |
| Mouth and sound in step | Film the screen with sound; is the mouth early or late? |
| How it looks | Watch it idle and talk for two minutes: anything that tears, flickers or jumps |
| Anything that went wrong | `journalctl -u companion-core -u companion-stage -b > log.txt`, and send `log.txt` |

## 7. If something is wrong

| Problem | Look at | Likely cause and fix |
|---|---|---|
| Nothing on the screen; the Pi's lights are on | Connect a TV by micro-HDMI to see messages; `journalctl -u companion-stage -b` | The screen line (step 4.6), the ribbon the wrong way round or not fully in, or the screen's power lead not on pins 2 and 6 |
| Screen lights up but stays black | `journalctl -u companion-stage -b` | The stage program is missing or did not start; run the installer again and read its last lines |
| A lightning-bolt symbol, or random restarts | `journalctl -k \| grep -i voltage` | The power supply is too weak: use the official one (27 W for a Pi 5) |
| No sound | `aplay -l` | The device picked another sound card: name the ReSpeaker Lite in `/etc/default/companion` (`13-build-and-install.md`), then `sudo systemctl restart companion-core` |
| Crackling or cutting out when loud | `journalctl -k` | Power: the 27 W supply on a Pi 5; on a Pi 4, try another USB port |
| It never hears you | Settings page, Home, *What is happening* | Listening is off (press the button); the microphone is not the ReSpeaker (`arecord -l`) |
| The listen button does nothing | Wiring | It must go to pins 11 and 9; check the leads are pushed fully on |
| The power button does nothing | Wiring | Pins 5 and 14. On a Pi 5 it shuts down only; it does not start the Pi again |
| `companion.local` is not found | Your router's device list | Use the address shown on the screen instead; some Windows computers do not look up `.local` names |
| "refused the key" | AI services | The key is wrong, or the account has no credit |
| The character is a still picture | `journalctl -u companion-stage -b` | The stage could not draw this character; the log names what is missing. Try another character |

## Later

- A **case**: once the parts are in hand, measure them and design one
  (3D-printed, or a stand that holds the screen upright).
- **Updates** on this unit: copy a newer download to the Pi and run
  `sudo ./device/update.sh` in it; it goes back by itself if the new
  version does not start (`13-build-and-install.md`, step 7).
- A second board to compare, the **Radxa Zero 3W** (`22-board-options.md`),
  needs its own image first; not yet built.
