# Animation: how the character moves

Status: 2026-11-19. The whole design this document set out (the director,
seven steps) is built. This document describes the system as it is. The
build order at the end records what each step was checked with.

Code: in the stage (Rust): `stage/src/behaviour.rs` (living motion),
`motion.rs` (moods, gestures, channels, budget, the library),
`director.rs` (drives, idle life, listening, reactions), `speech.rs`
(mouth shapes, movements on their syllables) and `mapping.rs`. The library
is `stage/motions/library.json`. In the core (Python): `conversation.py`,
`performance.py` (the speech plan and syllable timing), `speech_text.py`
(tags), `temperament.py` and `motions.py`. In the studio:
`record_motion.py` (gestures from video). `24-motion.md` covers the
generic controls and fitting characters to them.

## Two programs

The **core** runs the conversation and decides what the character feels,
says and wants to do. The **stage** draws the character about 60 times a
second and decides how it moves on each frame. They talk over a local
socket in short JSON lines (`stage/README.md` lists them all), such as
`{"emotion": "happy"}`, `{"gesture": "wave"}`, `{"mouth": 0.42}` and
`{"syllable": 3}`. Moving the character costs a fraction of a millisecond
a frame, next to nothing beside drawing it.

## Every frame, in layers

1. **Living motion** (`behaviour.rs`). The character breathes, blinks at
   natural uneven gaps, moves its eyes by what it is doing (looking about
   when alone, at you when listening, up and away when thinking, mostly at
   you when speaking), and its posture drifts. Everything passes through
   springs, so nothing jerks. While listening it leans in a little.
2. **Mood** (`motion.rs`). Seven moods follow the conversation's feelings.
   Each sets a resting face and posture, how lively the living motion is,
   how strong the beats with speech are, and which gestures it makes by
   itself. A change of mood blends in over half a second.
3. **Drowsiness** (from the director, below): heavy eyelids, slower
   breathing and blinking, the head dropping.
4. **Idle actions** when nobody is talking: a look around, gazing out of
   the window, a glance at you, settling, a contented sway, a stretch, a
   yawn, nodding off; on 3D avatars, a look over the shoulder (the wide
   head turn, below).
5. **Speech**: the mouth's opening and shape, beats on stressed syllables,
   and movements tied to words.
6. **Gestures**: nineteen clips the conversation can ask for, the words
   call for, or the mood starts (nod, firm nod, shake, tilt, bow, bounce,
   laugh, sigh, sway, look away, look down, lean in, lean back, start in
   surprise, think, hands on hips, wave, raised brows, shrug), plus any
   added on the device.
   **The wide head turn** (`head_turn`, 2026-11-21) is a control of its own beside the ordinary head turn:
   up to 90 degrees, a profile, on a 3D avatar (its `ParamHeadTurn`). A character without a parameter for it
   turns as far as its ordinary turn goes instead, so a gesture written with it works everywhere; gestures
   that need it (`look_back`, `look_over_shoulder`) say so, and a flat character looks away instead.
7. **Reactions**: looking up when spoken to, waking with a start, raised
   brows and a smile for someone back after a long gap.
8. **A model's own expressions**, as chosen under *Feelings* on the
   settings page; **physics** for hair and clothing; and the **mapping** of
   the generic controls onto the character's own parameters.

**A Live2D model's own idle motions** (2026-11-21, `stage/src/live2d_motion.rs`) go underneath all of this.
Most models come with idle animations their artist made (`.motion3.json` files in the model's `Idle`
group). The stage reads them as Live2D's own player does (linear, Bézier, stepped and inverse-stepped
curves; fades; parts that take turns, such as two sets of arms) and plays them one after another, at random,
each crossfading into the next. Each motion's change from the model's rest pose is **added** to the layers
above, the way Live2D's player adds dragging and breathing to a motion: where the motion turns the head 8
degrees, a nod nods from there. A gesture holding part of the body takes 80% of that part back while it
plays; the motion shows 60% while the character listens, 70% while it talks, 80% while it thinks and 30%
while it dozes, eased. The device keeps blinking, the mouth and the speech shapes its own (the model's
`EyeBlink` and `LipSync` groups), so the motions' own blinks are left out. The owner chooses *as made*,
*gentler* (half) or *off* per character; characters made with Character Studio start off, since the
rigger's generic idle loop would only double the device's own living motion. Checked on Live2D's sample
model: all 1,602 curve segments of its ten motions reach their points without a jump, its nine idle
motions play in turn, and the cost per frame is too small to measure.

