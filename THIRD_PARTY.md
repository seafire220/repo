# Third-party components and their licences

What the device and Character Studio include or download, where it comes
from, and under which terms. Keep this list current when a component is
added. The full audit, with what each licence asks of us and where that is
done, is `docs/34-licences.md`; the device's settings page and Character
Studio's page each have a *Licences and notices* link with every licence
text.

## On the device

| Component | What it does | Licence | Where |
|---|---|---|---|
| Inox2D (Inochi2D runtime), vendored with changes | Plays Inochi2D models | BSD-2-Clause | `stage/vendor/inox2d*`, changes in `stage/vendor/PATCHES.md` |
| Ayagami core, vendored | Plays other 2D rigged models, in the `.model3.json` format (an independent implementation; no Live2D software) | MIT or Apache-2.0 | `stage/vendor/ayagami`, commit in `stage/vendor/PATCHES.md` |
| sherpa-onnx 2.x (pinned below 3: no espeak-ng inside) | Runs the on-device speech recognition, voice and wake word | Apache-2.0 | Python package |
| Inside sherpa-onnx: ONNX Runtime 1.28 (Microsoft), Eigen 5.0.1, OpenFst, Kaldi decoder and feature parts, nlohmann/json, utf8proc, KISS FFT, fastcluster, pybind11 | Running the models | MIT; Eigen MPL-2.0 (source: gitlab.com/libeigen/eigen); Apache-2.0; BSD | Built into the package; notices in `core/companion/notices/libraries.txt` and `onnxruntime-notices.txt` |
| Moonshine tiny, English, 8-bit (Useful Sensors), packaged for sherpa-onnx | On-device speech recognition | MIT | Downloaded at install (`device/install.sh`) |
| Model2Vec potion-base-8M (Minish Lab), distilled from bge-base-en-v1.5 (BAAI), with its tokenizer | Matching memories and conversations by meaning (`core/companion/meaning.py`, read with numpy; the model2vec library is not used) | MIT (both) | Downloaded at install |
| sherpa-onnx English keyword-spotting model, zipformer 3.3M (k2-fsa project, trained with icefall on GigaSpeech XL) | The wake word (`core/companion/wakeword.py`) | Apache-2.0 | Downloaded at install from the sherpa-onnx releases |
| SentencePiece (Google) | Spelling the wake words as the wake-word model's word pieces | Apache-2.0 | Python package, installed at install |
| MediaPipe for the web (`@mediapipe/tasks-vision` 1.2.3, Google), with its Face Landmarker and Pose Landmarker (lite) models | Recording a gesture with a phone: the device gives them, unchanged, to the phone's browser, which finds the face and body in the phone's own video (`core/companion/phone_record.py`, `mocap_assets.py`) | Apache-2.0 (the library and, per their model cards, the models) | Downloaded at install from npm and Google's MediaPipe model storage at fixed versions, each file checked by SHA-256; a device updated over the internet fetches them when a phone first asks |
| Piper voice `en_GB-vctk-medium` (the Piper project), prepared for sherpa-onnx by `core/companion/piper_voice.py` | The free on-device voice (109 speakers, 72 offered) | MIT; trained on the CSTR VCTK Corpus 0.92 (J. Yamagishi, C. Veaux, K. MacDonald, University of Edinburgh, 2019, https://doi.org/10.7488/ds/2645) from a base voice trained on the Hi-Fi Multi-Speaker English TTS Dataset (E. Bakhturina, V. Lavrukhin, B. Ginsburg, Y. Zhang, NVIDIA, 2021), both CC BY 4.0: credit required, and given on the settings page | Downloaded at install from rhasspy/piper-voices at a fixed commit, checked by SHA-256 |
| espeak-ng | Turns text into phonemes for the voice | GPL-3.0; installed from the operating system's packages and run as a separate program (nothing of it is linked into our software) | Operating system |
| Raspberry Pi OS and its packages (Linux, Mesa, cage, NetworkManager, ALSA...) | The operating system | Their own licences (mostly GPL, LGPL, MIT); the source code of every package in each card image is published with the image (`device/fetch-sources.py`) | Operating system |
| aiohttp, numpy, Pillow, qrcode | The core service | Apache-2.0, BSD, HPND/MIT-CMU, BSD | Python packages |
| Rust libraries built into the stage program (glam, glow, glutin, winit, serde_json, image with its PNG and JPEG readers, among them zune-jpeg) | Windows, OpenGL, maths, reading pictures (including a VRM avatar's textures) | MIT or Apache-2.0 (zune-jpeg also Zlib) | `stage/Cargo.lock` |
| Anthropic Python library (only if Claude is chosen) | Talking to Claude | Apache-2.0 (earlier versions MIT) | Python package |

## In Character Studio (laptop)

| Component | What it does | Licence |
|---|---|---|
| Python 3.12 (python-build-standalone), with OpenSSL, expat, libffi, zlib, mpdecimal, bzip2, xz, SQLite, ncurses, libedit, libuuid, HACL* | Runs the app; inside the app | PSF-2.0, with Apache-2.0, MIT, BSD, zlib and public-domain parts (`studio/packaging/python-licences.txt`). Left out: Tcl/Tk, and the Linux build's `_dbm` (Berkeley DB, AGPL) |
| Microsoft Visual C++ runtime files (Windows) | Come with the Windows Python | Microsoft's redistribution terms |
| Inno Setup (Windows) | Builds the installer | Inno Setup licence: free, commercial use allowed; its makers ask commercial users to buy a licence (not required) |
| AppImage runtime (type2-runtime 20251108), with musl, libfuse, squashfuse, Zstandard, zlib, mimalloc | Starts the Linux app | MIT; libfuse LGPL-2.1, its source published with each release; BSD, zlib (`studio/packaging/appimage-runtime-licences.txt`) |
| See-through code (pinned commit) and its body-part model (`24yearsold/l2d_sam_iter2`) | Finds a character's parts | Apache-2.0, both; the model's page allows commercial use |
| image2live2d (pinned commit) | Rigs the parts; writes the `.model3.json` copy | Apache-2.0 |
| PyTorch, torchvision, NumPy, SciPy, Pillow, OpenCV (headless), diffusers, transformers, accelerate, safetensors, huggingface_hub, einops, omegaconf, tqdm, requests | Image work | BSD-3, MIT, Apache-2.0, MPL-2.0 (tqdm in part). With an NVIDIA card, PyTorch brings NVIDIA's CUDA libraries under NVIDIA's licence |
| aiohttp | Character Studio's local page (`studio/app.py`) | Apache-2.0 |
| MediaPipe (Google), with its Face Landmarker and Pose Landmarker (lite) models | Turns a video of a person into gestures (`studio/record_motion.py`, optional) | Apache-2.0 (library); the models are downloaded from Google's MediaPipe model storage on first use, under the terms on their model cards |

Character Studio downloads its AI tools on first start, onto the buyer's
computer, from their publishers: the Python packages from the Python
Package Index (PyTorch from its own server), the See-through code (at the
tested commit) and its body-part model weights from their authors' GitHub
and Hugging Face pages. None of them is bundled with the app.

## Not included

Live2D's Cubism SDK, Cubism Core, Cubism Editor output and sample models are
not in the product or the repository (`docs/19-live2d-format-licensing.md`).
The first free voice (Piper "LibriTTS-R medium") was replaced: it was
fine-tuned from Piper's lessac voice, whose recordings are licensed for
research only (`docs/34-licences.md`).
Test pictures of commercial characters are kept out as well (D-024), and so
are the VRM sample avatars used for testing (`docs/30-vrm.md`); the VRM
support is our own code, written from the VRM specifications (no VRM
library is included). The built-in full-body animations
(`stage/motions/vrma`) are original to this project, made with
`stage/tools/vrma_kit.py`; they follow the VRM Animation (`VRMC_vrm_animation`)
specification, and contain no third-party motion data.
