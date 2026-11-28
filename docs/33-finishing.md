# What else would make it complete and polished

Status: 2026-11-20. The owner asked for a review of anything else that would make the product more complete
and polished. This is that review, in order of importance, with what was built marked. The "strongly
recommended" items and power-cut protection were built in the next round, the same day.
Everything below is about the product as a buyer meets it; `21-roadmap.md` keeps the overall order of work.

## Built in this round

| | What | Where |
|---|---|---|
| ✓ | **Software updates for sold devices**: signed with your key, installed at night, undone if they fail, and finished or undone after a power cut | `32-updates.md` |
| ✓ | **Speaking first**, as an option: greets you when you come back, asks how your plans went | `31-memory.md` |
| ✓ | **Memory that matches by meaning**: "my pup" finds the dog | `31-memory.md` |
| ✓ | **Your own rigged models**: Inochi2D files added directly; Live2D models' pose files respected (a sample showed two sets of arms at once before) | `28-user-guide.md` |
| ✓ | **Backup and restore**: every character with its memory, choices and animations, and the settings, in one file. Keys, the password and Wi-Fi are never in it. For keeping, or for moving to a new device or a bigger card | Device tab |
| ✓ | **A support report**: versions, board, storage, settings, which keys are set, recent problems and logs. No keys, no passwords, nothing anyone said. A buyer can email it to you | Device tab |

Built in the next round (2026-11-20), at the owner's request:

| | What | Where |
|---|---|---|
| ✓ | **Power-cut protection**: a read-only system partition and a separate data partition (below) | `device/build-image.sh`, `device/readonly/` |
| ✓ | **A wake word**: "hey", "hi" or "hello" and the character's name, or the owner's own words; in one breath with the question | `28-user-guide.md`, `core/companion/wakeword.py` |
| ✓ | **Captions**, and optionally what it heard | `stage/src/overlay.rs`, `core/companion/overlays.py` |
| ✓ | **The screen at night**: dark or dim in the owner's hours; the backlight follows where it can be set | `core/companion/backlight.py` |
| ✓ | **Family-safe mode**; turning it off needs the password again | `core/companion/family.py` |
| ✓ | **The microphone off, shown on the screen**: a two-second hold, the settings page, or an optional slide switch | `28-user-guide.md` |
| ✓ | **A USB connection to a computer**: the settings page over a USB-C cable | `device/usb/` |
| ✓ | **Updates built on GitHub, signed on the owner's Mac** | `32-updates.md` |

Built in the round after (2026-11-21), at the owner's request:

| | What | Where |
|---|---|---|
| ✓ | **Live2D models' own idle motions**, under the device's own movement (as made, gentler, or off) | `stage/src/live2d_motion.rs`, `28-user-guide.md` |
| ✓ | **3D avatars turn their heads to a profile (90°)**: looking back over the shoulder, and now and then by itself | `stage/src/vrm/rig.rs`, `24-motion.md` |
| ✓ | **Character Studio as a real app on Windows, Mac and Linux**: an installer, a disk image and an AppImage, each with its own Python; the first start shows its one-time download in the browser. Built, installed and run on all three on GitHub | `14-making-characters.md` |
| ✓ | **The ready-to-flash card image built on GitHub** (passes all 51 checks) | `29-hardware-setup.md` |

## Before selling (needed)

1. **Run it on the real hardware.** Nothing has run on a Raspberry Pi yet; everything is tested in software and under emulation. Follow `29-hardware-setup.md` and `15-hardware-test-plan.md`: frame rates, memory, boot time, the hotspot, the buttons, the sound card, a whole day of use.
2. **Make your update key and choose the update address** before building the images you sell (`32-updates.md`). A device made without them can only be updated by re-flashing its card.
3. **Test power-cut protection on the board** (built; "Power cuts" below): pull the plug 50 times, at
   random moments and while it saves, and check that it always starts, keeps its data, and repairs itself.
4. **A character in the box.** Commission original art (no existing character may ship, D-024), so a new device greets its buyer with someone rather than text.
5. **Code signing for Character Studio** (optional but strongly recommended): without it, Windows and macOS
   warn buyers about an unknown publisher the first time (`14-making-characters.md`). Apple's developer
   programme is US$99 a year; Windows certificates vary.
