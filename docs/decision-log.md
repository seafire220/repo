# Decision log

One entry per significant decision: what was chosen, why, and what was
rejected. Settled entries are not reopened without new evidence.

## D-001 — Reference product

- Date: 2026-11-16
- Decision: the benchmark is the SyBran CODE27 Character Livehouse / Companion
  Hub (model SyBran-01).
- Why: it is the only SyBran product matching the brief.
- Open: confirm with the project owner that this is what "CODE Sybran" means.

## D-002 — Clean-room boundary

- Date: 2026-11-16
- Decision: reproduce function and user experience from publicly observable
  information only. No vendor firmware, source, character or voice assets,
  trademarks or trade dress. All software in this repository is original or
  from correctly licensed open-source projects.
- Rejected: firmware extraction, asset reuse, visual copies of the vendor's
  characters or interface.

## D-003 — No hardware commitment before requirements sign-off

- Date: 2026-11-16
- Decision: no SBC, display or audio part is selected until the requirements
  interview is answered and written up as a specification.
- Why: budget, depth-effect choice, model complexity and local-versus-cloud
  processing each change the board class. Choosing first would bake in guesses.

## D-004 — Scope changed to 2D, two tiers, for sale

- Date: 2026-11-16. Status: decided by the owner.
- Decision: no 3D or VRM. Lite uses pre-rendered 2D (video loops or frame
  sets); Standard uses a rigged 2D puppet. The design is intended to be sold.
- Consequence: every dependency must allow commercial distribution, and
  prototype and production sourcing are tracked separately.

## D-005 — No Live2D Cubism

- Date: 2026-11-16. Status: decided by the owner; confirmed by research.
- Why: a device that loads arbitrary user models is an "Expandable
  Application", which needs a paid agreement and revenue share regardless of
  business size.

## D-006 — One Linux platform for both tiers first; microcontroller Lite later

- Date: 2026-11-16. Status: proposed, awaiting the owner.
- Decision: prototype both tiers on one Linux board with one codebase. Keep
  the character package and web API free of Linux assumptions. Port Lite to
  an ESP32-P4 display board afterwards as a cost-reduction step.
- Why: one codebase that can be tested without hardware reaches a working
  product soonest; the puppet tier needs a graphics processor in any case.
- Rejected: starting Lite on the microcontroller now. It roughly halves Lite
  hardware cost and boots in seconds, but means a second codebase in C++ that
  cannot be tested here, and no on-device speech recognition.

## D-007 — Prototype hardware: Raspberry Pi 4 (1 GB), Touch Display 2 (5"), ReSpeaker Lite

- Date: 2026-11-16. Status: proposed, awaiting the owner.
- Why: cheapest board with a display connector, hardware video decode,
  reliable hotspot mode, first-party screen and compliance documents; the
  audio board removes echo in hardware. About A$239.
- Rejected: 2 GB boards (A$44+ for memory the software is designed not to
  need); Raspberry Pi 5 1 GB (kept as the fallback if recognition is slow);
  Radxa Zero 3W (cheaper overseas, more software risk; a production
  cost-reduction candidate); Pi Zero 2 W (512 MB, no display connector,
  scarce); second-hand tablets (not repairable, unstable supply).

## D-008 — The software must fit in 1 GB of RAM

- Date: 2026-11-16. Status: proposed, awaiting the owner.
- Why: in 2026 memory is the largest price step between otherwise similar
  boards.
- Consequence: lightweight embedded browser by default, small Python
  dependency set, no heavyweight frameworks.

## D-009 — The character is drawn by a web page in a kiosk browser

- Date: 2026-11-16. Status: proposed, awaiting the owner.
- Decision: plain JavaScript with canvas and WebGL, shown by WPE WebKit
  (`cog`) inside `cage`. Chromium is the fallback.
- Why: the same drawing code serves the device, the console's live preview
  and automated tests.
- Rejected: native Rust/C++ renderer; Godot; a bare video player.

## D-010 — Standard tier uses our own puppet format and runtime

- Date: 2026-11-16. Status: proposed, awaiting the owner.
- Why: no automatic rigger targets Inochi2D, so the rig generator must be
  written here regardless and it determines the runtime features needed.
  Inochi2D's reference runtime needs desktop OpenGL; its WebGL-capable port
  (Inox2D) describes itself as a prototype not for production use.
- Kept open: an importer for hand-made Inochi2D models later; its format is
  open and BSD-licensed.
- Rejected: Inochi2D runtime, Inox2D, nijilive, Live2D, Spine, Rive, Talking
  Head Anime 4.

## D-011 — Own lightweight conversation pipeline

- Date: 2026-11-16. Status: proposed, awaiting the owner.
- Why: full control of latency, interruption and memory use on a 1 GB
  device; adapters per protocol keep provider choice open.
- Rejected: Pipecat (BSD-licensed and capable, but its core dependency set is
  heavy for this device). To revisit if maintaining adapters becomes a
  burden.

## D-012 — Speech recognition runs on the device by default

- Date: 2026-11-16. Status: proposed, following the owner's stated preference.
- Decision: sherpa-onnx streaming recognition on the device; cloud
  recognition selectable.
- Open: speed on the Pi 4 is not yet measured.

## D-013 — Character creation runs from the device's web UI on the user's keys

- Date: 2026-11-16. Status: proposed, awaiting the owner.
- Why: the seller hosts nothing, pays for no AI usage and holds no user
  data. Processing that the device cannot do happens at the AI provider or in
  the user's browser.
- Rejected: a seller-run cloud service (ongoing cost, liability, lock-in).

## D-014 — Audio over USB

- Date: 2026-11-16. Status: proposed, awaiting the owner.
- Why: no driver or board-specific configuration; replaceable by unplugging;
  works on any future board.
- Rejected: header-pin audio boards (slightly cheaper, tied to one board
  family).

## D-015 — API keys protected by permissions, masking and exclusion, not encryption

- Date: 2026-11-16. Status: proposed, awaiting the owner.
- Why: without a secure element the decryption key would be stored beside
  the data. Keys are write-only in the console, masked in logs, and left out
  of every package, export and backup.

## Review round, 2026-11-16 — outcome of the owner's feedback

| Decision | Outcome |
|---|---|
| D-006 Platform strategy | **Agreed.** Standard first on Linux. Microcontroller Lite afterwards, with the owner providing access to the board through a laptop. |
| D-007 Prototype hardware | **Reopened**, see D-016. |
| D-008 Fit in 1 GB | Stands. |
| D-009 Character drawn by a web page | **Replaced** by D-017. |
| D-010 Own puppet format | **Reversed** by D-018. |
| D-011 Own conversation pipeline | Stands. |
| D-012 On-device speech recognition | Stands. |
| D-013 Character creation from the device's web UI | **Narrowed** by D-019: true for Lite only. |
| D-014 USB audio | Stands. |
| D-015 Key protection | Stands. |

## D-016 — Prototype board: two candidates, owner to choose

- Date: 2026-11-16. Status: awaiting the owner.
- Evidence: the owner proposed the Orange Pi Zero 3 and Radxa Zero 3W as
  cheaper. Current listings: Orange Pi Zero 3 1/2/4 GB about A$90/102/113;
  Radxa Zero 3W 2 GB with storage about A$68–96; Raspberry Pi 4 1 GB A$64.88
  and 2 GB A$109.33.
- Finding: the Radxa is cheaper than a 2 GB Raspberry Pi, as the owner said.
  The Orange Pi Zero 3 is no longer cheap. The cheapest board of all is the
  Raspberry Pi 4 1 GB, and 1 GB is enough under D-017.
- Options: (1) Raspberry Pi 4 1 GB with ribbon screen, about A$239 complete;
  (2) Radxa Zero 3W 2 GB with HDMI screen, about A$294 complete.
- Recommendation: option 1 for the first prototype on cost and risk. Option 2
  if the owner prefers to prototype on a non-Pi board or wants the memory
  headroom. The software is written to run on either.
