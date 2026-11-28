# User guide: your character device

Status: 2026-11-19. Written for buyers, from the software as it is today. It
has not yet been followed on a finished unit (no unit has been built, see
`29-hardware-setup.md`); correct it after the first build. Items marked
*not yet* are planned, not built.

## What it is

A small screen with a character on it that you talk to. It listens when you
press its button, answers out loud in its own voice, and moves while it
talks: its mouth follows the words, its face and body show how it feels, and
it nods, waves, tilts its head and gestures with what it says. Left alone,
it looks around, gazes out of the window, stretches and, late at night,
dozes off. It remembers what you tell it.

You choose the character: make one from a picture, bring an Inochi2D model
or another 2D rigged model, or a 3D VRM avatar you already have, or bring a
character card's personality.

## What you need

- The device and its power supply.
- Home Wi-Fi, and a phone or computer on it, to use the settings page.
- **A language model** (the character's mind): a key for a service (several
  have free allowances), or a model running on your own computer, which is
  free and keeps your words at home. See "AI services" below. The device
  comes with no keys: you add your own, and they never leave the device.
- Optional: a key for a voice service (the device has a free voice of its
  own), and a web-search key (only for models that cannot search by
  themselves; Claude can).
- To make characters from pictures: a Windows, Mac or Linux computer for
  Character Studio (see "Making one from a picture: Character Studio").

## Setting it up

1. **Plug it in.** After about 40 seconds the screen shows *Set up Wi-Fi*:
   the device's own network name, its password and a QR code.
2. **Join the device's network** with your phone (scan the QR code, or
   choose the network and type the password), then open
   `http://10.42.0.1`.
3. **Choose a password for the settings page.** Write it down; it protects
   your keys and your character's memory.
4. **Choose your home Wi-Fi** under **Device → Wi-Fi** and type its
   password. The device joins it and shows *Hello!*, its new address and a
   QR code.
5. **Put your phone back on your home Wi-Fi** and open the address on the
   screen, or `http://companion.local`.

No phone, or no Wi-Fi? See "Connecting by USB" below.
6. **Follow "Getting started"** on the settings page's Home tab: add a
   language-model key, choose a voice, add a character, then press the
   button and say hello. Each step links to where it is done. The page also
   gives the device your phone's time zone.

The settings page only answers on your home network.

## Talking

- **Press the listen button and speak.** In the usual mode it hears when
  you stop speaking and answers. You can change this under **Device →
  Listening** to *press to start, press again to send*, and set how long a
  pause ends your turn.
- **Press the button while it speaks** to stop it. With a sound card that
  cancels the device's own voice (the ReSpeaker Lite does), you can also
  turn on *Let me interrupt it by speaking*.
- **On the settings page**, the Home tab has *Start listening*, *Stop
  speaking*, a box to type a message instead of speaking, and *What is
  happening*: what it heard, what it is doing, and any problem.
- **Volume** is under **Device**.

While you speak it looks at you, nods in your pauses and looks up when you
start. While it thinks it looks away for a moment.

### Saying its name (the wake word)

Turn on **Device → Wake word**, and the character starts listening when you say **"hey"**, **"hi"** or
**"hello"** and its first name, as well as when you press the button.
- Say it all in one breath ("Hey Mika, what time is it?") and it answers straight away; or say its name,
  wait for it to look up, then ask.
- After it answers, it listens ten seconds more, so you can carry on without its name.
- You can choose other words (*the words*), and how readily it hears them.
- It listens only while the character is quiet and the microphone is on. It runs on the device: nothing
  it hears is kept or sent anywhere.

Some names are heard more easily than others. Tested with the device's own synthetic voices (`core/tools/wake_check.py`):

| Name | Heard at the usual setting | Started by everyday speech |
|---|---|---|
| "hey/hi/hello Lucy" | 96% | never, in 21 minutes of speech |
| "hey/hi/hello Oliver" | 81% | never |
| "hey/hi/hello Mika" | 66% ("hello Mika" 91%) | only by "hello Micah", which sounds the same |

Names with clear consonants work best. Short names that start with a vowel, like "Emma", are harder: if yours
is missed, set **how readily it hears them** to *more*, say "hello" rather than "hey", or choose different
words. The Home tab's *What is happening* shows each time it heard its name.