6. **The paperwork:**
   - the seven questions for a lawyer (`19-live2d-format-licensing.md`), including whether advertising the import of buyers' own Live2D models is safe (until then the product names Inochi2D and "other 2D rigged models", D-067);
   - the licence audit's remaining points (`34-licences.md`): the maker's name, contact and website in the device's notice (`core/companion/notices/product.json`; the website is also how the device names its makers to MET Norway when it asks for the weather), publishing each sale image's release with its source code and keeping it three years, the wake-word model's training data, and Inno Setup's request for a commercial licence;
   - in Australia, an electrical safety and EMC declaration (RCM) for the finished product. A pre-certified Pi and power supply help, but don't cover the whole device;
   - a privacy statement, saying what goes to the AI services the buyer chooses;
   - warranty and returns terms.
7. **A quick-start card in the box:** a QR code for the setup steps, and one for the user guide.

## Strongly recommended

7. ~~A wake word~~, 8. ~~captions~~, 9. ~~screen sleep at night~~, 10. ~~a family-safe mode~~, 11. ~~a
   microphone mute you can see~~: built (above). Still to do for them on the board: the wake word with
   real voices and the ReSpeaker Lite, captions read from across a room, the Touch Display 2's backlight,
   and fitting the mute switch (`15-hardware-test-plan.md`).

## Later

12. **Other languages**: the settings page, the voice and speech recognition are English today. Large: models and voices per language.
13. ~~Live2D models' own motions~~: built (above). Their other motions (a "tap" reaction, say) could become
    gestures the conversation asks for. Small.
14. **More than one person**: telling voices apart, so memory belongs to the right person and speaking first knows who came in. Large.
15. **Add the settings page to the home screen**, so it opens like an app on a phone. Small.
16. **Calendar, weather, smart home**, as tools the character can use. Medium each.
17. **Hearing about failed updates**: an opt-in report to you when a device goes back to an earlier version. Small, with a privacy statement.

## Power cuts: how the card is protected

A microSD card can be damaged if the power goes while it is being written: the file being written, and
sometimes data near it on the card. Images built now (`device/build-image.sh`; `--writable-system` builds
the earlier layout) protect against that in layers:

| Layer | What it does |
|---|---|
| **A read-only system** | The system partition (the operating system, the software, the models it came with) is mounted read-only, so in normal use nothing writes to it at all, and a power cut cannot damage it. The device always starts |
| **A data partition** | Everything the device saves goes on a third partition, `companion-data`: characters, memories, settings, keys, saved Wi-Fi, the time zone. It is made small in the image and grows to fill the card at the first start |
| **Changes kept in memory** | /var (logs, the network's state, temporary files) is an overlay whose changes live in memory; the logs are kept in memory, 20 MB at most |
| **Careful writes** | Every file the device saves is written whole and then swapped in (`write_atomic`), so a power cut leaves the old file or the new one, never half of one; the data partition is journaled ext4 |
| **A check at each start** | The data partition is checked and repaired at start. If it ever cannot be repaired, it is made again, empty, so the device still works; the settings page then asks for the latest backup |
| **Updates** | An update makes the system writable only while it installs (a minute or two, at night), then read-only again; a power cut in that time is finished or undone at the next start (`32-updates.md`) |
| **The power button** | Shuts down cleanly |

What it costs: a few hundred MB of the card set aside for the system's own use, logs that start afresh at
each start, and updates that cannot add operating-system packages (that was already so).

**Hardware that helps further** (not needed for the above to work):
- A **name-brand A1 or A2 card**, as in the parts list. For production runs, an **industrial or
  high-endurance card with power-loss protection** (SanDisk Industrial, Kingston Industrial, Swissbit):
  about A$15–35 for 8–32 GB.
- A board with **eMMC storage** instead of a card (the Radxa Zero 3W, or a Compute Module): more robust
  against power cuts than most cards.
- A small **supercapacitor or battery module** that keeps the board running for a few seconds after the
  power goes, and shuts it down cleanly: about A$15–40. Worth it only if testing shows a need.

## Not recommended

- **Encrypting the data on the card.** The device has nowhere safe to keep the key. A key on the card protects nothing, and asking the buyer for a password at every start-up is not appliance behaviour. The risk is someone who takes the card out and reads it. Mitigations instead:
  - keys can be revoked at their services in a minute;
  - **Device → Reset everything** before passing a device on;
  - the case can make the card hard to reach.
