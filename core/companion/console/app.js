// The device's web console. Plain JavaScript; talks to /api on this device only.
"use strict";

const $ = (id) => document.getElementById(id);
const KEY_NAMES = {
  openai: "OpenAI", anthropic: "Anthropic", groq: "Groq", openrouter: "OpenRouter",
  openai_compatible: "Gemini / other", elevenlabs: "ElevenLabs", cartesia: "Cartesia",
  fishaudio: "Fish Audio", inworld: "Inworld AI", minimax: "MiniMax", deepgram: "Deepgram",
  google_tts: "Google Cloud (voice)",
  search: "Web search", local_llm: "Your own model server",
};
const VOICE_LIST_PROVIDERS = new Set(["local", "elevenlabs", "cartesia", "fishaudio", "inworld", "minimax", "deepgram", "google"]);
// Services where the voice alone says everything (no model to choose).
const NO_MODEL = new Set(["local", "deepgram", "google"]);
const CLONE_INFO = {
  elevenlabs: {
    label: "ElevenLabs", key: "elevenlabs", languages: false,
    accept: ".wav,.mp3,.m4a,.ogg,.flac,.webm,audio/*",
    hint: "Clear WAV, MP3, M4A, OGG, FLAC or WebM recording (30 s to a few minutes, up to 20 MB).",
  },
  cartesia: {
    label: "Cartesia", key: "cartesia", languages: true,
    accept: ".wav,.mp3,.ogg,.flac,.webm,audio/*",
    hint: "Clear WAV, MP3, OGG, FLAC or WebM clip (10–60 seconds, up to 16 MB). Fast, low-latency cloning.",
  },
  fishaudio: {
    label: "Fish Audio", key: "fishaudio", languages: false,
    accept: ".wav,.mp3,.flac,audio/*",
    hint: "Clear WAV, MP3 or FLAC clip (10–30 seconds is best, up to 20 MB).",
  },
  inworld: {
    label: "Inworld AI", key: "inworld", languages: true,
    accept: ".wav,.mp3,audio/*",
    hint: "Clear WAV or MP3 clip (3 to 30 seconds; Inworld uses the first 30), up to 12 MB.",
  },
  minimax: {
    label: "MiniMax", key: "minimax", languages: false,
    accept: ".wav,.mp3,.m4a,audio/*",
    hint: "Clear WAV, MP3 or M4A clip (10 seconds to 5 minutes, up to 20 MB). The new voice says one word at once: MiniMax deletes cloned voices that have never spoken within 7 days.",
  },
};
let state = { active: "", characters: [], settings: null, editing: "" };
let keyStatus = {};

async function api(method, path, body, raw) {
  const opts = { method, headers: { "X-Companion": "1" }, credentials: "same-origin" };
  if (raw !== undefined) { opts.body = raw; }
  else if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  const r = await fetch(path, opts);
  if (r.status === 401 && path !== "/api/login") { showLogin(false); throw new Error("please log in"); }
  const type = r.headers.get("Content-Type") || "";
  const data = type.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error(typeof data === "string" ? data : JSON.stringify(data));
  return data;
}

function text(el, value) { el.textContent = value == null ? "" : String(value); }

// ----- login
async function start() {
  const hello = await api("GET", "/api/hello");
  if (hello.needs_setup) return showLogin(true);
  if (!hello.logged_in) return showLogin(false);
  enter();
}

function showLogin(setup) {
  for (const s of document.querySelectorAll(".tab")) s.hidden = true;
  $("tabs").hidden = true;
  $("login").hidden = false;
  text($("login-title"), setup ? "Set a password" : "Log in");
  text($("login-help"), setup ? "Choose a password for this device's settings page (8 or more characters). Anyone on your home network who knows it can change the device." : "");
  $("login-form").dataset.setup = setup ? "1" : "";
  $("login-password").autocomplete = setup ? "new-password" : "current-password";
}

$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const setup = $("login-form").dataset.setup === "1";
  try {
    await api("POST", setup ? "/api/setup" : "/api/login", { password: $("login-password").value });
    $("login-password").value = "";
    text($("login-error"), "");
    enter();
  } catch (err) { text($("login-error"), err.message); }
});

function enter() {
  $("login").hidden = true;
  $("tabs").hidden = false;
  show("home");
  refreshCharacters();
  loadSettings();
  listen();
}

// ----- tabs
function show(tab) {
  for (const s of document.querySelectorAll(".tab")) s.hidden = s.id !== tab;
  for (const b of document.querySelectorAll("#tabs button")) b.classList.toggle("on", b.dataset.tab === tab);
  if (tab === "memory") loadMemory();
  if (tab === "day") loadDay().catch((e) => text($("day-msg"), e.message));
  if (tab === "device") {
    loadWifi().catch((e) => text($("wifi-status"), e.message));
    loadUpdate().catch((e) => text($("upd-status"), e.message));
  }
  if (tab === "animation") loadAnimation().catch((e) => text($("an-speed"), e.message));
}
for (const b of document.querySelectorAll("#tabs button")) b.addEventListener("click", () => show(b.dataset.tab));

// ----- live events
let source = null;
function listen() {
  if (source) source.close();
  source = new EventSource("/api/events");
  source.onmessage = (m) => onEvent(JSON.parse(m.data));
  api("GET", "/api/status").then((s) => { setStatus(s); syncTimezone(s); }).catch(() => {});
}

function addLog(who, words, cls) {
  const li = document.createElement("li");
  if (cls) li.className = cls;
  const w = document.createElement("span"); w.className = "who"; text(w, who);
  li.append(w, document.createTextNode(words));
  $("log").prepend(li);
  while ($("log").children.length > 100) $("log").lastChild.remove();
}

function onEvent(e) {
  if (e.kind === "animation") {
    if (e.status) showAnimation(e.status);
    if (e.gesture) addAnimationLog(e.gesture);
    return;
  }
  if (e.kind === "recording") { showRecording(e); return; }
  if (e.kind === "live") { showLive(e); return; }
  if (e.kind === "state") text($("home-state"), e.state);
  else if (e.kind === "heard") addLog("You:", e.text);
  else if (e.kind === "replied" && e.text) addLog("Character:", e.text + (e.interrupted ? " (interrupted)" : ""));
  else if (e.kind === "error" || e.kind === "stage_error") addLog("Problem:", e.message, "error");
  else if (e.kind === "timer") {
    addLog(e.type === "wake" ? "Wake-up:" : e.type === "alarm" ? "Alarm:" : "Timer:", e.label + (e.did ? " " + e.did : " set"));
    if (e.did === "rang") showRinging([e.label]);
    if (!$("day").hidden) api("GET", "/api/day").then(showDayStatus).catch(() => {});
    else if (e.did && e.did !== "rang") api("GET", "/api/status").then((s) => showRinging(s.ringing)).catch(() => {});
  }
  else if (e.kind === "searching") addLog("Searching:", e.query);
  else if (e.kind === "memory" || e.kind === "replied") api("GET", "/api/status").then(setStatus).catch(() => {});
  else if (e.kind === "character") { refreshCharacters(); api("GET", "/api/status").then(setStatus).catch(() => {}); }
  else if (e.kind === "button") text($("btn-listen"), e.did === "listen on" ? "Stop listening" : "Start listening");
  else if (e.kind === "microphone") showMicrophone(e);
  else if (e.kind === "wake word") addLog("Heard its name:", e.phrase || "");
}

// The microphone: on, off from here or the button, or off by the mute switch.
function showMicrophone(s) {
  if (s.muted === undefined) return;
  const bySwitch = !!(s.mute_switch ?? s.switch);
  $("home-muted").hidden = !s.muted;
  $("btn-unmute").hidden = bySwitch;
  text($("mic-status"), !s.muted ? "On." : bySwitch ? "Off, by the mute switch on the device." : "Off.");
  text($("btn-mic"), s.muted ? "Turn the microphone on" : "Turn the microphone off");
  $("btn-mic").hidden = bySwitch;
  state.muted = !!s.muted;
}

async function setMicrophone(muted, where) {
  try { showMicrophone(Object.assign(await api("POST", "/api/microphone", { muted }), { muted })); text(where, ""); }
  catch (err) { text(where, err.message); }
}
$("btn-storage-seen").addEventListener("click", async () => {
  await api("POST", "/api/storage-recovered/seen", {});
  $("storage-recovered").hidden = true;
});
$("btn-mic").addEventListener("click", () => setMicrophone(!state.muted, $("mic-msg")));
$("btn-unmute").addEventListener("click", () => setMicrophone(false, $("mic-msg")));

// ----- the live view of the character's movement
const PARTS = { head: "Head", torso: "Body", eyes: "Eyes", brows: "Brows", expression: "Face", arm_left: "Left arm",
  arm_right: "Right arm", mouth: "Mouth", eyelids: "Eyelids" };
const DRIVES = [["energy", "Energy", 1.5], ["attention", "Attention", 1], ["boredom", "Boredom", 1],
  ["sleepiness", "Sleepiness", 1]];
const CAUSES = { command: "asked for", queued: "asked for, after another", speech: "the words", emotion: "a new feeling",
  mood: "its mood", idle: "by itself", reaction: "a reaction", listening: "listening to you" };

function clock(hour) {
  if (typeof hour !== "number") return "-";
  const h = Math.floor(hour), m = Math.floor((hour - h) * 60);
  return ((h + 11) % 12 + 1) + ":" + String(m).padStart(2, "0") + (h < 12 ? " am" : " pm");
}

function showAnimation(st) {
  if (!st || !st.state) return;
  text($("an-state"), st.state);
  text($("an-mood"), st.mood);
  const d = st.drives || {};
  text($("an-hour"), clock(d.hour));
  $("an-dozing").hidden = !d.dozing;
  const drives = $("an-drives");
  drives.replaceChildren();
  for (const [key, label, max] of DRIVES) {
    const row = document.createElement("label");
    const m = document.createElement("meter");
    m.min = 0; m.max = max; m.value = d[key] || 0;
    row.append(label + " ", m);
    drives.append(row);
  }
  const parts = $("an-channels");
  parts.replaceChildren();
  for (const [key, label] of Object.entries(PARTS)) {
    const chip = document.createElement("span");
    const holder = (st.channels || {})[key];
    chip.className = holder ? "busy" : "";
    text(chip, label + ": " + (holder ? holder.replace(/_/g, " ") : "free"));
    parts.append(chip);
  }
  const b = st.budget || {};
  text($("an-budget"), "By itself: " + (b.own || 0) + " of 10 gestures, " + (b.big || 0) + " of 2 big ones, " +
    (b.mood || 0) + " of 6 from its mood; " + (b.idle || 0) + " of 4 idle actions.");
  text($("an-speed"), (st.fps || 0) + " frames a second; " + (st.update_ms || 0) + " ms a frame to move it, " +
    (st.draw_ms || 0) + " ms to draw it.");
}

function addAnimationLog(g) {
  const li = document.createElement("li");
  const when = new Date(g.t * 1000).toLocaleTimeString();
  const name = (g.name || "").replace(/_/g, " ");
  let words;
  if (g.event === "emotion") words = "feels " + name;
  else if (g.event === "gesture_skipped") { words = name + " skipped: " + (g.why || ""); li.className = "muted"; }
  else if (g.event === "gesture_waiting") words = name + " waits for the gesture before it";
  else words = name + (g.played ? " (as " + g.played.replace(/_/g, " ") + ")" : "") + " - " + (CAUSES[g.cause] || g.cause || "");
  const w = document.createElement("span"); w.className = "who"; text(w, when);
  li.append(w, document.createTextNode(" " + words));
  $("an-log").prepend(li);
  while ($("an-log").children.length > 60) $("an-log").lastChild.remove();
}

// ----- the gesture editor
const TRACK_COLOURS = ["#3a5bd9", "#d9663a", "#2e9e5b", "#a03ad9", "#c9a227", "#d93a7a", "#3ab3d9", "#666"];
let library = null, editing = null;

