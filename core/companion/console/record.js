// Recording a gesture with a phone's camera (core/companion/phone_record.py).
//
// The phone films the gesture with its own camera app (a page on the home network may not use the camera
// directly, but it may ask for a video), finds the face and body in each frame with MediaPipe for the web,
// which the device serves, and sends only those points to the device. The device makes them into a gesture
// and plays it on its screen.

const $ = (id) => document.getElementById(id);
const say = (el, value) => { el.textContent = value == null ? "" : String(value); };
const STEPS = ["ask-code", "film", "trim", "read", "result", "kept"];
const FPS = 12;                     // frames read a second: enough for a gesture, quick on a phone

const code = (location.pathname.match(/^\/r\/([A-Za-z0-9-]+)\/?$/) || [])[1] || "";
let info = null;                    // what the device said about this link
let layout = null;                  // which face points, expressions and body points it wants, in order
let reader = null;                  // the face and body models, once loaded
let readerLoading = null;
let clock = 0;                      // MediaPipe wants its frames' times to keep going up
let stopped = false;
let mirror = false;

function show(step) {
  for (const s of STEPS) $(s).hidden = s !== step;
  window.scrollTo(0, 0);
}

function problem(message) {
  $("problem").hidden = !message;
  say($("problem"), message);
}

async function call(method, path, body) {
  const opts = { method, headers: { "X-Companion": "1" } };
  if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  const r = await fetch(path, opts);
  const type = r.headers.get("Content-Type") || "";
  const data = type.includes("json") ? await r.json() : await r.text();
  if (!r.ok) {
    const error = new Error(typeof data === "string" ? data.replace(/^\d+: /, "") : JSON.stringify(data));
    error.status = r.status;
    error.data = data;
    throw error;
  }
  return data;
}

// ----- the link
async function start() {
  if (!code) {
    show("ask-code");
    $("code-form").addEventListener("submit", (e) => {
      e.preventDefault();
      const typed = $("code").value.toUpperCase().replace(/[^A-Z0-9]/g, "");
      if (typed) location.href = "/r/" + typed;
    });
    return;
  }
  try {
    info = await call("GET", "/r/" + code + "/info");
  } catch (err) {
    problem(err.message);
    return;
  }
  layout = info.layout;
  if (info.character) say($("for"), "for " + info.character);
  for (const el of document.querySelectorAll(".max-seconds")) say(el, info.max_seconds);
  countdown(info.expires_in);
  show("film");
  prepareReader();
}

function countdown(seconds) {
  const until = Date.now() + seconds * 1000;
  const tick = () => {
    const left = Math.max(0, Math.round((until - Date.now()) / 60000));
    say($("expiry"), left > 1 ? "This link works for another " + left + " minutes." :
      left === 1 ? "This link works for another minute." : "This link has ended: ask for a new one on the settings page.");
  };
  tick();
  setInterval(tick, 20000);
}

// ----- MediaPipe, from the device (fetched once, then kept by the browser)
async function waitForAssets() {
  while (info.assets !== "ready") {
    if (info.assets === "failed") throw new Error(info.assets_message || "the device could not get what this page needs");
    $("getting-ready").hidden = false;
    say($("getting-ready"), "The device is downloading what it needs to read movement (once only). This can take a minute…");
    await new Promise((r) => setTimeout(r, 3000));
    info = await call("GET", "/r/" + code + "/info");
  }
}

let vision = null, files = null;

async function makeReader(delegate) {
  const base = (path) => ({ baseOptions: { modelAssetPath: path, delegate }, runningMode: "VIDEO" });
  const face = await vision.FaceLandmarker.createFromOptions(files,
    { ...base("/r/assets/face_landmarker.task"), outputFaceBlendshapes: true, numFaces: 1 });
  const pose = await vision.PoseLandmarker.createFromOptions(files,
    { ...base("/r/assets/pose_landmarker_lite.task"), numPoses: 1 });
  return { face, pose, delegate };
}

