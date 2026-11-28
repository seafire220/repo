# Licences: what we use, what each licence asks, what is left for a lawyer

Status: 2026-11-21. An audit of everything the device and Character Studio contain or download, written
by an engineer, not a lawyer: a lawyer should confirm it before units are sold (the questions are at the
end). The Live2D-format questions are in `19-live2d-format-licensing.md`.

## The short answer

- **Nothing in the product needs a negotiated licence or a fee for commercial use.** Every part we ship
  or download is under a licence that allows selling it. The one soft exception is the tool that builds
  the Windows installer: its makers *ask* companies to buy a licence, without requiring it (below).
- **What the licences ask of us, and where it is done:**
  - their texts and credits shipped with the product: the device's settings page, *Licences and
    notices*, and Character Studio's page, *Licences and notices*;
  - the source code of the GPL and LGPL parts: a written offer in the device's notice, and the source
    archive published with every card image and every Linux app;
  - no warranty from the open-source authors, said without limiting buyers' rights under consumer law.
- **Problems found and fixed in this audit:**
  - the first free voice was trained partly on research-only recordings, so it was replaced;
  - a copy of an LGPL library came inside a Python package; the operating system's own copy is used;
  - an AGPL database library was inside the Linux app's Python; it is left out;
  - the Linux app's runtime has an LGPL library; it is pinned and its source is published;
  - the speech package is pinned to versions with no GPL code in them;
  - the speech package's built-in libraries had no notices; they are added, with Eigen's source address
    as its licence (MPL-2.0) asks.
- **Wording:** the product names Inochi2D, and describes the other format as "other 2D rigged models
  (Live2D-style, `.model3.json`)". It does not advertise Live2D, and uses no Live2D logos or names in
  branding.

## On the device