async function loadEditor() {
  library = await api("GET", "/api/motions");
  $("ed-missing").hidden = library.ready;
  $("ed-body").hidden = !library.ready;
  if (!library.ready) return;
  const pick = $("ed-pick");
  const keep = editing && library.clips[editing.name] ? editing.name : null;
  pick.replaceChildren();
  const order = { gesture: 0, idle: 1, reaction: 2 };
  const names = Object.keys(library.clips).sort((a, b) =>
    (order[library.clips[a].kind] - order[library.clips[b].kind]) || a.localeCompare(b));
  for (const name of names) {
    const c = library.clips[name];
    const o = document.createElement("option");
    o.value = name;
    o.textContent = name.replace(/_/g, " ") + " (" + c.kind + (c.origin !== "built in" ? ", " + c.origin : "") +
      (!c.can ? ", this character cannot" : c.as ? ", here as " + c.as.replace(/_/g, " ") : "") + ")";
    pick.append(o);
  }
  pick.value = keep || names[0];
  editClip(pick.value, false);
}

function editClip(name, isNew, from) {
  // A new gesture starts from what the editor shows now.
  const c = isNew ? JSON.parse(JSON.stringify(from || library.clips[$("ed-pick").value])) : library.clips[name];
  editing = { name, isNew, entry: c };
  $("ed-name").value = name;
  $("ed-name").readOnly = !isNew;
  $("ed-kind").value = isNew ? "gesture" : c.kind;
  $("ed-kind").disabled = !isNew && c.origin !== "added";
  $("ed-words").value = c.words || "";
  $("ed-big").checked = !!c.big;
  $("ed-mirror").checked = !!c.mirror;
  text($("ed-origin"), isNew ? "new, not saved yet" : c.origin === "built in" ? "" : c.origin + " on this device");
  $("btn-ed-reset").hidden = isNew || c.origin === "built in";
  text($("btn-ed-reset"), c.origin === "added" ? "Delete" : "Put back as built");
  showWordsRow();
  const body = $("ed-tracks");
  body.replaceChildren();
  for (const [control, keys] of Object.entries(c.tracks)) addTrackRow(control, keys);
  drawGraph();
  text($("ed-msg"), "");
}

function showWordsRow() {
  $("ed-words-row").hidden = $("ed-kind").value !== "gesture" || !(editing && (editing.isNew || editing.entry.origin === "added"));
}

function addTrackRow(control, keys) {
  const tr = document.createElement("tr");
  const select = document.createElement("select");
  for (const k of library.controls) {
    const o = document.createElement("option");
    o.value = k; o.textContent = k.replace(/_/g, " ");
    select.append(o);
  }
  select.value = control;
  const input = document.createElement("input");
  const short = (x) => String(Math.round(x * 1000) / 1000);
  input.value = keys.map((k) => short(k[0]) + ":" + short(k[1])).join(", ");
  const remove = document.createElement("button");
  remove.className = "small danger";
  text(remove, "Remove");
  remove.addEventListener("click", () => { tr.remove(); drawGraph(); });
  for (const el of [select, input]) el.addEventListener("input", drawGraph);
  const cells = [select, input, remove].map((el) => { const td = document.createElement("td"); td.append(el); return td; });
  tr.append(...cells);
  $("ed-tracks").append(tr);
}

// The tracks as written in the table: {control: [[seconds, value], ...]}, or an error message.
function readTracks() {
  const tracks = {};
  for (const tr of $("ed-tracks").children) {
    const control = tr.querySelector("select").value;
    const keys = [];
    for (const part of tr.querySelector("input").value.split(",")) {
      const m = part.trim().match(/^(-?[\d.]+)\s*:\s*(-?[\d.]+)$/);
      if (!m) return { error: control.replace(/_/g, " ") + ": write keys as seconds:value, separated by commas" };
      keys.push([Number(m[1]), Number(m[2])]);
    }
    if (tracks[control]) return { error: control.replace(/_/g, " ") + " has two tracks" };
    tracks[control] = keys;
  }
  return { tracks };
}

function drawGraph() {
  const svg = $("ed-graph");
  svg.replaceChildren();
  const { tracks } = readTracks();
  const ns = "http://www.w3.org/2000/svg";
  const line = (x1, y1, x2, y2, colour) => {
    const l = document.createElementNS(ns, "line");
    l.setAttribute("x1", x1); l.setAttribute("y1", y1); l.setAttribute("x2", x2); l.setAttribute("y2", y2);
    l.setAttribute("stroke", colour); l.setAttribute("stroke-width", "1");
    svg.append(l);
  };
  const y = (v) => 85 - v * 70;
  line(0, y(0), 600, y(0), "#999");
  line(0, y(1), 600, y(1), "#ddd");
  line(0, y(-1), 600, y(-1), "#ddd");
  if (!tracks) return;
  const end = Math.max(0.5, ...Object.values(tracks).map((k) => k.length ? k[k.length - 1][0] : 0));
  Object.entries(tracks).forEach(([control, keys], i) => {
    const p = document.createElementNS(ns, "polyline");
    p.setAttribute("points", keys.map((k) => (k[0] / end * 590 + 5).toFixed(1) + "," + y(k[1]).toFixed(1)).join(" "));
    p.setAttribute("fill", "none");
    p.setAttribute("stroke", TRACK_COLOURS[i % TRACK_COLOURS.length]);
    p.setAttribute("stroke-width", "2");
    const t = document.createElementNS(ns, "title");
    t.textContent = control;
    p.append(t);
    svg.append(p);
  });
}

// The clip as the editor shows it, ready to send.
function editedClip() {
  const read = readTracks();
  if (read.error) throw new Error(read.error);
  const base = editing.entry;
  const clip = { kind: $("ed-kind").value, tracks: read.tracks, big: $("ed-big").checked, mirror: $("ed-mirror").checked };
  for (const keep of ["takes", "exact", "instead", "holds", "stroke"]) if (base[keep] !== undefined) clip[keep] = base[keep];
  // A new gesture needs the control it moves most; a changed one keeps what it needed.
  if (editing.isNew || !base.needs) {
    let most = null, size = -1;
    for (const [control, keys] of Object.entries(read.tracks)) {
      const peak = Math.max(...keys.map((k) => Math.abs(k[1])));
      if (peak > size) { size = peak; most = control; }
    }
    clip.needs = most ? [most] : [];
  } else clip.needs = base.needs;
  if ($("ed-words").value.trim()) clip.words = $("ed-words").value.trim();
  return clip;
}

$("ed-pick").addEventListener("change", () => editClip($("ed-pick").value, false));
$("ed-kind").addEventListener("change", showWordsRow);
$("btn-ed-add").addEventListener("click", () => { addTrackRow(library.controls[0], [[0, 0], [0.5, 0.3], [1, 0]]); drawGraph(); });
$("btn-ed-new").addEventListener("click", () => {
  const name = (prompt("A name for the new gesture (lower-case letters and _, such as little_dance)") || "").trim();
  if (!name) return;
  if (!/^[a-z_]{1,40}$/.test(name) || library.clips[name]) { alert("Choose another name: lower-case letters and _."); return; }
  let now;
  try { now = Object.assign({}, editing.entry, editedClip(), { origin: "added" }); }
  catch (err) { text($("ed-msg"), err.message); return; }
  editClip(name, true, now);
});
$("btn-ed-try").addEventListener("click", async () => {
  try {
    await api("POST", "/api/motions/try", editedClip());
    text($("ed-msg"), "Playing on the device's screen.");
  } catch (err) { text($("ed-msg"), err.message); }
});
$("btn-ed-save").addEventListener("click", async () => {
  // A new gesture is saved under the name in its box ("App test" becomes app_test).
  const name = !editing.isNew ? editing.name :
    $("ed-name").value.trim().toLowerCase().replace(/[^a-z]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 40);
  if (!name) { text($("ed-msg"), "Give it a name first, such as little_dance."); return; }
  if (editing.isNew && library.clips[name] && !confirm("There is already a gesture called " + name.replace(/_/g, " ") +
    ". Replace it with this one?")) return;
  try {
    await api("PUT", "/api/motions/" + name, editedClip());
    editing.name = name;
    editing.isNew = false;
    await loadEditor();
    text($("ed-msg"), "Saved on this device.");
  } catch (err) { text($("ed-msg"), err.message); }
});
$("btn-ed-reset").addEventListener("click", async () => {
  const added = editing.entry.origin === "added";
  if (!confirm(added ? "Delete this gesture from the device?" : "Put this gesture back as it was built?")) return;
  try {
    await api("DELETE", "/api/motions/" + editing.name);
    await loadEditor();
    text($("ed-msg"), added ? "Deleted." : "Put back as built.");
  } catch (err) { text($("ed-msg"), err.message); }
});

// ----- recording a gesture with a phone: a QR code and a link with a one-time code
let recording = null;

async function loadRecording() {
  showRecording(await api("GET", "/api/record"));
}

function showRecording(r) {
  // A recording started elsewhere (another page) is news without its link: ask for the whole of it.
  if (r.active && !r.links && !(recording && recording.links)) { loadRecording().catch(() => {}); return; }
  const before = recording && recording.saved ? recording.saved.length : 0;
  recording = r.active ? Object.assign(recording || {}, r) : null;
  $("rec-box").hidden = !recording;
  text($("btn-rec-start"), recording ? "Show a new QR code" : "Show a QR code for my phone");
  $("btn-rec-start").hidden = !!recording;
  if (!recording) return;
  $("rec-qr").hidden = !recording.qr;
  if (recording.qr) $("rec-qr").src = recording.qr;
  $("rec-link").href = recording.links[0];
  text($("rec-link"), recording.links[0]);
  text($("rec-short"), recording.short);
  text($("rec-code"), recording.code);
  const said = {
    waiting: "Waiting for your phone to open the link…",
    opened: "Your phone has the recorder open.",
    ready: recording.whole_body
      ? "A whole-body movement came from your phone and is playing on the screen. " + (recording.moves || []).join(" ")
      : "A gesture came from your phone and is playing on the screen" +
        (recording.moves && recording.moves.length ? ": it " + recording.moves.join(", ") : "") + ".",
    saved: "Kept: " + (recording.saved || []).map((n) => n.replace(/_/g, " ")).join(", ") + ". Record another on your phone, or stop.",
  }[recording.state] || "";
  const assets = recording.assets === "fetching" ? " The device is downloading what your phone needs to read movement (once only)." :
    recording.assets === "failed" ? " " + recording.assets_message : "";
  const minutes = Math.round((recording.expires_in || 0) / 60);
  text($("rec-status"), said + assets + (minutes ? " The link works for another " + minutes + " minute" + (minutes === 1 ? "" : "s") + "." : ""));
  if ((recording.saved || []).length > before) loadEditor().catch((e) => text($("ed-msg"), e.message));
}

async function startRecording() {
  text($("rec-msg"), "");
  try {
    recording = null;
    showRecording(await api("POST", "/api/record", {}));
  } catch (err) { text($("rec-msg"), err.message); }
}
// While a link is open, the minutes it has left (and its end) show without a reload.
setInterval(() => {
  if (recording && !$("animation").hidden) loadRecording().catch(() => {});
}, 60000);
$("btn-rec-start").addEventListener("click", startRecording);
$("btn-rec-new").addEventListener("click", startRecording);
$("btn-rec-end").addEventListener("click", async () => {
  try { await api("DELETE", "/api/record"); showRecording({ active: false }); } catch (err) { text($("rec-msg"), err.message); }
});

// ----- "Copy me" live, and the live camera (live_motion.py, phone_apps.py, tls.py)
let liveState = null;

async function loadLive() {
  showLive(await api("GET", "/api/live"));
}

function liveSaid(s) {
  const l = s.live;
  if (!l.on) return "";
  const from = l.kind === "app" ? l.label : "the phone's live camera";
  if (!l.fps) return "Connected to " + from + ", but nothing is coming from it yet. Check that it is open, on the same " +
    "Wi-Fi as the device" + (l.kind === "app" ? ", and showing this address: " + s.app.address : "") + ".";
  if (l.calibrating) return "Copying " + from + ": hold still for a moment, facing the phone…";
  if (!l.face) return "Copying " + from + ": it cannot see your face.";
  return "The character is copying you, from " + from + " (" + l.fps + " times a second).";
}

