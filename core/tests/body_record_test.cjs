// Recording the whole body of a 3D (VRM) character with a phone, end to end, in headless Chromium against
// tests/virtual_device.py: the owner adds a 3D avatar and asks for a link; the phone (Pixel 7) is told the whole
// body is recorded, loads the larger pose model, reads a video, and sends MediaPipe's 33 body points (in metres)
// with each frame; the device makes a whole-body or upper-body movement of what it saw, or, seeing too little,
// a head-and-arms gesture saying why; the phone keeps it.
//
//   python3 tests/virtual_device.py DATA PORT HOST
//   node tests/body_record_test.cjs http://HOST:PORT AVATAR.vrm VIDEO
//
// AVATAR.vrm: any VRM avatar (tests/vrm_sample.py makes a tiny one). VIDEO: a few seconds of a person facing the
// camera (any phone video of someone who agreed to it). Only text is checked: nothing is saved from the video.
const { chromium, devices } = require("playwright");
const [base, avatar, video] = process.argv.slice(2);
const checks = [];
const check = (name, ok, extra) => { checks.push([name, !!ok]); console.log((ok ? "PASS " : "FAIL ") + name + (extra ? "  [" + extra + "]" : "")); };

(async () => {
  const browser = await chromium.launch({ args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
  const errors = [];
  const watch = (page, who) => {
    page.on("pageerror", (e) => errors.push(who + " pageerror: " + e.message));
    // MediaPipe reports its own start-up ("INFO: Created TensorFlow Lite XNNPACK delegate") as an error.
    page.on("console", (m) => {
      if (m.type() === "error" && !m.text().startsWith("INFO:")) errors.push(who + " console: " + m.text().slice(0, 300));
    });
  };

  const owner = await (await browser.newContext({ viewport: { width: 1000, height: 900 } })).newPage();
  watch(owner, "owner");
  await owner.goto(base + "/");
  await owner.waitForSelector("#login:not([hidden]), #setup:not([hidden])");
  if (await owner.isVisible("#setup")) {
    await owner.fill("#setup-password", "a good password");
    await owner.click("#setup-form button");
  } else {
    await owner.fill("#login-password", "a good password");
    await owner.click("#login-form button");
  }
  await owner.waitForSelector("#home:not([hidden])");
  await owner.click("#tabs button[data-tab=characters]");
  await owner.setInputFiles("#char-file", avatar);
  await owner.waitForSelector("#char-list li");
  await owner.click("#tabs button[data-tab=animation]");
  await owner.click("#btn-rec-start");
  await owner.waitForSelector("#rec-box:not([hidden])");
  const link = (await owner.textContent("#rec-link")).trim();
  check("the settings page gives a link", /\/r\/[A-Z2-9]{6}$/.test(link), link);

  const phone = await (await browser.newContext({ ...devices["Pixel 7"] })).newPage();
  watch(phone, "phone");
  let sent = null, model = "";
  phone.on("request", (r) => {
    if (r.url().endsWith("/motion")) sent = r.postDataJSON();
    if (r.url().includes("pose_landmarker")) model = r.url().split("/").pop();
  });
  await phone.goto(link);
  await phone.waitForSelector("#film:not([hidden])");
  check("the phone says the whole body is recorded for a 3D character",
    await phone.isVisible("#film .body-hint"), (await phone.textContent("#film .body-hint")).trim().slice(0, 60));
  await phone.waitForFunction(() => document.getElementById("getting-ready").hidden, null, { timeout: 300000 });
  check("it loads the larger pose model", model === "pose_landmarker_full.task", model);
  check("the movement reader is ready", !(await phone.isVisible("#problem")), await phone.textContent("#problem"));

  await phone.setInputFiles("#pick", video);
  await phone.waitForSelector("#trim:not([hidden])");
  await phone.click("#btn-read");
  await phone.waitForSelector("#result:not([hidden]), #film:not([hidden]) #problem:not([hidden])", { timeout: 600000 })
    .catch(() => {});
  const withBody = sent ? sent.frames.filter((f) => f.body) : [];
  check("each frame with a person carries the 33 body points in metres",
    withBody.length > 0 && withBody.every((f) => f.body.length === 33 && f.body[0].length === 6) &&
    withBody.every((f) => f.body.every((r) => Math.abs(r[0]) < 5 && r[3] >= 0 && r[3] <= 1)),
    withBody.length + " of " + (sent ? sent.frames.length : 0) + " frames");
  const result = (await phone.textContent("#result-text")).trim();
  const whole = /whole body|upper body/.test(result);
  const fallback = result.includes("Not enough of your body was seen");
  check("the device made a movement of what it saw, saying what was kept or why not", whole || fallback, result.slice(0, 240));
  check("for a whole-body movement there is no idle-action choice", !whole || !(await phone.isVisible("#kind-row")));

  await phone.fill("#name", "phone body test");
  await phone.fill("#words", "do my phone test move");
  await phone.click("#btn-keep");
  await phone.waitForSelector("#kept:not([hidden])", { timeout: 20000 });
  check("it is kept", (await phone.textContent("#kept-text")).includes("phone body test"),
    (await phone.textContent("#kept-text")).trim());
  if (whole) {
    const r = await owner.evaluate(async () => {
      const list = await (await fetch("/api/characters")).json();
      const id = (list.characters || list)[0].id;
      return (await fetch("/api/characters/" + id + "/animations")).json();
    });
    check("the settings page lists it among the avatar's own animations",
      (r.added || []).some((a) => a.role === "phone_body_test" && a.kind === "gesture"),
      (r.added || []).map((a) => a.role).join(", "));
  }

  check("no errors on either page", errors.length === 0, errors.slice(0, 3).join(" | "));
  await browser.close();
  const failed = checks.filter((c) => !c[1]).length;
  console.log(failed ? failed + " FAILED of " + checks.length : "ALL " + checks.length + " PASSED");
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(2); });