### Captions

Turn on **Device → Captions** and what the character says is written at the bottom of the screen as it
says it, a few lines at a time. It helps in a noisy room, or for anyone hard of hearing. *…and what it
heard* also shows what it heard you say, at the top. Captions come in two sizes.

### The microphone off

**Hold the listen button for two seconds** (or **Device → Microphone**) to turn the microphone off. A
crossed-out microphone shows in the corner of the screen, and the recording program is stopped: nothing
is heard, recorded or sent. Hold it for two seconds again to turn it back on. While you hold the button,
the screen says what letting go will do. It stays off after a restart.

A device can also have a **mute switch** (`29-hardware-setup.md`). While the switch is on, the
microphone stays off whatever else asks.

### Things you can ask for

- **Timers and alarms**: "Set a timer for ten minutes for the pasta",
  "Wake me at half past seven", "Cancel the pasta timer". The character
  tells you itself when one goes off, and calls again, a little louder,
  until you answer or press the button (see **Your day**). They survive
  the device being switched off and on.
- **Things to look up**: turn on **Allow web search** under **Device**.
  Claude models search the web themselves; other models need a "Web
  search" key from tavily.com (free monthly allowance), added under **AI
  services → All saved keys**.
- **Anything you would ask a friend who remembers you**: it keeps notes
  about you and can recall earlier conversations.

## Characters

Your characters are on the **Characters** tab. Choose one to put it on the
screen; the one on the screen is the one you talk to. Each has its own
personality, voice, movement and memory.

### Making one from a picture: Character Studio

Character Studio is an app for your computer (Windows, Mac or Linux), not the device, that turns one
picture into a moving character. Download it for your computer and install it like any other app
(details, and what to do about the first-time security questions, are in `14-making-characters.md`):

- **Windows**: run `CharacterStudio-Setup.exe`; it is then in the Start menu.
- **Mac**: open `CharacterStudio.dmg` and drag Character Studio to Applications.
- **Linux**: make `CharacterStudio-x86_64.AppImage` executable and double-click it.

The first time it opens, a page in your browser shows it downloading its tools (about 1 GB, a few
minutes); then the studio opens. After that:

1. Drop in a picture and give the character a name. (The very first time, it downloads its part-finding
   model too: one click, about 1.3 GB.)
2. Watch it being made; a preview of it moving appears (about a minute).
3. **Check the arms in the preview.** If an arm moves wrongly (a sleeve
   with no hand, a coat taken for an arm), untick that arm and make it
   again: it then stays still, and the character greets you instead of
   waving.
4. Say who the character is (or do it later on the device).
5. **Send to the device**: its address and the settings-page password
   (used for that one upload, not kept). Or save the `.char` file and add
   it on the **Characters** tab.

Pictures that work best:

- One character, facing you, head to at least the waist (head to toe is
  best), on a plain or transparent background.
- The face clear of hair and hands; the eyes open.
- **For arms that move**: arms hanging at the sides from the shoulder, with
  the hands showing. Hands in pockets, arms folded or raised, hands at the
  face, wide sleeves, capes and long hair over the arms all keep the arms
  still (the rest of the character still moves).

### An Inochi2D model you rigged

