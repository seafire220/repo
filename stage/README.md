# stage

The program that draws the character on the device's own screen.

It loads a character, either an Inochi2D puppet (`.inp` or `.inx`) or a
Live2D-format model (`.model3.json` with the files it names), keeps it alive
with breathing, blinking and small head and body movement, and moves its mouth
and changes its manner on command from the core service. It tells the two
formats apart by the file's contents, so the device's link can have any name.
It holds no API keys and makes no network connections.

Status: works on a virtual display with software graphics, in both OpenGL ES
and desktop OpenGL modes, and cross-builds for 64-bit Raspberry Pi OS.
**Not yet run on a Raspberry Pi or any real graphics chip**, so its frame
rate there is unknown.

## Build

```
cargo build --release
```

Needs Rust 1.85 or newer (tested with 1.97). The Inox2D renderer and
Ayagami's core (which plays Live2D-format models) are kept in `vendor/`, with
our changes and their exact versions listed in `vendor/PATCHES.md`; nothing but
ordinary crates is fetched at build time. For the Raspberry Pi, see
`.cargo/config.toml` (`cargo build --release --target aarch64-unknown-linux-gnu`).

## Run

```
target/release/stage character.inp --fullscreen
target/release/stage model/model.model3.json --size 720x1280 --socket /run/stage.sock
```

`stage --help` lists all options. `--gl es` forces OpenGL ES, which is what
ARM boards provide; the default tries OpenGL ES first and falls back to
desktop OpenGL.

## Commands

One JSON object per line, on standard input or the local socket:

| Command | Effect |
|---|---|
| `{"state": "idle"}` | Also `listening`, `thinking`, `speaking`. Changes posture and how lively the idle movement is. |
| `{"mouth": 0.7}` | Mouth openness from 0 to 1. The core sends the loudness of the voice being played, about 50 times a second. |
| `{"look": [0.2, -0.1]}` | Turn the head; each axis from -1 to 1. |
| `{"blink": true}` | Blink now. |
| `{"set": {"ParamCheek": 1}, "ease": 0.3}` | Drive named puppet parameters directly, blending in over `ease` seconds (default 0.25). Used for expressions and previews. A value can be a number, `[x, y]`, or `null` to hand the parameter back to the built-in behaviour. |
| `{"clear": true}` | Hand every directly set parameter back to the built-in behaviour. |
| `{"emotion": "happy", "intensity": 0.8}` | Change mood: how the character moves (liveliness, strength of its beats with speech), its resting posture and face, and the gestures it makes by itself. Blends in over about half a second. Moods: neutral, happy, sad, angry, surprised, embarrassed, thinking. |
| `{"gesture": "wave"}` | Play a gesture from `motions/library.json` on top of whatever the character is doing (`intensity` optional). Only gestures the character has the parameters for are accepted; the `ready` event lists them. It takes over the parts of the body it needs from any gesture the character started by itself; if another gesture asked for is using them, it waits for that one (two can wait). Gestures on different parts of the body play at once. |
| `{"perform": {"syllables": "eaoe", "events": [[0, "nod", 0.7]]}}` | A sentence starts: the vowel of each syllable (`a`, `e`, `i`, `o`, `u`), which shapes the mouth, and the gestures its words call for, each with the syllable it starts on and a strength. |
| `{"syllable": 2}` | Syllable 2 of the sentence is being heard: the mouth takes its shape, and the gestures due by then start, if the parts of the body they need are free of gestures asked for and the budget allows (see below). |
| `{"random_gestures": false}` | Stop (or allow) gestures the mood starts by itself: now and then while speaking or idling, and small nods and tilts while listening. |
| `{"clock": 21.5}` | The local time of day, in hours: livelier in the morning, sleepy at night (dozing after a long quiet spell). Without it the stage assumes early afternoon. |
| `{"poke": true}` | Someone is there (the button was pressed): a dozing character wakes, one alone a long while greets them. |
| `{"heard": 0.4}` | How loud the person speaking is (0 to 1, against the room's noise), sent every 20 ms while the character listens: it looks up as they start and nods in their pauses. |
| `{"temperament": {"energy": 1.2, "expressiveness": 0.8}}` | How lively and how expressive the character is (1 usual, 0.4 to 1.6). |
| `{"gestures_off": ["wave", "yawn"]}` | Gestures and idle actions the character never starts by itself (asked for, they still play). |
| `{"define": {"name": "little_dance", "kind": "gesture", "needs": ["head_roll"], "tracks": {...}}}` | Add a gesture, idle action or reaction to the library, or change one, as the library writes it. |
| `{"undefine": "little_dance"}` | Put a changed clip back as built, or take an added one away. |
| `{"describe": true}` | Send the whole library (a `library` event with `clips`). |
| `{"overlay": {"slot": "caption", "image": "caption-3.png", "anchor": "bottom"}}` | Show a picture from the overlay folder (`--overlays`, by default `overlays` beside the socket) over the character, in a slot (one picture each, fading in and out); `"image": null` fades it out. Anchors: `top`, `bottom`, `center`, `top_left`, `top_right`, `bottom_left`, `bottom_right`. The core draws captions, the microphone-off sign and hints this way (`src/overlay.rs`) |
| `{"screen": "dim"}` | `on`, `dim` or `off`: a dark veil over everything; `off` also stops moving and drawing the character, to save power |
| `{"model_motions": 0.5}` | How much of a Live2D-format model's own idle motions shows, 0 (off) to 1 (as made); eased in. Also `--model-motions` and the settings file's `model_motions` (`src/live2d_motion.rs`) |
| `{"live": {"head_yaw": 0.3, "smile": 0.6}}` | A person's movement, live (a phone tracking them): the named controls (the motion library's, in its units) follow, smoothly between updates, over the character's own movement; the rest keep its own. It starts no gestures or idle actions by itself meanwhile. `null`, or half a second without an update, hands the controls back over a third of a second (`src/live.rs`) |
| `{"quit": true}` | Exit. |

