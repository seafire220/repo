# Low-cost AI character device

An independently designed, lower-cost alternative to the SyBran CODE27
Companion Hub: a desk device that shows a character you can talk to. The
character is a 2D anime-style character (Inochi2D and other 2D rigged
models, or one made from a single picture) or a 3D VRM avatar. It runs on
commodity hardware (a Raspberry Pi with a small screen) and original
software, uses the owner's own AI service keys or a free on-device voice,
and is not tied to one AI or voice provider.

Two tiers: **Lite** plays pre-rendered 2D animation; **Standard** drives a
rigged character. Standard is being built first.

**Status:** written and tested in software, including a ready-to-flash card
image built and checked on GitHub; not yet run on real hardware. What is
done and what comes next: [docs/21-roadmap.md](docs/21-roadmap.md).

## What's where

| Folder | What it is | Where it ends up |
|---|---|---|
| [`core/`](core/) | The device's main program (Python): conversation, voices, speech recognition, memory, characters, alarms, Wi-Fi setup, updates, and the settings page you open in a browser | On the device |
| [`stage/`](stage/README.md) | The screen program (Rust, OpenGL ES): draws and animates the character, with Inox2D for Inochi2D models, Ayagami for `.model3.json` models, and our own VRM renderer | On the device, built for the Pi's 64-bit ARM processor |
| [`device/`](device/) | What turns Raspberry Pi OS into the device: the card-image builder (`build-image.sh`) and checker (`check-image.sh`), the installer (`install.sh`), system services and settings, the updater, the update-signing tool (`release.py`), and the operating system's source-code collector (`fetch-sources.py`) | The services, settings and updater go on the device; the tools run on GitHub or the owner's computer |
| [`studio/`](studio/) | Character Studio, the app that makes a character from one picture and sends it to the device | On the buyer's computer (Windows, Mac or Linux), never on the device |
| [`docs/`](docs/) | Design, guides, measurements and decisions (list below) | Nowhere: for reading |
| [`.github/workflows/`](.github/workflows/) | The builds GitHub runs: the card image, updates, and the Character Studio apps | On GitHub |
| [`THIRD_PARTY.md`](THIRD_PARTY.md) | Every third-party part and its licence, in short ([docs/34-licences.md](docs/34-licences.md) in full) | Also on the device and in the app |

### What goes on the device

Only these parts of the repository: `core/companion/` (the main program and its settings page),
the stage program built from `stage/` (one binary file) with `stage/motions/`, and the services and
system settings from `device/`. With them come the speech-recognition, voice and wake-word models,
downloaded during the build from their publishers and checked against fixed hashes, and the licence
notices. Nothing else is copied to the device: not Character Studio, the docs, the tests or the tools.

`device/install.sh` is the one list of what is installed and where (under `/opt/companion`).
`build-image.sh` runs it inside an official Raspberry Pi OS Lite image to make the card image, and
updates (`release.py`) carry the same parts.

### Making a card image and putting it on a card

1. On GitHub: **Actions → Make a device image → Run workflow**, with a version such as `2026.11.21`.
   About half an hour.
2. The image goes on a release, **Releases → Device image 2026.11.21**: a draft only you can see until
   you publish it. Download `companion-<version>.img.xz` from it.
3. Write it to a microSD card with **Raspberry Pi Imager**, Raspberry Pi's free app
   (raspberrypi.com/software; it is not part of this repository): *Choose OS → Use custom*, pick the
   file, choose the card, and say *No* to customisation. Step by step:
   [docs/29-hardware-setup.md](docs/29-hardware-setup.md).