function showLive(s) {
  const wasRecording = liveState && liveState.live.recording != null;
  liveState = s;
  // A recording that stopped by itself at the longest a gesture can be: fetch it for the editor.
  if (wasRecording && s.live.recording == null && s.live.recorded) setTimeout(finishLiveRecording, 0);
  $("live-camera").checked = !!s.camera;
  $("live-camera").disabled = !s.https.available && !s.camera;
  text($("live-camera-msg"), !s.https.available ? "This device cannot offer it." : s.https.error ? s.https.error :
    s.https.on ? "On: the recorder's link now starts with https." : "");
  $("live-fingerprint").hidden = !s.https.fingerprint;
  text($("live-fingerprint"), "To check a warning is about this device: its certificate's SHA-256 fingerprint begins " +
    (s.https.fingerprint || "").slice(0, 23) + ".");
  const host = recording && recording.links ? new URL(recording.links[0]).hostname : location.hostname;  // as the warning names it
  for (const el of document.querySelectorAll(".live-host")) text(el, host);
  if (document.activeElement !== $("app-address") && !$("app-address").value) $("app-address").value = s.app.address || "";
  if (!s.app.connected && document.activeElement !== $("app-kind")) $("app-kind").value = s.app.app;
  $("btn-app-stop").hidden = !s.app.connected;
  text($("btn-app-connect"), s.app.connected ? "Connect again" : "Connect");
  text($("live-status"), s.app.error || liveSaid(s));
  $("live-tools").hidden = !s.live.on;
  $("live-mirror").checked = !!s.live.mirror;
  $("live-flip-row").hidden = s.live.kind !== "app";
  $("live-flip").checked = !!s.live.flip;
  const r = s.live.recording;
  text($("btn-live-record"), r == null ? "Record a gesture" : "Stop recording (" + Math.round(r) + " s)");
  $("btn-live-record").classList.toggle("danger", r != null);
}

async function liveCall(method, path, body, where) {
  try { showLive(await api(method, path, body)); if (!where) text($("live-record-msg"), ""); }
  catch (err) { text(where || $("live-status"), err.message); }
}

$("live-camera").addEventListener("change", (e) => {
  text($("live-camera-msg"), e.target.checked ? "Starting…" : "");
  liveCall("PUT", "/api/live", { camera: e.target.checked }, $("live-camera-msg"))
    .then(() => { if (recording) startRecording(); });          // a link for the other kind of connection
});
$("btn-app-connect").addEventListener("click", () =>
  liveCall("POST", "/api/live/app", { app: $("app-kind").value, address: $("app-address").value.trim() }));
$("btn-app-stop").addEventListener("click", () => liveCall("DELETE", "/api/live/app"));
$("live-mirror").addEventListener("change", (e) => liveCall("PUT", "/api/live", { mirror: e.target.checked }));
$("live-flip").addEventListener("change", (e) => liveCall("PUT", "/api/live", { flip: e.target.checked }));
$("btn-live-recentre").addEventListener("click", () => liveCall("POST", "/api/live/recentre"));
$("btn-live-record").addEventListener("click", async () => {
  const recordingNow = liveState && liveState.live.recording != null;
  if (!recordingNow) return liveCall("POST", "/api/live/record", { action: "start" }, $("live-record-msg"));
  finishLiveRecording();
});

async function finishLiveRecording() {
  try {
    const got = await api("POST", "/api/live/record", { action: "stop" });
    await loadEditor();
    editClip("my_gesture", true, got.entry);
    text($("live-record-msg"), "Recorded: it " + got.moves.join(", ") + " (" + got.seconds.toFixed(1) + " s), playing on " +
      "the screen. It is in the editor below: give it a name and what it means, then Save.");
    $("ed-name").scrollIntoView({ behavior: "smooth", block: "center" });
    loadLive().catch(() => {});
  } catch (err) { text($("live-record-msg"), err.message); loadLive().catch(() => {}); }
}
setInterval(() => {
  if (liveState && liveState.live.on && !$("animation").hidden) loadLive().catch(() => {});
}, 2000);

async function loadAnimation() {
  loadLive().catch((e) => text($("live-status"), e.message));
  loadRecording().catch((e) => text($("rec-msg"), e.message));
  loadEditor().catch((e) => text($("ed-msg"), e.message));
  const a = await api("GET", "/api/animation");
  $("an-log").replaceChildren();
  for (const g of a.log) addAnimationLog(g);
  showAnimation(a.status);
  if (!a.status || !a.status.state) text($("an-speed"), "The character's screen has not reported yet.");
}

function setStatus(s) {
  text($("home-state"), s.state);
  text($("btn-listen"), s.listening ? "Stop listening" : "Start listening");
  showMicrophone(s);
  if (s.ringing) showRinging(s.ringing);
  if (s.storage_recovered !== undefined) $("storage-recovered").hidden = !s.storage_recovered;
  if (s.backlight !== undefined) $("dev-brightness-row").hidden = !s.backlight;
  if (s.usb) {
    $("usb-status").hidden = !s.usb.available;
    text($("usb-status"), s.usb.connected ? "A computer is connected by USB: it reaches this page at " + s.usb.address + "." :
      "No Wi-Fi? A computer connected to the device's USB-C port by cable reaches this page at " + (s.usb.address || "") + ".");
  }
  if (s.wake_word) {
    const w = s.wake_word;
    text($("dev-wake-status"), w.error ? "The wake word cannot listen: " + w.error :
      w.words.length ? "It listens for: " + w.words.join(", ") + (w.ready ? "." : " (getting ready).") +
        " Say it clearly, then what you want, in one breath or after it looks up. Names with clear consonants work best." : "");
  }
  if (s.providers) showSteps(s);
  if (s.timezone !== undefined) text($("tz-device"), s.timezone || "not set");
}

// What a new owner still has to do, with links to where it is done.
function showSteps(s) {
  const keyFor = { openai: "openai", anthropic: "anthropic", groq: "groq", openrouter: "openrouter",
                   gemini: "openai_compatible", custom: "openai_compatible", elevenlabs: "elevenlabs",
                   cartesia: "cartesia", fishaudio: "fishaudio", inworld: "inworld", minimax: "minimax",
                   deepgram: "deepgram", google: "google_tts" };
  const hasKey = (p) => !p || p === "local" || !keyFor[p] || s.keys[keyFor[p]];
  const steps = [
    ["Choose a language model and add its key", s.providers.llm && hasKey(s.providers.llm), "services"],
    ["Choose a voice and add its key", s.providers.tts && hasKey(s.providers.tts), "services"],
    ["Add a character", s.characters > 0, "characters"],
    ["Press the button on the device (or Start listening) and say hello", s.characters > 0 && s.providers.llm && s.providers.tts, "home"],
  ];
  const list = $("start-steps");
  list.replaceChildren();
  for (const [label, done, tab] of steps) {
    const li = document.createElement("li");
    if (done) li.className = "done";
    const a = document.createElement("a"); text(a, label);
    a.addEventListener("click", () => show(tab));
    li.append(a);
    list.append(li);
  }
  $("start").hidden = steps.every(([, done]) => done);
}

// On first setup, give the device this phone's time zone, so alarms ring at the right time.
async function syncTimezone(s) {
  const phone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  text($("tz-phone"), phone);
  let asked = false;
  try { asked = localStorage.getItem("tz-set") === "1"; } catch (e) {}
  $("btn-tz").hidden = !s.can_set_timezone;
  if (!asked && phone && s.can_set_timezone && s.timezone !== phone) {
    try { const r = await api("POST", "/api/timezone", { zone: phone }); text($("tz-device"), r.timezone); } catch (e) {}
    try { localStorage.setItem("tz-set", "1"); } catch (e) {}
  }
}

$("btn-listen").addEventListener("click", () => api("POST", "/api/button").then(setStatus).catch((e) => addLog("Problem:", e.message, "error")));
$("btn-interrupt").addEventListener("click", () => api("POST", "/api/interrupt").catch(() => {}));
$("say-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const words = $("say-text").value.trim();
  if (!words) return;
  $("say-text").value = "";
  try { await api("POST", "/api/say", { text: words }); } catch (err) { addLog("Problem:", err.message, "error"); }
});

// ----- characters
// ----- backgrounds: the picture behind the characters (backgrounds.py)
let backgroundsInfo = null;

async function loadBackgrounds() {
  backgroundsInfo = await api("GET", "/api/backgrounds");
  showBackgrounds();
}

function showBackgrounds() {
  const info = backgroundsInfo;
  if (!info) return;
  const grid = $("bg-grid");
  grid.replaceChildren();
  let festiveShown = false;
  for (const p of info.pictures) {
    if (p.kind === "festive" && !festiveShown) {
      const heading = document.createElement("div");
      heading.className = "bg-group";
      text(heading, "Festive (shown by themselves on special days, if you like: Your day tab)");
      grid.append(heading);
      festiveShown = true;
    }
    const item = document.createElement("div");
    item.className = "bg-item" + (p.id === info.default.choice ? " on" : "");
    const pick = document.createElement("button");
    pick.type = "button";
    pick.title = p.title;
    pick.dataset.picture = p.id;
    if (p.kind === "special") {
      const swatch = document.createElement("span");
      swatch.className = "bg-swatch bg-" + p.id;
      pick.append(swatch);
    } else {
      const img = document.createElement("img");
      img.src = "/api/backgrounds/" + p.id + "/thumb";
      img.alt = "";
      img.loading = "lazy";
      pick.append(img);
    }
    const label = document.createElement("span");
    label.className = "bg-title";
    text(label, p.id === "auto" ? "With the time of day" : p.id === "none" ? "None" : p.id === "photos" ? "Your pictures, in turn" : p.title);
    pick.append(label);
    pick.addEventListener("click", () => setBackgroundDefault({ choice: p.id }));
    item.append(pick);
    if (p.kind === "yours") {
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "small secondary bg-remove";
      text(remove, "Remove");
      remove.addEventListener("click", () => removeBackground(p));
      item.append(remove);
    }
    grid.append(item);
  }
  $("bg-dim").value = info.default.dim || 0;
  $("bg-parallax").checked = info.default.parallax !== false;
  const select = $("char-bg");
  const keep = select.value;
  select.replaceChildren(new Option("The same as the others", "default"));
  for (const p of info.pictures) select.add(new Option(p.id === "auto" ? "Changes with the time of day" : p.title, p.id));
  select.value = keep || "default";
}

async function setBackgroundDefault(changes) {
  try {
    backgroundsInfo = await api("PUT", "/api/backgrounds/default", changes);
    showBackgrounds();
    text($("bg-msg"), "");
  } catch (e) { text($("bg-msg"), e.message); }
}

async function addBackground(file) {
  text($("bg-msg"), "Adding the picture…");
  try {
    const title = file.name.replace(/\.[^.]*$/, "");
    backgroundsInfo = await api("POST", "/api/backgrounds?title=" + encodeURIComponent(title), undefined, file);
    await setBackgroundDefault({ choice: backgroundsInfo.added });
  } catch (e) { text($("bg-msg"), e.message); }
}

async function removeBackground(p) {
  if (!confirm("Remove “" + p.title + "”? Characters that used it go back to the default.")) return;
  try {
    backgroundsInfo = await api("DELETE", "/api/backgrounds/" + p.id);
    showBackgrounds();
    text($("bg-msg"), "");
  } catch (e) { text($("bg-msg"), e.message); }
}

async function loadCharacterBackground(cid) {
  const own = await api("GET", "/api/characters/" + cid + "/background");
  $("char-bg").value = own.choice || "default";
}

async function refreshCharacters() {
  const data = await api("GET", "/api/characters");
  loadBackgrounds().catch((e) => text($("bg-msg"), e.message));
  state.active = data.active;
  state.characters = data.characters;
  const list = $("char-list");
  list.replaceChildren();
  for (const c of data.characters) {
    const li = document.createElement("li");
    if (c.id === data.active) li.className = "active";
    if (c.preview) { const img = document.createElement("img"); img.src = "/api/characters/" + c.id + "/preview"; img.alt = ""; li.append(img); }
    const name = document.createElement("div"); text(name, c.name); li.append(name);
    const cannot = Object.entries(c.capabilities || {}).filter(([, v]) => !v).map(([k]) => k.replace("_", " "));
    if (cannot.length) { const n = document.createElement("small"); text(n, "cannot: " + cannot.join(", ")); li.append(n); }
    if (c.drawing === "vrm") {
      // A VRM avatar's maker's terms, as the avatar carries them.
      const n = document.createElement("small"); text(n, "3D avatar. " + (c.licence || "")); li.append(n);
      if (c.licence_note) { const w = document.createElement("small"); w.className = "error"; text(w, c.licence_note); li.append(w); }
    }
    const actions = [["Use", () => api("POST", "/api/characters/" + c.id + "/activate").then(refreshCharacters)],
                     ["Edit", () => editCharacter(c)],
                     ["Save copy", () => { location.href = "/api/characters/" + c.id + "/export"; }],
                     ["Remove", () => confirm("Remove " + c.name + "? Its memories stay until you clear them.") &&
                                       api("DELETE", "/api/characters/" + c.id).then(refreshCharacters)]];
    for (const [label, fn] of actions) {
      const b = document.createElement("button"); text(b, label);
      b.addEventListener("click", () => Promise.resolve(fn()).catch((e) => text($("char-error"), e.message)));
      li.append(b);
    }
    list.append(li);
  }
  const active = data.characters.find((c) => c.id === data.active);
  text($("home-name"), active ? active.name : "No character yet");
  text($("mem-name"), active ? active.name : "");
  $("home-preview").hidden = !(active && active.preview);
  if (active && active.preview) $("home-preview").src = "/api/characters/" + active.id + "/preview?" + Date.now();
}

