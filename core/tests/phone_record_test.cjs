// Recording a gesture with a phone, end to end, in headless Chromium against tests/virtual_device.py:
// the owner asks for a link on the settings page; a phone (Pixel 7) opens it without logging in, chooses a
// video, finds the movement with MediaPipe, sends it, plays it mirrored, keeps it, keeps another under the same
// name after confirming, types the code instead of scanning, and finds the link ended once the owner stops it.
//
//   python3 tests/virtual_device.py DATA PORT HOST     (HOST: this computer's network address, so the phone's
//                                                       page is plain http off localhost, as on a home network)
//   node tests/phone_record_test.cjs http://HOST:PORT VIDEO SHOT_DIR
//
// VIDEO: a few seconds of a person facing the camera and moving their head (any phone video of someone who
// agreed to it). The device downloads MediaPipe's files (24 MB) when first asked, unless DATA/models/mocap has
// them already (python3 -m companion.mocap_assets DATA/models/mocap).
const { chromium, devices } = require("playwright");
const [base, video, shots] = process.argv.slice(2);
const checks = [];
const check = (name, ok, extra) => { checks.push([name, !!ok]); console.log((ok ? "PASS " : "FAIL ") + name + (extra ? "  [" + extra + "]" : "")); };
const local = /^https?:\/\/(localhost|127\.|\[::1\])/.test(base);

