# VRM avatars: 3D characters beside the 2D ones

Status: 2026-11-19. Built and tested in software (unit tests, four real
avatars, the full conversation check in both animation styles). Not yet run
on a Raspberry Pi.

## What it is

A buyer can add a **VRM avatar** (`.vrm`, the 3D humanoid format VRoid
Studio and most VTubing tools use) on the settings page, beside the 2D
characters made from pictures and imported Live2D-format models. It
talks, moves and feels as those do: the same moods, gestures, idle life,
listening, lip sync, temperament and Animation tab.

It moves in one of two styles, chosen for each avatar:

| Style | How it moves | Costs |
|---|---|---|
| **Full body** (the default) | Animation clips on the whole skeleton: an idle pose for each mood and for listening, thinking, talking and dozing; gestures such as a wave, a bow, hands on hips, a stretch, a yawn with a hand over the mouth. Where it looks, nods, its face and lip sync go on top. The owner can add their own clips. | About 2 MB more for the clips; posing still takes under 0.1 ms a frame |
| **Light** | The controls the 2D characters use, turned into bone turns (below). No clips are read. | The least |

Only the chosen style's data is in memory: in the light style no clip is
read at all.

The owner asked for an alternative to the 2D animation system: upload your
own VRM, have it matched to our default rig, and have it animated by our
prebuilt animations for speaking and moods, on a device with 1 to 2 GB of
memory. Two outside handoffs were read first:

- ChatGPT's brief (`VRM_ARM_animation_Gemini_handoff.pdf`) proposed a
  separate renderer with its own JSON IPC (Domovushka, or vrm-runtime with
  raylib), plugged in later.
- Gemini's notes (`28-gemini-vrm-handoff.md`) proposed a third backend
  inside the stage program instead, which is what was built here. Its code
  is not in the repository, so this was written from scratch. Its ideas
  that held up are used: memory profiles with texture caps, shrinking
  textures at import, and a first feelings map from the avatar's
  expressions.

## Does it work on any avatar? Does it need rigging?

Any reasonable `.vrm` works, and **nobody rigs anything**. A VRM file
carries its own map of a standard humanoid skeleton (hips, spine, chest,
neck, head, both arms and legs down to the fingers, eyes). That is what
VRM is for. The device reads that map, and every movement and clip is
written for that skeleton, then fitted to each avatar's own bones, rest pose
and proportions as it loads (below). Expressions (joy, sorrow, blinking,
the vowel mouth shapes) are named in the file too.

Tested on six avatars from four makers (VirtualCast, pixiv, Dwango, VRoid
Project), both VRM versions, 101 to 357 bones: all load, and every gesture,
idle loop and clip plays on each.

Limits:
- **Other 3D formats** (FBX, PMX, plain glTF) are not read. Convert them to
  VRM first, with VRoid Studio, UniVRM in Unity, or the VRM add-on for
  Blender.
- **Hands that touch the body** (on the hips, at the chin) are placed for
  average proportions. On an unusual avatar they can float or sink a little.
- **Very heavy avatars** are refused, or drawn lighter (failsafes, below).
- A missing optional bone (upper chest, toes) is fine: its turn passes to the
  next bone.

## How it fits: one rig, three drawings

```
core (Python): conversation, voice, moods, gestures; checks and packs .vrm uploads
   |  the same JSON-lines socket, the same commands
stage (Rust): director, behaviour, motion library, speech
   |  the same controls (24-motion.md): head, body, breath, eyes, mouth shapes, brows, arms, elbows, shoulders
   +-- Inochi2D puppet      (characters made from pictures)
   +-- Live2D-format model  (Ayagami)
   +-- VRM avatar           (stage/src/vrm: humanoid bones and expressions, drawn in 3D)
```

The "default rig" is the stage's set of generic controls (D-045). A VRM
avatar offers the stage the same parameter names a Live2D-format model
does (`ParamAngleX`, `ParamEyeLOpen`, `ParamArmLA` and the rest), plus one
per expression (`Expr:happy`). So the mapping, the movement settings page,
Feelings and every clip in the motion library drive it with no change.
Underneath, `stage/src/vrm/rig.rs` turns the controls into bone turns.