$("bg-dim").addEventListener("change", () => setBackgroundDefault({ dim: Number($("bg-dim").value) }));
$("bg-parallax").addEventListener("change", () => setBackgroundDefault({ parallax: $("bg-parallax").checked }));
$("bg-file").addEventListener("change", (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (file) addBackground(file);
});
$("char-bg").addEventListener("change", async () => {
  try {
    await api("PUT", "/api/characters/" + state.editing + "/background", { choice: $("char-bg").value });
    text($("char-bg-msg"), "");
  } catch (e) { text($("char-bg-msg"), e.message); }
});
$("char-file").addEventListener("change", async () => {
  const file = $("char-file").files[0];
  if (!file) return;
  text($("char-error"), file.name.toLowerCase().endsWith(".vrm") ? "Checking the avatar and fitting its pictures to this device…" : "Checking and installing…");
  try {
    const q = $("char-replace").checked ? "?replace=1" : "";
    await api("POST", "/api/characters" + q, undefined, file);
    text($("char-error"), "");
    refreshCharacters();
  } catch (err) { text($("char-error"), err.message); }
  $("char-file").value = "";
});

async function editCharacter(c) {
  const data = await api("GET", "/api/characters/" + c.id + "/persona");
  state.editing = c.id;
  text($("edit-name"), c.name);
  $("edit-persona").value = data.persona;
  $("edit-voice").value = (data.device && data.device.voice) || "";
  $("edit-voice-provider").value = (data.device && data.device.voice_provider) || "";
  $("btn-clone-sample").hidden = !data.has_voice_sample;
  text($("char-voices-msg"), "");
  text($("edit-msg"), "");
  const charProvider = data.device && data.device.voice_provider;
  const ttsProvider = state.settings && state.settings.tts && state.settings.tts.provider;
  const keyed = (data.cloning_providers || []).find((p) => p.has_key);
  $("clone-provider").value = CLONE_INFO[charProvider] ? charProvider :
    CLONE_INFO[ttsProvider] ? ttsProvider : keyed ? keyed.id : "elevenlabs";
  shapeCharVoice();
  shapeClone();
  for (const q of PERSONA_QUESTIONS) $("pf-" + q).value = (data.form && data.form[q]) || "";
  if (!$("pf-name").value) $("pf-name").value = c.name;
  $("char-edit").hidden = false;
  loadFeelings(c.id);
  loadMovement(c.id);
  loadVrm(c).catch((e) => text($("vrm-msg"), e.message));
  loadCharacterBackground(c.id).catch((e) => text($("char-bg-msg"), e.message));
  $("char-edit").scrollIntoView();
}

// A 3D avatar: full-body or light animation, the shot, a simpler drawing, and animations of its own.
async function loadVrm(c) {
  const box = $("edit-vrm");
  box.hidden = c.drawing !== "vrm";
  if (box.hidden) return;
  const data = await api("GET", "/api/characters/" + c.id + "/animations");
  $("vrm-style").value = data.settings.animation;
  $("vrm-shot").value = data.settings.shot;
  $("vrm-lite").checked = data.settings.quality === "lite";
  text($("vrm-msg"), "");
  // What it costs this device, and anything the device did to cope.
  const notes = [];
  if (data.cost && data.cost.triangles !== undefined) {
    notes.push("It has " + data.cost.triangles.toLocaleString() + " triangles and " + data.cost.texture_mb +
      " MB of textures (kept at " + data.cost.textures + " pixels); showing it takes about " + data.cost.estimate_mb + " MB.");
    if (data.cost.lite) notes.push("It is heavy for this device, so it starts with a simpler drawing.");
  }
  if (data.failsafe) notes.push("The screen stopped while showing it (" + data.failsafe.why + "), so it is now drawn more simply.");
  if (data.quality_seen) notes.push("The screen was slow with it, so the device made it simpler: " + data.quality_seen.change + ".");
  text($("vrm-cost"), notes.join(" "));
  $("vrm-anims").hidden = data.settings.animation !== "full";
  const list = $("vrm-anim-list");
  list.replaceChildren();
  if (!data.added.length) {
    const li = document.createElement("li");
    text(li, "None yet: the built-in animations play.");
    list.append(li);
  }
  for (const a of data.added) {
    const li = document.createElement("li");
    const what = document.createElement("span");
    text(what, a.label + " \u2014 " + (a.title || a.role) + " (" + a.seconds + " s)" + (a.words ? ", for \u201c" + a.words + "\u201d" : ""));
    li.append(what, " ");
    if (data.active) {
      const t = document.createElement("button"); t.className = "secondary"; text(t, "Try");
      t.addEventListener("click", () => api("POST", "/api/characters/" + c.id + "/animations/" + a.role + "/try")
        .catch((e) => text($("vrm-anim-msg"), e.message)));
      li.append(t, " ");
    }
    const r = document.createElement("button"); r.className = "secondary"; text(r, "Remove");
    r.addEventListener("click", () => api("DELETE", "/api/characters/" + c.id + "/animations/" + a.role)
      .then(() => loadVrm(c)).catch((e) => text($("vrm-anim-msg"), e.message)));
    li.append(r);
    list.append(li);
  }
  const select = $("vrm-role");
  select.replaceChildren();
  for (const r of data.roles) {
    const o = document.createElement("option"); o.value = r.role; o.textContent = r.label; select.append(o);
  }
  const fresh = document.createElement("option"); fresh.value = "_new"; fresh.textContent = "A new gesture\u2026"; select.append(fresh);
  $("vrm-new-row").hidden = true;
  state.vrmCharacter = c;
}

$("vrm-role").addEventListener("change", () => { $("vrm-new-row").hidden = $("vrm-role").value !== "_new"; });
$("btn-save-vrm").addEventListener("click", async () => {
  const c = state.vrmCharacter;
  if (!c) return;
  try {
    await api("PUT", "/api/characters/" + c.id + "/animations", {
      animation: $("vrm-style").value, shot: $("vrm-shot").value, quality: $("vrm-lite").checked ? "lite" : "auto" });
    await loadVrm(c);
    text($("vrm-msg"), c.id === state.active ? "Saved; the character starts again on the screen." : "Saved.");
  } catch (e) { text($("vrm-msg"), e.message); }
});
$("vrm-file").addEventListener("change", async () => {
  const c = state.vrmCharacter, file = $("vrm-file").files[0];
  if (!c || !file) return;
  let role = $("vrm-role").value;
  const q = new URLSearchParams({ title: file.name.replace(/\.vrma$/i, "") });
  if (role === "_new") {
    role = $("vrm-new-name").value.trim().toLowerCase().replace(/[^a-z_]+/g, "_");
    q.set("words", $("vrm-new-words").value.trim());
  }
  text($("vrm-anim-msg"), "Checking the animation\u2026");
  try {
    await api("POST", "/api/characters/" + c.id + "/animations/" + encodeURIComponent(role) + "?" + q, undefined, file);
    await loadVrm(c);
    text($("vrm-anim-msg"), c.id === state.active ? "Added; the character starts again on the screen with it." : "Added.");
  } catch (e) { text($("vrm-anim-msg"), e.message); }
  $("vrm-file").value = "";
});

// Which of a character's own expressions shows each feeling (Live2D-format models that have some).
async function loadFeelings(cid) {
  const data = await api("GET", "/api/characters/" + cid + "/emotions");
  const list = $("feelings-list");
  list.replaceChildren();
  $("edit-feelings").hidden = !data.expressions.length;
  text($("feelings-msg"), "");
  for (const emotion of data.emotions) {
    const label = document.createElement("label");
    label.append(emotion.charAt(0).toUpperCase() + emotion.slice(1) + " ");
    const select = document.createElement("select");
    select.dataset.emotion = emotion;
    const none = document.createElement("option");
    none.value = "";
    none.textContent = "(movement only)";
    select.append(none);
    for (const name of data.expressions) {
      const option = document.createElement("option");
      option.value = name;
      option.textContent = name;
      select.append(option);
    }
    select.value = data.map[emotion] || "";
    label.append(select);
    list.append(label);
  }
}

// Which of the character's own parameters each kind of movement uses; tested live on the device's screen.
let movement = null;

function showTemperament(values) {
  $("tp-liveliness").value = values.liveliness;
  $("tp-expressiveness").value = values.expressiveness;
}

function showOwnMotions(own) {
  $("edit-own-motions").hidden = !own;
  text($("own-msg"), "");
  if (!own) return;
  const n = own.idle.length;
  text($("own-about"), own.studio
    ? "This character was made with Character Studio, which gave it a simple idle loop. The device's own " +
      "movement is made for characters like this, so the loop is off at first."
    : "This model came with " + n + " idle motion" + (n === 1 ? "" : "s") + " made by its artist. They play " +
      "one after another, and the device's own movement (looking at you, nodding, gestures) adds to them. " +
      "The device still blinks and moves the mouth itself.");
  $("own-setting").value = own.setting;
}

async function loadMovement(cid) {
  movement = await api("GET", "/api/characters/" + cid + "/movement");
  showOwnMotions(movement.own_motions);
  showTemperament(movement.temperament || movement.suggested);
  text($("temper-msg"), movement.temperament ? "" : "Suggested from the personality; not saved yet.");
  const switches = $("switch-list");
  switches.replaceChildren();
  for (const name of movement.gestures.concat(movement.actions || [])) {
    const label = document.createElement("label");
    const box = document.createElement("input");
    box.type = "checkbox";
    box.value = name;
    box.checked = !movement.gestures_off.includes(name);
    label.append(box, " " + name.replace(/_/g, " "));
    switches.append(label);
  }
  $("move-inactive").hidden = movement.active && movement.ready;
  $("move-body").hidden = !(movement.active && movement.ready);
  text($("move-msg"), "");
  const list = $("move-list"), more = $("move-more");
  list.replaceChildren();
  more.replaceChildren();
  if (!movement.ready) return;
  const MAIN = 14;           // head, body, eyes, arms, elbows, shoulders, smile; the face's details under "More"
  const current = {};
  for (const c of movement.current) current[c.control] = c;
  movement.controls.forEach((c, index) => {
    const row = document.createElement("div");
    row.className = "move";
    row.dataset.control = c.control;
    const name = document.createElement("span");
    text(name, c.label);
    const select = document.createElement("select");
    const auto = document.createElement("option");
    auto.value = "";
    const now = movement.saved[c.control] === undefined ? current[c.control] : null;
    auto.textContent = "Automatic" + (now ? " (" + now.param + ")" : " (none found)");
    const off = document.createElement("option");
    off.value = "-";
    off.textContent = "Not used";
    select.append(auto, off);
    for (const p of movement.params) {
      const option = document.createElement("option");
      option.value = p.name;
      option.textContent = p.name;
      select.append(option);
    }
    const saved = movement.saved[c.control];
    select.value = saved === undefined ? "" : saved === null ? "-" : saved.param;
    const reverse = document.createElement("label");
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = !!(saved && saved.invert);
    reverse.append(box, "Reverse");
    const test = document.createElement("button");
    test.className = "small";
    text(test, "Test");
    test.addEventListener("click", () => testMove(c.control, select.value, box.checked, current[c.control]));
    row.append(name, select, reverse, test);
    (index < MAIN ? list : more).append(row);
  });
  const gestures = $("move-gestures");
  gestures.replaceChildren();
  if (movement.gestures.length) gestures.append("Try a gesture: ");
  for (const g of movement.gestures) {
    const b = document.createElement("button");
    b.className = "small";
    text(b, g.replace(/_/g, " "));
    b.addEventListener("click", () => api("POST", "/api/stage/preview", { gesture: g }).catch((err) => text($("move-msg"), err.message)));
    gestures.append(b, " ");
  }
}