function prepareReader() {
  if (!readerLoading) {
    readerLoading = (async () => {
      await waitForAssets();
      $("getting-ready").hidden = false;
      say($("getting-ready"), "Getting the movement reader ready…");
      vision = await import("/r/assets/vision_bundle.mjs");
      files = await vision.FilesetResolver.forVisionTasks("/r/assets");
      try {
        reader = await makeReader("GPU");
      } catch (err) {
        reader = await makeReader("CPU");    // a phone without WebGL 2 for it: slower, but the same
      }
      $("getting-ready").hidden = true;
      return reader;
    })();
    readerLoading.catch((err) => {
      readerLoading = null;
      $("getting-ready").hidden = true;
      problem("This phone's browser cannot read movement (" + err.message + "). Try another browser (Chrome or " +
        "Safari), or record the gesture in Character Studio on a computer.");
    });
  }
  return readerLoading;
}

// ----- 1. film, 2. trim
function chosen(input) {
  const file = input.files && input.files[0];
  input.value = "";
  if (!file) return;
  problem("");
  const video = $("video");
  if (video.src) URL.revokeObjectURL(video.src);
  video.src = URL.createObjectURL(file);
  video.addEventListener("loadedmetadata", () => {
    const length = isFinite(video.duration) ? video.duration : 0;
    if (!length) {
      problem("This phone's browser cannot open that video. Film it again, or choose another.");
      return;
    }
    for (const id of ["start", "end"]) $(id).max = length.toFixed(2);
    $("start").value = 0;
    $("end").value = Math.min(length, info.max_seconds).toFixed(2);
    trimmed();
    show("trim");
  }, { once: true });
  video.load();
}

function trimmed(moved) {
  let start = Number($("start").value), end = Number($("end").value);
  if (end - start < 1) {
    if (moved === "start") start = Math.max(0, end - 1); else end = Math.min(Number($("end").max), start + 1);
  }
  if (end - start > info.max_seconds) {
    if (moved === "start") end = start + info.max_seconds; else start = end - info.max_seconds;
  }
  $("start").value = start;
  $("end").value = end;
  say($("start-at"), start.toFixed(1) + " s");
  say($("end-at"), end.toFixed(1) + " s");
  say($("trim-note"), Number($("end").max) > info.max_seconds
    ? "A gesture is " + info.max_seconds + " seconds at most: choose the part with it."
    : "Leave out anything before and after the gesture, but keep a still moment at each end.");
  const video = $("video");
  if (moved && video.readyState >= 1) video.currentTime = moved === "start" ? start : end;
}

// ----- 3. read the movement
function seek(video, t) {
  return new Promise((resolve) => {
    if (Math.abs(video.currentTime - t) < 0.001 && video.readyState >= 2) { resolve(); return; }
    const timer = setTimeout(() => { video.removeEventListener("seeked", done); resolve(); }, 4000);
    function done() { clearTimeout(timer); resolve(); }
    video.addEventListener("seeked", done, { once: true });
    video.currentTime = t;
  });
}

const round = (v) => Math.round(v * 10000) / 10000;
const within = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const allFinite = (values) => values.every((v) => Number.isFinite(v));

function frameFrom(face, pose) {
  const out = { face: null, blend: null, pose: null };
  const points = face.faceLandmarks && face.faceLandmarks[0];
  if (points) {
    const xy = layout.face_points.map((i) => [points[i].x, points[i].y]);
    if (allFinite(xy.flat())) out.face = xy.map(([x, y]) => [round(within(x, -2, 3)), round(within(y, -2, 3))]);
  }
  const shapes = face.faceBlendshapes && face.faceBlendshapes[0];
  if (shapes) {
    const score = {};
    for (const c of shapes.categories) score[c.categoryName] = c.score;
    const values = layout.blendshapes.map((name) => score[name] ?? 0);
    if (allFinite(values)) out.blend = values.map((v) => round(within(v, 0, 1)));
  }
  const body = pose.landmarks && pose.landmarks[0];
  if (body) {
    const joints = layout.pose_points.map((i) => [body[i].x, body[i].y, body[i].z, body[i].visibility ?? 1]);
    if (allFinite(joints.flat())) {
      out.pose = joints.map(([x, y, z, v]) =>
        [round(within(x, -2, 3)), round(within(y, -2, 3)), round(within(z, -5, 5)), round(within(v, 0, 1))]);
    }
  }
  return out;
}