- Rejected: Orange Pi Zero 3 (dearer than the Radxa for less performance,
  weaker Wi-Fi driver); unbranded boards for a prototype (unstable supply).

## D-017 — The character is drawn by a small native program

- Date: 2026-11-16. Status: decided, on test evidence.
- Decision: the stage is a Rust program drawing through OpenGL ES under the
  `cage` display server. The same code is also compiled to WebAssembly for
  the console preview and for running in a browser on a laptop.
- Why: the working Inochi2D renderer is Rust and runs both ways (tested). A
  native program uses a fraction of a browser's memory, starts faster, and
  does not depend on a browser enabling the graphics chip on a lesser-known
  board. This is what makes 1 GB boards sufficient.
- Rejected: kiosk web browser as the stage (D-009). Kept as a fallback only.

## D-018 — Standard tier uses the Inochi2D puppet format with the Inox2D runtime

- Date: 2026-11-16. Status: decided, on test evidence (`07-spike-inochi2d.md`).
- Decision: puppets are Inochi2D `.inp`/`.inx` files. The runtime is Inox2D,
  pinned to a tested commit, carrying our own patches.
- Why: it rendered a complete rigged character correctly natively and in a
  browser. It is BSD-licensed, has a free editor for hand rigging, and has
  existing models. The owner asked for the Inochi2D ecosystem plus our own
  patching in preference to a home-made format.
- Correction recorded: D-010 rejected Inochi2D from its README without
  running it. That was a mistake in method.
- Known gaps to patch: stray edge fragments in the browser build; mesh
  groups unsupported.
- Rejected: official Inochi2D library today (current source does not build;
  0.9 unreleased; revisit on release); nijilive; Ayatsuri2D (beta, and its
  Live2D-file support raises a legal question for a sold product); our own
  format.

## D-019 — Standard characters are made by a laptop tool chain

- Date: 2026-11-16. Status: decided by the owner; implementation proposed.
- Decision: one image is split into named layers by See-through on the
  user's laptop; our rigger meshes the layers, applies a template rig and
  exports an Inochi2D file for upload. Requirements are stated to the user.
  Hand-rigged files are accepted as the alternative.
- Why: layer splitting needs an 8–16 GB graphics card, far beyond the device.
- New work: the template rigger and Inochi2D exporter. No existing tool
  produces Inochi2D files from a layered image.
- Reused: See-through (Apache-2.0) for splitting; Stretchy Studio (MIT) for
  mesh generation and pose detection.
- Lite characters are still generated from the device's web UI (D-013).

## D-020 — No ready-made speech-to-speech services

- Date: 2026-11-16. Status: decided by the owner.
- Decision: the conversation always runs as streamed recognition, LLM and
  cloned-voice synthesis.
- Why: speech-to-speech services speak only in their own voices, which
  defeats the purpose of a character voice.

## D-021 — Compliance deferred; Kickstarter first

- Date: 2026-11-16. Status: decided by the owner.
- Decision: regulatory work waits until after prototyping. Meanwhile, parts
  that already carry certification are preferred, and the mains adapter is
  an external, already-marked unit.

## D-016 resolved — Prototype board is the Raspberry Pi 4, 1 GB

- Date: 2026-11-16. Status: decided by the owner.
- Fallback if 1 GB proves too tight: Radxa Zero 3W or an Orange Pi.
- A second search found nothing reliably cheaper with at least 1 GB and
  OpenGL ES 3. Leads to revisit after measuring memory use: Orange Pi Zero 2W
  1 GB, Raspberry Pi Zero 2 W.

## D-022 — Stage implemented; OpenGL ES first; renderer kept in-tree

- Date: 2026-11-16. Status: implemented and tested on software graphics.
- What: `stage/`, a native program that draws an Inochi2D puppet and drives
  mouth, blink, head, body and breathing from commands.
- Finding that forced a change: the Inox2D renderer's native build assumed
  desktop OpenGL 3.3, which the Raspberry Pi 4 and Mali boards do not have.
  Its OpenGL part is therefore kept in `stage/vendor/inox2d-opengl` with
  three small changes so it runs on OpenGL ES 3.0. The same changes remove
  the stray fragments seen in the browser test.
- The stage asks for OpenGL ES first and falls back to desktop OpenGL.
- Not yet done: a run on real hardware; the browser build; clip and sprite
  kinds; on-screen status and setup screens.

## D-019 corrected — The rigger already exists

- Date: 2026-11-16.
- D-019 called the rigger and Inochi2D exporter new work. The owner's earlier
  project used an existing open-source one, image2live2d (Apache-2.0). It is
  to be reused. The unsolved step is splitting a flat image into layers.

## D-023 — Earlier work is evidence to check, not a base to assume

- Date: 2026-11-16. Status: decided.
- The handoff brief from the owner's earlier attempt is reviewed in
  `08-prior-work-review.md`. Its claims are unverified because the source
  was not supplied. The source has been requested so working parts can be
  reused. Until then, new code is limited to parts needed either way.

## D-024 — Other people's characters stay out of the repository

- Date: 2026-11-16. Status: decided.
- Test artwork depicting existing commercial characters is used locally only
  and never committed, shown in committed screenshots or shipped. Committed
  tests and evidence use openly licensed or original art.

## D-025 — Layers are cut from the original, not regenerated, as the first route

- Date: 2026-11-16. Status: adopted for now, on one result.
- Decision: the default route from a flat image to layers is
  `studio/flatsplit.py`, which separates parts by colour and position from a
  few marked points and never repaints the artwork.
- Why: on the owner's test image it kept every ornament and garment, where
  the earlier attempt with a generative splitter lost them; it needs no
  graphics card, so it runs on the owner's Mac and on customers' laptops.
- Limits: one image is not evidence of generality. See
  `09-flat-image-experiment.md`. See-through stays as the route to compare
  against once a graphics card is available.

## D-026 — The existing rigger is used as it is

- Date: 2026-11-16. Status: decided.
- image2live2d (Apache-2.0, commit 714e7cc) is run unmodified. It was
  installed and run with the owner's approval. Its files needed three fixes
  on our side, in the renderer, not in the rigger.

## D-022 updated — Renderer kept wholly in-tree

- Date: 2026-11-16.
- Both Inox2D crates now live in `stage/vendor/` and nothing is fetched at
  build time. Added since D-022: opacity bindings, applying controls that sit
  at zero, and a tolerant metadata reader. All eight changes are listed in
  `stage/vendor/PATCHES.md`.

## D-023 updated — Earlier source received

- Date: 2026-11-16.
- The archive arrived and its checksum matches the brief. Review so far is
  limited to how it calls the rigger; see `08-prior-work-review.md`.

## D-025 revised — Cutting from the original stays; choosing parts by colour does not

- Date: 2026-11-16. Status: decided, on trial evidence (`10-generalisation-trial.md`).
- Five unfamiliar images, method unchanged: no acceptable result. In all
  five the hair was found wrongly, because shaded or textured hair is not
  one colour and unshaded hair can match the clothing.
- Kept: layers are cut from the original pixels, never regenerated. No image
  lost or altered any detail.
- Replaced (next work): deciding which pixels belong to which part. A model
  that labels parts will propose them; the user's marks become corrections.
- Lesson recorded: the stage's automatic checks passed on all five failures.
  They are not evidence of quality.

## D-027 — Head turn is a warp in place, not a rigid rotation

- Date: 2026-11-16. Status: implemented; awaiting the owner's review.
- The rigger's rigid head rotation is replaced by `studio/headturn.py`, which
  bends the head parts as if their features slide over a ball and keeps the
  chin on the neck, as hand-made Live2D and Inochi2D rigs do.
- Rejected: reducing the amount of head movement to hide the problem.

## D-028 — Unsuitable pictures are refused or flagged, not processed silently

- Date: 2026-11-16. Status: implemented.
- `studio/prepare.py` removes plain backgrounds only and refuses others.
  `studio/flatsplit.py` refuses pictures without transparency and lists
  warnings when its result should not be trusted.

## D-029 — Large gestures will use alternative drawings, small ones bending