// Move one parameter to each end of its range and back, so the owner sees what the movement does.
async function testMove(control, chosen, reverse, current) {
  let param, lo, hi;
  if (chosen === "-") return;
  if (chosen) {
    const p = movement.params.find((x) => x.name === chosen);
    if (!p) return;
    param = p.name; lo = p.min[0]; hi = p.max[0];
  } else if (current) {
    param = current.param; lo = current.min; hi = current.max;
  } else return;
  if (reverse) [lo, hi] = [hi, lo];
  const step = (values, ease) => api("POST", "/api/stage/preview", { set: values, ease });
  try {
    await step({ [param]: lo }, 0.35);
    await new Promise((r) => setTimeout(r, 750));
    await step({ [param]: hi }, 0.5);
    await new Promise((r) => setTimeout(r, 900));
    await step({ [param]: null }, 0.4);
  } catch (err) { text($("move-msg"), err.message); }
}

$("btn-save-move").addEventListener("click", async () => {
  const controls = {};
  for (const row of $("edit-movement").querySelectorAll(".move")) {
    const value = row.querySelector("select").value;
    const invert = row.querySelector("input[type=checkbox]").checked;
    if (value === "-") controls[row.dataset.control] = null;
    else if (value) controls[row.dataset.control] = { param: value, invert };
    else if (invert) {
      const now = movement.current.find((c) => c.control === row.dataset.control);
      if (now) controls[row.dataset.control] = { param: now.param, invert: true };
    }
  }
  try {
    await api("PUT", "/api/characters/" + state.editing + "/movement", { controls });
    text($("move-msg"), "Saved. The character restarts on the screen with it.");
    setTimeout(() => loadMovement(state.editing).catch(() => {}), 4000);
  } catch (err) { text($("move-msg"), err.message); }
});

$("btn-save-own").addEventListener("click", async () => {
  try {
    const out = await api("PUT", "/api/characters/" + state.editing + "/own-motions", { setting: $("own-setting").value });
    showOwnMotions(out.own_motions);
    text($("own-msg"), "Saved.");
  } catch (err) { text($("own-msg"), err.message); }
});

$("btn-save-temper").addEventListener("click", async () => {
  try {
    await api("PUT", "/api/characters/" + state.editing + "/temperament",
      { liveliness: Number($("tp-liveliness").value), expressiveness: Number($("tp-expressiveness").value) });
    text($("temper-msg"), "Saved. It moves this way from now on.");
  } catch (err) { text($("temper-msg"), err.message); }
});

$("btn-suggest-temper").addEventListener("click", () => {
  if (!movement) return;
  showTemperament(movement.suggested);
  text($("temper-msg"), "Suggested from the personality. Save to keep it.");
});

$("btn-save-switches").addEventListener("click", async () => {
  const off = [...$("switch-list").querySelectorAll("input")].filter((b) => !b.checked).map((b) => b.value);
  try {
    await api("PUT", "/api/characters/" + state.editing + "/temperament", { gestures_off: off });
    text($("switch-msg"), off.length ? "Saved: it never makes these by itself." : "Saved.");
  } catch (err) { text($("switch-msg"), err.message); }
});

$("btn-reset-move").addEventListener("click", async () => {
  if (!confirm("Let the device choose this character's movement settings again?")) return;
  try {
    await api("DELETE", "/api/characters/" + state.editing + "/movement");
    text($("move-msg"), "Back to automatic.");
    setTimeout(() => loadMovement(state.editing).catch(() => {}), 4000);
  } catch (err) { text($("move-msg"), err.message); }
});

$("btn-card").addEventListener("click", async () => {
  const file = $("card-file").files[0];
  if (!file) { text($("card-msg"), "Choose a card first."); return; }
  try {
    const card = await api("POST", "/api/cards/read", undefined, file);
    $("edit-persona").value = card.persona;
    text($("card-msg"), "Read " + card.name + "'s card" + (card.creator ? " (by " + card.creator + ")" : "") +
      ". Check the persona below, then Save." + (card.name !== $("edit-name").textContent ? " The character keeps its own name on the device." : ""));
  } catch (err) { text($("card-msg"), err.message); }
});

$("btn-save-feelings").addEventListener("click", async () => {
  const map = {};
  for (const select of $("feelings-list").querySelectorAll("select")) map[select.dataset.emotion] = select.value;
  try {
    await api("PUT", "/api/characters/" + state.editing + "/emotions", { map });
    text($("feelings-msg"), "Saved.");
  } catch (err) { text($("feelings-msg"), err.message); }
});

const PERSONA_QUESTIONS = ["name", "personality", "story", "relationship", "likes", "dislikes", "speaking", "never", "greeting"];

$("btn-write-persona").addEventListener("click", async () => {
  const form = {};
  for (const q of PERSONA_QUESTIONS) form[q] = $("pf-" + q).value;
  try {
    const r = await api("PUT", "/api/characters/" + state.editing + "/persona", { form });
    $("edit-persona").value = r.persona;
    text($("edit-msg"), "Written and saved. Check it below; you can still edit it freely.");
  } catch (err) { text($("edit-msg"), err.message); }
});

function shapeCharVoice() {
  const vp = $("edit-voice-provider").value || (state.settings && state.settings.tts && state.settings.tts.provider) || "";
  $("btn-char-voices").hidden = !VOICE_LIST_PROVIDERS.has(vp);
}

function shapeClone() {
  const provider = $("clone-provider").value || "elevenlabs";
  const info = CLONE_INFO[provider] || CLONE_INFO.elevenlabs;
  $("clone-lang-row").hidden = !info.languages;
  $("voice-file").accept = info.accept;
  const help = $("clone-help");
  help.replaceChildren();
  help.append(info.hint + " ");
  if (keyStatus[info.key]) {
    help.append(info.label + " key saved ✓.");
  } else {
    help.append("No " + info.label + " key saved yet — ");
    const link = document.createElement("a");
    link.href = "#services";
    text(link, "add it under AI services");
    link.addEventListener("click", (e) => {
      e.preventDefault();
      show("services");
      const details = $("keys").closest("details");
      if (details) details.open = true;
      $("keys").scrollIntoView();
    });
    help.append(link, ".");
  }
}

$("edit-voice-provider").addEventListener("change", () => {
  shapeCharVoice();
  text($("char-voices-msg"), "");
});
$("clone-provider").addEventListener("change", shapeClone);

$("btn-char-voices").addEventListener("click", async () => {
  const vp = $("edit-voice-provider").value;
  text($("char-voices-msg"), "Loading…");
  try {
    const r = await api("GET", "/api/voices" + (vp ? "?provider=" + encodeURIComponent(vp) : ""));
    const list = $("edit-voice-list");
    list.replaceChildren();
    for (const v of r.voices) { const o = document.createElement("option"); o.value = v.id; o.label = v.name; list.append(o); }
    text($("char-voices-msg"), r.ok ? r.voices.length + " voices found: click the Voice ID box to choose" : "Could not list voices: " + r.message);
  } catch (err) { text($("char-voices-msg"), err.message); }
});

$("btn-save-persona").addEventListener("click", async () => {
  try {
    await api("PUT", "/api/characters/" + state.editing + "/persona", {
      persona: $("edit-persona").value,
      voice: $("edit-voice").value,
      voice_provider: $("edit-voice-provider").value,
    });
    text($("edit-msg"), "Saved.");
  } catch (err) { text($("edit-msg"), err.message); }
});

async function runClone(useSample) {
  const provider = $("clone-provider").value || "elevenlabs";
  const info = CLONE_INFO[provider] || CLONE_INFO.elevenlabs;
  let file;
  if (!useSample) {
    file = $("voice-file").files[0];
    if (!file) return text($("edit-msg"), "Choose an audio recording first.");
  }
  const q = new URLSearchParams({ provider });
  if (info.languages) q.set("language", $("clone-language").value || "en");
  if (useSample) q.set("use_package_sample", "1");
  text($("edit-msg"), "Cloning with " + info.label + "…");
  try {
    const r = await api("POST", "/api/characters/" + state.editing + "/voice?" + q, undefined, useSample ? "" : file);
    $("edit-voice").value = r.voice;
    $("edit-voice-provider").value = r.provider || provider;
    shapeCharVoice();
    if (!useSample) $("voice-file").value = "";
    text($("edit-msg"), "Done: this character now speaks with the cloned " + info.label + " voice.");
  } catch (err) { text($("edit-msg"), err.message); }
}

$("btn-clone").addEventListener("click", () => runClone(false));
$("btn-clone-sample").addEventListener("click", () => runClone(true));

// ----- memory
async function loadMemory() {
  if (!state.active) return;
  const m = await api("GET", "/api/memory/" + state.active);
  $("mem-notes").value = m.notes;
  const pick = $("mem-add-category");
  if (!pick.options.length) for (const c of m.categories) { const o = document.createElement("option"); o.value = c; o.textContent = c; pick.append(o); }
  text($("mem-now"), m.open ? "A conversation is going on (" + m.open.turns + " turns, began " + m.open.when + ")." : "");
  showMemories(m);
  showSessions(m);
  showDiary(m);
}

function memPath(kind, id) { return "/api/memory/" + state.active + "/" + kind + (id === undefined ? "" : "/" + id); }

function showMemories(m) {
  const box = $("mem-items");
  box.replaceChildren();
  text($("mem-item-count"), m.memories.length ? m.memories.length + " memories." : "None yet: they come from your conversations.");
  for (const category of m.categories) {
    const items = m.memories.filter((x) => x.category === category);
    if (!items.length) continue;
    const head = document.createElement("h4"); text(head, category.charAt(0).toUpperCase() + category.slice(1));
    const list = document.createElement("ul"); list.className = "log";
    for (const x of items) {
      const li = document.createElement("li");
      const words = document.createElement("span");
      const date = x.follow_up ? "  \u2014 to ask about after " + x.when + (x.asked ? " (asked)" : "") : x.when ? " (" + x.when + ")" : "";
      text(words, (x.pinned ? "\u{1F4CC} " : "") + x.text + date + "  " + "\u2605".repeat(x.importance || 3));
      const pin = button(x.pinned ? "Unpin" : "Pin", () => api("PUT", memPath("items", x.id), { pinned: !x.pinned }).then(loadMemory));
      const change = button("Change", () => {
        const input = document.createElement("input"); input.size = 50; input.value = x.text;
        const save = button("Save", () => api("PUT", memPath("items", x.id), { text: input.value }).then(loadMemory)
          .catch((err) => alert(err.message)));
        li.replaceChildren(input, " ", save, " ", button("Cancel", loadMemory));
        input.focus();
      });
      const forget = button("Forget", () => api("DELETE", memPath("items", x.id)).then(loadMemory), "danger");
      li.append(words, " ", pin, " ", change, " ", forget);
      list.append(li);
    }
    box.append(head, list);
  }
  if (m.retired && m.retired.length) {
    // What a conversation showed to be no longer true: kept out of mind, but the owner can bring one back.
    const more = document.createElement("details");
    const title = document.createElement("summary");
    text(title, "No longer true (" + m.retired.length + "): replaced or retired after a conversation");
    const list = document.createElement("ul"); list.className = "log";
    for (const x of m.retired) {
      const li = document.createElement("li");
      const words = document.createElement("span"); words.className = "muted"; text(words, x.text);
      li.append(words, " ", button("Restore", () => api("PUT", memPath("items", x.id), { status: "active" }).then(loadMemory)),
        " ", button("Forget", () => api("DELETE", memPath("items", x.id)).then(loadMemory), "danger"));
      list.append(li);
    }
    more.append(title, list);
    box.append(more);
  }
}

