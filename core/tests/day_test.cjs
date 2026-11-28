// The settings page's "Your day" tab, in headless Chromium against tests/virtual_device.py (whose weather is made
// up: nothing asks a weather service): about you, a weather service and its key, the weather's town, a wake-up,
// a day of your own, the clock; saving, checking the weather, trying a wake-up and stopping it.
//
//   python3 tests/virtual_device.py DATA PORT
//   node tests/day_test.cjs http://127.0.0.1:PORT CHAR_FILE
const { chromium } = require("playwright");
const [base, charFile] = process.argv.slice(2);
const checks = [];
const check = (name, ok, extra) => { checks.push([name, !!ok]); console.log((ok ? "PASS " : "FAIL ") + name + (extra ? "  [" + extra + "]" : "")); };

(async () => {
  const browser = await chromium.launch();
  const page = await (await browser.newContext({ viewport: { width: 420, height: 900 } })).newPage();
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
  await page.click("#tabs button[data-tab=day]");
  await page.waitForSelector("#day:not([hidden])");
  await page.waitForFunction(() => document.querySelectorAll("#day-bmonth option").length === 13);

  await page.fill("#day-name", "Sam");
  await page.selectOption("#day-bmonth", "07");
  await page.fill("#day-bday", "9");
  check("no weather service to begin with", (await page.inputValue("#day-wx-provider")) === "" && await page.isHidden("#day-wx-key"));
  await page.selectOption("#day-wx-provider", "openweather");
  check("choosing one asks for its key, with where to get it", (await page.textContent("#day-wx-key")).includes("openweathermap.org"));
  await page.fill("#day-wx-key-input", "a-made-up-weather-key-12345");
  await page.click("#btn-day-wx-key");
  await page.waitForFunction(() => document.querySelector("#day-wx-key").textContent.includes("Key saved"), null, { timeout: 5000 });
  check("the key is saved and not shown again", (await page.inputValue("#day-wx-key-input")) === "");
  await page.click("#btn-day-place");
  await page.fill("#day-place-q", "perth");
  await page.waitForSelector("#day-place-list button");
  const offered = await page.$$eval("#day-place-list button", (bs) => bs.map((b) => b.textContent));
  check("towns are found as the name is typed", offered.some((t) => t.includes("Western Australia")), offered.slice(0, 3).join(" | "));
  await page.click("#day-place-list button:has-text('Western Australia')");
  check("the town chosen is shown", (await page.textContent("#day-place")).startsWith("Perth, Western Australia"));

  await page.click("#btn-day-add-wake");
  await page.fill("#day-wakeups li input[type=time]", "06:45");
  await page.fill("#day-wakeups li input[type=text]", "work");
  const days = await page.$$eval("#day-wakeups li .days input", (bs) => bs.map((b) => b.checked));
  check("a new wake-up is for weekdays", days.join() === "true,true,true,true,true,false,false", days.join());
  await page.click("#day-wakeups li .days label[title=Saturday]");

  await page.click("#btn-day-add-date");
  const row = "#day-dates li";
  await page.selectOption(row + " select", "12");
  await page.fill(row + " input[aria-label=Day]", "1");
  await page.fill(row + " input[aria-label=Year]", "2026");
  await page.fill(row + " input[type=text]", "moving house");
  await page.check(row + " label input[type=checkbox]");
  await page.selectOption("#day-clock", "idle");
  await page.selectOption("#day-hours", "24");
  await page.click("#btn-save-day");
  await page.waitForFunction(() => document.querySelector("#day-msg").textContent === "Saved.", null, { timeout: 10000 });
  const s = await page.evaluate(async () => (await fetch("/api/settings")).json());
  check("about you is kept", s.owner.name === "Sam" && s.owner.birthday === "07-09", JSON.stringify(s.owner));
  check("the service and town are kept", s.weather.provider === "openweather" && s.weather.place &&
    s.weather.place.label.startsWith("Perth, Western Australia"), JSON.stringify(s.weather));
  check("the key is not in the settings", !JSON.stringify(s).includes("a-made-up-weather-key"));
  const w = s.alarms.wake_ups[0] || {};
  check("the wake-up is kept", w.time === "06:45" && w.label === "work" && w.days.join() === "0,1,2,3,4,5" && w.id,
    JSON.stringify(w));
  check("the day of your own is kept", JSON.stringify(s.special_days.dates) ===
    JSON.stringify([{ date: "2026-12-01", what: "moving house", once: true }]), JSON.stringify(s.special_days.dates));
  check("the clock is kept", s.clock.show === "idle" && s.clock.hours === "24", JSON.stringify(s.clock));
  check("the next wake-up is told", (await page.textContent("#day-next")).startsWith("Next wake-up:"));

  await page.click("#btn-day-weather-check");
  await page.waitForFunction(() => document.querySelector("#day-weather-msg").textContent.startsWith("The character would say"),
    null, { timeout: 15000 });
  check("the weather is checked", (await page.textContent("#day-weather-msg")).includes("The weather in Perth"),
    await page.textContent("#day-weather-msg"));
  check("the service's credit is shown", (await page.textContent("#day-credit")).includes("Weather data provided by OpenWeather"));

  await page.selectOption("#day-bmonth", "07");
  await page.fill("#day-bday", "");
  await page.click("#btn-save-day");
  await page.waitForFunction(() => document.querySelector("#day-msg").textContent.includes("Your birthday"), null, { timeout: 5000 });
  check("half a birthday is refused before saving", true, await page.textContent("#day-msg"));
  await page.fill("#day-bday", "9");

  await page.click("#btn-day-try-wake");
  await page.waitForSelector("#day-ringing:not([hidden])", { timeout: 5000 });
  check("a wake-up tried rings", true);
  await page.click("#btn-day-stop");
  await page.waitForSelector("#day-ringing", { state: "hidden", timeout: 5000 });
  check("and stops from the page", true);
  check("no errors on the page", errors.length === 0, errors.slice(0, 3).join(" | "));
  await browser.close();
  const failed = checks.filter((c) => !c[1]).length;
  console.log(failed ? failed + " FAILED of " + checks.length : "ALL " + checks.length + " PASSED");
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(2); });