- Date: 2026-11-16. Status: direction agreed in principle; not designed.
- A puppet bends the original picture and cannot show what was never drawn.
  Gestures such as crossed arms will be alternative poses switched in, as
  hand-made rigs do, with each gesture declaring the parts it needs so that
  a character lacking them skips it.

## D-030 — Parts are found by the See-through body-part model

- Date: 2026-11-16. Status: decided, on test evidence (`11-part-labelling-model.md`).
- Decision: `studio/parts_model.py` runs the See-through body-part model
  (Apache-2.0 code and weights, commercial use permitted) to label 19 parts;
  `studio/partsplit.py` cuts the original picture along those labels.
- Why: on the six test images it separated hair, face, neck, arms and
  clothing correctly in every case, where colour-picking got five of six
  wrong. It labels only, so nothing is repainted. It ran on a plain CPU in
  about 20 seconds.
- The owner asked for an existing project in place of the home-made script;
  this is that.
- Rejected: extending colour-picking; the full generative See-through
  pipeline for now; YOLO-based detectors (licence); general SAM with clicks
  (the fine-tune is the anime-specific form of it).
- Open: untested on Apple Silicon; closed-eye and mouth artwork is still
  poor on some faces.

## D-031 — Character preparation runs on the laptop only

- Date: 2026-11-17. Status: decided by the owner.
- Decision: the device never runs the part-finding model. The owner's laptop
  runs the whole preparation chain and the finished character package is
  copied to the device over Wi-Fi (or by USB stick or microSD).
- Why: measured here, the model needs 5.2 GB of memory at its peak and 32
  seconds per picture on four fast cores; its weights alone (1.3 GB) exceed
  the Pi 4's entire 1 GB of memory. The owner judged a shrunk on-device
  version not worth pursuing.
- Rejected: a quantised model on the device; running the model in the
  browser that opens the console.

## D-027 superseded — Head movement moves the whole head

- Date: 2026-11-17. Status: implemented in `studio/refine.py`; replaces
  `studio/headturn.py`.
- The owner rejected the in-place warp of D-027: the eyes and mouth stretched
  while the head itself stayed put ("it wasn't really head movement").
- Now: a tilt leans the whole head, hair included, as one rigid piece about a
  point in the neck, and the neck bends to follow. Turning and nodding slide
  the middle of the face over a rounded surface while the head's outline
  stays; eyes, eyebrows, nose and mouth move as rigid pieces and never change
  shape.
- The neck is continued up under the face in its own shadow colour, so a
  moving head uncovers neck rather than a hole, a smear or the collar.

## D-032 — Closed eyes and the open mouth are drawn from the character's own art

- Date: 2026-11-17. Status: implemented in `studio/partsplit.py` and
  `studio/refine.py`.
- The rigger's own closed eye is an upward arc sized from the eye's box,
  which reads as a happy squint and grew into a thick bar when the eye's box
  was wrong. Its open mouth stretches the drawn mouth line, which on many
  characters is a few pixels high, so the mouth barely moved.
- Now `partsplit.py` draws, hidden at rest: a closed eye as a smooth lid line
  in the eye's own lash colour and weight, curving up at the corners; and an
  open mouth sized to the face (dark interior, teeth, tongue) in the lip
  line's colour. As the mouth opens, the drawn line fades and the open mouth
  grows down from it. The rigger only draws its own versions when these are
  missing, so it now skips them.
- Pixels just outside the model's eye and mouth labels that are clearly not
  plain skin go with the feature, and the face underneath is filled from the
  surrounding skin, so a closed eye leaves no ghost of the open one.
- A mouth the model missed is looked for by colour where a mouth belongs; if
  there is none, a small line is added so the character can talk. Both cases
  are reported.

## D-033 — On-device speech recognition uses Moonshine tiny (8-bit)

- Date: 2026-11-17. Status: tested off the device.
- The sherpa-onnx packaging of Moonshine tiny English (MIT licence, Useful
  Sensors), about 120 MB, recognised both test recordings word for word at
  about 27 times real time on two x86 threads. It takes about 220 MB of
  memory once loaded, so the core loads it once and keeps it.
- The installer fetches it from Hugging Face, where the files were checked.
- Open: speed on the Pi 4; whether a smaller model is needed for memory.

## D-034 — The stage repeats "ready" to late connections

- Date: 2026-11-17. Status: implemented and tested.
- The first test of the real core with the real stage found that the core
  never learned the puppet's parameters: the stage announced them once at
  start, before the core connected. The stage now sends the latest "ready"
  to each client as it connects.

## D-035 — Body language through head, body and blush; arms only gently

- Date: 2026-11-17. Status: implemented (core/companion/stage_link.py).
- Rendered on two characters: the rig's body lean, bow and blush look
  right; its arm swings uncover the blurred fill behind the arm and leave a
  smear, like the first head movement did. So emotions now carry posture
  (sad looks down and slumps, embarrassed blushes and looks away, thinking
  tilts the head), and the language model can ask for short gestures with
  tags: [nod], [shake], [bow], [tilt]. Arm gestures wait for a better fill
  behind the arms, or for alternative drawings (D-029).
- Controls a character lacks are skipped, so a head-only character still
  nods and blushes: this is the partial mapping the owner asked for.

## D-036 — No engine rewrite; Live2D-format playback later, behind a licence check

- Date: 2026-11-17. Status: recommended (`17-engine-evaluation.md`); the
  owner decides.
- Ayagami (MIT/Apache-2.0) built, benchmarked and rendered Live2D's sample
  models correctly; its maths costs about the same as Inox2D's. Godot would
  add a full on-screen application framework at a cost in memory, start-up
  time and custom builds. Neither makes characters made from a picture look
  better: that depends on the rig, not the renderer.
- Recommendation: keep the current stage and core; for the higher-end model,
  add Live2D-format playback with Ayagami inside the stage once the format
  and Cubism Editor licence questions are answered; Umamo and Ayatsuri2D are
  optional desktop editors; Godot only for a later bigger-screen model.
- Snapshot before this evaluation: branch `snapshot-2026-11-17` and a zip
  given to the owner.

## D-037 — Units are made from one ready-made image

- Date: 2026-11-17. Status: implemented (`device/build-image.sh`,
  `device/check-image.sh`); not yet booted on a Pi.
- One image per software version, built from the official Raspberry Pi OS
  Lite image, flashed to every unit. The image switches off what a
  keyboard-less product must not have: Raspberry Pi OS's first-boot user
  prompt, cloud-init (which can create a login with a default password) and
  SSH. Nothing secret is shared between units.
- Out-of-the-box gaps closed with it: the setup hotspot was not wired into
  the service's start-up (bug); sound card detection; QR codes on the setup
  screens; the phone's time zone; factory reset and a 12-second button
  reset; an explanation screen when a character cannot be shown; a setup
  screen present from the very first start, and services that never stop
  restarting; return to the home Wi-Fi after a power cut.

## D-038 — Buyers enter their own keys on the settings page

- Date: 2026-11-17. Status: implemented.
- Units ship with no keys. Each AI service section of the settings page
  shows the key field for the chosen service, where to get a key, and a
  "Save and test" button. Keys are write-only, stored only on the device and
  never shown again. Typing keys on the device's own small touch screen was
  rejected: the screen shows a QR code that opens the settings page on the
  buyer's phone instead.

## D-039 — Live2D-format characters are converted from our puppet

- Date: 2026-11-17. Status: implemented (`studio/live2d_export.py`,
  `make_character.py --live2d`); played in Ayagami on a development PC, not
  yet on the device.
- The owner asked for a pipeline that makes Live2D-format models from one
  picture. Two routes were tried: the rigger's own Live2D output, and
  converting our finished puppet. The rigger's model has the older head
  movement the owner rejected; the converted one keeps ours, so it was
  chosen. One rig, two formats.
- Each part's own movements become exact keyforms; shared smooth movements
  (breathing, the body turn carrying the head) and the body's rotation go on
  warp deformers, decided per part so a part's own movement never changes
  how a shared one applies. Every export is replayed against Inox2D's maths;
  eleven characters were within 1.5-1.9 px (rotations between keys are
  blended in straight lines in this format).