function showSessions(m) {
  const list = $("mem-sessions");
  list.replaceChildren();
  text($("mem-session-count"), m.session_count === 0 ? "None yet." :
    m.session_count > m.sessions.length ? "The newest " + m.sessions.length + " of " + m.session_count + "." : "");
  for (const e of m.sessions) {
    const li = document.createElement("li");
    const when = document.createElement("span"); when.className = "who";
    const t0 = new Date(e.start * 1000), t1 = new Date(e.end * 1000);
    const hm = (d) => d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    const from = hm(t0), to = hm(t1);
    text(when, e.when + " " + from + (to !== from ? "\u2013" + to : ""));
    const words = document.createElement("span");
    if (e.title) { const b = document.createElement("b"); text(b, e.title); words.append(b, " \u2014 "); }
    words.append((e.summary || (e.done ? "" : "(to be summed up)")) + (e.mood ? " (" + e.mood + ")" : ""));
    const show = button("Show words", async () => {
      const r = await api("GET", memPath("sessions", e.id));
      const name = $("mem-name").textContent || "Character";
      const log = document.createElement("ol"); log.className = "log";
      for (const t of r.turns) {
        const row = document.createElement("li");
        row.append("You: " + (t.user || ""), document.createElement("br"),
          name + ": " + (t.reply || "") + (t.interrupted ? " (interrupted)" : ""));
        log.append(row);
      }
      show.remove();
      li.append(log);
    });
    const edit = button("Change", () => {
      const box = document.createElement("textarea"); box.rows = 3; box.maxLength = 700; box.value = e.summary || "";
      const save = button("Save", () => api("PUT", memPath("sessions", e.id), { summary: box.value }).then(loadMemory)
        .catch((err) => alert(err.message)));
      li.replaceChildren(when, box, save, button("Cancel", loadMemory));
      box.focus();
    });
    const forget = button("Forget", () => confirm("Forget this conversation, every word of it?") &&
      api("DELETE", memPath("sessions", e.id)).then(loadMemory).catch((err) => alert(err.message)), "danger");
    li.append(when, words, " ", show, " ", edit, " ", forget);
    list.append(li);
  }
}

function showDiary(m) {
  const list = $("mem-diary");
  list.replaceChildren();
  if (!m.diary.length) { const li = document.createElement("li"); text(li, "No entries yet."); list.append(li); }
  for (const d of m.diary) {
    const li = document.createElement("li");
    const day = document.createElement("span"); day.className = "who"; text(day, d.day);
    const words = document.createElement("span"); text(words, d.text);
    const edit = button("Change", () => {
      const box = document.createElement("textarea"); box.rows = 5; box.value = d.text;
      const save = button("Save", () => api("PUT", memPath("diary", d.day), { text: box.value }).then(loadMemory));
      li.replaceChildren(day, box, save, button("Cancel", loadMemory));
    });
    const forget = button("Forget", () => api("DELETE", memPath("diary", d.day)).then(loadMemory), "danger");
    li.append(day, words, " ", edit, " ", forget);
    list.append(li);
  }
}

function button(label, onClick, className) {
  const b = document.createElement("button");
  text(b, label);
  b.className = "small" + (className ? " " + className : "");
  b.addEventListener("click", onClick);
  return b;
}

$("btn-save-notes").addEventListener("click", () => api("PUT", "/api/memory/" + state.active, { notes: $("mem-notes").value }).then(loadMemory));
$("btn-new-conversation").addEventListener("click", () => api("POST", memPath("new")).then(loadMemory));
$("btn-add-memory").addEventListener("click", async () => {
  try {
    await api("POST", memPath("items"), { text: $("mem-add-text").value, category: $("mem-add-category").value,
      when: $("mem-add-when").value || null, follow_up: !!$("mem-add-when").value, pinned: false });
    $("mem-add-text").value = ""; $("mem-add-when").value = "";
    text($("mem-add-msg"), "Added.");
    loadMemory();
  } catch (err) { text($("mem-add-msg"), err.message); }
});
$("btn-clear-memory").addEventListener("click", () => confirm("Forget everything this character remembers: every conversation, every memory, the diary and your notes?") &&
  api("DELETE", "/api/memory/" + state.active).then(loadMemory));

// ----- services and keys
const FIELDS = { llm: ["provider", "model", "base_url", "effort", "max_tokens", "server", "context"], stt: ["provider", "model", "base_url"],
                 tts: ["provider", "model", "voice", "base_url"] };
// For each service: which saved key it uses, where to get one, and a typical model.
const SERVICES = {
  openai: { key: "openai", hint: "Get a key at platform.openai.com → API keys.", llm: "e.g. gpt-4.1-mini", stt: "e.g. gpt-4o-mini-transcribe", tts: "e.g. gpt-4o-mini-tts" },
  anthropic: { key: "anthropic", hint: "Get a key at console.anthropic.com → API keys.", llm: "claude-opus-5-5 (or claude-haiku-5-5: faster, cheaper)" },
  groq: { key: "groq", hint: "Get a free key at console.groq.com → API keys.", llm: "press List models", stt: "whisper-large-v3-turbo" },
  openrouter: { key: "openrouter", hint: "Get a key at openrouter.ai → Keys.", llm: "press List models" },
  gemini: { key: "openai_compatible", hint: "Get a key at aistudio.google.com → Get API key (has a free tier).", llm: "e.g. gemini-3.5-flash-lite" },
  custom: { key: "openai_compatible", hint: "The key for the service at the address below, if it needs one.", llm: "", stt: "", tts: "" },
  elevenlabs: { key: "elevenlabs", hint: "Get a key at elevenlabs.io → Developers → API keys.", tts: "eleven_flash_v2_5" },
  cartesia: { key: "cartesia", hint: "Get a key at play.cartesia.ai → API keys.", tts: "sonic-3.6" },
  fishaudio: { key: "fishaudio", hint: "Get a key at fish.audio → API keys (includes a free model: s2.1-pro-free).", tts: "s2.1-pro" },
  inworld: { key: "inworld", hint: "Get a Basic key at platform.inworld.ai → Settings → API keys.", tts: "inworld-tts-2" },
  minimax: { key: "minimax", hint: "Get a key at platform.minimax.io → API keys.", tts: "speech-2.8-turbo" },
  deepgram: { key: "deepgram", hint: "Get a key at console.deepgram.com → API keys. The voice is its model name: press List voices.", tts: "" },
  google: { key: "google_tts", hint: "A Google Cloud API key with the Text-to-Speech API turned on (console.cloud.google.com → APIs & Services). Press List voices; Chirp 3 HD voices sound best.", tts: "" },
  local: { key: "", hint: "Runs on the device: no key and no cost." },
  selfhosted: { key: "local_llm", hint: "Free, and private: the model runs on your own computer. A key only if your server asks for one.", llm: "press Find on my network, or List models" },
};

async function loadSettings() {
  const s = state.settings = await api("GET", "/api/settings");
  for (const [group, names] of Object.entries(FIELDS)) {
    for (const n of names) { const el = $(group + "-" + n); if (el) el.value = s[group][n] ?? ""; }
  }
  if (!$("llm-effort").value) $("llm-effort").value = "low";
  $("dev-volume").value = s.volume;
  $("dev-mode").value = s.listening.mode;
  $("dev-end_silence").value = s.listening.end_silence;
  $("dev-voice_interrupt").checked = !!s.listening.voice_interrupt;
  $("dev-memory").checked = !!s.conversation.memory;
  $("dev-search").checked = s.conversation.web_search !== false;
  $("dev-gestures").checked = s.conversation.gestures !== false;
  $("dev-random-gestures").checked = s.conversation.random_gestures !== false;
  $("dev-format").value = s.character_format || "live2d";
  const first = s.speak_first || {};
  $("dev-speak-first").checked = !!first.on;
  $("dev-speak-after").value = String(first.after_hours || 3);
  $("dev-quiet-from").value = first.quiet_from ?? 22;
  $("dev-quiet-to").value = first.quiet_to ?? 8;
  const wake = s.wake_word || {};
  $("dev-wake").checked = !!wake.on;
  $("dev-wake-phrase").value = wake.phrase || "";
  $("dev-wake-sens").value = wake.sensitivity || "normal";
  const captions = s.captions || {};
  $("dev-captions").checked = !!captions.on;
  $("dev-captions-heard").checked = !!captions.heard;
  $("dev-captions-size").value = captions.size || "normal";
  const screen = s.screen || {};
  $("dev-sleep").checked = !!screen.sleep;
  $("dev-sleep-how").value = screen.how || "off";
  $("dev-sleep-from").value = screen.sleep_from ?? 23;
  $("dev-sleep-to").value = screen.sleep_to ?? 7;
  $("dev-brightness").value = screen.brightness ?? 100;
  $("dev-family").checked = !!(s.family_safe || {}).on;
  $("dev-family-pw-row").hidden = true;
  renderKeys(await api("GET", "/api/keys"));
}

// Show only what the chosen service needs: its key, its address, its options.
function shapeGroup(group) {
  const provider = $(group + "-provider").value;
  const info = SERVICES[provider] || {};
  const box = document.querySelector('fieldset[data-group="' + group + '"]');
  for (const el of box.querySelectorAll(".for-custom")) el.hidden = provider !== "custom";
  for (const el of box.querySelectorAll(".for-selfhosted")) el.hidden = provider !== "selfhosted";
  for (const el of box.querySelectorAll(".for-address")) el.hidden = provider !== "custom" && provider !== "selfhosted";
  const address = box.querySelector(".for-address input");
  if (address) address.placeholder = provider === "selfhosted" ? "e.g. 192.168.1.20 or my-laptop.local" : "https://…/v1";
  for (const el of box.querySelectorAll(".for-anthropic")) el.hidden = provider !== "anthropic";
  for (const el of box.querySelectorAll(".for-elevenlabs")) el.hidden = provider !== "elevenlabs";
  for (const el of box.querySelectorAll(".for-cloud")) el.hidden = group === "tts" ? NO_MODEL.has(provider) : provider === "local";
  for (const el of box.querySelectorAll(".for-local")) el.hidden = provider !== "local";
  for (const el of box.querySelectorAll(".for-voicelist")) el.hidden = !VOICE_LIST_PROVIDERS.has(provider);
  const model = $(group + "-model");
  if (model && info[group] !== undefined) model.placeholder = info[group];
  const keybox = box.querySelector(".keybox");
  keybox.replaceChildren();
  keybox.hidden = !provider;
  if (!provider) return;
  const hint = document.createElement("span"); hint.className = "hint"; text(hint, info.hint || "");
  keybox.append(hint);
  if (!info.key) return;
  const saved = !!keyStatus[info.key];
  const status = document.createElement("span"); status.className = saved ? "ok" : "";
  text(status, saved ? "Key saved ✓ " : "No key yet. ");
  const input = document.createElement("input"); input.type = "password"; input.autocomplete = "off";
  input.placeholder = saved ? "paste a new key to replace it" : "paste the key here";
  input.className = "key-input";
  const save = document.createElement("button"); save.type = "button"; text(save, "Save key");
  save.addEventListener("click", async () => {
    try { keyStatus = await api("PUT", "/api/keys/" + info.key, { value: input.value.trim() }); refreshKeys(); }
    catch (err) { text(status, err.message + " "); }
  });
  keybox.append(status, input, save);
}

function refreshKeys() {
  for (const g of Object.keys(FIELDS)) shapeGroup(g);
  renderKeys(keyStatus);
}

for (const g of Object.keys(FIELDS)) $(g + "-provider").addEventListener("change", () => shapeGroup(g));

function renderKeys(status) {
  keyStatus = status;
  const box = $("keys");
  box.replaceChildren();
  for (const [name, set] of Object.entries(status)) {
    const row = document.createElement("div"); row.className = "key";
    const label = document.createElement("span"); text(label, (KEY_NAMES[name] || name) + (set ? " (set)" : ""));
    const input = document.createElement("input"); input.type = "password"; input.autocomplete = "off";
    input.placeholder = set ? "replace key" : "paste key";
    const save = document.createElement("button"); text(save, "Save");
    save.addEventListener("click", async () => { keyStatus = await api("PUT", "/api/keys/" + name, { value: input.value.trim() }); refreshKeys(); });
    row.append(label, input, save);
    if (set) {
      const clear = document.createElement("button"); clear.className = "danger"; text(clear, "Remove");
      clear.addEventListener("click", async () => { keyStatus = await api("PUT", "/api/keys/" + name, { value: "" }); refreshKeys(); });
      row.append(clear);
    }
    box.append(row);
  }
  for (const g of Object.keys(FIELDS)) shapeGroup(g);
  shapeCharVoice();
  shapeClone();
}

async function saveServices() {
  const changes = {};
  for (const [group, names] of Object.entries(FIELDS)) {
    changes[group] = {};
    for (const n of names) {
      const el = $(group + "-" + n);
      if (el) changes[group][n] = el.type === "number" ? Number(el.value) : el.value.trim();
    }
  }
  state.settings = await api("PUT", "/api/settings", changes);
  shapeCharVoice();
}

