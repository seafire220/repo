// The settings page's backgrounds, in headless Chromium against tests/virtual_device.py: the built-in pictures
// are shown, one is chosen for every character, a picture of the owner's is added (and chosen), a character is
// given its own, and the owner's picture is removed.
//
//   python3 tests/virtual_device.py DATA PORT
//   node tests/backgrounds_test.cjs http://127.0.0.1:PORT CHAR_FILE PICTURE
//
// CHAR_FILE: any character package (as for console_test.cjs). PICTURE: any JPEG or PNG.
const { chromium } = require("playwright");
const [base, charFile, picture] = process.argv.slice(2);
const checks = [];
const check = (name, ok, extra) => { checks.push([name, !!ok]); console.log((ok ? "PASS " : "FAIL ") + name + (extra ? "  [" + extra + "]" : "")); };

(async () => {
  const browser = await chromium.launch();
  const page = await (await browser.newContext({ viewport: { width: 1000, height: 900 } })).newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text().slice(0, 200)); });
  await page.goto(base + "/");
  await page.waitForSelector("#login:not([hidden]), #setup:not([hidden])");
  if (await page.isVisible("#setup")) {
    await page.fill("#setup-password", "a good password");
    await page.click("#setup-form button");
  } else {
    await page.fill("#login-password", "a good password");
    await page.click("#login-form button");
  }
  await page.waitForSelector("#home:not([hidden])");
  await page.click("#tabs button[data-tab=characters]");
  if (!(await page.$("#char-list li"))) {
    await page.setInputFiles("#char-file", charFile);
    await page.waitForSelector("#char-list li");
  }
  await page.waitForSelector("#bg-grid .bg-item");
  const shown = await page.$$eval("#bg-grid .bg-item button[data-picture]", (bs) => bs.map((b) => b.dataset.picture));
  check("the choices are shown: with the time of day, none, your pictures in turn, eight built-in and six festive pictures",
    shown.length === 17, shown.join(" "));
  check("the festive ones have their own heading", (await page.textContent("#bg-grid .bg-group")).includes("Festive"));
  const loaded = await page.$$eval("#bg-grid img", (imgs) => Promise.all(imgs.map((i) => i.complete && i.naturalWidth
    ? i.naturalWidth : new Promise((r) => { i.onload = () => r(i.naturalWidth); i.onerror = () => r(0); }))));
  check("each built-in and festive picture has its thumbnail", loaded.length === 14 && loaded.every((w) => w === 120), loaded.join(","));
  check("with the time of day is the default", await page.$eval(".bg-item.on button", (b) => b.dataset.picture) === "auto");

  await page.click("#bg-grid button[data-picture=sunset]");
  await page.waitForSelector(".bg-item.on button[data-picture=sunset]");
  check("choosing one makes it the default", true);
  await page.fill("#bg-dim", "0.3");
  await page.dispatchEvent("#bg-dim", "change");
  await page.waitForTimeout(500);
  const def = await page.evaluate(async () => (await (await fetch("/api/backgrounds")).json()).default);
  check("darker is kept", def.dim === 0.3 && def.choice === "sunset", JSON.stringify(def));

  await page.setInputFiles("#bg-file", picture);
  await page.waitForFunction(() => document.querySelectorAll("#bg-grid .bg-remove").length === 1, null, { timeout: 20000 });
  const mine = await page.$eval(".bg-item.on button", (b) => b.dataset.picture);
  check("a picture of the owner's is added and chosen", mine.startsWith("my_"), mine);

  await page.click("#char-list li button:text-is('Edit')");
  await page.waitForSelector("#char-edit:not([hidden])");
  await page.selectOption("#char-bg", "room");
  await page.waitForTimeout(500);
  const id = await page.evaluate(async () => (await (await fetch("/api/characters")).json()).characters[0].id);
  const own = await page.evaluate(async (cid) => (await fetch("/api/characters/" + cid + "/background")).json(), id);
  check("a character can have its own", own.choice === "room", JSON.stringify(own));

  page.once("dialog", (d) => d.accept());
  await page.click("#bg-grid .bg-remove");
  await page.waitForFunction(() => document.querySelectorAll("#bg-grid .bg-remove").length === 0, null, { timeout: 10000 });
  check("the owner's picture is removed", true);
  check("no errors on the page", errors.length === 0, errors.slice(0, 3).join(" | "));
  await browser.close();
  const failed = checks.filter((c) => !c[1]).length;
  console.log(failed ? failed + " FAILED of " + checks.length : "ALL " + checks.length + " PASSED");
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(2); });
