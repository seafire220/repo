# Trying the device's software on a computer, with a phone

Status: 2026-11-28. A Mac (or a Linux PC) can stand in for the device while the hardware is not to hand:
the settings page, the character drawn in a window, and everything a phone does with the device:
recording a gesture (test 34 in `15-hardware-test-plan.md`), the live camera (35) and face-tracking
apps (36). It runs the device's own software from this repository: `device/try-on-a-computer.sh`.

What is different from the device:

- No microphone or speaker: it does not listen or speak aloud (typing to it on the settings page works).
- It never looks for device updates, has no Wi-Fi setup and no buttons. The window stands in for the screen.
- Everything it keeps (characters, settings, any keys typed in) is in `~/.companion-try` on the computer.

## A Mac and an iPhone

No cable and no pairing: the iPhone reaches the Mac over the home Wi-Fi. Both must be on the same
network, and not a guest network (those usually keep devices apart).

1. **Tools, once.** In Terminal (Applications → Utilities):

   ```bash
   xcode-select --install
   ```

   Apple's command line tools: git, Python, and what Rust needs to build. Then Rust:

   ```bash
   curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
   ```

   Press Enter for the standard install, then close Terminal and open it again.

2. **The software, once.**

   ```bash
   git clone https://github.com/seafire220/repo.git companion
   ```

   Later, to bring it up to date: `cd companion` then `git pull`.

3. **Start it.**

   ```bash
   cd companion
   ```

   ```bash
   device/try-on-a-computer.sh
   ```

   The first time it builds the screen's program, which takes a few minutes. Then it prints two
   addresses: one for the Mac (`http://localhost:8080`) and one for the phone (`http://192.168.x.y:8080`).
   Leave Terminal open; Ctrl-C there stops it. Starting it again later is quick.

4. **What macOS may ask.**
   - *Do you want the application "Python" to accept incoming network connections?* Allow. (Only when
     the Mac's firewall is on; without it the phone cannot open the pages.)
   - When you connect a face-tracking app: *"Terminal" would like to find and connect to devices on your
     local network.* Allow. If it was refused: System Settings → Privacy & Security → Local Network →
     Terminal on.

5. **On the Mac**, open `http://localhost:8080`, choose a password, and add a character on the
   Characters tab: a `.char` from Character Studio, or a VRM avatar. Its window opens (it may open
   behind Terminal).

6. **On the iPhone**, open the phone's address in Safari, or scan the QR codes the settings page shows.

Then tests 34 to 36:

- **34, a gesture filmed with the phone**: Animation tab → *Show a QR code for my phone*; scan it with the
  iPhone's camera.
- **35, the live camera**: Animation tab → *Live camera* → *Use the live camera*. Open the new `https`
  address (port 8443) on the iPhone. Safari warns *This Connection Is Not Private*: tap *Show Details*,
  then *visit this website*, then *Visit Website*. The settings page shows the certificate's fingerprint
  to compare.
- **36, a face-tracking app**: VTube Studio on the iPhone (free; it needs an iPhone with Face ID). In its
  settings turn on *3rd Party PC Clients* and note the address it shows. On the settings page: *Copy me
  live* → *VTube Studio or MeowFace* → type that address → *Connect*. iFacialMocap works the same way.

## If something does not work

| What | Look at |
|---|---|
| The phone cannot open the address | Same Wi-Fi, not a guest network? Python allowed through the firewall? The Mac's address can change: the script prints it each time it starts |
| The live camera's page will not load after the Mac's address changed | The certificate still names the old address; Safari's warning lets you go on all the same |
| No character window | It opens once there is something to show; `~/.companion-try/stage.log` |
| The build failed | `~/.companion-try/build.log` |
| Start again from nothing | Delete `~/.companion-try` |

## A Linux PC

The same script, in a desktop session (the character needs a window), with Python 3.9 or newer
(`python3-venv`), Rust and `openssl`.

## Checked

`.github/workflows/computer.yml` runs on a Mac (with Apple's own Python, bash and openssl, as a Mac has
after `xcode-select --install`) and on Linux, on every change to the core, the stage or the script: the
script's own check (`--check`: it sets everything up, starts both halves, and checks that the settings
page and the phone's page answer and the screen's program draws), the live camera's certificate, and the
stage drawing a picture with desktop OpenGL (`stage/tools/smoke_test.py`), the only kind a Mac has. The
stage asks for an OpenGL 3.3 core context there (`stage/src/window.rs`), as a Mac requires, and all its
shaders put their version line first.

First results, 2026-11-28, on GitHub's Mac (Apple silicon): the first run found the stage would not build
on a Mac at all (the window library, winit 0.29, depends on a Mac library current Rust refuses); the
stage moved to winit 0.30. Then everything passed: Python 3.9.6, OpenGL 4.1, the certificate made with
LibreSSL 3.3.6 naming the computer's address, and the stage drawing with no shader errors.