| Part | Licence | What it asks of us | Where that is done |
|---|---|---|---|
| The operating system, based on Raspberry Pi OS Lite (Debian): Linux, the C library, systemd, NetworkManager, ALSA, Mesa, cage, Python, espeak-ng, aiohttp, NumPy, Pillow, DejaVu fonts and the rest | Each package's own, mostly GPL-2.0, GPL-3.0, LGPL, MIT and BSD (its `debian/copyright`) | The licence texts. The **source code** of GPL and LGPL packages: a written offer valid three years, or the source alongside. **GPLv3 "installation information"** for a consumer product: how to install changed software | *Licences and notices → Operating-system packages* shows every package's copyright file. The written offer is in the device's notice (`core/companion/notices/device.txt`). Every image's release carries its sources (`device/fetch-sources.py`). The notice says how to change the software: the card can be rewritten in any computer, and nothing on the device checks or locks the system |
| Raspberry Pi firmware, Wi-Fi firmware | Their makers' binary licences, which allow redistribution unchanged | Keep their licence texts | In their copyright files, as above |
| Our own software: `core/`, the stage, the device scripts | The owner's (all rights reserved) | | |
| The stage's 121 Rust libraries, among them glam, glow, glutin, winit and image | MIT, Apache-2.0, BSD, ISC, Zlib, Unicode-3.0 | Their licence texts and NOTICE files | `stage/THIRD_PARTY_LICENSES.txt`, made from `Cargo.lock` by `stage/tools/notices.py`; both stage builds fail if it is out of date. *Licences and notices → The screen program's libraries* |
| Inox2D (plays Inochi2D models), vendored with changes | BSD-2-Clause | Its copyright and licence text | Among the stage's libraries. The changes are listed in `stage/vendor/PATCHES.md` |
| Ayagami's core (plays `.model3.json` models), vendored | MIT or Apache-2.0 (MIT followed) | Its licence text | Among the stage's libraries |
| Python packages installed with pip: sherpa-onnx 2.x, sentencepiece, the Anthropic library and its helpers (httpx, pydantic, anyio and others) | Apache-2.0, MIT, BSD-3-Clause, PSF-2.0 | Their licence texts | *Licences and notices → Python packages*, read from the installed packages |
| Inside sherpa-onnx: ONNX Runtime 1.28, Eigen 5.0.1, OpenFst, Kaldi parts, nlohmann/json, utf8proc, KISS FFT, fastcluster, pybind11 | MIT, MPL-2.0 (Eigen), Apache-2.0, BSD | Their licence texts; for Eigen, where its source code is | *Licences and notices → Libraries inside the speech package* (`notices/libraries.txt`, with ONNX Runtime's own third-party notices) |
| ALSA's library, which sherpa-onnx's package brings its own copy of | LGPL-2.1 | Source, and the ability to swap the library | The install points that copy at the operating system's own library, so the LGPL library used is the system's, with its source in the image's source archive |
| Speech recognition: Moonshine tiny, English (Useful Sensors / Moonshine AI) | MIT (only Moonshine's older non-English models are non-commercial; we use the English one) | Its licence text | *Licences and notices → Speech, voice and other models* |
| The voice: Piper `en_GB-vctk-medium` | MIT (Piper), trained on the VCTK corpus and from a base voice trained on Hi-Fi TTS, both CC BY 4.0 | **Credit** to the datasets' authors, a link to the licence, and a note that the recordings were changed (made into a voice) | `notices/models.txt`, shown under *Licences and notices*, and in short under *About this device* on the settings page |
| Matching by meaning: Model2Vec potion-base-8M, distilled from BAAI's bge-base-en-v1.5 | MIT, both | Licence text | Models document |
| The wake word: sherpa-onnx's English keyword model (zipformer 3.3M) | Apache-2.0; trained on GigaSpeech, whose own terms are for non-commercial research | Licence text | Models document. **A lawyer question** (below). The wake word is off unless the owner turns it on |
| Recording a gesture with a phone: MediaPipe for the web (`@mediapipe/tasks-vision` 1.2.3) and its Face Landmarker and Pose Landmarker (lite) models, by Google | Apache-2.0 (the package says so; the models' cards say so) | The licence text, to whoever receives them: the device serves them, unchanged, to the owner's phone. Keep their notices (the files are served as published) | Models document (`notices/models.txt`, and the `LICENSE` written beside the files), and the recorder page links to *Licences and notices* |
| espeak-ng (text to phonemes for the voice) | GPL-3.0 | Source code (in the image's source archive) | It runs as a separate program; nothing of it is linked into our software (`core/companion/phonemes.py`) |
| The built-in full-body animations (`stage/motions/vrma`) | Ours: made with `stage/tools/vrma_kit.py` | | |
| The backgrounds, festive pictures, clock and weather pictures | Ours: drawn in code by the device (`backgrounds.py`, `overlays.py`) | | |
| Weather forecasts, when the owner chooses OpenWeather or WeatherAPI.com and adds their own key | The owner's own account, under that service's terms (OpenWeather's free plan: credit "Weather data provided by OpenWeather"; WeatherAPI.com's free plan: a link back appreciated) | Nothing shipped; the credit shown where the weather is set up | The *Your day* tab shows the chosen service's credit with a link; `notices/models.txt` names both (D-075) |
| The towns for the weather (`core/companion/data/places.tsv.gz`, from GeoNames' cities15000) | CC BY 4.0 | **Credit**, a link to the licence, and that it was changed (cut down to a few fields) | `notices/models.txt` and the *Your day* tab; `core/tools/make_places.py` makes it |
| Characters | None third-party shipped. Test pictures of commercial characters and sample models stay out of the repository and the product (D-024) | | A default character needs original, commissioned art with a written licence |

The AI services buyers connect (OpenAI, Anthropic, ElevenLabs, Tavily and others) are used with the
buyer's own key and account, under the terms the buyer accepts with each service; the device does not
resell them.

## In Character Studio (the laptop app)

| Part | Licence | What it asks of us | Where that is done |
|---|---|---|---|
| Our own code | The owner's | | |
| Python 3.12 (python-build-standalone): CPython with OpenSSL 3, expat, libffi, zlib, mpdecimal, bzip2, xz, SQLite, ncurses, libedit, libuuid, HACL*, BLAKE2 | PSF-2.0; Apache-2.0, MIT, BSD, zlib, public domain, CC0 | Licence texts | The app's `licenses/` folder (`studio/packaging/python-licences.txt`, with Python's own LICENSE.txt); the studio's *Licences and notices* |
| Its `_dbm` module, in the Linux build only | Berkeley DB 6, **GNU AGPL-3.0** | Would require AGPL compliance | **Left out** of the app (the studio never uses it); `build_app.py` checks it is gone |
| Tcl/Tk, IDLE, Python's tests | | | Left out |
| Microsoft's Visual C++ runtime files (Windows) | Microsoft's redistribution terms (the same files ship with python.org's Windows Python) | Ship them unchanged | Noted in the Python licences |
| Windows installer: Inno Setup | Inno Setup's licence: free, commercial use allowed | Keep its copyright notices (the setup program carries them) | The studio's notice credits it. Its makers **request** that companies with over US$5,000 a year in revenue buy a commercial licence; they say it is "not strictly required" (jrsoftware.org/isorder.php). The owner's choice: buy one when sales start (one single-user licence covers the build machine), or switch to NSIS (zlib licence, no request) |
| Linux AppImage runtime (type2-runtime, release 20251108), with musl, libfuse 3.15.0, squashfuse, Zstandard, zlib and mimalloc built in | MIT; **libfuse is LGPL-2.1**; BSD, zlib | Licence texts; libfuse's **source**, and what is needed to rebuild the runtime | The app's `licenses/appimage-runtime.txt`; the runtime is pinned by checksum; every release carries `CharacterStudio-appimage-runtime-source.tar.gz` (the runtime's source with its build scripts, libfuse and squashfuse) |
| What the first start downloads onto the buyer's computer, from the publishers: PyTorch, torchvision, NumPy, SciPy, Pillow, OpenCV (headless), diffusers, transformers, accelerate, safetensors, huggingface_hub, einops, omegaconf, tqdm, requests, aiohttp, image2live2d | BSD-3, MIT, Apache-2.0, MPL-2.0 (tqdm, in part) | Nothing for us to ship: buyers download them, under their licences. On computers with an NVIDIA card, PyTorch's build includes NVIDIA's CUDA libraries under NVIDIA's own licence, which allows this use | Listed with their licence files on the studio's *Licences and notices* |
| The part finder, See-through (code at a fixed commit) and its model `24yearsold/l2d_sam_iter2` | Apache-2.0, both; the model's page says commercial use is allowed | | Downloaded by the buyer; shown on the studio's *Licences and notices* |
| The characters it makes | From the buyer's own pictures | | The studio's notice reminds buyers to use only pictures they have the right to use |

## Trademarks

- **Live2D and Cubism** (Live2D Inc.). The product names **Inochi2D** in what it promotes, and describes
  the other format plainly: "another 2D rigged model (Live2D-style, with a `.model3.json` file)" on the
  settings page; "other 2D rigged models" in the guides. No Live2D name in the product's name, no logo,
  no badge. The notices say the names belong to Live2D Inc., that there is no affiliation, and that no
  Live2D software is included. In Australia, using another's mark in good faith to describe what goods
  do, or what they are for, is a defence to infringement (Trade Marks Act 1995 s 122(1)(b)–(c)); a
  lawyer should confirm the exact wording for the box and the campaign page (`19`).
- **Inochi2D.** The name is used to say what the device plays. The Inochi2D project's logo and branding
  are its copyright: ask the project (it takes requests as GitHub issues) before using them.
- **Raspberry Pi.** Say the device *uses* a Raspberry Pi; the notice says its system is *based on*
  Raspberry Pi OS, because it is changed. Do not put "Raspberry Pi" in the product's name or use its logo
  without Raspberry Pi Ltd's approval (its "Powered by Raspberry Pi" programme).
- **Others named on the settings page or in the guides** (Anthropic and Claude, OpenAI and ChatGPT,
  ElevenLabs, Tavily, VTube Studio, MeowFace, iFacialMocap, VRoid Studio, VRM, Python, Windows, macOS,
  Linux, NVIDIA, PyTorch): only to say what the product works with. The notices list the owners. The
  device speaks the face-tracking apps' published protocols (their developers describe them for other
  programs to use); none of their code is in it.
- **SyBran CODE27.** The README calls the product a lower-cost alternative to it. On a campaign page,
  comparisons must be accurate and must not suggest any link with SyBran (Australian Consumer Law
  s 18); no SyBran names or pictures in branding.

## Source code we publish, and why our own code is not among it

- **The card image's operating system.** The workflow lists every package in the image
  (`check-image.sh --packages`) and downloads its source package at exactly that version, from the
  image's own archives (Debian and Raspberry Pi), or from Debian's snapshot archive if it is no longer
  current (`device/fetch-sources.py`). The parts, each under 2 GB, go on the image's release,
  `device-image-<version>`. Any package that could not be found is listed in the run's summary and in
  `MISSING.txt`: fix those before selling. The device's notice links to that release: **publish it
  before selling devices with that image, and keep it for at least three years after the last one is
  sold.**
- **The Linux app's runtime**, on every Character Studio release.
- **Our own code is not required to be published:** no GPL or LGPL code is linked into it.
  - espeak-ng runs as a separate program, and sherpa-onnx 2.x has none of it inside (version 1.x had;
    the install pins 2.x).
  - The LGPL libraries the device uses (ALSA, the C library) are the operating system's own shared
    libraries, which owners can replace.
  - The Python, Rust and model parts are under permissive licences.

## For the owner

1. Fill in your business name and a contact address in `core/companion/notices/product.json` before
   building sale images: the device's notice and the written offer use them.
2. Publish each sale image's release (with its source parts) and keep it for three years after the last
   sale.
3. Inno Setup: buy a commercial licence once sales start, or ask for the switch to NSIS.
4. Commission the default character's art with a written licence (or assignment) for commercial use.
5. Not licensing, but before selling: sign the Mac app (Apple Developer Program, which the owner has)
   and the Windows installer (a code-signing certificate).

## For a lawyer (Australia, and where backers live)

1. The Live2D-format questions in `19-live2d-format-licensing.md`.
2. The wake-word model: Apache-2.0, but trained on GigaSpeech, whose terms limit the *dataset* to
   non-commercial research. Is shipping the model (off by default) a problem? If so, sale images can
   leave it out; the device works with the button alone.
3. Models generally: we rely on each model's own licence (MIT, Apache-2.0, CC BY 4.0 credits), not on
   the terms of every recording or text it was trained on. Moonshine, for example, was trained on public
   datasets and on audio collected from the web. Is relying on the model licences enough?
4. Trademark wording (above), and comparisons with CODE27.
5. The warranty wording in the notices against the Australian Consumer Law, and any warranty-against-
   defects text the box or terms of sale will carry.

## Keeping it right

- **Adding a Rust library:** run `python3 stage/tools/notices.py` (the builds check it).
- **Adding a Python package to the device:** check its licence first: no GPL or AGPL, nothing
  non-commercial. Its licence files show up on the settings page by themselves.
- **Updating sherpa-onnx:** keep it at 2.x or later. Check the libraries its build includes (the
  `cmake/` folder at its release tag) against `core/companion/notices/libraries.txt`, and the ONNX
  Runtime version against `onnxruntime-notices.txt`.
- **Adding a model:** check the licence of the model *and* of its training data, and add its credits
  to `core/companion/notices/models.txt` and `THIRD_PARTY.md`.
- **Changing the studio's Python** (a new version): redo `studio/packaging/python-licences.txt` from
  that version's `Doc/license.rst`, and check its modules for copyleft libraries as was done for `_dbm`.
- **Changing the AppImage runtime:** update `RUNTIME_*` in `.github/workflows/studio-apps.yml` and
  `studio/packaging/appimage-runtime-licences.txt` together.

## Sources

- Blizzard Challenge 2013 data licence (the lessac voice's recordings: research only, excluding the
  commercialisation of speech synthesis products), via Piper's lessac model card.
- VCTK 0.92: https://doi.org/10.7488/ds/2645 (CC BY 4.0); Hi-Fi TTS: https://www.openslr.org/109/
  (CC BY 4.0); Piper voices: https://huggingface.co/rhasspy/piper-voices (MIT).
- Moonshine licence: https://github.com/moonshine-ai/moonshine/blob/main/LICENSE.
- sherpa-onnx 2.0.2 build files (`cmake/*.cmake`), and the deprecation notes in its library ("espeak-ng is
  no longer used").
- Inno Setup licence and commercial-licence questions: https://jrsoftware.org/files/is/license.txt,
  https://jrsoftware.org/isorder.php.
- AppImage type2-runtime: https://github.com/AppImage/type2-runtime (LICENSE lists the libraries inside).
- CPython's incorporated software: `Doc/license.rst` (3.12 branch).
- GPL-2.0 §3, GPL-3.0 §6, LGPL-2.1 §6, MPL-2.0 §3.2, CC BY 4.0 §3(a).