function drawSeen(canvas, video, face, pose) {
  const scale = Math.min(480 / video.videoWidth, 480 / video.videoHeight);
  const w = Math.max(1, Math.round(video.videoWidth * scale)), h = Math.max(1, Math.round(video.videoHeight * scale));
  if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
  const g = canvas.getContext("2d");
  g.drawImage(video, 0, 0, w, h);
  const dot = (p, colour) => { g.fillStyle = colour; g.beginPath(); g.arc(p.x * w, p.y * h, 4, 0, 2 * Math.PI); g.fill(); };
  const body = pose.landmarks && pose.landmarks[0];
  if (body) {
    g.strokeStyle = "#8ea5ff"; g.lineWidth = 3;
    for (const chain of [[16, 14, 12, 11, 13, 15], [12, 24, 23, 11]]) {
      g.beginPath();
      chain.forEach((i, k) => (k ? g.lineTo(body[i].x * w, body[i].y * h) : g.moveTo(body[i].x * w, body[i].y * h)));
      g.stroke();
    }
    for (const i of layout.pose_points) if ((body[i].visibility ?? 1) > 0.5) dot(body[i], "#3a5bd9");
  }
  const points = face.faceLandmarks && face.faceLandmarks[0];
  if (points) for (const i of layout.face_points) dot(points[i], "#ffb74d");
}

async function readMovement() {
  const video = $("video");
  const start = Number($("start").value), end = Number($("end").value);
  stopped = false;
  $("progress").value = 0;
  say($("read-note"), "This takes a little while; keep this page open.");
  show("read");
  let models;
  try {
    models = await prepareReader();
  } catch (err) {
    show("film");
    return;
  }
  try { await video.play(); } catch (err) { /* some browsers only show frames after playing once */ }
  video.pause();
  const count = Math.floor((end - start) * FPS) + 1;
  const frames = [];
  let found = 0;
  const began = performance.now();
  for (let i = 0; i < count; i++) {
    if (stopped) { show("trim"); return; }
    await seek(video, Math.min(start + i / FPS, video.duration - 0.001));
    clock += Math.round(1000 / FPS);
    let face, pose;
    try {
      face = models.face.detectForVideo(video, clock);
      pose = models.pose.detectForVideo(video, clock);
    } catch (err) {
      if (models.delegate !== "GPU") throw err;
      reader = models = await makeReader("CPU");   // the phone's graphics gave up: read the rest without them
      clock += 1000;
      face = models.face.detectForVideo(video, clock);
      pose = models.pose.detectForVideo(video, clock);
    }
    const frame = frameFrom(face, pose);
    if (frame.face || frame.pose) found++;
    frames.push(frame);
    drawSeen($("seen"), video, face, pose);
    $("progress").value = (i + 1) / count;
    if (i === 4) {
      const each = (performance.now() - began) / 5 / 1000;
      const left = Math.round(each * (count - 5));
      if (left > 8) say($("read-note"), "About " + left + " seconds more; keep this page open.");
    }
  }
  if (found < count / 2) {
    problem("You were found in only " + Math.round(100 * found / count) + "% of the video. Film it again facing " +
      "the camera, with your head and shoulders in view, in good light.");
    show("film");
    return;
  }
  await send({ fps: FPS, width: video.videoWidth, height: video.videoHeight, frames });
}