**Normalised humanoid.** Every VRM is in a T-pose at rest, but its bones'
own axes differ from model to model. Poses are written once in the
normalised humanoid VRM 1.0 defines (the T-pose with every bone's axes
along the world's). A rotation given there turns the bone by that much in
world terms on top of its parent's, whatever the model's own axes. VRM 0.x
avatars, which face the other way, are turned round first.

| Control | VRM avatar |
|---|---|
| Head turn, nod, tilt | head (two thirds) and neck (one third) |
| Wide head turn (`head_turn`, the avatar's `ParamHeadTurn`, up to 90 degrees each way: a profile) | a quarter in the spine, chest and upper chest, then the neck (40% of the rest) and the head; added to clips in the full-body style too |
| Body turn, lean, sway | spine, chest and upper chest, shared |
| Breath | the chest lifts a little; the shoulders too |
| Shoulders | both shoulder bones up |
| Arm (A) | the upper arm out from the side (rest: hanging, 11 degrees out) |
| Elbow (B) | folding back (B < 0): hand to the hip; small B: forearm forward (a shrug's open hands); large B: forearm up beside the head, palm to the viewer, fingers open (a wave) |
| Eyes | the eye bones, within the avatar's own look-at ranges; or its look expressions |
| Blink | blinkLeft and blinkRight (else blink) |
| Mouth | the vowel shapes aa, ih, ou, ee, oh from the speech timing; aa for a plain opening |
| Smile, eye smile, brows, sad brows | a VRoid Studio face's own parts (`Fcl_MTH_Fun`, `Fcl_EYE_Joy_L/R`, `Fcl_BRW_*`); otherwise a little of the relaxed or happy expression |
| Blush | an expression or shape named for one (blush, cheek, shy, 照れ, 頬) |
| Moods (through Feelings) | the avatar's own happy, sad, angry and surprised, shown a little softer than full (a full "happy" closes many avatars' eyes for as long as the mood lasts) |

That table is the light style. The full-body style plays clips for the
body and keeps the rows from Eyes down (the face, the mouth, the moods)
exactly as they are; see below.

Fingers rest slightly curled, as a relaxed hand does. Expressions follow
VRM's override rules (an expression that hides blinking hides it), except
that the mouth is never silenced: speech always shows at half or more.

The same clips play on 2D and 3D characters, so their directions match: a
positive turn faces the viewer's right, a positive tilt leans the head's
top to the right, on both (checked side by side).

**Hair and clothes** swing with VRM's spring bones (`VRMC_springBone` and
VRM 0.x's `secondaryAnimation`), pushed out of the body's collider spheres
and capsules. They settle for a second before the first frame.
**Constraints** (`VRMC_node_constraint`: rotation, roll and aim) keep
twist bones and accessories with the bones they follow.

## Full-body animation (`stage/src/vrm/anim.rs`)

The owner asked for real 3D animation for VRM avatars, beside the light
style, with their own animations and with failsafes for avatars too heavy
for the device. Clips are **VRM Animation** files (`.vrma`, the
`VRMC_vrm_animation` glTF extension), the VRM consortium's format for
humanoid animation: VRoid Hub, VRM Posing Desktop, Blender with the VRM
add-on and other tools save them, so animations made anywhere play on any
avatar.

**What plays.** Underneath everything, an **idle loop**, chosen by what the
character is doing and its mood, and faded into the next over 0.7 s:

| Loop | Plays |
|---|---|
| `idle_sleep` | while it dozes |
| `idle_think` | while it thinks (and in the thinking mood) |
| `idle_listen` | while someone speaks to it |
| `idle_talk` | while it speaks (neutral or happy; in other moods the mood's loop) |
| `idle_<mood>` | its mood's loop: happy, sad, angry, embarrassed ... |
| `idle` | otherwise |

Each falls back to the next that exists. On top play **gestures and idle
actions**: a clip named like a motion-library gesture (`wave`, `bow`,
`stretch` ...) plays that gesture's body. The motion system stays the brain
(`24-motion.md`): it decides which gesture plays, when (words, moods, the
budget, idle time), on which parts of the body, mirrored or not, and when a
more important one takes a part over; the clip only supplies the
movement. The library's face tracks for the gesture (a laugh's smile and
closed eyes) still play; its head, body and arm tracks give way to the
clip's. A clip with no gesture of its name becomes a new gesture, asked for
by name.

**How they combine.**

- The arms take a gesture clip's pose (faded in and out), so a wave goes
  up from wherever the arms were.
- The head and body *add* a gesture clip's movement (measured from its
  first frame) to the idle loop's, so breathing, looking at the person and
  nodding carry on through a bow.
- The stage's own turns go on top of the loop: where it looks, head turns
  and nods from speech and listening, a shrug's shoulders, the body's turns
  at a little over half strength. The clips breathe themselves, so the
  controls' breathing is left out.
- Gestures without a clip (nods, shakes, the owner's own gestures from the
  editor) still play through the controls: the arms follow them while such
  a gesture holds them.
- A mood with its own idle loop leaves the posture to it (a sad loop's
  slump is not slumped again); the face still shows the mood.
- Expressions in a clip (a smile, a custom one) are added; blinking,
  looking and the mouth's shapes stay the stage's, so speech always shows.

**Fitting any avatar.** A clip's keys become normalised humanoid turns,
`n = P x q x W^-1` (P the bone's parent's turn at rest, W its own), the
same space the light style poses in, so the avatar's own bone axes never
matter; a clip made on a skeleton facing the other way is turned round;
the hips' movement is kept in hips heights and scaled to the avatar's legs,
held near where it stands (a clip that walks stays in place on the
screen); bones the avatar lacks pass their turns to the bones below them.
Clips are resampled to 30 frames a second, a bone that holds still keeps
one value, and idle loops are joined end to start. Files of at most 16 MB
and 2 minutes; an avatar's clips may take at most 24 MB.

## The built-in animations (`stage/motions/vrma`)

Thirty-four clips, written from scratch for this project with
`stage/tools/vrma_kit.py` and `stage/tools/vrma_library.py` (no
third-party motion). The kit poses a reference skeleton (a 1.55 m VRoid
girl in VRM's T-pose) the way an animator describes a pose: the hips moved,
the pelvis, torso and head turned, shoulders raised, each **hand placed**
where it should be (relative to the shoulder, the chest, the hips or the
floor; the elbow finds its own place, bending towards a given side, and the
palm faces a given way), the fingers curled, and the **feet planted**
(the knees bend as the hips move). Keys are eased, and breathing, sways
and beats are layered on; the kit writes standard `.vrma` files.

| Kind | Clips |
|---|---|
| Idle loops | `idle` (weight drifting foot to foot), `idle_happy` (hands behind the back, rocking in time), `idle_sad` (slumped, a sigh), `idle_angry` (fists, shoulders up, a foot tapping), `idle_embarrassed` (hands together, toes in), `idle_listen` (leaning in, head tilted), `idle_think` (hand to the chin), `idle_talk` (hands moving with the words), `idle_sleep` (dozing on its feet) |
| Gestures | `wave`, `greet` (a small hello), `bow`, `bounce`, `laugh`, `sigh`, `sway`, `lean_in`, `lean_back`, `surprise`, `think`, `hands_on_hips`, `shrug` |
| Idle actions | `stretch` (arms over the head), `look_around`, `look_out_window`, `yawn` (hand over the mouth), `hum_sway`, `settle`, `nod_off`, `wake` |
| Turning | `turn_around` (round on the spot to show the back, a peek back over the shoulder, round again), `twirl` (a happy spin, arms flaring; happy moods sometimes choose it), `turn_aside` (side-on a while, then back), `look_back` (over the shoulder, head first, then the body) |

The turning clips **step**: the feet lift and set down in turn (the kit's
stepping helper), with the knees following the way the body faces, so the
avatar never spins on a fixed pair of feet. Each is also played mirrored.
The first three are gestures only full-body animation can play (`clip_only` in
`stage/motions/library.json`): a 2D character has no back to show, so it
never asks for them, and the model is offered them only when the avatar
can. `look_back` also plays from the controls (2026-11-21), so a light-style
avatar looks back too: the eyes first, then the wide head turn to about 77
degrees with the shoulders following; a flat character looks away instead.
A new idle action for 3D avatars, `look_over_shoulder`, does the same now and
then when nobody is talking.

Head-only gestures (nods, shakes, tilts, glances) stay with the motion
library, which times them to speech. The device installs the clips beside
the stage (`/opt/companion/share/vrma`); together they are 2.0 MB.

`stage/tools/vrma_sheets.py AVATAR.vrm OUT` plays every clip on an avatar
with the real stage and saves sheets of frames (and GIFs with `--gif`).

## The owner's own animations (`core/companion/animations.py`)

Under the character, **3D animation** on the settings page:

- **Style** (full body or light), **Show** (head to thighs, head and
  shoulders, the whole body) and **Simplify the drawing**.
- **Its own animations**: add a `.vrma` file as an idle loop (any of the
  table above), *instead of* a built-in gesture or idle action, or as a
  **new gesture** with a name and a few words saying what it means (the
  language model is told, as for gestures made in the gesture editor).
  **Try** plays it once on the screen; **Remove** takes it away.
- **Recorded with a phone**: the Animation tab's phone recording records a 3D character's whole body and
  keeps it here as a new gesture (or in place of one), with only the parts of the body the camera saw
  (`26-animation-director.md`, "The whole body, for 3D avatars").

Files are checked first: a glTF binary with the VRM Animation extension
and a hips bone, its data inside the one file, at most 16 MB and 2 minutes,
and no API key in its text. They are kept in
`characters/<id>/animations/` on the device, beside the character's other
device choices: never written into its package, kept when it is
reinstalled. The core writes the character's choices to
`/var/lib/companion/current-stage.json`, which the stage reads when it
starts (`--settings`): the style, the clip folders (the built-in ones,
then the character's own, which win), the drawing's cost and the shot.

## Failsafes for heavy avatars

Avatars vary a lot: VRoid Studio's have 30,000 to 70,000 triangles, while
some made for VRChat have several hundred thousand and a dozen 4K
textures. On a 1 GB board, the stage's service may use 300 MB in all.

1. **At import** (`vrm.fit`) the device works out what the avatar would
   cost: points, triangles, textures as drawn, and the stage's memory for it
   at most (the program, the file while it loads, its meshes and the
   processor's copy of them while they go to the graphics chip, its face
   shapes, its textures). Points that several parts share are counted once,
   as the stage keeps them. Textures are scaled to the device's size, and to
   512 pixels if it would not fit otherwise. An avatar with more than
   400,000 triangles or 300,000 points, or still over 260 MB, is refused,
   saying why and what to export instead (VRoid Studio's polygon reduction
   and texture atlas size; Blender's Decimate). One that fits but is heavy
   (over 240 MB or 120,000 triangles) starts with its drawing kept light.
   The settings page shows the figures.
2. **When the stage loads it**, textures are kept within a budget (96 MB on
   1 GB boards, 160 MB above), halving their size until they fit. **Lite**
   (the owner's switch, or set by the device) draws textures at 512 pixels,
   leaves out outlines and moves hair and clothes at half rate.
3. **While it runs**, a watchdog times the frames. Under 20 a second for two
   3-second spells in a row, it gives something up, one step at a time:
   outlines, then hair and clothes at half rate, then drawing at three
   quarters and then half resolution, scaled up to the screen. Each step is
   reported, and noted on the settings page.
4. **If the stage stops** with a 3D avatar (it crashes, runs out of memory,
   or never starts), the core shows it again drawn lightly; if it fails
   again, it shows a card saying the avatar is too detailed for this
   device, with what to do.

## Drawing (`stage/src/vrm/render.rs`)

OpenGL ES 3.0, the same context the 2D drawings use (with a depth buffer
added for 3D).

- **Skinning on the graphics chip.** Each skin's joint matrices are sent as
  a small texture every frame, so avatars with hundreds of bones (the
  samples have 101 to 357) work on chips that allow few uniforms.
- **Points shared between parts.** VRoid Studio and UniVRM 0.x exports give
  every part of a mesh (skin, clothes, hair) the same set of points, each
  part drawing its own triangles from it. The stage keeps one copy of each
  set on the graphics chip and shapes it once a frame. Before this, each part
  had its own copy: AvatarSample_B used 5.6 times its vertex memory, and
  AvatarSample_A was refused as too detailed.
- **Face shapes on the processor**, only for meshes whose shape weights
  changed, from sparse deltas (a face's targets each touch few points).
- **MToon's toon look**: lit and shade colours split at a soft line
  (shading shift and toony), a rim of light, emission, inverted-hull
  outlines (world or screen width), cut-out and see-through materials in
  their order. VRM 0.x MToon settings are converted to 1.0's.
- **Framing**: a VRM frames itself from its bones. `--shot upper` (head to
  mid-thigh, the default) shows the face large and the arms' gestures;
  `bust` and `full` are the others (a setting under the character). The
  camera pulls back a little (at most a third) while a hand is raised out
  to the side or above the head, so a wave or a stretch stays on the
  portrait screen.
- **Lighter drawing** when asked or needed: no outlines, smaller textures,
  or the whole frame drawn at a lower resolution and scaled up.

## Memory

| Profile | For | Textures kept up to |
|---|---|---|
| L1 | 1 GB boards | 1024 pixels a side |
| L2 | 1.5 GB or more | 2048 |

The stage chooses by the board's memory (`--profile auto`); the import
does the same. Textures are scaled down **once, at import**, so the device
never decodes a huge picture again. The stage loads only the textures it
draws with, and drops the processor's copies of meshes and pictures once
the graphics chip has them.

Measured here (a desktop, software drawing at 720 x 1280; peak memory
includes the software renderer's own buffers, which a Pi's graphics chip
holds separately):

| Avatar | Format | Bones | Textures (L1) | Peak, light | Peak, full body | Peak, lite |
|---|---|---|---|---|---|---|
| Seed-san (VirtualCast) | VRM 1.0 | 132 | 33 MB | 167 MB | 169 MB | 147 MB |
| VRM1_Constraint_Twist_Sample (pixiv) | VRM 1.0 | 357 | 42 MB | 175 MB | 177 MB | 144 MB |
| Alicia Solid (Dwango) | VRM 0.x | 272 | 27 MB | 161 MB | 163 MB | 143 MB |
| AvatarSample_A (VRoid Project) | VRM 0.x, VRoid Studio 0.14 | 103 | 50 MB | 180 MB | 182 MB | 148 MB |
| AvatarSample_B (VRoid Project) | VRM 1.0, VroidStudio export | 339 | 65 MB | 202 MB | 204 MB | 154 MB |
| AvatarSample_C (VRoid Project) | VRM 0.x, VRoid Studio 0.14 | 101 | 44 MB | 175 MB | 176 MB | 147 MB |

All are under the stage's 300 MB limit (measured 2026-11-19, after points
shared between parts were kept once; AvatarSample_B fell from 216 MB). The
full-body style adds 1 to 2 MB (the clips); lite saves 18 to 50 MB. Posing (clips, bones, springs, face)
takes 0.06 ms a frame in either style; drawing is the cost. Frame rates on a Pi are
still to be measured (`15-hardware-test-plan.md`); compressed textures
(ETC2, which every OpenGL ES 3 chip reads) would cut texture memory by
four if needed.

## Import (`core/companion/vrm.py`, `package.from_vrm`)

Upload a `.vrm` on **Characters → Add a character**. The device:

1. checks it: a glTF binary with VRM information (1.0 or 0.x), the fifteen
   bones VRM requires, its data in the one file, at most 160 MB, and no
   API key in its text;
2. works out what it would cost the device and scales its textures down to
   fit (PNG stays PNG, JPEG stays JPEG), re-packing the file, or refuses it
   (see Failsafes);
3. reads its maker's terms (who may use it, commercial use, the licence
   address) into the package. The settings page shows them under the
   character, with a warning if only its maker may use it;
4. uses its own thumbnail as the preview, and makes a first feelings map
   (happy, sad, angry and surprised to the expressions of the same names;
   embarrassed and thinking to expressions whose names suggest them),
   which the owner can change in Feelings;
5. installs it as a character like any other, with the usual persona
   template to fill in.

The terms are shown, not enforced. As with Live2D-format models, the
person who imports an avatar is responsible for having the right to use it
(`28-user-guide.md`). User-uploaded avatars give us no right to
redistribute them.

## Tested

- `stage/src/vrm/tests.rs` (12 tests, on a stick-figure avatar built in the
  test, in both VRM versions):
  - reading both versions;
  - facing the viewer with the arms down;
  - turn and nod directions;
  - the wave and the hand on the hip;
  - blinking and mouth shapes;
  - an expression that blocks blinking;
  - the eyes' range;
  - hair that swings but keeps its length and stays out of the head;
  - refusing damaged files;
  - sparse and strided data.
- `core/tests/test_vrm.py` and the upload and display tests (on a made-up
  avatar):
  - packaging both versions;
  - shrinking textures while keeping the mesh data;
  - refusing missing bones, external data and keys;
  - licence notes;
  - expressions and feelings;
  - the device showing the VRM drawing;
  - uploading on the settings page.
- Six real avatars (above), played through all 19 gestures, 11 idle
  actions, 7 moods and the states with `stage/tools/clip_sheets.py`, which
  now takes `.vrm` files. All 37 clips play on all of them with no stand-ins,
  because a VRM offers every control. Brows move only on VroidStudio faces;
  other avatars' brows stay still, since VRM has no standard brow
  expression. The sample models stay out of the repository.
- The full conversation check (`core/tests/stack_check.py`, real core and
  stage, the on-device voice) on two (VRoid 1.0 and Alicia 0.x): every
  mood in order, the asked-for wave and nod, looking up as the person
  starts speaking, nodding in their pauses, gestures with the words, idle
  actions alone. Repeated in the full-body style on AvatarSample_B
  (`stack_check.py ... --vrm-animation full`): the same, with the wave,
  bounce, think and surprise played from their clips and the listening,
  thinking and talking loops in turn.
- Full-body animation: `stage/src/vrm/anim.rs` (reading clips on skeletons
  with their own rest turns, facing either way; hips; expressions;
  mirroring; loops), `motion.rs` (gestures from clips last as long as their
  clip, keep their face tracks, hold the right channels; new gestures from
  clips; a mood's posture left to its loop), the watchdog's steps, the
  `try_animation` command. `core/tests/test_animations.py` (checking clips,
  the built-in ones too; roles; the style; the settings file; reinstalling;
  the crash fallback) and the web test of the 3D animation section.
  Every built-in clip on all six avatars with `vrma_sheets.py`: hands land
  where they should (on the hips, at the chin, over the mouth, palms up in
  a shrug) on avatars from 101 to 357 bones, both VRM versions. The four
  turning clips on three (AvatarSample_A and B, Alicia), as GIFs: the feet
  step, the knees follow, and the avatar ends where it began.
- Points shared between parts: `stage/src/vrm/tests.rs` (parts that share
  points share them on the graphics chip) and the import's estimate counting
  each set once (`test_vrm.py`).
- Failsafes: import refuses too-detailed and too-large avatars and shrinks
  textures further when that is enough (`test_vrm.py`); lite and half
  resolution drawn and compared by eye.
- The stage builds for the Pi (aarch64).

## Not done yet

- **On a Pi**: frame rate, memory and loading time, per the test plan.
- **Foot contact at run time**: clips plant the feet on the reference
  skeleton; on avatars with very different legs the feet can slide a
  little (out of shot in the default framing).
- **Hands that touch the body** (on the hips, at the chin) are placed for
  average proportions; on unusual avatars they can float or sink a little.
- **Parts of MToon left out**: matcap, UV animation, normal maps and
  outline-width textures. The look is close without them, and they cost
  memory.
- **Character Studio** taking a `.vrm` for its personality step before
  sending. Today a `.vrm` goes straight to the device's settings page.
