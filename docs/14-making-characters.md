# Making a character from a picture

Status: the chain runs here on Linux (CPU only). It has not been run on a
Mac yet; the steps below are what should work there, marked *unchecked*.

The device cannot make characters itself: the part-finding model needs about
5 GB of memory (decision D-031). A laptop makes the `.char` file and you add
it to the device through its settings page.

## What the laptop needs

- Python 3.10 or newer.
- About 6 GB of free memory while the part-finding model runs, and 3 GB of
  disk for the model and libraries.
- No graphics card is needed: one picture takes about 30 seconds on four
  ordinary processor cores. On an Apple Silicon Mac the model can use the
  Mac's graphics (`--device mps`), *unchecked*.

## The easy way: Character Studio

Character Studio is the character maker: an app for a laptop or desktop computer (**Windows, Mac or
Linux**). It runs on that computer only and shows its page in the browser. Adding and managing characters
afterwards is done on the device's own settings page, in any browser.

### Installing it (buyers)

Download the app for your computer and install it like any other. It brings everything it needs, its own
Python included: nothing else to install, no terminal.

| Computer | Download | Install and open |
|---|---|---|
| **Windows 10 or 11** (64-bit) | `CharacterStudio-Setup.exe` (17 MB) | Double-click it, then **Install**. Character Studio is then in the Start menu (and on the desktop). Remove it in **Settings → Apps** |
| **Mac** with Apple silicon (M1 or newer), macOS 12 or newer | `CharacterStudio.dmg` (49 MB) | Open it and drag **Character Studio** to **Applications**, then open it from there |
| **Linux** (64-bit) | `CharacterStudio-x86_64.AppImage` (33 MB) | Allow it to run (right-click → **Properties** → **Permissions** → *Allow executing as a program*), then double-click it |

The files are on the release page (**Character Studio** under *Releases*); the version is in the release's
name. The *Licences and notices* link at the bottom of the studio's page lists what the app is made of.

**The first start sets it up**: a page in the browser shows it downloading its AI tools, once (about 1 GB;
about 3 GB with an NVIDIA graphics card), step by step with a progress bar, in a few minutes. Then the
studio opens in its place. Later starts take a few seconds. If the internet drops, the page says so, with
**Try again** (what is downloaded is kept) and **Repair** (download it all again).

The studio's page has a **Quit** button. Closing the page is enough, too: the studio stops by itself 15
minutes later (never while it is making a character or downloading). Opening the app again while it runs
just shows its page again.

**Until the apps are signed** (see *Code signing* below), Windows and macOS warn about an app from a new
publisher. Once per computer:
- **Windows**: the browser may say the file is not commonly downloaded: choose **Keep**. Then "Windows
  protected your PC": choose **More info**, then **Run anyway**.
- **Mac** (macOS 15 or newer): it says Apple could not check Character Studio: choose **Done**. Open
  **System Settings → Privacy & Security**, scroll down to *Character Studio was blocked*, choose **Open
  Anyway** and confirm with your password. (On macOS 14 or older: Control-click the app, choose **Open**,
  then **Open**.)

Where it keeps things: the downloaded tools and the part-finding model are in a folder in your home
folder, `.character-studio` (several GB). The Windows uninstaller asks whether to delete it; on a Mac or
Linux, delete that folder by hand to get the space back.

### Using it