for (const b of document.querySelectorAll("[data-test]")) {
  b.addEventListener("click", async () => {
    const out = $("test-" + b.dataset.test);
    text(out, "Saving and testing…");
    try {
      await saveServices();
      const r = await api("POST", "/api/test/" + b.dataset.test);
      text(out, (r.ok ? "Working: " : "Not working yet: ") + r.message);
      api("GET", "/api/status").then(setStatus).catch(() => {});
    } catch (err) { text(out, "Not working yet: " + err.message); }
  });
}

$("btn-models").addEventListener("click", async () => {
  try { await saveServices(); } catch (err) { return text($("test-llm"), err.message); }
  const r = await api("GET", "/api/models");
  const list = $("llm-models");
  list.replaceChildren();
  for (const m of r.models) { const o = document.createElement("option"); o.value = m; list.append(o); }
  text($("test-llm"), r.ok ? r.models.length + " models found: click the Model box to choose" : "Could not list models: " + r.message);
});

// A model on the owner's own computer: look for servers on the home network (or at the address typed).
$("btn-find-llm").addEventListener("click", async () => {
  const list = $("find-llm-list");
  list.replaceChildren();
  text($("find-llm-msg"), "Looking on your home network… (up to 20 seconds)");
  try {
    const r = await api("POST", "/api/llm/find", { host: $("llm-base_url").value.trim() });
    text($("find-llm-msg"), r.servers.length ? "Found " + r.servers.length + ". Choose one:" :
      "None found. Is the computer on, and its model server open to the network (OLLAMA_HOST=0.0.0.0, or LM Studio's Serve on Local Network)?");
    for (const s of r.servers) {
      const li = document.createElement("li");
      const b = document.createElement("button"); b.type = "button"; b.className = "secondary";
      text(b, s.label + " at " + s.address + (s.models.length ? " (" + s.models.length + " models)" : "") + (s.needs_key ? ", needs a key" : ""));
      b.addEventListener("click", () => {
        $("llm-server").value = s.kind;
        $("llm-base_url").value = s.address;
        const models = $("llm-models");
        models.replaceChildren();
        for (const m of s.models) { const o = document.createElement("option"); o.value = m; models.append(o); }
        if (s.models.length && !s.models.includes($("llm-model").value)) $("llm-model").value = s.models[0];
        text($("find-llm-msg"), "Chosen. Check the model, then Save and test.");
      });
      li.append(b);
      list.append(li);
    }
  } catch (e) { text($("find-llm-msg"), e.message); }
});

$("btn-voices").addEventListener("click", async () => {
  try { await saveServices(); } catch (err) { return text($("test-tts"), err.message); }
  const r = await api("GET", "/api/voices");
  const list = $("tts-voices");
  list.replaceChildren();
  for (const v of r.voices) { const o = document.createElement("option"); o.value = v.id; o.label = v.name; list.append(o); }
  text($("test-tts"), r.ok ? r.voices.length + " voices found: click the Voice box to choose" : "Could not list voices: " + r.message);
});

// ----- your day: about you, the weather, wake-ups, special days, the clock (docs/28-user-guide.md, "Your day")
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
  "November", "December"];
const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
let day = { place: undefined, wakeUps: [], dates: [], status: null };

function monthSelect(value) {
  const select = document.createElement("select");
  select.setAttribute("aria-label", "Month");
  select.add(new Option("month", ""));
  MONTHS.forEach((m, i) => select.add(new Option(m, String(i + 1).padStart(2, "0"))));
  select.value = value || "";
  return select;
}

// "MM-DD", or "YYYY-MM-DD" with a year; "" when nothing is filled in; an Error when half of it is.
function dateFrom(month, date, year, what) {
  const m = month.value, d = date.value.trim(), y = year.value.trim();
  if (!m && !d && !y) return "";
  if (!m || !d) throw new Error(what + ": choose the month and type the day");
  const dd = String(Number(d)).padStart(2, "0");
  if (!(Number(d) >= 1 && Number(d) <= 31)) throw new Error(what + ": the day is a number from 1 to 31");
  if (y && !/^\d{4}$/.test(y)) throw new Error(what + ": the year has four figures");
  return (y ? y + "-" : "") + m + "-" + dd;
}

function datePieces(text) {
  const t = text || "";
  return t.length === 10 ? { y: t.slice(0, 4), m: t.slice(5, 7), d: String(Number(t.slice(8))) }
    : t.length === 5 ? { y: "", m: t.slice(0, 2), d: String(Number(t.slice(3))) } : { y: "", m: "", d: "" };
}

function whenText(at) {
  const t = new Date(at * 1000), now = new Date();
  const clockText = t.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  const days = Math.round((new Date(t.getFullYear(), t.getMonth(), t.getDate()) - new Date(now.getFullYear(), now.getMonth(), now.getDate())) / 86400000);
  return (days === 0 ? "today" : days === 1 ? "tomorrow" : WEEKDAYS[(t.getDay() + 6) % 7]) + " at " + clockText;
}

async function loadDay() {
  const s = state.settings = await api("GET", "/api/settings");
  const owner = s.owner || {}, weather = s.weather || {}, alarms = s.alarms || {}, special = s.special_days || {};
  const clockOpts = s.clock || {};
  $("day-name").value = owner.name || "";
  const b = datePieces(owner.birthday);
  if (!$("day-bmonth").options.length) {
    $("day-bmonth").add(new Option("month", ""));
    MONTHS.forEach((m, i) => $("day-bmonth").add(new Option(m, String(i + 1).padStart(2, "0"))));
  }
  $("day-bmonth").value = b.m; $("day-bday").value = b.d; $("day-byear").value = b.y;
  $("day-weather").checked = !!weather.on;
  $("day-units").value = weather.units || "auto";
  day.place = undefined;
  $("day-place-find").hidden = true;
  $("day-morning").checked = (s.speak_first || {}).morning !== false;
  $("day-repeat").checked = alarms.repeat !== false;
  day.wakeUps = (alarms.wake_ups || []).map((w) => Object.assign({}, w));
  day.dates = (special.dates || []).map((d) => Object.assign({}, d));
  $("day-special").checked = special.on !== false;
  $("day-holidays").checked = special.holidays !== false;
  $("day-festive").checked = special.festive !== false;
  $("day-clock").value = clockOpts.show || "off";
  $("day-hours").value = clockOpts.hours || "auto";
  $("day-clock-weather").checked = clockOpts.weather !== false;
  $("day-every").value = String((s.background || {}).every || 10);
  renderWakeUps();
  renderDates();
  showDayStatus(await api("GET", "/api/day"));
}

function showDayStatus(st) {
  day.status = st;
  if (day.place === undefined) text($("day-place"), st.place ? st.place.label : "no town yet (the time zone has none): choose one");
  text($("day-credit"), st.credit);
  showRinging(st.ringing);
  text($("day-next"), st.next_wake_up ? "Next wake-up: " + whenText(st.next_wake_up.at) +
    (st.next_wake_up.label && st.next_wake_up.label !== "wake-up" ? " (" + st.next_wake_up.label + ")" : "") + "." : "");
  const timers = $("day-timers");
  timers.replaceChildren();
  for (const t of st.timers) {
    const li = document.createElement("li");
    const label = document.createElement("span");
    text(label, (t.kind === "timer" ? "Timer" : "Alarm") + (t.label && t.label !== t.kind ? ": " + t.label : "") + ", rings " + whenText(t.due));
    li.append(label, button("Cancel", async () => {
      try { showDayStatus(await api("DELETE", "/api/timers/" + encodeURIComponent(t.label))); }
      catch (e) { text($("day-msg"), e.message); }
    }, "small secondary"));
    timers.append(li);
  }
  if (!st.timers.length) { const li = document.createElement("li"); li.className = "muted"; text(li, "None now."); timers.append(li); }
  const today = st.specials.map((x) => x.what);
  text($("day-today"), (today.length ? "Today: " + today.join(", ") + (st.festive ? " (with a festive background)" : "") + ". " : "") +
    (st.arrived ? "Your character arrived on " + new Date(st.arrived + "T12:00:00").toLocaleDateString([], { day: "numeric", month: "long", year: "numeric" }) + "." : ""));
  if (st.weather_said && !$("day-weather-msg").textContent) text($("day-weather-msg"), st.weather_said);
}

function showRinging(ringing) {
  const on = !!(ringing && ringing.length);
  $("day-ringing").hidden = !on;
  $("home-ringing").hidden = !on;
}

function renderWakeUps() {
  const list = $("day-wakeups");
  list.replaceChildren();
  day.wakeUps.forEach((w, i) => {
    const li = document.createElement("li");
    const on = document.createElement("input"); on.type = "checkbox"; on.checked = w.on !== false; on.title = "On";
    on.addEventListener("change", () => { w.on = on.checked; });
    const at = document.createElement("input"); at.type = "time"; at.value = w.time || "07:00"; at.required = true;
    at.addEventListener("change", () => { w.time = at.value; });
    const days = document.createElement("span"); days.className = "days";
    const chosen = new Set(w.days || [0, 1, 2, 3, 4, 5, 6]);
    WEEKDAYS.forEach((name, k) => {
      const l = document.createElement("label"); l.title = name;
      const box = document.createElement("input"); box.type = "checkbox"; box.checked = chosen.has(k);
      box.setAttribute("aria-label", name);
      box.addEventListener("change", () => {
        if (box.checked) chosen.add(k); else chosen.delete(k);
        w.days = [...chosen].sort();
      });
      const letter = document.createElement("span"); text(letter, name.slice(0, 2));
      l.append(box, letter);
      days.append(l);
    });
    const label = document.createElement("input"); label.type = "text"; label.maxLength = 40; label.value = w.label || "";
    label.placeholder = "what for (if you like)";
    label.addEventListener("input", () => { w.label = label.value; });
    li.append(on, at, days, label, button("Remove", () => { day.wakeUps.splice(i, 1); renderWakeUps(); }, "small secondary"));
    list.append(li);
  });
  if (!day.wakeUps.length) { const li = document.createElement("li"); li.className = "muted"; text(li, "No wake-ups yet."); list.append(li); }
}

function renderDates() {
  const list = $("day-dates");
  list.replaceChildren();
  day.dates.forEach((d, i) => {
    const li = document.createElement("li");
    const p = datePieces(d.date);
    const month = monthSelect(p.m);
    const date = document.createElement("input"); date.type = "number"; date.min = 1; date.max = 31; date.value = p.d;
    date.placeholder = "day"; date.className = "short"; date.setAttribute("aria-label", "Day");
    const year = document.createElement("input"); year.type = "number"; year.min = 1900; year.max = 2100; year.value = p.y;
    year.placeholder = "year"; year.className = "short"; year.setAttribute("aria-label", "Year");
    const what = document.createElement("input"); what.type = "text"; what.maxLength = 60; what.value = d.what || "";
    what.placeholder = "what it is, e.g. our anniversary";
    const onceLabel = document.createElement("label");
    const once = document.createElement("input"); once.type = "checkbox"; once.checked = !!d.once;
    onceLabel.append(once, document.createTextNode(" only that year"));
    d.read = () => ({ date: dateFrom(month, date, year, "“" + (what.value.trim() || "a day") + "”"), what: what.value.trim(), once: once.checked });
    li.append(month, date, year, what, onceLabel, button("Remove", () => { day.dates.splice(i, 1); renderDates(); }, "small secondary"));
    list.append(li);
  });
  if (!day.dates.length) { const li = document.createElement("li"); li.className = "muted"; text(li, "None yet."); list.append(li); }
}

function dayChanges() {
  const dates = [];
  for (const d of day.dates) {
    const r = d.read ? d.read() : d;
    if (!r.date && !r.what) continue;
    if (!r.date || !r.what) throw new Error("each of your own days needs a date and what it is");
    if (r.once && r.date.length !== 10) throw new Error("“" + r.what + "” is only that year, so it needs the year");
    dates.push(r.once ? r : { date: r.date, what: r.what });
  }
  for (const w of day.wakeUps) if (!w.time) throw new Error("each wake-up needs a time");
  for (const w of day.wakeUps) if (!(w.days || [0]).length) throw new Error("each wake-up needs at least one day");
  const changes = {
    owner: { name: $("day-name").value.trim(), birthday: dateFrom($("day-bmonth"), $("day-bday"), $("day-byear"), "Your birthday") },
    weather: { on: $("day-weather").checked, units: $("day-units").value },
    speak_first: { morning: $("day-morning").checked },
    alarms: { repeat: $("day-repeat").checked, wake_ups: day.wakeUps.map((w) => ({
      id: w.id || Math.random().toString(16).slice(2, 10), time: w.time || "07:00",
      days: w.days || [0, 1, 2, 3, 4, 5, 6], label: (w.label || "").trim(), on: w.on !== false })) },
    special_days: { on: $("day-special").checked, holidays: $("day-holidays").checked, festive: $("day-festive").checked, dates },
    clock: { show: $("day-clock").value, hours: $("day-hours").value, weather: $("day-clock-weather").checked },
    background: { every: Number($("day-every").value) },
  };
  if (day.place !== undefined) changes.weather.place = day.place;
  return changes;
}