Several fields can share one line.

## Events

One JSON object per line, on standard output and to socket clients:

| Event | Meaning |
|---|---|
| `ready` | Character loaded. Gives its format (`inochi2d` or `live2d`), its parameters, which controls were connected to which parameters, the moods, the gestures it can make (with a stand-in where needed) and when each lands (`strokes`), its idle actions, and the OpenGL version in use. |
| `features` | Live2D-format models only, after `ready`: how many meshes use masks (and inverted masks), culling, additive, multiplicative and newer blend modes, parts drawn off screen, whether there is physics, and the canvas size. |
| `warning` | Something that does not stop the character playing, such as a physics file that cannot be read. |
| `emotion`, `gesture` | A mood took effect; a gesture started, with its cause (`command`; `queued` for one asked for that waited its turn; `speech` for one the words called for; `emotion` for a mood's entry gesture; or `mood` for one it started by itself). |
| `library` | After `define`, `undefine` or `describe`: the gestures and idle actions it can make and their strokes; with `describe`, every clip as the library writes it, marked built in, changed or added, whether this character can make it and with which stand-in. |
| `gesture_waiting`, `gesture_skipped` | A gesture asked for waits for another asked for on the same part of the body; a gesture was not played, with its cause and why (the body part was busy with something that matters more, or the budget for the minute was used). |
| `state` | The state changed. |
| `heartbeat` | Once a second: frames per second, the state, the mood, the gestures playing, which clip holds each channel, this minute's budget used, the drives (hour, energy, attention, boredom, sleepiness, dozing), and the average milliseconds per frame spent moving the character (`update_ms`) and handing its drawing to the graphics chip (`draw_ms`). The supervisor restarts the stage if these stop. |
| `error` | A command was not understood. |
| `fatal` | The stage cannot run; it exits with a non-zero status. |

## Gestures at the same time

The body is split into channels: head, torso, eyes, brows, expression
(smile, smiling eyes, blush), left arm, right arm, mouth and eyelids. A gesture holds the
channels it moves strongly (0.25 or more on any of their controls, or as
listed under `"holds"` in the library), so others can play on the rest at
the same time: a nod plays through a wave. Who started a gesture decides
who gets a busy channel: a reaction (looking up, waking, greeting) over one
asked for (a `gesture` command) over one the words call for, a new mood
brings or listening calls for, over one the mood starts by itself, over an
idle action.
A gesture that takes a channel over gets it with a quarter-second fade;
the other carries on with the channels it still holds, or ends if it has
none left. Where a gesture holds a channel, the living motion there
(drifting, breathing, the beats with speech) gives way to it by 70%,
eased in and out.

The gestures the character starts by itself are kept to 10 a minute, big
ones (marked `"big": true` in the library: wave, laugh, shrug, bow, lean
in, hands on hips) to 2, and those the mood starts with nothing said to 6.
The mood also prefers gestures it has not made lately (small ones for 20
seconds, big ones for a minute), and rarely repeats its last one. Gestures
asked for always play. Idle actions have their own budget, 4 a minute.

Each clip varies a little every time (speed, size unless its poses are
`"exact"`, and which of its `"takes"`), may play `"mirror"`ed (and does
when only that side can move), and names a stand-in (`"instead"`) for
characters that cannot make it. `docs/26-animation-director.md` describes
the whole system: the director's drives and idle life (`src/director.rs`),
listening, speech, and temperament.

## Characters it accepts

**Inochi2D puppets** whose nodes are plain nodes, parts, composites and
simple physics. Files using other node types (for example mesh groups) are
refused with a clear message rather than shown half-working.

**Live2D-format models**: a `.model3.json` and the `.moc3`, textures and
physics it names, read only from the model's own folder (the link to it is
followed first). Ayagami's core (`vendor/ayagami`) works out each frame;
`src/live2d.rs` draws it with OpenGL ES 3: premultiplied, mipmapped
textures; clipping masks, also inverted ones; Cubism's additive and
multiplicative blends; the newer blend modes, worked out in the shader
against a copy of what is already drawn; parts drawn off screen first; and
back-face culling. Textures larger than the graphics chip allows are scaled
down. The model's own expressions show with the moods (*Feelings*), its pose
file's parts take turns, and its own **idle motions** (`.motion3.json`, its
`Idle` group) play underneath the stage's movement, added to it, crossfading
from one to the next (`src/live2d_motion.rs`; blinks and the mouth stay the
stage's). Its other motions are not played. `--view` is in canvas pixels for
these models, and `--no-physics` stops hair and clothes swinging (for
comparisons).

**VRM avatars** (`.vrm`): see `docs/30-vrm.md`. They also take the **wide
head turn** (`head_turn`, up to 90 degrees) that flat characters make as an
ordinary turn.

Controls are connected automatically when the puppet uses conventional
parameter names, either Inochi2D style (`Mouth:: Shape`, `Eye:: Left:: Blink`,
`Head:: Yaw-Pitch`, `Breath`, ...) or Live2D style (`ParamMouthOpenY`,
`ParamEyeLOpen`, `ParamAngleX`, ...). For other names, pass `--map file.json`:

```json
{
  "mouth_open": {"param": "Kuchi", "axis": "y"},
  "blink_left": {"param": "MeL", "invert": true},
  "head_roll": null
}
```

## Tests

```
cargo test --release                       # logic: 79 tests
python3 tools/visual_test.py target/release/stage PUPPET.inp out/ --gl es
python3 tools/compare_formats.py target/release/stage UNPACKED_CHARACTER/ out.png
```

`compare_formats.py` draws a character's Inochi2D puppet and its
Live2D-format copy (`make_character.py --live2d`) in thirteen poses and
reports how well they overlap; the studio's end-to-end test runs it on the
synthetic test figure and requires 0.98 or more. The Live2D drawing was also
checked against Ayagami's own renderer on Live2D's eight sample models, which
between them use every drawing feature above (overlap 0.970 to 0.9997 in
seven poses each; nearly all remaining differences are outline pixels, from
filtering textures in sRGB space as Cubism's OpenGL renderer does). The
sample models are not stored here.

The visual test needs `Xvfb`, Pillow and a rigged puppet with conventional
parameter names. It passes with a hand-rigged model (the "Aka" example from
`Inochi2D/example-models`, by seagetch, CC BY 4.0, distributed through Git
LFS and not stored here) and with puppets produced by the image2live2d
rigger, including its built-in sample (`python -m image2live2d --sample
--fullbody -o sample.inp`). It checks that the
character is drawn and framed, that opening the mouth and blinking each
change only a small area in the right place, and that the picture returns to
rest afterwards.

Those checks have passed puppets that looked wrong, so they are not evidence
of quality. For judging by eye, `tools/pose_sheet.py STAGE PUPPET OUT_DIR`
renders the head close up (using `--view`) and the whole figure in nine
poses (rest, turn, nod, tilt, blink, mouth open) into `sheet.png`, and a
sweep through turn, nod and tilt into `motion.gif`.