The page first offers to download the part-finding model once (about 1.3 GB, from its authors' pages).
After that:

1. Drop a picture in and give the character a name. A character card (the
   PNG files other AI-companion apps share, with the character's
   personality inside) brings its name and personality too: the picture
   becomes the drawing and the card who they are.
2. Watch the steps; a preview of the character moving appears when it is
   made (drawn on this computer; the device moves more smoothly).
3. Check what it can do. If an arm moved wrongly in the preview (a coat's
   side taken for an arm, say), untick it and make it again.
4. Say who the character is (the same questions as the device's settings
   page), or leave that for later.
5. Send it to the device: its address (`companion.local`, or the address the
   device shows) and the settings-page password, which is used for that one
   upload and not kept. Or save the `.char` file and add it on the device's
   settings page.

PyTorch, the AI library, comes for the computer's graphics: an NVIDIA card on Windows or Linux, the Mac's
own graphics on Apple silicon, otherwise the processor, which also works (about 30 seconds a picture on
four cores).

### Making the apps (the owner)

On GitHub: **Actions → Make the Character Studio apps → Run workflow**, with a version (`2026.11.21`)
and what to do with the release: **draft** (the default), **publish**, or **none**. In about ten minutes
**Releases → Character Studio 2026.11.21** has the Windows installer, the Mac disk image and the Linux
AppImage (with the Linux runtime's source code and the files' checksums). A draft is seen only by you:
check it, then **Edit → Publish release**. The workflow builds each app with its own Python
(`studio/packaging/build_app.py`), then **installs it the way a buyer would and runs it**: the first start
with its downloads, the page, the studio answering, Quit; on Windows also through the Start-menu icon's
own program and an uninstall; on a Mac also after moving the app (which must not download again).

Once published, it is the repository's latest release, and these links always give the newest apps, for
your web site or the campaign page:
`https://github.com/seafire220/repo/releases/latest/download/CharacterStudio-Setup.exe`, `.../CharacterStudio.dmg`
and `.../CharacterStudio-x86_64.AppImage`. Not the update repository: devices read its newest release as
their update.

**Code signing** takes away the warnings above. It needs, once a year:
- **Apple**: the Apple Developer Program (US$99, about A$150). The workflow can then sign and notarize the
  Mac app; that needs your certificate and an app-specific password added as secrets.
- **Windows**: a code-signing certificate (prices vary by seller; Microsoft's own signing service is the
  cheapest where it is offered to your kind of business). Windows may still warn for a while, until
  enough people have installed the app.

Neither is built in yet: tell me when you have them.

### From the project's files (developers)

`studio/start.sh` (Linux, Mac), `studio/start-mac.command` (a Mac, double-clicked) and `studio/start.bat`
(Windows) start the studio from a copy of this repository, with a Python 3.10 or newer already installed,
writing what they do in a window. They use the same launcher (`studio/launch.py`) and the same folder.

**Checked** (2026-11-21). On GitHub's computers, the first build passed everything: Windows (Server 2025:
installed, started from the Start-menu icon's own program, uninstalled), macOS 26 on Apple silicon (dragged
from the disk image, started, moved, started again without downloading) and Linux (the AppImage started
twice), each with its first start downloading PyTorch 2.15 and the studio's other parts for real. On Linux
here, the AppImage built and started from two different places with the
studio's tools already downloaded (it pointed its environment at its own Python again and downloaded
nothing), and a first start in an empty folder up to the download this sandbox refuses (the page showed
each step, then *Try again*). On **Windows, macOS and Linux** on GitHub's computers: the apps are built,
installed and run as above (`.github/workflows/studio-apps.yml`), and the studio's own tests run with the
same environment a buyer's computer gets (`.github/workflows/studio.yml`).

**Older computers.** An Intel Mac gets no new PyTorch (its last is 2.2), so the app is made for Apple
silicon only: with an Intel Mac, use a Windows or Linux computer instead. A Mac on macOS 13 or older gets an
older PyTorch, which should work, *unchecked*. An older NVIDIA card is served by the CUDA 12.6 build the
studio asks for on Windows; on Linux, if PyTorch says the card is too old, set
`CHARACTER_STUDIO_TORCH_INDEX=https://download.pytorch.org/whl/cu126` and use **Repair** on the setup page.

## From the command line

### One-time setup

```bash
python3 -m venv ~/character-maker
```

```bash
source ~/character-maker/bin/activate
```

```bash
pip install -r studio/requirements.txt -r studio/requirements-model.txt
```

The rigger, image2live2d (Apache-2.0), is installed from its source at the
version that was tested:

```bash
pip install "git+https://github.com/Wzhang3912/image2live2d@714e7cc"
```

The part-finding model comes from the See-through project (Apache-2.0).
Fetch its code and the model weights (1.3 GB):

```bash
git clone https://github.com/shitagaki-lab/see-through ~/see-through
```

```bash
huggingface-cli download 24yearsold/l2d_sam_iter2 --local-dir ~/see-through-weights
```

### Making a character

```bash
python studio/make_character.py picture.png --name "Aria" \
    --see-through ~/see-through --weights ~/see-through-weights/checkpoint-18000.pt
```

This writes `aria.char`. Add it on the device's settings page under
**Characters**. The tool prints notes about anything it could not do, for
example:

```
wrote aria.char (2.6 MB)
  note: the mouth was found by its colour, not by the model; check that it moves in the right place
  cannot: arm left
```

Edit the persona (who the character is, how it speaks, its greeting) on the
settings page, or write it in a file beforehand and pass `--persona file.md`.
The greeting is the text under a `## Greeting` heading.

## Which pictures work

Best:

- One character, whole body or at least head and shoulders, facing the
  viewer or turned slightly.
- At least 800 pixels tall. Small pictures work but look soft, and heavily
  compressed small JPEGs show faint lines round the eyes when enlarged.
- A transparent or plain one-colour background. Busy backgrounds are refused
  for now (no background-removal model is included yet).
- Eyes open and mouth closed or slightly open.

Works, with limits:

- Faces partly in profile: turning the head shows a small patch beside the
  face at the far ends of a turn.
- Arms hanging at the sides move: each turns at the shoulder and bends at
  the elbow, and the body is painted in behind them, so the character can
  wave, put one or both hands on its hips and shrug. An arm that is mostly
  hidden (behind long hair), very thin in the picture, raised (a hand at
  the chin) or crossed in front of the body stays part of the body and does
  not move; the tool's notes say which, and gestures that need it are not
  offered. Check the preview: the part-finding model sometimes marks a coat's
  sides as arms.
- No visible neck: a plain one is added under the face.

## A second copy in the .model3.json format (optional)

Add `--live2d` to put a copy of the character in the `.model3.json` format
(the format Live2D-style models use; played on the device by Ayagami) in the package
as well (`visual/live2d/`: `model.moc3`, `model.model3.json`, textures,
physics and motions):

```bash
python studio/make_character.py picture.png --name "Aria" --live2d \
    --see-through ~/see-through --weights ~/see-through-weights/checkpoint-18000.pt
```

The copy is converted from the finished puppet, movement for movement
(`studio/live2d_export.py`), so it moves the same as the Inochi2D version,
including the improved head movement. The tool replays both versions
against each other and prints the largest difference: a few pixels at
most (under 4 on the test characters), in poses that combine a full body
turn with a full head turn. Players that read this format, such as
Ayagami, can then show the character. No Live2D software is used or needed.

Read `19-live2d-format-licensing.md` before selling or sharing characters in
this format. The device itself still plays the Inochi2D version.

## What the character can do

Each package lists its abilities (blink, speak, head turn, hair sway, each
arm, legs) and the settings page shows what it cannot do. Blinking and the
open mouth are drawn from the character's own colours and stay hidden until
used, so a character at rest looks exactly like its picture.

## Gestures of your own, from a video

Every character gets the device's gestures and idle actions. To add one
of your own, or to make one look more like a real person, film it.

The easiest way needs no computer: the device's settings page shows a QR
code that opens a recorder on your phone, which films the gesture, finds
your movement and hands it to the device (*Teaching it a gesture with your
phone* in `28-user-guide.md`). The steps below do the same from a video on
a computer, with Character Studio:

1. Film yourself (or someone who agreed to it) facing the camera, in good
   light, head and shoulders or the upper body in view, doing the gesture
   once and starting and ending still. A phone video is fine.
2. Install what the tool needs once, in the studio's Python (set up by
   the app or `studio/start.sh`; `python3 studio/launch.py --print-python` says where it is):

```bash
"$(python3 studio/launch.py --print-python)" -m pip install -r studio/requirements-motion.txt
```

3. Make the gesture, and save it on the device (it asks for the
   settings-page password):

```bash
"$(python3 studio/launch.py --print-python)" studio/record_motion.py my-video.mp4 --name little_dance --words "do a little dance" --send companion.local
```

The first run downloads MediaPipe's face and body landmark models (about
10 MB). Use `--start` and `--end` (seconds) to take part of a longer
video, and `--kind idle` for something the character does by itself when
nobody is talking. `--out little_dance.json` writes the gesture to a file
instead.

On the device's settings page, the Animation tab plays it on the screen
(*Try it on the device*). Change its tracks there if needed, then save
it. A new gesture with `--words` can be asked for by the conversation. The
character moves as the video shows: a head turned towards the right of the
picture turns the character towards the right of its screen. The lean
towards or away from the camera is not read.

## Use of pictures and voices

Only use pictures and voices you have the right to use. The device ships with
no characters made from other people's work, and a package never contains
API keys or memories.