### Channels: several things at once

The body is split into channels: head, torso, eyes, brows, expression
(smile, smiling eyes, blush), left arm, right arm, mouth and eyelids. A
clip holds the channels it moves strongly (0.25 or more on a control, or
as its library entry lists under `"holds"`). Others play at the same time
on the rest, so a nod plays through a wave.

When two want the same channel, the one that matters more gets it:

1. a reaction;
2. a gesture asked for (a tag from the language model, or the settings
   page);
3. one the words call for, or a new mood's entry gesture;
4. one the mood starts by itself;
5. an idle action.

The one that gives way lets go of that channel with a quarter-second fade
and carries on with the rest; with nothing left, it ends. Two asked-for
gestures on the same channel take turns. Where a clip holds a channel, the
living motion there gives way by 70%, eased in and out, so the clip reads
clearly.

## The director: drives, time of day, idle life

`director.rs` keeps four drives, each from 0 to 1, rising and falling
slowly:

- **attention**: high while you talk or it talks; fading over minutes
  alone;
- **boredom**: grows through a quiet spell, full after ten minutes, gone
  the moment you speak or press the button;
- **sleepiness**: from the time of day (none by day, rising after 9 pm,
  most in the small hours), more after a long quiet spell, less while
  talking;
- **energy**: livelier in the morning, calmer in the evening, low when
  sleepy, scaled by the character's temperament.

The core tells the stage the local time when it connects and every ten
minutes, and pokes it when the button is pressed.

From the drives the director sets how lively the living motion is, how
often the character does things by itself, and which idle actions suit the
moment. Bored, it gazes out of the window and looks around more and more
often. In the morning it may stretch. At night it yawns and nods off,
looks about less, and moves less often. Idle actions mix with the mood's
own gestures. The mood's share falls as the character gets sleepier.

**Dozing.** Alone at night, sleepy, after eight quiet minutes, the
character dozes: eyes shut, slow breathing, head dropped, nothing of its
own. Spoken to, or the button pressed, it wakes with a small start; its
eyes open in a third of a second.

**Coming back.** Someone speaking or pressing the button after a quarter
of an hour alone is greeted with raised brows and a smile.

**Eyes made from a picture.** A character made from a single picture
swaps an open eye drawing for a closed one, so half-closed eyes show both.
For these characters sleepiness shows as longer, more frequent blinks.
Their gestures' eyelids are open or shut, passing quickly between.
Live2D-format models keep a drawn eyelid's droop.

## Listening

While the character listens (or records, with push-to-talk), the core
sends the loudness of the person's voice against the room's noise, every
20 ms. The director follows it:

- when the person starts speaking after 1.5 s or more of quiet, the
  character looks up at them;
- in a pause of a quarter-second or more, after at least 0.8 s of speech,
  it responds now and then: mostly a nod, sometimes raised brows or a tilt.
  It never responds while they speak, and at most once every 2.5 s. How
  often (55% of such pauses for a usual character) follows how expressive
  the character is;
- it matches their energy a little: livelier when they sound animated.

While it hears the person, its own random gestures give way to these
responses. Without a microphone it still nods and tilts now and then by
itself.

## While it talks

**How far the mouth opens** follows the loudness of the voice, sent every
20 ms and timed to when the sound is heard. The mouth opens in about 35 ms
and closes in about 70 ms.

**The mouth's shape** follows the syllables. Before a sentence plays, the
core splits it into syllables, each with its vowel from espeak-ng's
phonemes (or the spelling). There are five shapes: open "a", half-open
"e", wide "i", round "o", small round "u". The stage scales the opening
by the vowel (an "i" opens 60% as far as an "a") and widens or rounds the
mouth on the mouth-shape control. Models with a parameter per vowel
(ParamA to ParamO, or ParamMouthA to ParamMouthO) also get the vowel being
said, as far as the mouth is open.