- Findings recorded in the code: in this format y grows downwards (Live2D's
  samples play upright that way in Ayagami), and each parameter's
  "decimal places" field must be 3, as in official files (Ayagami refuses
  the 4 the writer used).
- Packages may carry the copy under `visual/live2d/`, with the same checks as
  everything else in a package.

## D-040 — Live2D-format playback stays off in units for sale until a lawyer says otherwise

- Date: 2026-11-17. Status: superseded by the owner's decision, D-044
  (`19-live2d-format-licensing.md`).
- We use no Live2D code, SDK, editor or sample model; the writer
  (image2live2d) and the player (Ayagami) are independent open-source
  projects whose authors describe how they learned the format without
  Live2D's software. Live2D's official route for a device that loads users'
  models needs approval and a share of every sale.
- Open questions for a lawyer: our position as a downstream user of
  independently written code (Australia, US, Japan, EU), patents (not yet
  searched), wording about compatibility, and buyers' rights in their own
  models. Inochi2D stays the default format.

## D-041 — The neck follows the head when the body turns or leans

- Date: 2026-11-17. Status: implemented (`studio/refine.py`).
- The rigger's body turn shifts the head but not the neck, whose top is
  drawn wide in shadow colour to fill under the jaw; it showed beside the
  face as a skin-coloured block. Found while comparing the two formats; it
  showed in both. The neck now takes the head's shift fully up to the point
  the head leans from, fading out to the shoulders.
- Still visible at full body turn: smears where the long hair moves off the
  chest, which the picture never showed. That is the same problem as the
  arms, for the alternative-drawings work.

## D-042 — Heads turn about the neck, Live2D style; the body turns as a cylinder

- Date: 2026-11-17. Status: implemented (`studio/refine.py`,
  `studio/partsplit.py`); the owner judged the turn "a massive
  improvement" and pointed out a concave look at a strong turn, since fixed.
- Replaces v3 (D-029 era): features moved unchanged inside a fixed
  outline, and the rigger's body turn slid the head 40 px. Now each part is
  turned on a rounded head about an axis behind it (the far side narrows,
  the near side widens, the outline shifts slightly, parts in front move
  more, hair behind moves the other way), with hair behind the head and a
  tapered neck so nothing behind shows. The body turns as a cylinder about
  the spine, one smooth field for every part. Details and measurements in
  `20-head-turns.md`; tools: `studio/reveal.py`, `stage/tools/pose_strips.py`.

## D-043 — 90° turns later, with drawn profile artwork

- Date: 2026-11-17. Status: planned.
- A side profile cannot come from warping the front picture: the tutorial
  the owner pointed to draws a side nose, side lips and a fill behind the
  profile, splits the hair with draw-order changes, and uses inverse masks.
  For us that artwork must be generated per character (drawing workflow),
  and the converter and device drawing code must carry masks and draw-order
  changes. The techniques are summarised in `20-head-turns.md`.


## D-044 — The device plays characters with Ayagami, Live2D-format copy first

- Date: 2026-11-17. Status: the owner's decision; implemented.
- The stage plays Live2D-format models with Ayagami's core (vendored,
  MIT or Apache-2.0) and our own OpenGL ES 3 drawing (`stage/src/live2d.rs`:
  masks, inverted masks, Cubism's blends and the newer blend modes, parts
  drawn off screen, culling). It decides the format by a file's contents.
  Inochi2D stays for screen cards, older packages and characters without a
  Live2D-format copy.
- The core links the Live2D-format copy when a character has one; a device
  setting (`character_format`) can put Inochi2D first, which is also how
  units would be switched back if a lawyer advises it.
- Buyers can add their own Live2D-format models as a zipped model folder;
  the page asks them to add only models they have the right to use. Not to
  be advertised until the Cubism Editor licence question (reportedly
  section 5.1.9) is answered.
- Checked: on Live2D's eight sample models (local only) the drawing matches
  Ayagami's own renderer (overlap 0.970-0.9997); on a converted character
  the two formats overlap 0.989 or more in thirteen poses. The converter now
  packs part pictures into one texture page, which cut a test character's
  peak memory on the device from 158 to 68 MB.
- Not done here: a Raspberry Pi run and the device image build (no base
  image or ARM emulation in this session); the stage cross-builds for the
  Pi.

## D-045 — Motion is defined once, on a generic skeleton, and fitted to each character

- Date: 2026-11-17. Status: proposed (from the owner's look at a friend's
  VTubing app); investigation is roadmap item 3a.
- VTubing apps built on standard humanoid skeletons (VRM) work with any
  model because tracking, expressions and smoothing act on the standard
  skeleton, and every model is mapped onto it. Our equivalent is the stage's
  control layer (head, body, eyes, mouth, breath; `mapping.rs`) and the
  standard parameter names our rigger writes. Proposed: grow that into a
  generic upper-body skeleton with arms and hands, author idle motion and
  gestures once against it (including recorded human motion), smooth every
  control with filters and springs, and fit both our own characters and
  buyers' models to it, with a calibration step for models whose ranges or
  directions differ.

## D-046 — An arm moves on its own only when the picture shows it whole

- Date: 2026-11-19. Status: decided (from testing all eleven test characters,
  `27-character-tests.md`).
- Played on the real stage, six of the nine arms the studio had cut to move
  came apart or swung wrongly: forearm stubs under armour, a cape or hair,
  sleeves whose hands were folded up or in pockets, bell sleeves. A broken
  arm looks far worse than a still one, and every arm gesture has a stand-in
  (a greeting for a wave, leaning back for hands on hips, a tilt for a
  shrug).
- So the studio errs towards still: an arm moves only if it starts near the
  shoulder, is long enough and no wider than the face, and shows a hand at
  its end (`studio/partsplit.py`). Cases the rules miss (hands in pockets,
  a bent arm with the hand on the hip) are switched off by the buyer in
  Character Studio's review, which shows the wave before sending.

## D-047 — VRM avatars are a third drawing inside the stage, on the same rig

- Date: 2026-11-19. Status: built (owner's request, after handoffs from
  ChatGPT and Gemini; `30-vrm.md`).
- Buyers can add VRM avatars (3D humanoids) beside the 2D characters. The
  stage draws them as a third format next to Inochi2D and Live2D-format;
  the core, the socket commands and the whole animation system are
  unchanged. A separate renderer process with its own protocol (ChatGPT's
  brief) would have duplicated the director, motion library and speech
  timing, and doubled the memory on a 1 GB board.