async function saveDay() {
  await api("PUT", "/api/settings", dayChanges());
  await loadDay();
}

$("btn-save-day").addEventListener("click", async () => {
  text($("day-msg"), "");
  try { await saveDay(); text($("day-msg"), "Saved."); } catch (e) { text($("day-msg"), e.message); }
});
$("btn-day-add-wake").addEventListener("click", () => {
  day.wakeUps.push({ id: Math.random().toString(16).slice(2, 10), time: "07:00", days: [0, 1, 2, 3, 4], label: "", on: true });
  renderWakeUps();
});
$("btn-day-add-date").addEventListener("click", () => { day.dates.push({ date: "", what: "" }); renderDates(); });
$("btn-day-place").addEventListener("click", () => { $("day-place-find").hidden = false; $("day-place-q").focus(); });
$("btn-day-place-zone").addEventListener("click", () => {
  day.place = null;
  text($("day-place"), "the time zone's city (save to keep)");
  $("day-place-find").hidden = true;
});
let placeTimer = null;
$("day-place-q").addEventListener("input", () => {
  clearTimeout(placeTimer);
  placeTimer = setTimeout(async () => {
    const list = $("day-place-list");
    try {
      const r = await api("GET", "/api/places?q=" + encodeURIComponent($("day-place-q").value));
      list.replaceChildren();
      for (const p of r.places) {
        const li = document.createElement("li");
        li.append(button(p.label, () => {
          day.place = { name: p.name, label: p.label, latitude: p.latitude, longitude: p.longitude };
          text($("day-place"), p.label + " (save to keep)");
          $("day-place-find").hidden = true;
          list.replaceChildren();
        }, "secondary"));
        list.append(li);
      }
      if (!r.places.length && $("day-place-q").value.trim().length > 1) {
        const li = document.createElement("li"); li.className = "muted";
        text(li, "No town of that name in the list (it has every town of 15,000 people or more): try the nearest larger one.");
        list.append(li);
      }
    } catch (e) { text($("day-weather-msg"), e.message); }
  }, 250);
});
$("btn-day-weather-check").addEventListener("click", async () => {
  text($("day-weather-msg"), "Saving and asking MET Norway…");
  try {
    $("day-weather").checked = true;
    await saveDay();
    const st = await api("POST", "/api/weather/check");
    showDayStatus(st);
    text($("day-weather-msg"), st.problem || ("The character would say: " + st.weather_said));
  } catch (e) { text($("day-weather-msg"), e.message); }
});
$("btn-day-try-wake").addEventListener("click", async () => {
  text($("day-try-msg"), "");
  try { showDayStatus(await api("POST", "/api/alarms/try")); text($("day-try-msg"), "Listen to the device. Press its button, or Stop it, to end it."); }
  catch (e) { text($("day-try-msg"), e.message); }
});
async function stopAlarm() {
  try { showRinging((await api("POST", "/api/alarms/stop")).ringing); } catch (e) { addLog("Problem:", e.message, "error"); }
}
$("btn-day-stop").addEventListener("click", stopAlarm);
$("btn-home-stop").addEventListener("click", stopAlarm);

// ----- device
$("dev-family").addEventListener("change", () => {
  // Turning family-safe mode off asks for the password again (a child may have the page open).
  $("dev-family-pw-row").hidden = $("dev-family").checked || !(state.settings.family_safe || {}).on;
});
$("btn-save-device").addEventListener("click", async () => {
  try {
    await api("PUT", "/api/settings", {
      volume: Number($("dev-volume").value),
      listening: { mode: $("dev-mode").value, end_silence: Number($("dev-end_silence").value),
                   voice_interrupt: $("dev-voice_interrupt").checked },
      conversation: { memory: $("dev-memory").checked, web_search: $("dev-search").checked,
                      gestures: $("dev-gestures").checked, random_gestures: $("dev-random-gestures").checked },
      character_format: $("dev-format").value,
      speak_first: { on: $("dev-speak-first").checked, after_hours: Number($("dev-speak-after").value),
                     quiet_from: Number($("dev-quiet-from").value), quiet_to: Number($("dev-quiet-to").value) },
      wake_word: { on: $("dev-wake").checked, phrase: $("dev-wake-phrase").value.trim(), sensitivity: $("dev-wake-sens").value },
      captions: { on: $("dev-captions").checked, heard: $("dev-captions-heard").checked, size: $("dev-captions-size").value },
      screen: { sleep: $("dev-sleep").checked, how: $("dev-sleep-how").value, sleep_from: Number($("dev-sleep-from").value),
                sleep_to: Number($("dev-sleep-to").value), brightness: Number($("dev-brightness").value) },
    });
    const family = $("dev-family").checked;
    if (family !== !!(state.settings.family_safe || {}).on) {
      await api("POST", "/api/family-safe", family ? { on: true } : { on: false, password: $("dev-family-pw").value });
      $("dev-family-pw").value = "";
    }
    await loadSettings();
    text($("device-msg"), "Saved.");
  } catch (err) { text($("device-msg"), err.message); }
});

$("pw-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try { await api("POST", "/api/password", { old: $("pw-old").value, new: $("pw-new").value }); text($("pw-msg"), "Changed."); }
  catch (err) { text($("pw-msg"), err.message); }
  $("pw-old").value = ""; $("pw-new").value = "";
});

// ----- Wi-Fi
async function loadWifi() {
  const w = await api("GET", "/api/wifi");
  if (!w.available) { text($("wifi-status"), "Wi-Fi is managed outside this page on this machine."); $("wifi-form").hidden = true; return; }
  text($("wifi-status"), w.error ? "Problem: " + w.error : w.connected ? "Connected" + (w.address ? " as " + w.address : "") + "." :
       w.hotspot ? "Not on a network yet: you are connected through the device's own setup hotspot." : "Not connected.");
  const sel = $("wifi-ssid");
  sel.replaceChildren();
  for (const n of w.networks || []) {
    const o = document.createElement("option"); o.value = n.ssid;
    text(o, n.ssid + " (" + n.signal + "%" + (n.secure ? ", secured" : ", open") + ")"); sel.append(o);
  }
}
$("btn-wifi-scan").addEventListener("click", () => loadWifi().catch((e) => text($("wifi-msg"), e.message)));
$("wifi-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  text($("wifi-msg"), "Joining… if this page stops answering, reconnect your phone to your home Wi-Fi and find the device's new address on its screen.");
  try { await api("POST", "/api/wifi", { ssid: $("wifi-ssid").value, password: $("wifi-password").value }); text($("wifi-msg"), "Joined."); }
  catch (err) { text($("wifi-msg"), err.message); }
  $("wifi-password").value = "";
});

$("btn-tz").addEventListener("click", async () => {
  try { const r = await api("POST", "/api/timezone", { zone: Intl.DateTimeFormat().resolvedOptions().timeZone });
        text($("tz-device"), r.timezone); text($("tz-msg"), "Set."); }
  catch (err) { text($("tz-msg"), err.message); }
});

$("reset-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!confirm("Remove everything from this device? This cannot be undone.")) return;
  try {
    await api("POST", "/api/reset", { password: $("reset-password").value, confirm: $("reset-confirm").value.trim() });
    text($("reset-msg"), "Resetting. The device restarts; this page will ask for a new password.");
  } catch (err) { text($("reset-msg"), err.message); }
  $("reset-password").value = "";
});

$("btn-logout").addEventListener("click", async () => { await api("POST", "/api/logout"); if (source) source.close(); showLogin(false); });

start().catch((err) => { document.body.prepend(Object.assign(document.createElement("p"), { className: "error", textContent: err.message })); });

// ----- software updates
let updateTimer = null;
async function loadUpdate() {
  const u = await api("GET", "/api/update");
  text($("upd-version"), u.version === "0" ? "a development copy" : u.version);
  const pending = u.ready || u.found;
  const installer = u.installer || {};
  let line = "";
  if (!u.can_update) line = "This copy of the software cannot be updated from here (it has no update key).";
  else if (u.busy) line = "Fetching the update…";
  else if (installer.state === "installing") line = "Installing version " + installer.version + "; the device restarts its software…";
  else if (u.ready) line = "Version " + u.ready.version + " is ready to install.";
  else if (u.found) line = "Version " + u.found.version + " is available.";
  else if (u.online && u.checked) line = "Up to date (checked " + new Date(u.checked * 1000).toLocaleString() + ").";
  else if (!u.online) line = "Updates come as files (no update address is set).";
  if (["installed", "rolled back", "failed"].includes(installer.state) && installer.message) line += " Last: " + installer.message;
  if (u.error) line += " Problem: " + u.error;
  text($("upd-status"), line);
  text($("upd-notes"), pending && pending.notes ? "What is new: " + pending.notes : "");
  $("btn-upd-install").hidden = !pending || !u.can_update || u.busy || installer.state === "installing";
  $("btn-upd-check").hidden = !u.online;
  $("btn-upd-rollback").hidden = !u.previous || installer.state === "installing";
  $("upd-check").checked = (u.settings || {}).check !== false;
  $("upd-auto").checked = (u.settings || {}).auto_install !== false;
  clearTimeout(updateTimer);
  if (u.busy || installer.state === "installing") updateTimer = setTimeout(() => loadUpdate().catch(() => {}), 4000);
}
$("btn-upd-check").addEventListener("click", async () => {
  text($("upd-msg"), "Looking…");
  try { await api("POST", "/api/update/check"); text($("upd-msg"), ""); } catch (err) { text($("upd-msg"), err.message); }
  loadUpdate().catch(() => {});
});
$("btn-upd-install").addEventListener("click", async () => {
  if (!confirm("Install the update now? The character stops for a minute or two while the software restarts.")) return;
  try { await api("POST", "/api/update/install"); text($("upd-msg"), "Started."); } catch (err) { text($("upd-msg"), err.message); }
  setTimeout(() => loadUpdate().catch(() => {}), 1500);
});
$("btn-upd-rollback").addEventListener("click", async () => {
  if (!confirm("Go back to the previous version of the software?")) return;
  try { await api("POST", "/api/update/rollback"); text($("upd-msg"), "Going back…"); } catch (err) { text($("upd-msg"), err.message); }
});
for (const id of ["upd-check", "upd-auto"]) {
  $(id).addEventListener("change", () => api("PUT", "/api/update/settings",
    { check: $("upd-check").checked, auto_install: $("upd-auto").checked }).catch((err) => text($("upd-msg"), err.message)));
}
$("upd-file").addEventListener("change", async () => {
  const file = $("upd-file").files[0];
  if (!file) return;
  text($("upd-msg"), "Checking the update file…");
  try {
    const r = await api("PUT", "/api/update/file", undefined, file);
    text($("upd-msg"), "Version " + r.version + " checked and ready: press Install the update.");
  } catch (err) { text($("upd-msg"), err.message); }
  $("upd-file").value = "";
  loadUpdate().catch(() => {});
});

// ----- backup
$("restore-file").addEventListener("change", async () => {
  const file = $("restore-file").files[0];
  if (!file) return;
  if (!confirm("Restore this backup? Characters in it replace those with the same name, with their memory.")) { $("restore-file").value = ""; return; }
  text($("backup-msg"), "Restoring…");
  try {
    const r = await api("PUT", "/api/backup", undefined, file);
    const names = r.characters.map((c) => c.id).join(", ") || "none";
    text($("backup-msg"), "Restored: " + names + "." + (r.skipped.length ? " Not restored: " + r.skipped.join("; ") : "") +
      " Put your keys in again under AI services.");
    refreshCharacters();
  } catch (err) { text($("backup-msg"), err.message); }
  $("restore-file").value = "";
});