**Where each syllable is heard** comes from counting phonemes. In a voice's
speech each phoneme lasts about as long as any other (35 to 45 ms in the
device's voice), so a syllable's place among the sentence's phonemes is
close to its place in time. Pauses at commas and full stops anchor the
count:

- the device's own voice makes each sentence whole: the syllables are
  spread over the time it sounds, each clause after a pause starting where
  the sound starts again;
- a cloud voice streams: its first sentence is followed by a clock that
  counts phonemes at a usual pace and is corrected at each pause. Its later
  sentences are held back until all of their sound has come, and placed
  like a whole sentence. They are held only while more than 0.4 s of
  earlier sound is still to be heard, so the voice never waits; one that
  has not all come by then streams on with the clock.

Measured against the exact timings of the device's voice
(`core/tools/measure_syllable_marks.py`: 12 sentences, 6 speakers, 3
speeds, 3,204 syllables):

| How the sound comes | Vowels within 0.1 s | Median error | 90% within |
|---|---|---|---|
| Whole (the device's voice; a cloud voice's held sentences) | 79% (87% at the voice's usual speaker and speed) | 0.05 s | 0.15 s |
| Streamed (a cloud voice's first sentence) | 39% | 0.15 s | 0.58 s |
| Streamed, the first method (loudness peaks) | 27% | 0.21 s | 0.80 s |

Pinning syllables to loudness peaks was tried and measured worse. In
running speech the loudness does not rise once per syllable, so one extra
peak shifts every later mark. Learning each voice's pace did not help
either: one voice varies 10 to 20% from sentence to sentence.

**Beats.** A sudden rise in loudness (a stressed syllable) brings a small
quick nod, raised brows on strong ones, and a small accent of one arm,
left and right in turn. The mood scales them.

**Movements with the words.** The core plans a few movements from each
sentence's words, with no extra call to the language model:

| Words | Movement | Chance |
|---|---|---|
| A sentence starting "yes", "sure", "exactly"; "no problem" | nod | 90% |
| "no", "not", "never", "don't" (not in a question, not "no problem") | small head shake | 75% |
| A question | tilt on its last word | 65% |
| "maybe", "I think", "not sure" | tilt, or sometimes a shrug | 60% |
| "hello", "hi" at the start of the first sentence | wave | 85% |
| "bye", "good night", "see you" | wave | 80% |
| "haha", "hehe", "lol" | laugh | 90% |
| "thanks", "thank you" | nod | 70% |
| "sorry", "unfortunately" | look down | 60% |
| "really", "amazing", "love", a word in capitals | brows raised | 55% |
| "first", "next", "finally" (two or more) | a small nod on each | 80% |
| A sentence ending "!" | small nod at its end | 40% |

The chances scale with how expressive the character is. Each movement
starts early enough to land on its word: the stage reports when each
gesture lands (its stroke), and the core starts it that many syllables
ahead, at 0.18 s a syllable (the measured median of the device's voice).
Each sentence gets at most two, or one if the language model asked for a
gesture. At most one is big (a wave, laugh, shrug, bow, lean in, hands on
hips), and after a big one the next two sentences get none except a laugh.

**Feelings in step with the words.** The language model puts a feeling
tag such as `[happy]` before a sentence; each sentence's feeling is sent
as its sound starts. After a reply the feeling lingers 6 s, then calms.

## How gestures are chosen

- **Asked for**: the language model is told the gestures this character
  can make (with a stand-in, below) and not switched off, and to use them
  sparingly. A tag plays as its sentence starts.
- **A new mood's entry gesture**: happy bounces, surprised starts,
  embarrassed looks away, thinking looks up.
- **The words** (above), and **listening** (above).
- **The mood by itself**, when nothing is playing, at random intervals
  (happy every 4 to 8 s, sad every 8 to 15 s), less often when idle or
  low in energy.
- **Idle actions** when nobody is talking (the director, above).
- **Reactions** (the director, above).

**Variety.** The mood prefers gestures it has not made lately (small ones
for 20 s, big ones for a minute), and rarely repeats its last pick (it does
8% of the time; it did 38% before). Each clip also varies when it plays
(below).

**The budget.** The gestures the character starts by itself (all but
those asked for, and reactions) are kept to 10 a minute, 2 of them big, 6
from the mood alone. Idle actions have their own budget of 4 a minute.
Asked-for gestures always play.

## The clips

Each gesture, idle action and reaction is a clip in the library: tracks of
keys (seconds, value) on the generic controls, each starting and ending at
0. A clip may also have:

- **takes**: other ways of playing it, one chosen at random (a double or a
  single nod; a laugh or a short chuckle);
- variation: every time, 0.88 to 1.12 times its speed and 0.85 to 1.1
  times its size, unless its poses are **exact** (the wave and hands on
  hips, whose arm positions are worked out per character);
- **mirror**: it plays mirrored at random (a tilt either way), and does so
  when only that side can move. A character whose right arm stays still
  waves with its left;
- **instead**: what plays on a character that cannot make it. With no
  arms, a wave becomes raised brows, a smile and a tilt. With no elbows,
  hands on hips becomes a proud lean back. With no brows, raised brows
  become a nod. Such gestures are still offered to the language model;
- **big**, **holds** and **stroke** (when it lands), worked out from its
  tracks unless given.

## Per character

- **Movement settings** (settings page, under the character): which of the
  character's own parameters each movement uses, reversed or off, tested
  live.
- **Temperament**: calm to lively, reserved to expressive, two sliders.
  It starts from the personality text. Shy, quiet, reserved or calm lower
  it; bubbly, energetic or dramatic raise it. After that it is the owner's.
  Lively: livelier idle motion, gestures sooner. Expressive: bigger
  gestures, more responses while listening, more movements with the words,
  more stretching and swaying. Through the same ten minutes, a lively,
  expressive character makes 1.4 times or more the gestures and movement of
  a calm, reserved one.
- **Gestures and idle actions it makes by itself**: any can be switched off
  for a character. The stage never starts it by itself and the language
  model is not told of it; asked for or tried, it still plays.

## On the settings page: the Animation tab

- **What the character is doing**, live: its state, feeling and time of
  day, whether it dozes; its drives as bars; which clip holds each part of
  its body; this minute's budget against its limits; its frame rate; and
  the latest gestures with what started each ("the words", "listening to
  you", "its mood", "a reaction", "asked for"), stand-ins ("wave, as
  greet"), and those skipped with why ("enough gestures this minute",
  "a gesture that matters more is using that part of the body").
- **Change or add a gesture**: pick any clip (marked built in, changed or
  added; and whether this character can make it, or with which stand-in),
  edit its tracks as keys with a graph, **try it on the device's screen**,
  save it, put a changed one back as built, or make a new one from what the
  editor shows. A new gesture says what it means ("do a little dance"), and
  the language model may then ask for it. Saved clips stay on the device
  for every character (`config/custom-motions.json`). They are checked as
  the stage checks its own library and given to the stage whenever it
  starts.

## Gestures from video

`studio/record_motion.py` makes a clip from a video of a person (yourself,
or someone who agreed) doing the gesture once. MediaPipe's face and body
landmark models run on the computer and read:

- the head's turn, nod and tilt;
- the gaze, brows, smile, smiling eyes and mouth;
- the shoulders and the body's tilt;
- each arm at the shoulder and the elbow, measured in the body's own frame,
  so leaning does not move them.

Leaning towards or away from the camera is not read; one camera's depth
proved too unsure. The readings are:

- smoothed with the 1-euro filter;
- measured from the pose at the start;
- eased in and out;
- cut down to the keys that matter.

The result is printed, saved to a file, or sent to the device (`--send`),
where it shows on the Animation tab. The character moves as the video
shows, side for side.

The conversion (landmarks to controls, then the clip) is the device's own
`core/companion/mocap.py`, shared by both ways of recording.

## Gestures from a phone

The owner can record a gesture with a phone, with no app and no computer
(`core/companion/phone_record.py`, the page `console/record.html` and
`record.js`):

1. **The link.** *Record a gesture with your phone* on the Animation tab
   asks for one (`POST /api/record`, logged in). The device makes a
   one-time code of six letters and digits, without the ones easily
   confused (0, O, 1, I, L), and shows a QR code of
   `http://<its Wi-Fi address>/r/<code>`, the link itself, and the short
   address `/r` where the code can be typed instead. The Wi-Fi address
   comes first because the settings page may be open over USB, which a
   phone cannot reach. One link at a time; a new one ends the old one.
2. **No login on the phone.** The code stands for one: the `/r/` pages and
   requests need it and nothing else, for 30 minutes or until the owner
   presses *Stop*. After 30 wrong codes in a minute none is accepted for a
   minute, so codes cannot be found by trying them. The phone's requests
   that change anything carry `X-Companion: 1` like the settings page's,
   so a web page elsewhere cannot send them.
3. **The camera.** A page on the home network is plain `http`, not a
   secure context, so it may not use the camera directly (`getUserMedia`).
   It asks for a video instead (`<input type="file" accept="video/*"
   capture="user">`), which the phone films with its own camera app, or
   picks from its gallery.
4. **Tracking on the phone.** MediaPipe for the web (Google's
   `@mediapipe/tasks-vision` 1.2.3, Apache-2.0) with its Face Landmarker
   (with blendshapes) and Pose Landmarker (lite) runs in the phone's
   browser, on its graphics if it can (WebGL 2), else on its processor.
   The device serves the files (`/r/assets/...`, 24 MB, kept by the
   browser after the first time); only the recorder's page may run
   WebAssembly (its Content-Security-Policy adds `'wasm-unsafe-eval'`).
   The page seeks through the chosen part of the video (at most
   20 seconds) 12 times a second and keeps, for each frame, the six face
   points, the 22 blendshapes and the nine body points (with how sure the
   model is that each is in view) that `mocap.py` reads, in the order the
   device gives it (`layout` in `/r/<code>/info`). A frame is about
   0.5 KB; a 4-second gesture was 26 KB.
5. **The clip.** The device checks every number (`frames_from_payload`),
   turns the frames into controls and a clip exactly as Character Studio
   does (`mocap.frame_controls`, `clip_from_frames`), thins any track past
   the stage's 200 keys, and plays it at once as the editor's *try*
   (`_try`). A person must be found in at least half the frames.
6. **On the phone**: what the clip does in words (its main movements
   only), *Play it again*, *Swap left and right* (`mocap.mirror_tracks`,
   for a phone that saved its front camera's video mirrored), and *Film
   again*. *Keep it* saves it as a gesture with its words or as an idle
   action, through the same store as the editor
   (`config/custom-motions.json`); an existing name, built in or added,
   is replaced only when the phone confirms. The settings page follows
   along (`recording` events) and shows the kept gesture in the editor.
7. **The files.** The installer fetches the tracking into
   `models/mocap` (`python3 -m companion.mocap_assets FOLDER`): the npm
   package and the two models at fixed versions, each file checked
   against its SHA-256, all or nothing. Updates carry no models, so a
   device that got this feature through an update fetches them in the
   background when the owner first asks for a link; the phone waits,
   saying so.

The video stays on the phone. The device receives only the points, so a
phone recording costs it no video decoding or model running at all.

## When tags are missing or wrong

- **Talking never depends on tags.** The mouth, the beats and the mood's
  own gestures run whatever the model writes; the planned movements need no
  tags.
- **No feeling tag in a reply**: each sentence's feeling is guessed from
  its words ("sorry" sad, "wow" surprised, "awesome" happy, "hmm"
  thinking). Only a clear winner counts, and a tagged reply is never
  second-guessed.
- **Tags written other ways** count: `[Emotion: Happy!]`, `(smiles)`,
  `<sad>`, `*giggles softly*`, `[joyful]`, `*nervously*`, and an emoji
  (never read out). Other bracketed words are dropped, not read out.
- **Words that only look like tags are said**: "that was *really* good",
  "I'm *glad* you came".
- **A failed plan** leaves the loudness-only mouth and the beats; **a voice
  too even to show its syllables** still gets a mark every 0.3 s; **an
  older stage or core** ignores the new messages and works as before.
- **A gesture the character cannot make** plays its stand-in, or is
  skipped. **One switched off** is not offered. **An asked-for one on a
  busy part of the body** waits its turn.

## Checked

- Stage tests (79): channels (a nod through a wave leaves the arm pixel for
  pixel unchanged on the real stage), no control ever moving faster per
  frame than the steepest clip alone, gestures per minute staying in a band
  over a simulated ten-minute conversation, variety, an hour alone by day
  (varied, calm) and at night (yawning and nodding off, then dozing with
  its eyes shut), waking and greeting, listening (responses only in pauses,
  spaced, a look up after quiet), temperament, variations, mirroring,
  stand-ins, strokes, and clips defined, changed and put back at runtime.
- Core tests (145): the speech plan, syllable timing, holding back later
  sentences and streaming on when the voice is slow (no sound lost), tags
  and failsafes, temperament, switched-off gestures, the live view and the
  editor's checks.
- Studio tests (90, with the rest of the studio): the landmark maths on
  exact poses, and a video made by tilting a real photo.
- End to end (`core/tests/stack_check.py`): the real core, stage and
  device voice through listening to a scripted speaker (it looked up, then
  nodded in a pause), a tagged reply (every mood and asked-for gesture
  played, a look down on "sorry"), and time alone (an idle glance). Plus the
  settings page in a browser against the virtual device: the live view,
  temperament, switches, and trying and saving a changed gesture.
- Recording with a phone (`core/tests/test_phone_record.py`, 22 tests):
  codes (alphabet, expiry, a new one ends the old, guessing stopped), every
  malformed number a phone could send refused, the routes (owner-only
  link, no login on the phone, the header, the recorder alone allowed
  WebAssembly, only the five tracking files served, 413 past 4 MB),
  mirroring, replacing only when confirmed, and fetching the tracking all
  or nothing. End to end in headless Chromium (`core/tests/phone_record_test.cjs`,
  with any short video of a person): the settings page on a computer and a
  phone (Pixel 7) on the device's network address, so not a secure context; the real MediaPipe files, fetched by the installer's own
  fetcher (every SHA-256 matched); a 4-second test video read in 24 s on
  software graphics, sent (26 KB), played, mirrored, kept, kept again under
  the same name after confirming, the code typed in small letters, a
  stopped link refused; the QR code decoded back to the link at the size
  the page shows it.

## What it cannot do

- No closed lips for "m", "b" and "p". Placed by counting phonemes, half
  would land more than 40 ms off a 50 to 80 ms closure. Even snapped to
  dips in the loudness, only about half land on a real m, b or p, and the
  loudness already shuts the mouth on b and p.
- On characters made from a picture, the mouth-shape control lifts or
  lowers the corners, so "o" and "u" show mainly as a smaller opening.
  Their half-closed eyes are not possible (above).
- A cloud voice's first sentence is placed by the clock (39% within
  0.1 s).
- The planner knows words, not intent: a teasing "no" and a firm one get
  the same small shake.
- Clips have no wind-up, hold and retract phases; a clip that gives way
  fades out over a quarter-second.
- No startle at a sudden noise: the microphone is only read while
  listening.
- Recording from video was tried on a video made from a photo, not yet on a
  real recording of someone talking. Recording with a phone was checked in
  an emulated phone (Chromium as a Pixel 7, software graphics), not yet on
  real iPhones and Android phones; their camera apps, video formats and
  graphics drivers vary.

## How it was built

The design, in seven steps:

| Step | What | Done when | Built and checked |
|---|---|---|---|
| 1 | Mouth shapes from the voice's phonemes | The mouth rounds and closes on the right sounds, with no lag | Five vowel shapes on the openness and mouth-shape controls, and vowel parameters on models that have them; syllables placed as measured above; closed lips measured and left out |
| 2 | The speech planner, and gestures landing on their word | Strokes within 0.1 s of their word; nods on yes, shakes on no, tilts on questions | The words table above; leads from each gesture's stroke; word starts within 0.1 s 73% of the time with the device's voice |
| 3 | Channels, the scheduler and the budget | A nod plays through a wave; nothing snaps; gestures per minute stay in a band | 8 to 13 gestures a minute in ten simulated minutes, at most 2 big ones not asked for |
| 4 | Drives, idle actions, times of day | An hour of idling shows varied actions, sleepy at night | Seven or more different things in an hour by day, 1.5 to 5 a minute, rarely repeating; at night yawns and nodding off, then dozing |
| 5 | Listening and reactions | Nods in the speaker's pauses; it looks up when speech starts | Both, on the real stage and end to end with the real core |
| 6 | Clips version 2, recorded clips, the editor and the live view | A recorded clip plays; the live view shows each layer | Takes, variation, mirroring, stand-ins and strokes; `record_motion.py`; the Animation tab |
| 7 | Temperament per character | Shy and confident characters move differently with the same reply | 1.4 times or more the gestures and movement for a lively, expressive character |
