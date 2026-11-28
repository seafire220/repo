# What else would make it complete: second review

Status: 2026-11-28. The owner asked for a pass through the project against its original goals and earlier
plans, with ideas from comparable products. The first review was `33-finishing.md` (2026-11-20); everything
it marked "strongly recommended" has been built. **Backgrounds** were built in this round (D-073); then the
owner chose 3, 4 and 5 below, now built as **Your day** (D-074), and saved touch reactions (2) for later. The
rest below is a list to choose from, most worthwhile first.

## Against the original goals

The goals are `03-requirements-spec.md`'s hard requirements and the features carried over from the reference
product (SyBran's CODE27).

| Goal | Where it stands |
|---|---|
| H1: two tiers, Lite (pre-rendered loops) and Standard (a rigged puppet) | Standard built, and more: Live2D-format and 3D (VRM) characters were added at the owner's request. **Lite is not built**; it matters again if a cheaper board (or a TV box, `37-android-tv-box.md`) is wanted |
| H2: much cheaper than the CODE27 | On track on paper (`05-hardware-and-bom.md`, `22-board-options.md`); nothing measured on a board yet |
| H3: everything shippable for sale | Licence audit done (`34-licences.md`); the lawyer questions remain (`19`) |
| H4–H5: the buyer's own keys; several language models and voices | Done: seven kinds of language model service plus a model on the owner's computer; a free voice on the device and eight voice services with cloning |
| H6: a character from one picture | Character Studio on Windows, Mac and Linux; head turns now read as turns both ways (`20-head-turns.md`); the drawing workflow's later steps (hidden areas, alternate drawings) remain |
| H7–H8: characters added and switched; memory readable and editable | Done |
| H9–H12: an appliance; the button; set up and managed from a phone; updates | Done in software; signed updates over the internet. Not yet run on a Pi |
| H13: English first, other languages by configuration | English only; the page's words are in its code, so other languages are still a medium job |
| H14–H15: voice first; no camera of its own | Kept. The phone features use the owner's phone camera |
| Carried over: expressions, lip movement, interrupting, persona, memory, a cloned voice, idle movement, timers, speaking first | Done |
| Carried over: **backgrounds** | **Done this round** |
| Carried over: touch | Not built (the spec said "later, optional"; the Touch Display 2 is a touch screen) |

## What comparable products do

In short, in our words (sources at the end):

- **SyBran CODE27** (the reference): a room for its character, dancing to music, camera gestures (thumbs up,
  a V sign, smiles), being petted by touch, a turning base that follows your face, a light panel.
- **Razer Project AVA** (shown at CES 2026, shipping date uncertain): a small cylinder on the desk with a 5.5"
  display, far-field microphones and a camera with eye tracking, so the avatar looks at you; a personality
  tone you can set; a mode that watches your game and coaches you.
- **Gatebox** (Japan): greets you when you come home, wakes you with the weather, works lights and appliances
  (infrared and smart home), sends you messages while you are out, marks anniversaries.
- **Dipal D1**: a curved display with eye tracking that reacts to voice, touch and gestures; a large catalogue
  of characters and dances; a marketplace for creators.
- **Lepro Ami** (CES 2026): an always-on desk companion with eye tracking, sold on being present rather than
  waiting to be called.
- **Grok's companions**: a closeness score that rises with how you talk to it and changes how it behaves
  (reports differ on the details).
- **Desktop Mate** (a PC app): characters that live on the desktop and announce your alarms; paid character packs.
- **Open-source companions** (AIRI, Open-LLM-VTuber): Live2D and VRM avatars, local voices, seeing through a
  camera or the screen, reacting to touch, a desktop-pet mode, even playing games alongside you.

What they share, beyond conversation: the character **notices you**, **reacts to touch**, **lives somewhere**
(a room or scene), **fits daily life** (wake-ups with the weather, reminders, the home), **changes over time**
(closeness, outfits, special days) and **moves to music**.

## Ideas, most worthwhile first

Sizes are rough: small is a day or two, medium about a week, large several.

| # | Idea | Why | Size | Notes |
|---|---|---|---|---|
| 1 | ~~Backgrounds~~ | A character in a scene, not on black | done | D-073; eight built-in pictures, the owner's own, by the time of day |
| 2 | **Touch reactions** | Every comparable product has them; the screen is a touch screen already | small–medium | A pat on the head (it smiles, laughs), a poke (surprise, "hey!"), a tap to wake it from dozing, a long press for the mute. 2D characters by their parts; 3D by where the bones are on screen. The button keeps working (touch stays optional, A9) |
| 3 | ~~A morning hello with the weather, and wake-up alarms it says itself~~ | Daily-life use, like Gatebox and Desktop Mate | done | D-074; the weather from MET Norway (Open-Meteo's free service is for non-commercial use only), the town from a list on the device; the alarm is the character calling your name, then louder; the button stops it and it listens |
| 4 | ~~Special days~~ | Delight, and a reason to come back | done | D-074; birthday, the day it arrived and its hundredth day, your own days, seven holidays; six festive backgrounds |
| 5 | ~~A clock, or a photo frame, while nobody is talking~~ | The device is useful at a glance even when quiet | done | D-074; a clock with the date and the weather at the top; your pictures in turn as the background |
| 6 | **Moving to music** | The CODE27 dances; it reads as "alive" | medium | Find the beat in what the microphone hears; bob, sway or dance in time (built-in clips for 3D avatars) |
| 7 | **Closeness that grows** | Relationship progression, as in Grok's companions, without the manipulation | medium | Time spent and kind words make it more familiar (a nickname, inside jokes, new idle actions). Nothing locked behind it; it never pressures; family-safe mode keeps it plain |
| 8 | **Outfits** | Variety and seasons | medium (needs art) | 3D: a second avatar file per outfit, switched by the hour or the season; 2D: alternate drawings from Character Studio once that workflow exists |
| 9 | **Smart home through Home Assistant** | "Turn off the lights", as Gatebox does | medium | The owner's own Home Assistant, with a token kept like the other keys; a list of what it may touch |
| 10 | **A calendar** | Reminders without telling it everything | medium | A private calendar link (ICS) read daily |
| 11 | **The settings page on the phone's home screen** | Opens like an app | small | A web app manifest and icon |
| 12 | **Copying the whole body live, for 3D avatars; fingers** | Follows on from this round's recording | medium each | The live camera sends the body's points as it records; a hand model for fingers |
| 13 | **Two characters at once** | Banter between them | large | Two drawings on one screen, turns in conversation |
| 14 | **Other languages** | More buyers | large | The page's words out of its code; models and voices per language |
| 15 | **The Lite tier** | The cheapest product (original goal H1) | large | Pre-rendered loops per state and feeling, made by Character Studio; the way onto boards without OpenGL ES 3 |

Left out on purpose: a camera of its own (H15; the phone's camera does the camera jobs) and a turning base.

Still needed before selling, unchanged from `33-finishing.md`: a run on the real hardware, the update key and
address, the power-cut test, an original character in the box, code signing for Character Studio, the
paperwork (the lawyer questions, compliance marks, privacy statement, warranty) and a quick-start card.

**Recommended next:** touch reactions (2), saved for later by the owner; then moving to music (6). (The
morning hello and spoken alarms, special days, and the clock and photo frame were built: D-074.)

## Sources

- Razer Project AVA: [Razer](https://www.razer.com/newsroom/product-news/project-ava/),
  [Engadget](https://www.engadget.com/gaming/pc/razer-put-a-waifu-in-a-bottle-at-ces-2026-205315908.html),
  [Gizmodo](https://gizmodo.com/razer-seems-even-more-confused-about-its-ai-waifu-than-the-rest-of-us-2000712483),
  [TechCrunch](https://techcrunch.com/2026/01/08/the-most-bizarre-tech-announced-so-far-at-ces-2026)
- CES 2026 companions: [Gizmodo](https://gizmodo.com/ces-2026-really-wants-you-to-own-a-holographic-anime-girlfriend-2000707138),
  Lepro Ami: [press release](https://www.newswire.ca/news-releases/lepro-unveils-lepro-ami-at-ces-2026-a-desktop-ai-companion-that-feels-in-the-room--847670849.html),
  [Techlicious](https://www.techlicious.com/blog/lepro-ami-ai-companion-lives-on-your-desk/)
- Gatebox: [Tech Xplore](https://techxplore.com/news/2016-12-gatebox-virtual-home-robot-life.amp),
  [Digital Trends](https://digitaltrends.com/mobile/gatebox-digital-human-smart-home-release-news),
  [SlashGear](https://www.slashgear.com/gatebox-virtual-home-robot-automates-your-home-15467605)
- Dipal D1: [Notebookcheck](https://www.notebookcheck.com/Anime-Partner-auf-3D-Display-Character-Pod-bringt-AI-Assistent-in-die-reale-Welt.1085618.0.html),
  [Tripo](https://www.tripo3d.ai/blog/dipal-d1-with-tripo)
- Grok companions: [Engadget](https://www.engadget.com/ai/groks-ai-chatbot-now-includes-companions-for-you-to-romance-133017601.html),
  [CyberLink guide](https://www.cyberlink.com/blog/trending-topics/4066/grok-ani)
- Desktop Mate: [Steam](https://store.steampowered.com/app/3301060/Desktop_Mate/)
- AIRI: [README](https://raw.githubusercontent.com/moeru-ai/airi/HEAD/README.md); Open-LLM-VTuber:
  [review](https://dev.to/andrew-ooo/open-llm-vtuber-review-offline-ai-companion-with-live2d-327m)
- The CODE27: `01-reference-product-research.md`