(async () => {
  const browser = await chromium.launch({ args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
  const errors = [];
  const watch = (page, who) => {
    page.on("pageerror", (e) => errors.push(who + " pageerror: " + e.message));
    page.on("console", (m) => { if (m.type() === "error") errors.push(who + " console: " + m.text().slice(0, 300)); });
  };
  const saysOn = (page, text) => page.waitForFunction((t) => document.querySelector("#rec-status").textContent.includes(t),
    text, { timeout: 10000 });

  // The owner, on a computer.
  const owner = await (await browser.newContext({ viewport: { width: 1000, height: 900 } })).newPage();
  watch(owner, "owner");
  await owner.goto(base + "/");
  await owner.waitForSelector("#login:not([hidden])");
  await owner.fill("#login-password", "a good password");
  await owner.click("#login-form button");
  await owner.waitForSelector("#home:not([hidden])");
  await owner.click("#tabs button[data-tab=animation]");
  await owner.click("#btn-rec-start");
  await owner.waitForSelector("#rec-box:not([hidden])");
  const link = (await owner.textContent("#rec-link")).trim();
  const code = (await owner.textContent("#rec-code")).trim();
  check("the settings page shows a link with a one-time code", /\/r\/[A-Z2-9]{6}$/.test(link) && link.endsWith(code), link);
  check("and a QR code, and where to type the code", await owner.$eval("#rec-qr", (i) => !i.hidden && i.naturalWidth > 100) &&
    (await owner.textContent("#rec-short")).endsWith("/r"));
  await (await owner.$("#rec-box")).screenshot({ path: shots + "/settings-qr.png" });

  // The phone: no login, only the link.
  const phoneCtx = await browser.newContext({ ...devices["Pixel 7"] });
  const phone = await phoneCtx.newPage();
  watch(phone, "phone");
  let sent = null;
  phone.on("request", (r) => { if (r.url().endsWith("/motion")) sent = r.postDataJSON(); });
  await phone.goto(link);
  await phone.waitForSelector("#film:not([hidden])");
  check("the phone opens the recorder without a password", true);
  if (!local) check("as plain http on the network: not a secure context", !(await phone.evaluate(() => window.isSecureContext)));
  await saysOn(owner, "has the recorder open");
  check("the settings page sees the phone open it", true);
  await phone.waitForFunction(() => document.getElementById("getting-ready").hidden, null, { timeout: 300000 });
  check("the movement reader loads from the device", !(await phone.isVisible("#problem")), await phone.textContent("#problem"));
  await phone.screenshot({ path: shots + "/phone-1-film.png", fullPage: true });

  await phone.setInputFiles("#pick", video);
  await phone.waitForSelector("#trim:not([hidden])");
  const start = Number(await phone.inputValue("#start")), end = Number(await phone.inputValue("#end"));
  check("a chosen video goes to the trim step", end > start, start + " to " + end + " s");
  const began = Date.now();
  await phone.click("#btn-read");
  await phone.waitForSelector("#result:not([hidden]), #problem:not([hidden])", { timeout: 600000 });
  const result = await phone.textContent("#result-text");
  check("the movement is found and played on the screen", result.includes("doing your gesture now"),
    result + " " + (await phone.textContent("#problem")) + " (" + ((Date.now() - began) / 1000).toFixed(1) + " s)");
  check("the phone sends only points, frame by frame", sent && sent.frames.length === Math.floor((end - start) * 12) + 1 &&
    Object.keys(sent).sort().join() === "fps,frames,height,mirror,width", sent && Math.round(JSON.stringify(sent).length / 1024) + " KB");
  const visibility = new Set((sent ? sent.frames : []).filter((f) => f.pose).flatMap((f) => f.pose.map((j) => j[3].toFixed(2))));
  check("with how sure the body model is of each joint", visibility.size > 3);
  await phone.screenshot({ path: shots + "/phone-2-result.png", fullPage: true });
  await saysOn(owner, "is playing on the screen");
  check("the settings page says a gesture came", true);

  await phone.check("#mirror");
  await phone.waitForTimeout(500);
  check("swapping left and right plays it again", !(await phone.isVisible("#problem")));
  await phone.fill("#name", "Phone test");
  await phone.fill("#words", "do the phone test");
  await phone.click("#btn-keep");
  await phone.waitForSelector("#kept:not([hidden])");
  check("it is kept under its name", (await phone.textContent("#kept-text")).includes("phone test"));
  await phone.screenshot({ path: shots + "/phone-3-kept.png", fullPage: true });
  await saysOn(owner, "Kept: phone test");
  check("the settings page shows it kept", true);

  // Another under the same name: the phone asks before replacing.
  await phone.click("#btn-another");
  await phone.waitForSelector("#film:not([hidden])");
  await phone.setInputFiles("#camera", video);
  await phone.waitForSelector("#trim:not([hidden])");
  await phone.click("#btn-read");
  await phone.waitForSelector("#result:not([hidden])", { timeout: 600000 });
  await phone.fill("#name", "phone test");
  await phone.click("#btn-keep");
  await phone.waitForSelector("#replace:not([hidden])");
  check("keeping another under the same name asks first", (await phone.textContent("#replace-text")).includes("already a gesture"));
  await phone.click("#btn-replace");
  await phone.waitForSelector("#kept:not([hidden])");
  check("and replaces it when asked", true);

  // Typing the code instead of scanning.
  const typed = await phoneCtx.newPage();
  watch(typed, "typed");
  await typed.goto(base + "/r");
  await typed.waitForSelector("#ask-code:not([hidden])");
  await typed.fill("#code", code.toLowerCase());
  await typed.click("#code-form button");
  await typed.waitForSelector("#film:not([hidden])");
  check("typing the code (in small letters) opens the recorder", true);

  // The owner stops it: the link ends.
  await owner.click("#btn-rec-end");
  await owner.waitForSelector("#rec-box", { state: "hidden" });
  await typed.reload();
  await typed.waitForSelector("#problem:not([hidden])");
  check("a stopped link says so", (await typed.textContent("#problem")).includes("ask for a new one"));
  await owner.waitForFunction(() => [...document.querySelectorAll("#ed-pick option")].some((o) => o.textContent.startsWith("phone test")),
    null, { timeout: 10000 });
  check("the kept gesture is in the gesture editor", true);

  // Expected: MediaPipe's own note, the name clash asked about, and the stopped link.
  const unexpected = errors.filter((e) => ![/XNNPACK delegate/, /status of 409/, /status of 404/].some((x) => x.test(e)));
  check("no other script errors", !unexpected.length, unexpected.join(" | "));
  await browser.close();
  process.exit(checks.every(([, ok]) => ok) ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(2); });
