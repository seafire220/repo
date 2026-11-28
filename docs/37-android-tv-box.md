# Running on a cheap Android TV box: an assessment

Status: 2026-11-28. Assessment only; nothing is built (the owner: "don't do this yet").

## The short answer

**Feasible, by putting Linux on the box instead of Android, on boxes with the right graphics chip.** Our
device software already runs on Linux with any OpenGL ES 3 graphics (it runs on a Pi, on a Mac and on Linux
PCs today: `35-try-on-a-computer.md`). Keeping Android and making an Android app is possible too, but it is
a second product to build and maintain. Either way a TV box brings risks a Pi does not: the hardware inside a
model changes without notice, Linux on it is community-made, Wi-Fi often has no Linux driver, and cheap
Android boxes have a malware problem.

## What a TV box is

A small box with a system-on-chip, 1–4 GB of memory, 8–32 GB of built-in storage, Wi-Fi, Ethernet, HDMI out,
USB and an infrared remote, running Android (often an old or uncertified build), with a case and power
supply included. It has **no screen, no microphone and no speaker**: it is made for a TV.

Prices move a lot and I found no reliable current Australian price. Older listings put S905X3 boxes (4 GB,
32 GB) at about US$30–60 and RK3566 boxes at about US$45–130; one Australian listing for an S905X3 box was
about A$78. Check eBay AU, Amazon AU or AliExpress before deciding.

## Which boxes could work

Our screen program needs **OpenGL ES 3.0**. Under Linux that means the open Mesa drivers:

| Chip (common boxes) | Graphics | Under Linux | Suitable? |
|---|---|---|---|
| Amlogic S905X3 (X96 Max+, HK1 Box, Vontar X3) | Mali-G31 | Panfrost: OpenGL ES 3.1 (works; not formally conformant) | **Yes**: the best-supported box chip; community Armbian images list these boxes as stable |
| Amlogic S905X4, S905W2 | Mali-G31 | Panfrost | Probably; fewer Linux images |
| Allwinner H616 / H618 (Vontar H618, X98H) | Mali-G31 | Panfrost | Possibly; some community images list them as stable, others advise against Allwinner boxes |
| Rockchip RK3566 (H96 Max, X96 X6) | Mali-G52 | Panfrost: OpenGL ES 3.1, **officially conformant** | **Yes**: the same chip as the Radxa Zero 3W in `22-board-options.md` |
| Rockchip RK3318 / RK3328 / RK3528 | Mali-450 | Lima: OpenGL ES 2.0 only | **No** (our drawing needs ES 3) |
| Allwinner H313, older S905W | Mali-G31 / Mali-450 | Varies | Check each |

## The two ways

### A. Replace Android with Linux (recommended, if any)

- **How**: boot a community Armbian image for the box from a microSD card (or USB), then install our software
  as on a Pi: `device/install.sh` on Debian-based Armbian. The image builder (`device/build-image.sh`) makes
  Raspberry Pi images; a TV-box variant would start from Armbian, with that box's kernel and device tree.
- **What carries over**: everything. The core, the screen program, the phone features, updates (signed the same
  way), the settings page. The screen program's desktop or ES path is chosen at start.
- **Sound**: a USB microphone and speaker (the ReSpeaker Lite is USB already), or the TV's speakers over HDMI.
- **Screen**: the TV, in landscape, or a small HDMI monitor. The character's framing and the new backgrounds
  already fill any shape of screen; a portrait monitor keeps today's look.
- **Wi-Fi**: the weak point. Many boxes use Wi-Fi chips with no Linux driver; Ethernet always works. This
  decides which box models are usable.
- **Same name, different inside**: a box sold under one name can change its chip, memory or Wi-Fi from one
  batch to the next, so every batch needs checking. For a product that is a serious problem.
- **Security**: putting Linux on it removes the box's own firmware, which matters: the FBI warned in June 2025
  about BADBOX 2.0, malware found preinstalled on cheap uncertified Android TV boxes (over a million
  infected, and over 10 million by Google's count), used to turn them into proxies for crime.

### B. Keep Android, and make an Android app

- **How**: the screen program (Rust, OpenGL ES 3) can run on Android; the core (Python with aiohttp, numpy, the
  speech models) would need packaging for Android (Chaquopy or Briefcase) or rewriting; sound and the
  microphone through Android's own interfaces; starting at power-on by being the box's home app, which TV
  boxes allow unevenly (kiosk tools rely on Device Owner mode, set up over adb).
- **Cost**: a second platform to build, test and update, with its own app signing, and the box's Android
  (with whatever came on it) underneath.
- **Certified boxes** (Google TV devices) avoid the malware risk but lock the home screen and cannot run Linux.

## Is it cheaper?

The box itself, with case, power supply, storage and Wi-Fi included, is likely cheaper than a Pi 5 1GB
(A$78–83) plus case, supply and card. But a TV box has no screen, microphone or speaker, which the desk
device must still add (a 5–7" screen and a USB microphone and speaker). So the saving is real but smaller
than the box's price suggests, unless the product becomes **"use your own TV"**: no screen in the box, the
character on the living-room TV. That is a different product, and a cheaper one.

## If you want to try it later

1. Buy two boxes: an S905X3 one (X96 Max+ or HK1 Box) and an RK3566 one, with Ethernet.
2. Boot a community Armbian image for each from a microSD card.
3. Copy this repository over and run `device/try-on-a-computer.sh --check`: it builds everything, starts it,
   and checks the settings page answers and the screen program draws (and reports its OpenGL version).
4. Note the frame rate, Wi-Fi and sound. A day's work tells us whether a box is worth a proper image.

**My recommendation:** for the desk device, the Radxa Zero 3W (the same RK3566 chip, with official images and a
fixed design) is the safer cheap board; a TV box is worth trying only as the probe above, or for a
"your own TV" edition.

## Sources

- Community Armbian for TV boxes: [ophub's amlogic-s9xxx-armbian](https://github.com/igorpecovnik/amlogic-s9xxx-armbian),
  [Armbian for TV boxes](https://github.com/Tanter135/armbian-for-tvboxes/blob/main/README.md),
  [a 2026 install guide](https://electronics.alibaba.com/buyingguides/install-linux-on-android-tv-box-practical-guide-2026)
- Panfrost: [Mesa's driver page](https://docs.mesa3d.org/drivers/panfrost.html),
  [Collabora: OpenGL ES 3.1 conformance on Mali-G52](https://collabora.com/news-and-blog/news-and-events/panfrost-achieves-opengl-es-3.1-conformance-on-mali-g52.html),
  [CNX Software](https://www.cnx-software.com/2021/06/14/panfrost-opengl-es-3-1-midgard-mali-t760-bifrost-mali-g31-g52-g76-gpu/)
- BADBOX 2.0: [FBI PSA I-060525](https://www.ic3.gov/PSA/2025/PSA250605),
  [Help Net Security](https://www.helpnetsecurity.com/2025/06/06/millions-of-android-devices-roped-into-badbox-2-0-botnet-is-yours-among-them/),
  [PCMag via Yahoo (Google's count)](https://tech.yahoo.com/cybersecurity/articles/google-malware-spread-over-10-175846557.html)
- Kiosk apps on Android TV: [Android dedicated devices](https://developer.android.com/work/dpc/dedicated-devices/cookbook),
  [Fully Kiosk FAQ](https://www.fully-kiosk.com/docs/en/faq/tv)
- Box prices (old listings): [AndroidPCTV](https://androidpctv.com/?p=24073), [CNX Software (H96 Max RK3566)](https://www.cnx-software.com/2021/03/04/rockchip-rk3566-tv-box-h96-max-android-11/)
