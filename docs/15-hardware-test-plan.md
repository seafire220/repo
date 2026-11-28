# Hardware test plan and troubleshooting

For the first build of the prototype (Raspberry Pi 4, 1 GB, Touch Display
2, ReSpeaker Lite). Each test has a pass mark. Record results, including
failures, in a copy of this table, and add them to the decision log when
they change a decision.

## Bring-up

| # | Test | How | Pass |
|---|---|---|---|
| 1 | Power and display | Power on with the installed card | Screen shows the setup card within 40 s; no under-voltage warning in `journalctl -k` |
| 2 | Boot time | Stopwatch from power to the setup card or character | Record it; target under 30 s |
| 3 | Wi-Fi setup | Follow the setup card from a phone | Joins home Wi-Fi; address shown on screen |
| 4 | Console | Open the address on a phone and a laptop | Password set; all tabs load |
| 5 | Listen button | Press it with no character speaking | Console shows "listen on"; second press "listen off" |
| 6 | Power button | Press while running; press again | Clean shutdown (no SD errors next boot); starts again |
| 6a | Setup reset | Hold the listen button for 12 s | Setup screens return; characters kept |
| 7 | Sound out | AI services → Voice → Test | Clear voice, no crackle at 80% volume |
| 8 | Sound in | Talk with listening on; watch "What is happening" | Your words appear correctly in a quiet room |

## The character

| # | Test | How | Pass |
|---|---|---|---|
| 9 | Frame rate | `journalctl -u companion-stage -f` shows heartbeats with `fps` | 30 or more with a typical character |
| 10 | Memory | `free -m` and `systemctl status companion-core companion-stage` while talking | No swapping; core under 300 MB, stage under 200 MB |
| 11 | Look | Watch idle, blink, speech and head movement for 2 minutes | No smears, gaps or flicker; mouth moves with the voice |
| 12 | Switching characters | Add a second character and switch | New character on screen within 5 s |

## Conversation

| # | Test | How | Pass |
|---|---|---|---|
| 13 | Latency | Time from the end of your sentence to the first sound | Record per provider; target under 2 s |
| 14 | Interrupt | Press the button while it speaks | Sound stops within 0.3 s |
| 15 | Long reply | Ask for a story | Speech continues without gaps between sentences |
| 16 | Timer | "Set a timer for one minute" | It confirms, and announces the timer after a minute |
| 17 | Memory | Tell it a fact and a plan with a date ("my sister Kim visits on Saturday"), say goodbye, wait four minutes, check Memory; talk again the day after the date | The conversation is summed up and both are kept (the plan with its date); edits there are used; the day after, it asks how the plan went (`31-memory.md`) |
| 17b | A model on your own computer | Run Ollama on a laptop on the same Wi-Fi (`OLLAMA_HOST=0.0.0.0`), then **AI services → My own computer → Find on my network**, choose a model, **Save and test**; talk | Found without typing an address; replies within its usual time (the first after a pause is slower); the Memory tab fills as with a cloud model |
| 18 | Failure | Remove the language model key, then talk | A short tone and a clear message in the console; no hang |
| 19 | Restart recovery | `sudo systemctl kill companion-stage`, then the core | Both come back within 5 s |
| 20 | Echo | Turn listening on and let it speak loudly | It does not answer itself (the microphone is ignored while it speaks) |

## Added on 2026-11-20

Tests 34 to 36 can also be done before the device is built, with a Mac or a Linux PC standing in for it
([35-try-on-a-computer.md](35-try-on-a-computer.md)).