// ----- 4. on the screen, 5. keep it
async function send(payload) {
  say($("read-note"), "Sending it to the device…");
  let answer;
  try {
    answer = await call("POST", "/r/" + code + "/motion", Object.assign(payload, { mirror }));
  } catch (err) {
    problem(err.message);
    show(err.status === 404 || err.status === 429 ? "" : "film");     // a link that has ended: nothing more to do
    return;
  }
  problem("");
  const moves = answer.moves.length ? answer.moves.join(", ").replace(/, ([^,]*)$/, " and $1") : "moves";
  say($("result-text"), (answer.played ? "The character is doing your gesture now: it " : "Your gesture: it ") +
    moves + " (" + answer.seconds.toFixed(1) + " seconds).");
  noteFor(answer);
  $("replace").hidden = true;
  say($("keep-msg"), "");
  show("result");
}

function noteFor(answer) {
  say($("result-note"), answer.played === false
    ? "The device's screen is not showing a character just now, so it cannot play it; you can still keep it."
    : answer.can === false
      ? "This character cannot make all of that movement, so it may not show it; characters that can will."
      : "Not quite right? Play it again, swap left and right, or film it again.");
}

async function playAgain() {
  try {
    noteFor(await call("POST", "/r/" + code + "/play", { mirror }));
    problem("");
  } catch (err) { problem(err.message); }
}

async function keep(replace) {
  const kind = document.querySelector("input[name=kind]:checked").value;
  const body = { name: $("name").value, kind, words: kind === "gesture" ? $("words").value : "", mirror, replace };
  say($("keep-msg"), "");
  $("btn-keep").disabled = true;
  try {
    const answer = await call("POST", "/r/" + code + "/save", body);
    say($("kept-text"), "Kept as “" + answer.name.replace(/_/g, " ") + "”. " +
      (answer.kind === "gesture" ? "The character can make it from now on, when it fits."
        : "The character will do it now and then, when it is idle."));
    show("kept");
  } catch (err) {
    if (err.status === 409 && err.data && err.data.exists) {
      say($("replace-text"), "There is already " + (err.data.exists === "built in" ? "a built-in gesture" : "a gesture") +
        " called “" + err.data.name.replace(/_/g, " ") + "”. Replace it with yours?" +
        (err.data.exists === "built in" ? " (The settings page can put the built-in one back.)" : ""));
      $("replace").hidden = false;
      $("btn-keep").hidden = true;
    } else {
      say($("keep-msg"), err.message);
    }
  } finally {
    $("btn-keep").disabled = false;
  }
}

function renamed() {
  $("replace").hidden = true;
  $("btn-keep").hidden = false;
}

// ----- wiring
for (const id of ["camera", "pick"]) $(id).addEventListener("change", (e) => chosen(e.target));
$("start").addEventListener("input", () => trimmed("start"));
$("end").addEventListener("input", () => trimmed("end"));
$("btn-read").addEventListener("click", () => readMovement().catch((err) => { problem(err.message); show("film"); }));
$("btn-trim-back").addEventListener("click", () => show("film"));
$("btn-stop").addEventListener("click", () => { stopped = true; });
$("btn-again").addEventListener("click", playAgain);
$("mirror").addEventListener("change", (e) => { mirror = e.target.checked; playAgain(); });
$("btn-refilm").addEventListener("click", () => { problem(""); show("film"); });
$("keep-form").addEventListener("submit", (e) => { e.preventDefault(); keep(false); });
$("btn-replace").addEventListener("click", () => { renamed(); keep(true); });
$("btn-rename").addEventListener("click", () => { renamed(); $("name").focus(); });
$("name").addEventListener("input", renamed);
for (const r of document.querySelectorAll("input[name=kind]")) {
  r.addEventListener("change", () => { $("words-row").hidden = r.value !== "gesture" || !r.checked; });
}
$("btn-another").addEventListener("click", () => {
  $("name").value = ""; $("words").value = ""; $("mirror").checked = false; mirror = false;
  problem("");
  show("film");
});

start();