[Inochi Creator](https://inochi2d.com) is a free, open-source rigging program. Export your model as an `.inp` file and choose it on **Characters → Add a character**; Inochi Creator's own `.inx` project files work too.
- The device reads the model's name, artist and licence from the file.
- It works out which parameters turn the head, blink and open the mouth, from Inochi2D's standard names ("Head:: Yaw-Pitch", "Eye:: Left:: Blink", "Mouth:: Open") or the `.model3.json` format's ("ParamAngleX").
- If a movement is wrong, the **Movement** section under the character lets you choose the right parameter and test it on the screen.

### Another 2D rigged model you made or bought

The device also plays other 2D rigged models: Live2D-style models, the kind with a `.model3.json` file. On **Characters → Add a character**, choose a `.zip` of the model's folder: the folder with its `.model3.json`, as used by VTube Studio.
- The device plays the model itself (with Ayagami), with its physics (hair and clothes swaying) and its expressions.
- If the model shows alternate parts in turn (two sets of arms, say), its pose file is respected. The device
works out which of its settings turn the head, blink, open the mouth and so
on. If something moves wrongly, the **Movement** section under the
character lets you choose the right setting for each movement and test it
on the screen. **Feelings** maps the model's own expressions to the
character's moods.
- **Its own idle motions.** Most models come with idle animations made by their artist. The device plays
  them, one after another, and its own movement (looking at you, nodding, gestures) adds to them, so the
  character moves as its artist intended and still reacts to you. While it listens or talks they show a
  little less, and a gesture takes over the part of the body it needs. The device still does the blinking
  and the mouth itself (following the voice), so the motions' own blinks are left out. Under the
  character, **Its own idle motions** chooses *as its artist made them*, *gentler* or *off*. (Characters
  made with Character Studio start with it off: the device's own movement is made for them.)

### A 3D avatar (VRM) you already have

On **Characters → Add a character**, choose a `.vrm` file (as made with
VRoid Studio and most VTubing programs). Adding it takes up to a minute
while its pictures are fitted to the device's memory. If an avatar is too
detailed for the device, it says so and what to do (export it again with
fewer polygons and smaller textures). Its maker's terms (who may use it,
whether commercially) are shown under it; follow them. **Feelings** starts
with its own happy, sad, angry and surprised expressions.

It talks like any other character, with the same moods, gestures and lip
sync. Under **3D animation** you choose how it moves:

- **Full body** (the usual): it stands and moves like a person, with a
  different idle pose for each mood and for listening, thinking and
  talking, and gestures with its whole body (waving, bowing, a hand on the
  chin while it thinks, stretching, yawning behind its hand).
- **Light**: it moves the way the 2D characters do, using a little less of
  the device.
- **Show**: head to thighs, head and shoulders, or the whole body.
- **Simplify the drawing**: no outlines and smaller pictures, for a slow
  device. The device does this by itself if the screen gets slow.

A 3D avatar can turn its head much further than a flat character: as far as a profile (90 degrees), with
its shoulders following a little, to look back over its shoulder when the conversation calls for it, or
now and then by itself when nobody is talking. Flat characters turn as far as their drawing allows.

**Its own animations**: add VRM Animation files (`.vrma`, from VRoid Hub,
VRM Posing Desktop or Blender) as an idle pose (for a mood, or for
listening, thinking or talking), in place of one of its gestures, or as a
new gesture with a few words saying when to use it ("do a little dance").
**Try** plays one on the screen. Use only animations you have the right to
use.

### Who the character is

Under the character, **Describe the character** asks a few questions (name,
personality, story, how they treat you, likes, dislikes, how they speak,
things they never do, a greeting) and writes the persona from your answers.
You can then edit the persona freely. A **character card** (a PNG or JSON
file from other AI-companion apps) fills it in for you to check and save.

### Its voice

The device's own voice is free and works without the internet. With a voice
service (ElevenLabs, Cartesia, Fish Audio, Inworld AI, MiniMax, Deepgram,
Google Cloud, OpenAI or an OpenAI-style server), each character can have its
own voice service and voice ID under the character (with **List voices** to
pick from your account).

**Clone a voice** makes a custom voice from a clear recording of one person
speaking, in your own account on ElevenLabs, Cartesia, Fish Audio, Inworld AI
or MiniMax, and switches the character to it straight away. The page says
what each service takes:

| Service | Recording | Notes |
|---|---|---|
| ElevenLabs | WAV, MP3, M4A, OGG, FLAC or WebM; 30 seconds to a few minutes; up to 20 MB | |
| Cartesia | WAV, MP3, OGG, FLAC or WebM; 10 to 60 seconds; up to 16 MB | Choose the language spoken in the recording |
| Fish Audio | WAV, MP3 or FLAC; 10 to 30 seconds is best; up to 20 MB | |
| Inworld AI | WAV or MP3; 3 to 30 seconds (it uses the first 30); up to 12 MB | Choose the language, or let it tell |
| MiniMax | WAV, MP3 or M4A; 10 seconds to 5 minutes; up to 20 MB | The new voice says one word at once: MiniMax deletes cloned voices that have never spoken within 7 days |

Deepgram and Google Cloud have voices to choose from, but no cloning. If a
character package includes its own `voice/sample.wav`, you can clone directly
from it. The recording goes to the service you chose, and the cloned voice
stays in your account there; delete it there if you no longer want it.

### Backgrounds

The character stands in front of a picture. Under **Characters → Background** on the settings page:

- choose one of eight built-in pictures (a studio, a soft morning, a clear sky, a sunset, a starry night, a
  cosy room, blossom, paper), **With the time of day** (the morning, the day, a sunset and a starry night as
  the day goes on; the default), or **None** for a plain dark colour;
- **add a picture of your own**: a photo or any picture (JPEG or PNG). It is fitted to the screen, filling
  it without black bars, and the details a phone keeps with a photo (where it was taken, among them) are
  not kept;
- **Darker** makes the picture dimmer so the character stands out;
- **It moves a little as the character turns** gives a hint of depth; turn it off for a still picture.

Each character can have its own picture instead: edit the character and choose **Background**. Your own
pictures are in backups; **Remove** takes one away, and characters that used it go back to the default.

## How it moves

The character moves by itself; you can shape how. Under each character:

- **Temperament**: two sliders, *Calm ↔ Lively* (how much and how soon it
  moves) and *Reserved ↔ Expressive* (how many and how big its gestures
  are). *Suggest from the personality* sets them from the persona.
- **Gestures and idle actions it makes by itself**: switch off any you do
  not want (a character who never waves, say).
- **Movement** (mostly for imported models): which of the model's settings
  each movement uses, with a **Test** button for each.

The **Animation** tab shows what the character is doing right now: its
mood, the time of day it thinks it is, how energetic, attentive, bored or
sleepy it is, which parts of its body are busy, and the last gestures and
why it made them. **Change or add a gesture** lets you edit a gesture's
movement, try it on the screen, and save it as a new gesture with the words
that bring it on. Character Studio can also make a gesture from a video of
you doing it (`14-making-characters.md`).

### Teaching it a gesture with your phone

You can film yourself making a gesture (a wave, a shrug, a little dance)
and the character learns to make it. Your phone needs to be on the same
Wi-Fi as the device; nothing needs installing.

1. On the settings page, open the **Animation** tab and press **Show a QR
   code for my phone**.
2. Scan the code with your phone's camera. If it will not scan, open the
   link shown on your phone, or go to the short address shown (such as
   `http://192.168.1.23/r`) and type the six-letter code.
3. Press **Film it now** (or **Choose a video** for one you already have).
   Prop the phone up so your head and shoulders are in view (and your arms,
   for a gesture with them), face it in good light, hold still for a
   moment, make the gesture, then hold still again. Up to 20 seconds.
4. Choose the part of the video to use, then **Find my movement**. Your
   phone finds your face and body in the video; it takes a few seconds to
   half a minute.
5. The character makes the gesture on its screen straight away. **Play it
   again**, **Swap left and right**, or **Film again** until you like it.
6. Give it a name and choose what it is: a **gesture** the character makes
   when it fits what it says (say what it means, such as "do a little
   dance", so it knows when), or an **idle action** it does now and then by
   itself. Press **Keep it**. Giving it the name of an existing gesture
   (even a built-in one, such as "wave") asks before replacing it.

The video never leaves your phone: only the movement found in it (where
your face and body are, frame by frame) goes to the device. The link needs no password, so it
works for 30 minutes and one recording session only; **Stop** on the
settings page ends it sooner, and a new link ends the old one. Recorded
gestures are kept with the other gestures, for every character, and can be
changed or removed under **Change or add a gesture**. A character without
the body part a gesture uses most (arms, say) cannot make it; the phone
tells you when that is so.

### The live camera (optional)

Normally your phone films with its own camera app. With the **live
camera**, the recorder page uses the camera directly: you see yourself, it
counts three and records while you watch, and it can make the character
copy you live (below). It is off until you turn it on, because of one
thing to know first:

**Your phone will warn you the first time.** A phone's browser only lets a
page use the camera live over a secure (HTTPS) connection. The device makes
its own, but your phone does not know the device, so it says the connection
is not private. That is expected for a device on your own network; nothing
leaves your home. To continue, once on each phone:

- **Android (Chrome):** tap *Advanced*, then *Proceed to 192.168.1.23
  (unsafe)* (your device's address).
- **iPhone (Safari):** tap *Show Details*, then *visit this website*, then
  *Visit Website*.

Only do this for your device's own address, as the settings page shows
it, never for a website on the internet. To be sure the warning is about
your device, compare the certificate's fingerprint the phone can show with
the one on the settings page. Some work phones do not let you continue; use
the camera app with those.

To turn it on: **Animation** tab, open *Live camera*, tick **Use the live
camera**. The QR code and link then start with `https://`. On the phone,
press **Start the camera** (and allow the camera), then **Record a
gesture** (it counts three; press **Stop** when done), or **Copy me on the
screen**. *Or film with the camera app* is still there.

**A 3D character records your whole body.** For a 3D (VRM) character the phone records your whole
body: stand back so the camera sees you from head to feet (a phone propped up works best). If only your
top half is in the picture, that is fine: its legs keep their own movement. After it reads the movement,
the phone tells you what it kept and what it left to the character (a leg that was out of the picture,
say). If it could not see your shoulders for most of the recording, it makes a head-and-arms gesture
instead and says why. Kept movements appear under the character's **3D animation** on the settings page,
where you can try or remove them.

### Copy me live

The character copies your face as you move (your head, eyes, brows, smile
and mouth, and with the live camera also your shoulders and arms), on its
screen as it happens. Hold still for a second when it starts: that is your
resting pose. Press **Recentre** if you sit differently later.

- **With the live camera:** as above, press **Copy me on the screen** on
  the phone. Press it again to stop.
- **With a face-tracking app** on your phone (no warning involved):
  - *VTube Studio* (iPhone): in its settings, first tab, turn on *3rd
    Party PC Clients*. It shows your phone's address.
  - *MeowFace* (Android, free): press *Start*. It shows your phone's
    address.
  - *iFacialMocap* (iPhone): its address is at the top.

  On the **Animation** tab, under **Copy me live**, choose the app, type
  the phone's address and press **Connect**. Keep the app open, on the same
  Wi-Fi as the device.

**Like a mirror** (on to begin with) makes the character copy you as your
reflection does; turn it off and your right is its right. With an app, if
it nods the wrong way, tick **Flip up and down**; if it turns the wrong
way, change **Like a mirror**. Each app is a little different, and the
device remembers your choices.

While copying, the character makes no gestures of its own. When you stop,
or the phone goes quiet for half a second, it carries on by itself.

**Record a gesture** while it copies you: press it, make the gesture, press
it again (at most 20 seconds). The gesture plays on the screen and opens
in **Change or add a gesture** below: give it a name and what it means,
then **Save**.

## Memory

The character remembers each conversation as a whole, the lasting things
it learns about you, and keeps a diary (`31-memory.md`).

- **A conversation ends** after 20 quiet minutes, a few minutes after you
  say goodbye, when a new day starts (4 am), or when you press **Start a
  new conversation**. It is then summed up in the background: a title, a
  summary, how you seemed, and what it learnt.
- **What it learns** is kept as short memories with a category and an
  importance: your name, your people, what you like, plans with their
  dates. When something changes ("Kim moved to Darwin"), the old memory is
  updated, not kept beside the new one. Once a plan's day has passed, it
  asks how it went.
- **Each evening** it writes a diary entry in its own voice, and the next
  day it picks up where you left off.

The **Memory** tab shows all of it:
- **Always remember**: notes it keeps in mind in every conversation, which
  it never changes itself.
- **What it remembers about you**: its memories, which you can add, pin,
  change or forget; ones it found no longer true can be restored.
- **Conversations**: each with its summary; **Show words** shows every word.
- **Diary**: its entries.

**Forget everything** clears that character's memory.

### Speaking first

Turn on **Device → Speak first** and the character greets you when you come back after a while. It asks how your plans went, then listens for half a minute so you can just answer.
- It notices speech-like sound in the room; nothing is recorded or sent.
- Choose how long apart counts (3 hours by default) and the quiet hours when it never speaks first (10 pm to 8 am by default).
- A television left talking can set it off, so it is off unless you turn it on.

## Your day

The **Your day** tab of the settings page makes the character part of your day. Everything here is off
or empty until you choose it, except special days (holidays and its own arrival day) and calling again
until you answer.

### About you

Your **name** (what the character calls you) and your **birthday** (the month and day; the year only if
you want it to know your age). The character is told your name in every conversation.

### The weather

Turn on **Know the weather** and the character knows today's and tomorrow's weather where you live: it
mentions it when it says good morning and when it wakes you, the clock shows it, and you can ask ("will
it rain this afternoon?"). Until you choose a town it uses your time zone's main city; **Change** finds
yours in a list kept on the device (every town of 15,000 people or more; choose the nearest larger one if
yours is not there). Temperatures are in **Celsius** or **Fahrenheit**, or the usual way where you are.
**Save and check the weather** shows what the character would say.

Forecasts come from MET Norway (the Norwegian Meteorological Institute), which offers them to everyone
for free. Only your town's position, to about a kilometre, is sent; what you type to find your town is
not sent anywhere. The device asks at most every half an hour, and only when something will show or
say the weather.

### Good morning

With **Device → Speak first** on, the first time it hears you each morning (5 am to noon) it says good
morning, in its own words, with the weather and any plans you told it about for the day, however short
the night was. Turn off **Good morning** to keep only the usual "welcome back".

### Wake-ups, alarms and timers

**Add a wake-up**: a time, the days of the week (weekdays to begin with) and, if you like, what it is
for. When the time comes the character wakes you itself, in its own words: good morning, the time, the
weather and your first plan. If you don't answer, it calls again every minute, a little louder each
time, up to five times. **Press the button**, or just answer it ("I'm up!"), to stop it; it then
listens, so you can ask for "ten more minutes" (it sets a timer). **Try one now** rings a wake-up at
once, to hear it. A wake-up missed while the device was off is not rung late.

Alarms and timers you ask for by voice work the same way (timers call three times, half a minute apart,
at the usual volume), and are listed under **Set by voice**, where you can cancel them. If the AI
service cannot be reached, the character still says it, in a few set words; if its voice cannot be
reached either, the device's own voice says it; failing everything, it beeps. While one is ringing, a
note on the screen says how to stop it, and the settings page has a **Stop it** button.

### Special days

On **your birthday**, on **the day the character arrived** (every year, and its hundredth day), on
**your own days** (add a date and what it is, such as "our anniversary"; it counts the years when you
give the year, or tick **only that year** for a one-off) and on **holidays** (New Year's Day and Eve,
Valentine's Day, Easter, Halloween, Christmas Eve and Day), the character knows, and wishes you well the
first time you talk that day. With **a festive background** (on to begin with), the screen shows one of
six festive pictures all that day (balloons, fireworks, hearts, a friendly Halloween night, snowy lights,
spring), unless your background is **None**. The festive pictures can also be chosen as ordinary
backgrounds.

### The clock

**A clock over the character** shows the time, the date and the weather in the top corner:
**while nobody is talking** (it steps aside while you talk), **all the time**, or never. Choose a 12- or
24-hour clock, or the usual way where you are.

### The photo frame

Add your own pictures (**Characters → Background**), then choose **Your pictures, one after another**:
they take turns behind the character, a new one every few minutes (choose how often on **Your day**),
changing only while nobody is talking. With no pictures of your own yet, it follows the time of day.

## AI services

Under **AI services**, in three parts:

1. **Language model**: Claude (Anthropic), OpenAI, Gemini, Groq, OpenRouter,
   or any service with an OpenAI-style address. Choose the service and model,
   paste the key, then **Save and test**.

   **A model on your own computer** (*My own computer or server*):
   - Supported: Ollama, LM Studio, llama.cpp, vLLM, KoboldCpp,
     text-generation-webui, Jan or GPT4All on a computer in your home. Free,
     and nothing you say leaves your home network. With the device's own voice
     and speech recognition, the character then works without the internet.
   - **Open the model to your network:**
     - Ollama: start it with `OLLAMA_HOST=0.0.0.0`.
     - LM Studio: turn on *Serve on Local Network*.
     - llama.cpp: start its server with `--host 0.0.0.0`.
   - **Connect:** press **Find on my network**, pick your server and a model,
     then **Save and test**. If it isn't found, type the computer's address.
   - **Context** is how much the model reads at once (8,192 if left empty).
     More lets it remember more of a long conversation, but needs more of the
     computer's memory.
   - **Model size matters most for memory.** A model of 7 to 8 billion
     parameters (such as `qwen2.5:7b` or `llama3.1:8b`) chats well on a recent
     laptop and keeps memory mostly right; glance at the Memory tab now and
     then. A 3-billion model can chat, but keeps poor memories. Larger models
     on a computer with a graphics card do better.
   - The first reply after a while is slower, as the model loads.
2. **Voice**: the free on-device voice, with 72 voices to choose from in
   English accents from around the world (English, Scottish, Irish, Welsh,
   American, Canadian, Australian, New Zealand, South African and Indian), or
   a cloud voice service: ElevenLabs, Cartesia, Fish Audio, Inworld AI or
   MiniMax (all of them can clone a voice), Deepgram or Google Cloud (voices
   to choose from), OpenAI, or any OpenAI-compatible server. Each needs its
   own key, from its web site; the box under the service says where.
3. **Speech recognition** (hearing you): free and on the device by default,
   or a cloud service.

**All saved keys** lists which keys are saved, without showing them.

## Privacy and your keys

- **Keys** are stored only on the device, in a file only its software can
  read. Once saved, a key is never shown again: not on the page, the screen,
  in logs, or in character files you export or share.
- **What you say** goes to the language-model service you chose, as text
  (with the on-device speech recognition, your voice itself stays on the
  device). With a cloud voice or cloud speech recognition, the text or audio
  goes to that service. Their own privacy terms apply.
- **Memory** (conversations, memories, the diary and your notes) stays on
  the device. What the character reads for a reply goes to the language
  model you chose; with a model on your own computer, it stays in your home.
- **The settings page** only answers on your home network, behind your
  password.
- **The weather** (only if you turn it on): your town's position, to about
  a kilometre, goes to MET Norway, with the software's name; nothing else.
  Your name, birthday, town and days are kept on the device, and left out
  of support reports.

## The screen at night

Turn on **Device → Screen at night**: in the hours you choose (11 pm to 7 am to begin with), once
nobody has talked to it for a minute and a half, the screen goes **dark** (or **dim**). Press the
button, say its name, or an alarm, and it lights up; it sleeps again a little after. Where the screen's
backlight can be set (the Raspberry Pi touch screen), there is also a **brightness** slider.

## Family-safe mode

Turn on **Device → Family-safe**, for homes with children. Every reply then follows rules that come
before the character's own description: nothing unsuitable for a young child, no personal details asked
for, kind advice to talk to a grown-up about anything worrying. Its memories and diary follow them too, and
any strong swearing is taken out of what it says before it is spoken or shown.
- **Turning it off asks for the settings-page password again.**
- How well the rules are kept depends on the language model: the large cloud models keep them closely; a
  small model on your own computer may not.

## Connecting by USB

No Wi-Fi to hand, or a phone that won't join the setup network? Connect a computer to the device's
**USB-C port** with a USB-C cable, and open **http://10.12.194.1** in its browser (or
`http://companion.local`). The settings page works exactly as over Wi-Fi: set it up, add characters,
choose Wi-Fi.
- Windows 11, macOS and Linux need nothing installed. The computer's own internet is untouched.
- On a Raspberry Pi 4 or 5, the USB-C port is also the power socket: unplug the power supply, and the
  computer powers the device. Use a USB-C port on the computer (a USB-A port gives too little power).
  The device then runs on the computer's power, a little quieter and dimmer, until you plug its own
  supply back in.

## Software updates

The device keeps itself up to date. It looks for updates from its maker twice a day, and installs them at night (2 to 5 am) when nobody is talking to the character.
- Your characters, memories, keys and settings are kept.
- If an update ever doesn't start properly, the device goes back to the version before by itself. That includes after a power cut in the middle of one.

**Device → Software** shows the version, and lets you:
- check for updates and install one now;
- turn automatic installing off;
- go back to the previous version;
- install an update file you were sent (for a device without internet).

## Backup, and help

**Device → Backup → Download a backup** saves every character, with its memory, choices and animations, and the settings, in one file.
- Keep it somewhere safe, or restore it on a new device or a bigger card: **Restore from a backup**.
- Keys, the password and Wi-Fi are never in a backup, so put your keys in again afterwards.

If something is wrong, **Device → Help → Download a support report** and send it to the device's maker.
- It has versions, settings, recent problems and logs.
- It has no keys, no passwords, and nothing anyone said.

## Rights

Use only pictures, models, character cards and voices you have the right to
use. A character made from a picture of a well-known character is for your
own private use. Clone only your own voice, or a voice whose owner agrees.

## Buttons

| Button | Press | Hold |
|---|---|---|
| Listen | Start listening; press again to stop; press while it speaks to interrupt | 2 seconds: the microphone off, or on again. 12 seconds: forget the settings-page password and the saved Wi-Fi (characters, memories and keys stay), and show the setup screens again. While you hold it, the screen says which |
| Power | Shut down cleanly; press again to start | — |

## Troubleshooting

| Problem | What to do |
|---|---|
| It does not hear its name | Is the wake word on (Device), and the microphone on? Try "hello" and its name, or set how readily it hears to *more*, or choose other words |
| It starts listening when nobody asked | Set how readily it hears its name to *less*, or choose less common words |
| A crossed-out microphone shows on the screen | The microphone is off: hold the button for two seconds, or slide the mute switch |
| The screen is dark | It is asleep for the night: press the button. **Device → Screen at night** sets the hours |
| The settings page says the storage had to be made again | The device's data was damaged (perhaps by a power cut while it saved) and was emptied: restore your latest backup, then put your keys in again |
| The screen shows *Set up Wi-Fi* again | The device cannot find your Wi-Fi (new router or password?): join its network and choose your Wi-Fi again |
| The settings page will not open | Is your phone on the same Wi-Fi? Use the address on the screen rather than `companion.local` |
| Forgot the settings-page password | Hold the listen button for 12 seconds, then set up again (characters, memories and keys stay) |
| It does not hear you | Is listening on (press the button)? Look at *What is happening* on the Home tab |
| "refused the key" | The key is wrong, expired, or the account has no credit: paste it again under AI services and **Save and test** |
| It answers slowly | Choose a faster model, or lower *Effort* under the language model. With a model on your own computer, choose a smaller one; the first reply after a pause is slower while it loads |
| **Find on my network** finds nothing | Is the computer on, with the model server running and opened to the network (see AI services)? Type its address instead, such as `192.168.1.20`; a firewall may need to allow the port (Ollama: 11434, LM Studio: 1234) |
| "requires more system memory" | The model is too big for that computer: choose a smaller one, or lower *Context* |
| It forgot something it should know | Look on the **Memory** tab: add or correct the memory, or pin it so it is always in mind |
| The Home tab says the memory was not brought up to date | The language model could not be reached; it tries again in 15 minutes |
| An arm moves wrongly | Make the character again in Character Studio with that arm unticked |
| It moves too much, or too little | Its **Temperament** sliders |
| An imported model moves the wrong part | Its **Movement** section: choose another setting for that movement and test it |
| The screen says a character cannot be shown | Choose another character; the file may be damaged or of an unsupported kind |
| The screen says a 3D avatar is too detailed for this device | Choose another character, or export the avatar again with fewer polygons and smaller textures and add it again |
| A 3D avatar moves stiffly or the screen is slow | Under **3D animation**, tick *Simplify the drawing*, or choose the *Light* style |
| A 3D animation you added will not load | It must be a `.vrma` file of at most 16 MB and 2 minutes; the settings page says what is wrong |
| The screen says *Updating* for more than ten minutes | Unplug it and plug it in again: it starts on the version that works and finishes or undoes the update |
| Giving the device away | **Device → Reset everything** (password, then type RESET): removes characters, memories, keys, settings, timers and saved Wi-Fi |
