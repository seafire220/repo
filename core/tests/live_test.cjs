// "Copy me" and the live camera, end to end, in headless Chromium against tests/virtual_device.py and
// tests/fake_phone_app.py:
//
//   python3 tests/virtual_device.py DATA PORT HOST 8443      (HOST: this computer's network address)
//   python3 tests/fake_phone_app.py HOST
//   node tests/live_test.cjs http://HOST:PORT CHAR_FILE CAMERA SHOT_DIR HOST
//
// The owner turns the live camera on (the warning is explained first); a phone (Pixel 7) cannot open the
// device's own HTTPS until it accepts the warning, then it starts its camera, the character copies it, and it
// records and keeps a gesture live. Then the owner connects to a face-tracking app (the stand-in), the
// character copies it, and a gesture recorded from it opens in the gesture editor and is saved.
// CAMERA: a few seconds of a person facing the camera, as .y4m or .mjpeg (Chromium plays it as its camera).
const { chromium, devices } = require("playwright");
const [base, charFile, camera, shots, phoneApp] = process.argv.slice(2);
const checks = [];
const check = (name, ok, extra) => { checks.push([name, !!ok]); console.log((ok ? "PASS " : "FAIL ") + name + (extra ? "  [" + extra + "]" : "")); };

(async () => {
  // Straight to the device, as on a home network (not through any proxy the test machine has).
  const browser = await chromium.launch({ args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
    "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", "--use-file-for-fake-video-capture=" + camera,
    "--proxy-server=direct://"] });
  const errors = [];
  const watch = (page, who) => {
    page.on("pageerror", (e) => errors.push(who + " pageerror: " + e.message));
    page.on("console", (m) => { if (m.type() === "error") errors.push(who + " console: " + m.text().slice(0, 300)); });
  };
  const waitText = (page, selector, text, timeout = 30000) => page.waitForFunction(([s, t]) => {
    const el = document.querySelector(s);
    return el && el.textContent.includes(t);
  }, [selector, text], { timeout });

  // The owner, on a computer.
  const owner = await (await browser.newContext({ viewport: { width: 1000, height: 1000 } })).newPage();
  watch(owner, "owner");
  await owner.goto(base + "/");
  await owner.waitForSelector("#login:not([hidden])");
  await owner.fill("#login-password", "a good password");
  await owner.click("#login-form button");
  await owner.waitForSelector("#home:not([hidden])");
  await owner.click("#tabs button[data-tab=characters]");
  await owner.setInputFiles("#char-file", charFile);
  await owner.waitForSelector("#char-list li");
  await owner.click("#tabs button[data-tab=animation]");
  await owner.waitForSelector("#live-camera-box summary");
  await owner.click("#live-camera-box summary");
  await owner.waitForSelector("#live-camera");
  check("the live camera is off until the owner turns it on", !(await owner.isChecked("#live-camera")));
  const explained = await owner.textContent("#live-camera-box");
  check("the warning is explained before it appears", explained.includes("Proceed to") && explained.includes("visit this website"));
  await owner.check("#live-camera");
  await waitText(owner, "#live-camera-msg", "https");
  check("turned on, the device serves its own HTTPS", true);
  check("with its certificate's fingerprint to compare", /([0-9A-F]{2}:){7}[0-9A-F]{2}/.test(await owner.textContent("#live-fingerprint")));
  await (await owner.$("#live-camera-box")).screenshot({ path: shots + "/settings-live-camera.png" });
  await owner.click("#btn-rec-start");
  await owner.waitForSelector("#rec-box:not([hidden])");
  const link = (await owner.textContent("#rec-link")).trim();
  check("the recorder's link is https now", link.startsWith("https://"), link);

  // A phone that has not accepted the warning cannot open it.
  const strict = await (await browser.newContext({ ...devices["Pixel 7"] })).newPage();
  let refused = "";
  try { await strict.goto(link); } catch (e) { refused = e.message; }
  check("a phone first meets the certificate warning", /CERT/.test(refused), refused.split("\n")[0]);

  // The phone, after tapping past the warning.
  const phone = await (await browser.newContext({ ...devices["Pixel 7"], ignoreHTTPSErrors: true, permissions: ["camera"] })).newPage();
  watch(phone, "phone");
  await phone.goto(link);
  await phone.waitForSelector("#live:not([hidden])");
  check("then the recorder offers the live camera", await phone.evaluate(() => window.isSecureContext));
  await phone.click("#btn-cam");
  await phone.waitForSelector("#cam-tools:not([hidden])", { timeout: 120000 });
  check("the camera starts and the movement reader loads", true);
  await phone.click("#btn-copy");
  await waitText(phone, "#cam-note", "copying you", 60000);
  check("Copy me: the phone says the character is copying it", true, await phone.textContent("#cam-note"));
  await waitText(owner, "#live-status", "from the phone's live camera", 10000);
  check("and the settings page says so", true, await owner.textContent("#live-status"));
  await phone.click("#btn-copy");
  await waitText(phone, "#btn-copy", "Copy me on the screen");
  await owner.waitForFunction(() => !document.querySelector("#live-status").textContent.includes("live camera"), null, { timeout: 10000 });
  check("stopping hands the character back", true);

  await phone.click("#btn-live-rec");
  await phone.waitForSelector("#cam-count:not([hidden])");
  check("recording counts three first", true);
  await waitText(phone, "#btn-live-rec", "Stop", 10000);
  await phone.waitForTimeout(4000);
  await phone.click("#btn-live-rec");
  await phone.waitForSelector("#result:not([hidden]), #problem:not([hidden])", { timeout: 60000 });
  const result = await phone.textContent("#result-text");
  check("a live recording is played on the screen", result.includes("doing your gesture now"), result + " " + (await phone.textContent("#problem")));
  await phone.screenshot({ path: shots + "/phone-live-result.png", fullPage: true });
  await phone.fill("#name", "Live test");
  await phone.click("#btn-keep");
  await phone.waitForSelector("#kept:not([hidden])");
  check("and kept", (await phone.textContent("#kept-text")).includes("live test"));

  // A face-tracking app on the phone (the stand-in).
  await owner.selectOption("#app-kind", "vts");
  await owner.fill("#app-address", phoneApp);
  await owner.click("#btn-app-connect");
  await waitText(owner, "#live-status", "from VTube Studio or MeowFace (", 20000);
  check("Copy me from a face-tracking app", true, await owner.textContent("#live-status"));
  check("with its own switches (flip up and down)", await owner.isVisible("#live-flip"));
  await owner.click("#btn-live-record");
  await waitText(owner, "#btn-live-record", "Stop recording");
  await owner.waitForTimeout(3500);
  await owner.click("#btn-live-record");
  await waitText(owner, "#live-record-msg", "Recorded", 10000);
  check("a gesture recorded from it opens in the editor", (await owner.inputValue("#ed-name")) === "my_gesture" &&
    (await owner.textContent("#ed-origin")).includes("new"), await owner.textContent("#live-record-msg"));
  await owner.fill("#ed-name", "app_test");
  await owner.fill("#ed-words", "do the app test");
  await owner.click("#btn-ed-save");
  await owner.waitForFunction(() => [...document.querySelectorAll("#ed-pick option")].some((o) => o.textContent.startsWith("app test")),
    null, { timeout: 10000 });
  check("and is saved with the others", true);
  await (await owner.$("#animation")).screenshot({ path: shots + "/settings-copy-me.png" });
  await owner.click("#btn-app-stop");
  await owner.waitForSelector("#btn-app-stop", { state: "hidden" });
  check("disconnecting the app", !(await owner.textContent("#live-status")).includes("copying"));

  await owner.uncheck("#live-camera");
  await owner.waitForFunction(() => !document.querySelector("#live-camera-msg").textContent.includes("https"), null, { timeout: 10000 });
  let closed = "";
  try { await phone.goto(link, { timeout: 5000 }); } catch (e) { closed = e.message; }
  check("turned off, the device's HTTPS is gone", /REFUSED|CONNECTION|ERR_/.test(closed), closed.split("\n")[0]);

  const unexpected = errors.filter((e) => ![/XNNPACK delegate/, /status of 40[49]/, /ERR_CONNECTION_REFUSED/].some((x) => x.test(e)));
  check("no other script errors", !unexpected.length, unexpected.join(" | "));
  await browser.close();
  process.exit(checks.every(([, ok]) => ok) ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(2); });