4. After that, sold devices update themselves over the internet from signed updates published in
   [`seafire220/update-repo`](https://github.com/seafire220/update-repo), with no reflashing:
   [docs/32-updates.md](docs/32-updates.md).

The image's release also gets the source code of every operating-system package in it, which the
GNU GPL and LGPL require to be offered with the device. The device's notice sends owners there, so
publish the release before selling devices with that image, and keep it for at least three years
after the last one is sold ([docs/34-licences.md](docs/34-licences.md)).

### Character Studio, for buyers' computers

**Actions → Make the Character Studio apps → Run workflow** builds the Windows installer, the Mac
app and the Linux AppImage, installs and tests each, and puts them on a release, **Character Studio
\<version\>** (a draft until published). Once published, these links always give the newest:

- Windows: `https://github.com/seafire220/repo/releases/latest/download/CharacterStudio-Setup.exe`
- Mac: `https://github.com/seafire220/repo/releases/latest/download/CharacterStudio.dmg`
- Linux: `https://github.com/seafire220/repo/releases/latest/download/CharacterStudio-x86_64.AppImage`

Installing and using it: [docs/14-making-characters.md](docs/14-making-characters.md).

Built files (card images, apps, updates) are never committed to the repository: they are made by the
workflows and kept on releases (and, for two weeks, on each run's page under *Artifacts*).

## Licences

This repository's own code and documents are the owner's (all rights reserved; no licence to reuse
them is given). The third-party software, models and data it uses are each under their own licence:
all allow commercial use without a negotiated licence, and their notices are shipped with the device
and the app. The audit, with what each licence asks of us and the questions left for a lawyer:
[docs/34-licences.md](docs/34-licences.md); the short list: [THIRD_PARTY.md](THIRD_PARTY.md).

Live2D and Cubism are trademarks or registered trademarks of Live2D Inc.; Raspberry Pi is a
trademark of Raspberry Pi Ltd. This project is not affiliated with or endorsed by either. No Live2D
software is used.

## Documents

1. [Reference product research](docs/01-reference-product-research.md)
2. [Requirements interview and answers](docs/02-requirements-interview.md)
3. [Requirements specification](docs/03-requirements-spec.md)
4. [Technology research](docs/04-technology-research.md)
5. [Hardware options and bill of materials](docs/05-hardware-and-bom.md)
6. [System architecture](docs/06-architecture.md)
7. [Test: Inochi2D on Linux](docs/07-spike-inochi2d.md)
8. [Review of the owner's earlier work](docs/08-prior-work-review.md)
9. [Experiment: flat image to puppet without repainting](docs/09-flat-image-experiment.md)
10. [Trial on five unfamiliar images](docs/10-generalisation-trial.md)
11. [Finding the parts with an existing model](docs/11-part-labelling-model.md)
12. [Trial on five fresh images, and the third head-movement design](docs/12-fresh-image-trial.md)
13. [Building and installing the device](docs/13-build-and-install.md)
14. [Making a character from a picture (Character Studio)](docs/14-making-characters.md)
15. [Hardware test plan and troubleshooting](docs/15-hardware-test-plan.md)
16. [Measurements](docs/16-measurements.md)
17. [Evaluation: Ayagami, Umamo and Godot](docs/17-engine-evaluation.md)
18. [How the device works out of the box](docs/18-out-of-the-box.md)
19. [Licensing check: .model3.json models without Live2D's software](docs/19-live2d-format-licensing.md)
20. [How heads turn](docs/20-head-turns.md)
21. [Roadmap: what is done, what comes next, how to resume](docs/21-roadmap.md)
22. [Board options for the Standard device](docs/22-board-options.md)
23. [Drawing workflow: hidden areas, alternate poses, new views (candidate assessment)](docs/23-drawing-workflow.md)
24. [Motion: a generic skeleton every character is fitted to](docs/24-motion.md)
25. [Does the whole product fit together?](docs/25-product-review.md)
26. [Animation: how the character moves](docs/26-animation-director.md)
27. [Animation tested on all eleven test characters](docs/27-character-tests.md)
28. [User guide: your character device (for buyers)](docs/28-user-guide.md)
29. [Setting up the first device, once the parts arrive](docs/29-hardware-setup.md)
30. [VRM avatars: 3D characters beside the 2D ones](docs/30-vrm.md)
31. [Memory: conversations, lasting memories and a diary](docs/31-memory.md)
32. [Software updates for sold devices](docs/32-updates.md)
33. [What else would make it complete and polished](docs/33-finishing.md)
34. [Licences: what we use, what each licence asks, what is left for a lawyer](docs/34-licences.md)
35. [Trying the device's software on a computer, with a phone](docs/35-try-on-a-computer.md)
36. [What else would make it complete: second review (with comparable products)](docs/36-polish-review.md)
37. [Running on a cheap Android TV box: an assessment](docs/37-android-tv-box.md)
- [Decision log](docs/decision-log.md)

## Known gaps

- Nothing has run on real hardware yet; memory use, boot time, frame rate and speech speed on the Pi
  are estimates until measured (`15-hardware-test-plan.md`).
- The AI service adapters follow each service's documentation and are tested against stand-ins.
- Before selling: the questions for a lawyer in `19` and `34`, an original default character,
  signing the apps and the maker's details in the device's notice (`core/companion/notices/product.json`).
- The Lite tier is not started.