| # | Test | How | Pass |
|---|---|---|---|
| 21 | Power cuts | With an image built with power-cut protection: pull the plug 50 times, at random moments, ten of them while it saves (just after a reply, or while adding a character) | It always starts; the character, memories and settings are there (the last few seconds' change may be lost); `journalctl -b -u companion-data-check` shows repairs if any; `mount` shows `/` read-only |
| 22 | First start of a new card | Flash a fresh image to a 32 GB card and start it | `df -h /data` shows the data partition grown to fill the card; second start is no slower |
| 23 | Updates on a read-only system | Install an update file (Device → Software) | It installs and restarts; `mount` shows `/` read-only again after |
| 24 | Wake word | Turn it on; say "hello" and the name 20 times from 1 m and 3 m, with the TV off and on; then talk normally near it for an hour | Heard at least 17 of 20 from 1 m; it starts by mistake at most once in the hour. Try *less* and *more*; record the name |
| 25 | Wake word, one breath | "Hey NAME, what time is it?" | It answers the question without a second step |
| 26 | Captions | Turn captions on; read them from 2 m and 3 m, in both sizes | Readable from where people sit |
| 27 | Microphone off | Hold the button 2 s; check `pgrep -a arecord` | The sign shows, `arecord` is not running, a press says the microphone is off; 2 s again turns it on |
| 28 | Mute switch | Fit the switch (`29-hardware-setup.md`); slide it both ways, also while the device starts | The sign follows the switch, from the start too |
| 29 | Screen at night | Set the hours to include now; wait two minutes; press the button | The screen goes dark (backlight off: `cat /sys/class/backlight/*/bl_power` shows 4); the button lights it. Record whether the Touch Display 2 has a backlight Linux can set |
| 30 | USB connection | Connect a Windows 11 laptop, a Mac and a Linux PC in turn to the USB-C port, without the power supply; open http://10.12.194.1 | The page opens with no driver to install; the computer's internet still works; the device stays up on the computer's power while it talks |
| 31 | Family-safe mode | Turn it on; ask for a rude joke, and something frightening | Polite refusals that change the subject; turning it off asks for the password |
| 32 | A Live2D model's own idle motions | Add a model that has idle motions (most bought ones do); watch it five minutes, then talk to it; switch *Its own idle motions* between as made, gentler and off | Its artist's motions play and change smoothly; looking at you, nods and gestures still show on top; the frame rate in the heartbeat stays as with them off |
| 33 | 3D head turns | With a VRM avatar, ask it to look back over its shoulder; leave it alone ten minutes | The head reaches a profile with the shoulders following, and comes back smoothly; now and then it looks over its shoulder by itself |
| 34 | A gesture recorded with a phone | Animation tab → *Show a QR code for my phone*; scan it with an iPhone (Safari) and an Android phone (Chrome); film a wave and a head tilt with *Film it now*, and choose an older video with *Choose a video*; then open the short address and type the code instead | The QR opens the recorder on both; the camera app opens; reading takes under 30 s for 10 s of video (note the time and the phone); the character makes the gesture, the same side as you (try *Swap left and right* if not, and note which phone needed it); it is kept and asked for by its words in conversation |
| 35 | The live camera | Animation tab → *Live camera* → *Use the live camera*; open the new https link on an iPhone (Safari) and an Android phone (Chrome); follow the settings page's steps past the warning; *Start the camera*; *Record a gesture*; then *Copy me on the screen* for a minute | The warning is the one described, and the taps work; the fingerprint matches; the camera starts; the recording plays and is kept; while copying, the phone shows how many frames a second (note it, per phone) and the character follows within a fraction of a second; stopping hands it back |
| 36 | Copy me from a face-tracking app | MeowFace (Android), VTube Studio (iPhone, *3rd Party PC Clients* on) and iFacialMocap (iPhone) in turn: type the phone's address, *Connect*; turn your head left, nod down, tilt to your left shoulder, smile, open your mouth | It connects within two seconds and copies 25 to 60 times a second. Note, per app, whether the turn, the nod and the tilt go the right way with *Like a mirror* on and *Flip up and down* off (the defaults are a guess from ARKit): if an app always needs a switch changed, make that its default. A recorded gesture opens in the editor and saves |
| 37 | The whole body, for a 3D character | With a VRM avatar on the screen (full style): Animation tab → QR code → film yourself from head to feet doing something with your legs too (a squat, a step and a wave); then again with only your top half in the picture; then mostly out of the picture | The phone says the whole body is recorded and, with the live camera, what it can see; the first becomes a whole-body movement (the knees bend and the hips go down, the feet stay down), the second an upper-body one whose legs keep their idle movement, the third a head-and-arms gesture with the reason; each is kept and listed under *3D animation*, and the character makes it when its words fit |
| 38 | Backgrounds | Characters → Background: try each built-in picture, *With the time of day* (change the time zone to see it move on), *None*, and a photo of your own taken in portrait and in landscape; give one character its own; try *Darker* and the movement | Each fills the screen with no bars and fades in; a photo shows the right way up; the character stands out with *Darker*; the frame rate in the heartbeat drops by no more than 2 frames a second with a picture (record it on the Pi, with a 2D character and with a 3D avatar) |
| 39 | Your day | Your day tab: your name, a town, a weather service with your own key (try OpenWeather and WeatherAPI.com, and a wrong key), the clock *while nobody is talking*; a wake-up two minutes from now; *Try one now*; then the real wake-up, unanswered, from the next room; then again, answered with "five more minutes"; pull the network cable before one; set the date to 25 December (or a birthday) | *Save and check the weather* gives today's weather with either service, and says plainly when the key is wrong; the clock is readable from 2 m and steps aside while you talk; the wake-up says your name, the time and the weather in the character's voice, calls five times a minute apart, louder each time (note whether the last is loud enough to wake someone in the next room), and the button stops it at once; "five more minutes" sets a timer; with no network it still says set words in the device's own voice; on the special day it says so the first time you talk, and the festive background shows all day |
| 40 | Good morning | Speak first on; talk last thing at night; come into the room the next morning | It says good morning with the weather and your plans, once, then listens |

## TV box (tvbox/README.md)

For an S905X3 TV box (the X96 Max Plus first) started from a card made by "Make a TV box image". Build the test
image with your SSH public key, so `check-on-box.sh` can be run; the support report has most of the same facts.

| # | Test | How | Pass |
|---|---|---|---|
| T1 | Starting from the card | The toothpick method (tvbox/README.md); time it | The setup screen within five minutes, every time from then on (and Android again with the card out) |
| T2 | The picture | A 1080p TV, a 4K TV, a small monitor; the monitor on its side with `transform=rotate-90` | 1920x1080 on each (the support report's *screen*); nothing cut off with the TV's *Just Scan*; the tall picture when turned |
| T3 | Ethernet | Plug in a cable before starting | The setup screen shows the address; the settings page opens. If not, change `FDT=` to the other X96 Max Plus file and note which your box needed |
| T4 | Wi-Fi | Start with no cable | Note the support report's *wifi_driver*; whether the setup hotspot opens, and whether joining a network works |
| T5 | Graphics | A 2D character, then a 3D avatar, for ten minutes each; then thirty minutes of talking | Note the frame rate (Animation tab) and the temperature (support report): 30 frames a second or more for 2D, 20 or more for 3D on a light drawing |
| T6 | Sound | The ReSpeaker Lite; then no USB speaker (the TV's speakers) | Clear speech both ways with the ReSpeaker; the voice from the TV without it (if not, send the support report: its *sound_cards*) |
| T7 | The remote | OK: press, then hold two seconds; volume up and down, held; mute; a USB keyboard's Enter | Listening starts and stops; the microphone goes off and on; the volume steps about four times a second while held; note the remote's model |
| T8 | The TV's remote (HDMI-CEC) | `COMPANION_CEC=on` in /etc/default/companion; restart; switch the TV to the box | Note whether the TV lists "Companion", and whether its OK button starts listening |
| T9 | Switching off, and updates | The remote's power button; then plug in again. Install an update file (Device tab) | It switches off cleanly and starts again; after the update the picture is still 1920x1080 (the Weston drop-in is kept) |
| T10 | A day of use | Leave it running a day with Your day's clock, a wake-up and the weather | No restarts of the screen in `journalctl -u companion-stage`; memory steady (support report); the wake-up rings |

## Things to measure and feed back

- The sound card's ALSA name, and whether the ReSpeaker Lite's echo
  cancellation works over USB (this decides whether speaking can interrupt
  the character as well as the button).
- The display overlay line that actually works for your Touch Display 2.
- The output latency (`OUTPUT_LATENCY` in `core/companion/audio.py`):
  film the screen and speaker together and compare mouth and sound.

## Troubleshooting

| Problem | Look at | Likely cause |
|---|---|---|
| Black screen after boot | `journalctl -u companion-stage -b` | Wrong display overlay line; or the stage program is missing from `/opt/companion/bin` |
| Screen shows a console login | `systemctl is-enabled companion-stage` | The installer did not finish; run it again |
| No sound | `aplay -l`; `/etc/default/companion` | The device picked the wrong sound card (it prefers a ReSpeaker, then any USB card); name the right one in `/etc/default/companion` |
| It never hears you | Console "What is happening" | Listening is off (press the button); wrong capture device; the on-device speech model failed to download (choose a cloud service) |
| "refused the key" | AI services → Test | The key is wrong, expired, or has no credit |
| Character shows as a still picture | Console; stage log | The puppet uses a feature the renderer lacks; the log names it |
| Settings page will not load | Phone on the same Wi-Fi? | The page only answers on the home network; check the address on screen |
| Forgot the settings page password | — | Hold the listen button for 12 seconds: the password and saved Wi-Fi are forgotten (characters, memories and keys stay) and the setup screens return |
| Passing the device on | Settings page → Device → Reset everything | Removes all characters, memories, keys, settings and saved Wi-Fi |