- A VRM offers the stage the same parameter names a Live2D-format model
  does, so the motion library (D-045's generic controls) drives it. Poses
  are written in VRM's normalised humanoid, so they fit every avatar. Where
  3D can do better than a picture (an open-palmed wave, forearms forward
  in a shrug), the same clip values are read the 3D way.
- No VRM library is used: the format is read from its specifications.
  vrm-runtime (Rust) reads VRM 1.0 only, and most avatars people own are
  VRM 0.x. Textures are scaled down once at import (1024 pixels on 1 GB
  boards, 2048 above).
- An avatar's own licence terms are shown on the settings page, not
  enforced; whoever imports it is responsible for the right to use it.

## D-048 — 3D avatars can move with full-body animation clips, the motion system still deciding

- Date: 2026-11-19. Status: built (owner's request: "allow more complex VRM
  animation ... choose which animation style ... upload your own
  animations ... failsafes"; `30-vrm.md`).
- Each VRM character moves in one of two styles, chosen on the settings
  page: **full body** (the default) plays animation clips on the whole
  skeleton; **light** keeps the generic controls of D-045. Only the chosen
  style's data is loaded (no clip is read in the light style).
- Clips are **VRM Animation** (`.vrma`), the VRM consortium's standard,
  so owners can bring animations from other tools. The motion system stays
  the brain: it picks gestures, times them to words and moods, keeps the
  budget and channels; a clip only supplies a gesture's body movement, and
  idle loops are chosen by state and mood. Arms take a clip's pose; the
  head and body add its movement, so looking, nods and breathing carry on.
  A separate animation player with its own state machine would have split
  the character's mind in two.
- The 30 built-in clips are written from scratch with our own kit (poses
  described with hand placement and planted feet, solved on a reference
  skeleton), so there are no third-party motion licences to carry.
  Head-only gestures stay with the library, which times them to speech.
- Heavy avatars are met at every stage: refused or shrunk at import with
  an estimate of their memory, kept within a texture budget when loaded,
  drawn more simply by a frame-rate watchdog, and shown drawn lightly (then
  replaced by a card) if the stage fails with them.

## D-049 — Turning is a full-body gesture that steps, offered only where a back can be shown

- Date: 2026-11-19. Status: built (owner: "why not add a few animations of
  characters turning?"; `30-vrm.md`).
- Four clips: turn around, twirl, turn aside, look back. The feet step in
  turn, and the knees follow the way the body faces; a turn on fixed feet
  reads as a statue on a turntable.
- They are `clip_only` gestures: playable only where a clip exists, so only
  3D avatars in the full-body style can do them. The language model is
  offered them only then. A 2D character has no back to show, and faking one
  by squashing the picture would look broken.
- The same change made the stage keep points that several parts share
  once, as VRoid and UniVRM 0.x exports intend. Without it, the import
  refused an ordinary VRoid avatar, and another used 5.6 times the vertex
  memory it needed. Any VRM is fitted from its own humanoid bone map, so
  owners never rig anything.

## D-050 — A language model on the owner's own computer, spoken to in its own way

- Date: 2026-11-19. Status: built (owner: "an option for the user to
  connect to their own self-hosted LLM"; `28-user-guide.md`).
- **Ollama is spoken to natively** (`/api/chat`). Its OpenAI-style door
  takes neither a context length nor a keep-alive, and Ollama's default
  context is small enough to cut the character's memory short. We ask for
  the context the owner sets (8,192 by default) and keep the model loaded
  for 30 minutes. Every other server (LM Studio, llama.cpp, vLLM, KoboldCpp,
  text-generation-webui, Jan, GPT4All) is spoken to as OpenAI-compatible,
  at its usual port.
- **Finding servers** looks only at this device and the private addresses
  around it, on the usual ports, and asks each what it is. It never looks
  beyond the home network.
- **Patience**: up to 120 seconds for the first words, since a home
  computer may need to load the model; the cloud keeps its shorter limit.
- A reasoning model's thinking (`<think>…</think>`) is never spoken.
- A server error carries the server's own words ("model requires more
  system memory than is available").
- The key, if a server wants one, is stored like every other key.
- Checked against a real Ollama with the device's own client
  (`31-memory.md`). On a 4-core processor without a graphics card, qwen2.5:3b
  replied in 4–8 s and qwen2.5:7b in 4–17 s. Memory needs the bigger one: the
  3-billion model kept poor memories even held to a JSON schema. The settings
  page and user guide say so, and `core/tools/memory_check.py` lets an owner
  try their own model.

## D-051 — Memory as conversations, lasting memories and a diary

- Date: 2026-11-19. Status: built (owner: "how does it decide one
  conversation has ended and another has begun … long-term and dynamic
  memory capabilities, to try and match what Code27 Sybran does";
  `31-memory.md`).
- **A conversation has clear ends**: 20 quiet minutes, three minutes after
  a goodbye, a new day at 4 am, or the owner's *Start a new conversation*.
  Before this, a summary covered "a stretch of turns", which matched
  nothing a person would call a conversation.
- **Lasting memories are items, not a page of notes**: one sentence each,
  with a category, an importance and dates. The model changes them by
  number (add, update, forget), so what was said replaces what was
  remembered without rewriting everything. A free-text page drifted, and
  grew or lost things at each rewrite.
- **One call when a conversation ends**, in the background, instead of a
  call every six turns. Long conversations are condensed as they go,
  between replies.
- **A dated plan comes back by itself** once its day has passed, until the
  person says how it went. This doesn't depend on the model remembering to
  ask for a follow-up, which a small model often forgot.
- **The diary** is the character's own voice and continuity, as in CODE27.
- **Words, not meaning, for recall**: on-device and instant, with no extra
  service. Embeddings can be added later if matching by meaning is missed.
- **Forgiving of the model**: answers are read in every form a real small
  model wrote, and a plain or empty answer still leaves a summary.
- Not done: speaking first, encryption at rest, more than one person.

## D-052 — Sold devices update themselves from updates signed with the owner's key

- Date: 2026-11-20. Status: built (owner: "a way to enable the device to
  receive updates ... even after I've sold the device"; `32-updates.md`).
- **Signed, not merely downloaded over HTTPS**: an update is accepted only if
  the owner's Ed25519 key signed its manifest, which names the payload's
  hash. The update address can be any static host, even a hijacked one; it
  doesn't matter. The private key stays with the owner, never in the
  repository or on a device. Older versions are refused, so a known-bad old
  update can't be replayed.
- **Split by privilege**: the device's service (unprivileged, read-only
  system) finds, downloads and checks updates, and asks by writing a request
  file. A root installer, started by a systemd path unit, checks the
  signature again itself and does the installing. The service never gains
  the power to change software.
- **A folder per version, switched by links**: the new version is unpacked
  beside the running one, and `current` switches in one step. If it doesn't
  answer its health check (or the screen program keeps stopping), `current`
  switches back. A start after a power cut finishes or undoes an update.
  Each version carries its own system files, so going back restores them.
- **Night-time, idle installs**, on by default and with a switch to turn them
  off, plus manual update files for devices without internet.
- The installer uses only Python's standard library and `openssl`, so it
  survives an update that breaks the Python packages. New Python packages
  come as wheels inside the update, never from the internet at install time.
  System packages need a new image.

## D-053 — Speaking first is an option, by sound alone

- Date: 2026-11-20. Status: built (owner: "have that as an option that can be
  toggled on or off"; `31-memory.md`).
- **Off by default.** The owner turns it on, chooses how long apart counts
  (3 hours by default) and the quiet hours (10 pm to 8 am by default).
- **Presence is speech-like sound** of a second or more, from the
  microphone's loudness only, with nothing recorded, recognised or sent. A
  camera or a presence sensor would know better, but would add hardware and
  worry; the button and a reply already show someone is there.
- **What it says comes from memory**: how long it has been and plans whose
  day has come. That makes it a check-in, not just a "hello". The greeting
  is kept as a turn where only the character spoke. The next message to the
  model begins with a placeholder, because some services need the person's
  turn first.
- **It listens for half a minute afterwards**, so the person can just
  answer, then stops listening as before.
- A television can set it off: hence the gap, the quiet hours, once per
  gap, and off by default.

## D-054 — Memories are matched by meaning with a static embedding model, read with numpy

- Date: 2026-11-20. Status: built (owner: "Yes, please do so" to recall by
  meaning; `31-memory.md`).
- Three models were compared on 30 remarks whose memories share few words
  with them: Model2Vec potion-base-8M and potion-retrieval-32M (static), and
  all-MiniLM-L6-v2 (a small transformer, in two forms). potion-base-8M found
  the right memory in its top three for 29 (MiniLM 30, words alone 9), at
  0.04 ms a remark against about 1 ms, and needs no runtime but numpy. On a
  1 GB board that settles it.
- **Read by our own code** (WordPiece tokenizer, safetensors table, average,
  unit length), checked to give the official library's vectors exactly. The
  device gains no Python packages.
- **Added to word matching, not replacing it**: names and rare words still
  match by words. Meaning adds up to four memories (at least 0.18 alike,
  within 0.15 of the closest), chosen on the same test (27 of 30 right, about
  one extra memory a reply).
- **Vectors are kept per memory and conversation**, keyed by a checksum of the
  text, made in the background (at most 64 during a reply), and removed when
  their memory or conversation is forgotten.

## D-055 — Owners add their own rigged models in either open 2D format

- Date: 2026-11-20. Status: built (owner: "allow users to upload their own
  rigged live2D models"; `28-user-guide.md`).
- Live2D models were already accepted (a zip of the model's folder). Testing
  with Live2D's own sample model found that a model's pose file (parts shown
  one at a time, such as two sets of arms) was ignored, so both showed. The
  stage now shows each group's first part, as Live2D's player does at rest.
- **Inochi2D models** (`.inp`, or Inochi Creator's `.inx`) can now be added
  on their own. That gives owners a free, open-source rigging route. The
  model's name, artist and licence come from its header; its abilities and
  controls come from its parameter names, Inochi2D's or Live2D's.
- Stretchy Studio was looked at too (`23-drawing-workflow.md`): its Live2D
  model export does not yet carry the deformers that move a face, and its other
  exports are formats the device does not play. To watch.

## D-056 — The system partition is read-only; what the device saves has its own partition

- Date: 2026-11-20. Status: built (owner: "how do we protect the card from power cuts?"; `33-finishing.md`).
- The operating system, the software and the models it comes with are mounted read-only, so a power cut
  cannot damage what the device needs to start. Everything it saves (characters, memories, settings,
  keys, saved Wi-Fi, the time zone) is on a third partition, `companion-data`, grown to fill the card at
  the first start; /var's changes and the logs are kept in memory.
- **Updates make the system writable only while they install**, then read-only again, rather than an
  overlay that would need a restart after each update.
- **Raspberry Pi OS's own first-start resize is switched off** (it grows the system partition, which the
  data partition now follows); the device grows the data partition itself.
- If the data partition cannot be repaired at start, it is made again, empty, so the device still works,
  and the settings page asks for a backup. `--writable-system` builds the earlier layout.

## D-057 — The wake word is keyword spotting on the device, and keeps the whole utterance

- Date: 2026-11-20. Status: built (`28-user-guide.md`).
- sherpa-onnx's English keyword spotter (3.3M parameters, Apache-2.0) listens for any words given as
  text, so the character's name works without training. Measured on the device's 904 synthetic voices
  (`core/tools/wake_check.py`): names with clear consonants are heard well ("Lucy" 96%), some short names
  less ("Mika" 66%, "hello Mika" 91%); no false starts from 21 minutes of everyday speech except a name that
  sounds the same.
- Transcribing everything said in the room and looking for the name was tried and rejected: the device's
  recogniser returns nothing for most short phrases said alone, and it would run far more often.
- When it wakes mid-sentence, the whole utterance is kept and the wake words taken off, so a question in
  one breath is answered at once.

## D-058 — Pictures over the character are drawn by the core and shown by the stage

- Date: 2026-11-20. Status: built.
- Captions, what it heard, the microphone-off sign and hints are drawn as PNGs by the core (Pillow, the
  same fonts as the setup cards), in a folder in /run, and named to the stage, which only fades them in
  and out. The stage needs no text layout or fonts. The same pass dims the screen or makes it dark (it
  then stops moving and drawing the character).

## D-059 — The USB connection is the device's own small gadget, not Raspberry Pi's package

- Date: 2026-11-20. Status: built (owner: "include a usb connection option").
- Raspberry Pi's `rpi-usb-gadget` (January 2026) shares internet both ways and can make a laptop send its
  traffic through the device. The device only needs its settings page reachable: a NCM network gadget
  (built into Windows 11, macOS and Linux), with an address given and no route, so the computer's own
  internet is untouched. ECM and RNDIS are a setting away if a computer needs them.
- On a Pi 4 or 5 the USB-C port is also the power input: the computer powers the device while it is
  connected. A power-and-data splitter in the case would allow both at once (`05-hardware-and-bom.md`).

## D-060 — Family-safe mode cannot be turned off without the password again

- Date: 2026-11-20. Status: built.
- Its rules come after the character's description and say they come first; a reminder ends the
  instructions; the memory's work is told too; strong swearing is taken out of what is said as a second
  line. A child with the settings page open on a parent's phone cannot turn it off.

## D-061 — A Live2D model's own idle motions play underneath the device's movement, added to it

- Date: 2026-11-21. Status: built (`stage/src/live2d_motion.rs`).
- Live2D's own player replaces its idle motion with whatever else plays. The device's movement (gaze,
  nods, gestures timed to speech, moods) is the product, so the model's motion is a base instead: its
  change from the rest pose is added, as Live2D's player adds dragging and breathing. Gestures take back
  the parts of the body they hold; the motion shows less while listening, talking and dozing.
- Blinks and the mouth stay the device's (its blinks follow speech and gaze; its mouth the voice), so the
  model's EyeBlink and LipSync parameters are left out of its motions.
- Characters made with Character Studio start with it off: the rigger's generic idle loop would double the
  device's own living motion. Imported models start with it on, as their artist made them.

## D-062 — A wide head turn is a control of its own; flat characters make an ordinary turn instead

- Date: 2026-11-21. Status: built.
- Widening the ordinary head turn's range would have made every existing nod, glance and look-away three
  times bigger on 3D avatars. A separate control, `head_turn`, leaves them as they are; a VRM avatar offers
  it as `ParamHeadTurn` (90 degrees each way, a quarter of it in the chest and spine).
- On a character without it, it adds to the ordinary turn as far as that goes, so a gesture written with
  it still works; gestures that need it say so and have a substitute (`look_back` -> `look_away`).
- 2D profiles still need drawn side artwork (D-043).

## D-063 — Character Studio starts from one Python launcher on every system

- Date: 2026-11-21. Status: built; tested on Windows, macOS and Linux on GitHub (`.github/workflows/studio.yml`).
- `studio/launch.py` uses only Python's own library, so it runs before anything is installed, and does
  what `start.sh` did in bash: the studio's own environment, PyTorch for the computer's graphics, the
  packages, then the app. The per-system starters (`start.bat`, `start-mac.command`, `start.sh`) only find
  Python and run it. One place to get right, and testable.
- The rigger is installed from GitHub's zip of its fixed commit rather than with git, which Windows does not
  have. Text is UTF-8 everywhere (`PYTHONUTF8`), so names with accents survive Windows.
- Not a packaged program (.exe, .app): those need code signing to open without warnings, and PyTorch makes
  them several GB. Python from python.org is the one thing a buyer installs.

## D-064 — Character Studio is installed as a real app, with its own Python (replaces the last point of D-063)

- Date: 2026-11-21. Status: built; built, installed and run on Windows, macOS and Linux on GitHub
  (`.github/workflows/studio-apps.yml`).
- D-063 left buyers to install Python, download the project's files and watch a terminal. That is not the
  one-click app the plan promised. Now: a Windows installer (Inno Setup, per user, no administrator), a Mac
  disk image (an app dragged to Applications) and a Linux AppImage. Each holds its own relocatable Python
  (python-build-standalone, about 30 MB of a download), so nothing else is installed.
- The heavy AI libraries (1-3 GB) are not in the download: the first start fetches the build of PyTorch that
  suits the computer (NVIDIA, Apple, or the processor), showing each step and the download's progress on a
  page in the browser, with Try again and Repair. Shipping them inside would make the download 1-3 GB and
  could not suit every computer.
- The studio stays a page in the browser (as it was), with no window of its own: one less thing to go
  wrong on three systems. It gets a Quit button and stops by itself once its page has been closed a while.
- Unsigned for now: Windows SmartScreen and macOS Gatekeeper warn once, and the guide says what to press.
  Signing costs a yearly fee, so it is the owner's decision; the workflow is ready to gain it.

## D-065 — The free voice is Piper's VCTK voice: no research-only recordings in it

- Date: 2026-11-21. Status: done (`core/companion/piper_voice.py`, `device/install.sh`).
- The first free voice (Piper "LibriTTS-R medium", 904 speakers) was fine-tuned from Piper's lessac voice,
  whose recordings (Blizzard Challenge 2013) are licensed for research only, excluding the sale of speech
  synthesis products. Piper's current VCTK voice is trained on VCTK from a base voice trained on Hi-Fi TTS,
  both CC BY 4.0: commercial use with credit, which the notices give.
- 109 speakers, 72 offered after the survey; real-time factor 0.07-0.08 on the PC (the old voice: 0.045),
  still far ahead of speech. Measurements: `16-measurements.md`.

## D-066 — Licences ship with the product; the OS source goes with every image; nothing copyleft is linked into our code

- Date: 2026-11-21. Status: done (`34-licences.md`).
- The device and Character Studio each have a *Licences and notices* page with every licence text and
  credit, read from what is installed where possible, so it stays true.
- The GPL and LGPL are met by a written offer in the device's notice, and by the source of every package
  in each image published on the image's release (`device/fetch-sources.py`), so the offer can always be
  honoured.
- Our own software stays the owner's. That means keeping copyleft code out of it:
  - espeak-ng runs as a separate program, and sherpa-onnx is pinned to 2.x, which has none of it inside;
  - the LGPL libraries used are the operating system's shared ones;
  - the Linux app's Python leaves out its AGPL Berkeley DB module;
  - the AppImage runtime's LGPL libfuse has its source published with each release.

## D-067 — Inochi2D is the format we name; Live2D-style models are described, not advertised

- Date: 2026-11-21. Status: done. The owner's instruction.
- Guides and the settings page promote Inochi2D and "other 2D rigged models"; where the other format must
  be named, it is "Live2D-style" or "`.model3.json`". No Live2D names or logos in branding. The notices
  credit the trademarks and say no Live2D software is included.
- Fits the open Cubism Editor question in `19`: the import still works, but it is not advertised.

## D-068 — Builds put their files on GitHub releases, as drafts unless told to publish

- Date: 2026-11-21. Status: built (`.github/workflows/make-image.yml`, `studio-apps.yml`).
- Artifacts last two weeks, need a GitHub login and come zipped; a release keeps the files, at a fixed
  address. Each workflow run (by hand) makes a release:
  - `device-image-<version>`, with the image and, from a second job, its operating system's source;
  - `studio-v<version>`, with the three apps under names that stay the same.
- A draft by default, so the owner checks before anything is public; "publish" makes it public at once.
- Character Studio's releases are the repository's "latest", so `releases/latest/download/...` links
  always give the newest apps. Image releases are never "latest".
- Updates for sold devices stay in `seafire220/update-repo`, whose latest release devices read; nothing
  else is released there.

## D-069 — Voice services besides ElevenLabs, five of them with cloning

- Date: 2026-11-27. Status: built; tested against stand-in servers made from each service's API reference
  (no keys here, so not yet against the real services).
- Asked for by the owner: popular voice services and voice cloning besides ElevenLabs. Chosen from late-2026
  comparisons and each service's current API documents:
  - **Cartesia** (Sonic 3.6): the low-latency favourite for voice agents; instant cloning from 10-60 s.
  - **Fish Audio** (S2.1): good value, popular for character voices; cloning by "voice models".
  - **MiniMax** (Speech 2.8): expressive, many languages; cloning by upload, then clone.
  - **Inworld** (TTS-2): the cheapest of the popular ones; instant cloning from up to 30 s.
  - **Deepgram** (Flux TTS and Aura-2) and **Google Cloud** (Chirp 3 HD and others): widely used, voices
    only (no cloning through their API).
  - Left out: PlayHT (shut down) and Hume's Octave (its text-to-speech closed on 13 November 2026).
- Most of the work (the four cloning services, per-character voice services, the console) was done in a
  separate session (commit 05fdf4e) and checked against the API documents here. Corrected:
  - Inworld takes WAV and MP3 samples only, and its language goes in `languageCode` (the old `langCode` knows
    15 languages). Background-noise removal is off, as Inworld warns it can spoil a clean recording. All
    pages of its voice list are read.
  - Fish Audio is limited to the sample formats it documents (WAV, MP3, FLAC).
  - Cartesia's list asks for the owner's own voices separately, so clones show even when the library's
    first page of 100 does not hold them.
  - MiniMax deletes a cloned voice that has never spoken within 7 days, so the new voice says one word
    straight after cloning.
- Keys for every service are kept like the others: write-only, redacted from logs and support reports,
  left out of backups.

## D-070 — Gestures recorded with a phone: the phone films and tracks, the device makes the clip

- Date: 2026-11-27. Status: built; checked in an emulated phone (Chromium as a Pixel 7), not yet on real
  iPhones and Android phones.
- Asked for by the owner: let owners make their own gestures with their phone as the camera, reached by a
  QR code or, when that will not scan, a link. Inspired by Visio (a VTuber app, EUPL-1.2) without copying it:
  nothing of Visio is used (no code, page design or assets), only the general idea of a phone as a motion
  camera, which no licence covers. The pieces are MediaPipe (Apache-2.0), the conversion Character Studio
  already had (moved to `core/companion/mocap.py` and shared), and pages of our own.
- **The phone films with its own camera app** (`<input type="file" accept="video/*" capture>`), not a live
  camera view: `getUserMedia` needs a secure context (HTTPS or localhost), and a device on a home network
  has no certificate a phone trusts. A self-signed one would greet owners with a security warning. So there
  is no live puppeteering from the phone for now; recording a gesture does not need it.
- **Tracking runs on the phone**, in its browser, with the files the device serves: a phone is many times
  faster than the device, the device never decodes video, and the video stays on the phone. Only the points
  MediaPipe finds go to the device (about 0.5 KB a frame).
- **The same conversion as Character Studio**, so a gesture recorded either way plays the same; the device
  checks every number it is sent.
- **The link needs no password**: a one-time code (six of 31 easily told-apart letters and digits, about
  887 million codes), one at a time, for 30 minutes, made only by the logged-in owner; 30 wrong codes in a
  minute stop all codes for a minute. Only the recorder's page may run WebAssembly.
- **The files** (24 MB) are fetched at install at fixed versions, each checked by SHA-256; a device that gets
  this through an update fetches them when the owner first asks for a link, since updates carry no models.
- Later, if wanted: live puppeteering from a phone (needs HTTPS the phone trusts, or an app); recording on
  real phones of several makes, to check their camera apps' video formats and graphics drivers.

## D-071 — Live from a phone: an opt-in live camera over the device's own HTTPS, and face-tracking apps

- Date: 2026-11-27. Status: built; checked with an emulated phone and a stand-in app, not yet real phones.
- Asked for by the owner, after learning how Visio does it: a live camera mode by the self-signed approach,
  with the warning explained for people to click past, and a route through phone apps such as VTube Studio
  or MeowFace. (Visio's desktop app serves its phone page over HTTPS with its own certificate and tells
  people to tap past the warning; it also takes face tracking from those apps. Nothing of Visio is used.)
- **The live camera is opt-in** (off by default), because every phone warns about a certificate it does not
  know. The settings page explains the warning, why it is expected on a home network, and the exact taps on
  Android and iPhone, before the owner turns it on, and warns never to do it for a website on the internet;
  it shows the certificate's fingerprint to compare. The camera-app recording stays the default and needs no
  warning.
- **The device makes its own certificate** with `openssl`, to the rules phones apply to every server
  certificate (P-256, SHA-256, names in subjectAltName, server authentication, at most 825 days), and
  renews it a month before it runs out; the key stays in `config/tls`, readable by the service alone, out of
  backups, removed by a factory reset. A real certificate per device (a domain, a service we run, and some
  routers block it) is not worth it for this.
- **The HTTPS serves the phone's recorder alone**, on 443: the settings page and its API stay on the plain
  address, so turning the live camera on adds nothing an owner's other pages depend on.
- **Face-tracking apps need no certificate**: the device speaks their published protocols (VTube Studio's,
  which MeowFace also speaks; iFacialMocap's) over UDP on the home network, to and from the address the
  owner typed only. Their angles' directions are not documented: ARKit's are assumed, and the owner can
  mirror and flip.
- **One engine for both** (`live_motion.py`): resting pose, mirror (on: like a reflection, as VTubing apps
  do), smoothing, at most 30 updates a second, and recording into a gesture; one stage command
  (`{"live": ...}`) that works for every kind of character and hands back by itself when updates stop.

## D-072 — The whole body, recorded with a phone, for 3D avatars

- Date: 2026-11-28
- Decision: for a 3D (VRM) character playing clips, the phone's recording is of the whole body. The phone
  sends MediaPipe's 33 3D body points (its larger pose model, pinned by SHA-256); the device turns them into
  the avatar's bone turns and keeps them as one of the avatar's own VRM Animation clips. Only parts of the
  body seen at least half the time are kept; the avatar's own movement shows in the rest; short gaps are
  bridged and long ones rest the part; with the shoulders unseen most of the time it makes the
  head-and-arms gesture instead, saying why (`26-animation-director.md`).
- Why: the owner asked for it, with failsafes for a body not wholly in the picture; a clip that moves only
  the bones it has lets the stage keep the avatar's own idle movement in the rest, so an upper-body
  recording still looks whole.
- Rejected: solving on the phone (the device's numpy is quicker to test and the same for every phone);
  following sideways steps (a hand-held phone moves too); copying the whole body live (later, if wanted).

## D-073 — Backgrounds behind the character

- Date: 2026-11-28
- Decision: the stage draws a picture behind every kind of character (`stage/src/background.rs`): filling the
  screen with no bars, faded from one to the next, darker if asked, and moving a little the other way as
  the character turns. Eight built-in pictures are drawn in code by the device itself (`core/companion/backgrounds.py`),
  so no picture's licence needs checking; "with the time of day" is the default. Owners add their own photos,
  turned upright, fitted to the screen and stripped of camera details (location among them). One choice for
  all characters, and one per character; both are in backups.
- Why: backgrounds were a "wanted" feature carried over from the reference product (`03-requirements-spec.md`),
  and the owner asked for them; every comparable product shows its character in a scene.
- Rejected: shipping photos or drawings from elsewhere (licences, and D-024); moving pictures (video
  backgrounds), until frame rates on the Pi are measured.

## D-074 — Your day: the weather, spoken alarms, special days, a clock and a photo frame

- Date: 2026-11-28
- Decision: the owner chose items 3, 4 and 5 of `36-polish-review.md` (touch reactions are saved for later).
  - **Weather** (`core/companion/weather.py`): first from MET Norway; replaced the same day by a service the
    owner chooses, with their own key (D-075). Asked at most every 30 minutes, only when the weather will be
    said or shown; the last forecast kept six hours. Off until the owner chooses a service.
  - **The town is chosen from a list on the device** (`core/companion/data/places.tsv.gz`, 34,000 towns of
    15,000 people or more from GeoNames, CC BY 4.0, made by `core/tools/make_places.py`), so what the owner
    types never leaves the device; until then, the time zone's main city from the system's time zone tables.
  - **The morning hello** is speaking first's first greeting of the morning, with the weather and the day's
    plans from memory; **today's context** (the owner's name, special days, the weather, plans dated today) is
    in every reply's instructions.
  - **Alarms and timers are said by the character**: the first call from the language model, later ones (and
    any when the model fails) in set words, in the character's voice, else the device's own voice, else tones;
    up to five calls a minute apart, louder each time (timers three, half a minute apart, no louder). The
    button or any answer stops it; then it listens. **Daily wake-ups** are settings, rung once a day within
    five minutes of their time.
  - **Special days** (`special_days.py`): the owner's birthday, each character's arrival day (kept from
    install) and hundredth day, the owner's own days, and seven holidays (Easter worked out each year); a line
    in the instructions, and one of six **festive backgrounds** drawn in code for the day.
  - **A clock overlay** (time, date, weather drawn on the device) at the top left while nobody is talking, or
    always; **the photo frame** is the background choice "photos" (the owner's pictures in turn, changed only
    while nobody is talking).
- Why: daily-life use is what comparable products (Gatebox, Desktop Mate) offer beyond conversation, and
  the owner asked for these.
- Rejected: **Open-Meteo**'s free service (its terms keep it for non-commercial use; its paid plan needs a key
  per seller); **an online town search** (Nominatim's policy and privacy: the list on the device needs
  neither); **ringing forever** (five calls, then it stops and notes it, so a device in an empty house is not
  heard all day); **holidays of every country** (the owner adds their own days instead; more can come with
  other languages).

## D-075 — The weather from the buyer's own key, like the AI services

- Date: 2026-11-28
- Decision: the weather comes from a service the buyer chooses, **OpenWeather** or **WeatherAPI.com**, with the
  buyer's own key, kept and protected like the AI keys (`store.SECRET_NAMES`: never shown again, never in
  backups, logs or support reports). Each service's forecast is turned into the device's own weather words
  (`weather.py`: `parse_openweather`, `parse_weatherapi`), so summaries, the clock and the alarms work the same
  with either. Only the key and the position (two decimals) are sent. A refused key is reported in plain words
  on the settings page and not tried again for an hour, unless a new key is saved; nothing logged holds the
  key or the address it was sent in. Each service's credit ("Weather data provided by OpenWeather", "Powered by
  WeatherAPI.com") is shown on the settings page, as their free plans ask.
- Why: the owner: "The user ought to supply their own API." With MET Norway (D-074) the device asked under the
  seller's name, making the seller the service's user, responsible for its terms and for all devices' traffic
  together; with the buyer's own key, the buyer is the user, as with the language models and voices (H4-H5).
  Both services' free plans allow this use (OpenWeather: a million calls a month, commercial use allowed with
  credit; WeatherAPI.com: 100,000 a month, "including commercial apps"); the device needs a few thousand.
- Rejected: **MET Norway** (no key to bring: the seller would be its user); **Open-Meteo** (its free service has
  no keys; its keys are for paid plans); **more services** for now (Tomorrow.io, Visual Crossing, Pirate Weather:
  easy to add the same way if buyers ask).

## D-076 — A TV box variant: Linux on the box, in its own folder

- Date: 2026-11-28
- Decision: the owner asked for a variant for an Android TV box (the X96 Max Plus, S905X3), in a separate folder,
  keeping the Raspberry Pi device as it is. Built as `37-android-tv-box.md` recommended (way A): the box starts
  Linux from a card (the community Armbian image for S905X3 boxes, Debian 13 like the Pi image), and runs the same
  `core/` and `stage/`. Everything the box needs instead is in `tvbox/`: its installer (which runs
  `device/install.sh`, unchanged, then the box's parts), card-image builder and checker, the boxes' device-tree
  list, and a workflow (`make-tvbox-image.yml`) beside the Pi's.
  - **The screen**: Weston's kiosk shell instead of cage, at 1920x1080 whatever the TV would like (a 4K picture is
    four times the work for the Mali-G31; cage cannot choose the mode), with no blanking. A systemd drop-in, which
    updates leave alone; the character's program gets the screen service's own arguments from its unit file.
  - **The button**: any input device with OK, Select, Enter, the keypad's Enter or Play/Pause (the box's remote
    through the kernel's own X96 key map, a USB button or keyboard, the TV's remote through HDMI-CEC once turned
    on); the remote's volume and mute keys; devices plugged in later are found. `core/companion/__main__.py`, by
    settings in `/etc/default/companion`; the Pi's behaviour is unchanged.
  - **Sound**: USB first, as on the Pi; else the box's own card, routed to HDMI at each start.
  - **The address on an Ethernet cable** is now shown on the setup screen (shared: the Pi's Ethernet benefits too).
  - **Security**: the community images come with root/1234 and SSH on; the box image locks root and turns SSH
    off, unless a maker's public key is given for a test image.
- Why: a box costs less than a Pi with its case, power supply and storage, and brings Wi-Fi and Ethernet; way A
  keeps one software for both (an Android app would be a second product, `37`). A separate folder keeps the Pi's
  build and checks exactly as they were.
- Not yet: power-cut protection on the box, the source-code release for its kernel and boot loader, and a run on a
  real box (T1 to T10 in `15-hardware-test-plan.md`).
- Rejected: copying `core/` and `stage/` into the folder (two copies would drift apart; a fix to memory or a
  character would have to be made twice); cage with a 4K picture; an Android app (for now).
