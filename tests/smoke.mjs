// Browser smoke test: the page renders without script errors, and the PIN dialog gates a change.
//   node tests/smoke.mjs            (needs playwright; CHROMIUM=/path/to/chromium to use a system browser)
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT || "playwright");
const here = dirname(fileURLToPath(import.meta.url));
const tmp = mkdtempSync(join(tmpdir(), "dmrdash-"));
writeFileSync(join(tmp, "MMDVM.ini"), "[General]\nTimeout=180\n\n[Modem]\nRXOffset=-350\n\n[DMR]\nColorCode=1\n\n[DMR Network]\nPassword=s3cret\n");
writeFileSync(join(tmp, "conf.json"), JSON.stringify({ device_id: 268000000, api_key: "", pin: "4321", mmdvm_ini: join(tmp, "MMDVM.ini") }));
const port = 18000 + Math.floor(Math.random() * 1000);
const srv = spawn("python3", [join(here, "serve.py"), String(port)], {
  env: { ...process.env, DMRDASH_CONF: join(tmp, "conf.json"), STATE_DIRECTORY: tmp, DMRDASH_LATEST: "99.0.0" }, stdio: "inherit" });

const fail = (msg) => { console.error("FAIL:", msg); process.exitCode = 1; };
let browser;
try {
  await new Promise((r) => setTimeout(r, 800));
  browser = await chromium.launch(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {});
  for (const viewport of [{ width: 1280, height: 900 }, { width: 390, height: 844 }]) {
    const page = await browser.newPage({ viewport });
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    await page.goto(`http://localhost:${port}/`);
    await page.waitForSelector("#lcd1");
    await page.waitForTimeout(500);
    if (errors.length) fail(`${viewport.width}px: ${errors.join("; ")}`);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    if (overflow > 0) fail(`${viewport.width}px: page scrolls sideways by ${overflow}px`);
    await page.close();
  }

  const page = await browser.newPage();
  await page.goto(`http://localhost:${port}/`);
  // the same path every change takes (Clear is disabled while there are no events)
  await page.evaluate(() => { window.__res = post({ action: "clearEvents" }); });
  await page.waitForSelector("#pindlg[open]");
  await page.fill("#pinin", "0000");
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => document.querySelector("#pindlg[open] #pinwhy.bad"));
  await page.fill("#pinin", "4321");
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => !document.querySelector("#pindlg").open);
  const res = await page.evaluate(() => window.__res);
  if (!res.ok) fail("change refused after the right PIN: " + JSON.stringify(res));
  await page.click("#ibtn");
  const kept = await page.evaluate(() => sessionStorage.getItem("dmrdash.pin"));
  if (kept !== "4321") fail("PIN not kept for the tab");
  const dash = await page.textContent("#stfoot");
  if (!/dmrdash \d+\.\d+\.\d+ · PIN on/.test(dash)) fail("station footer lacks the version: " + dash);

  // a newer dmrdash in the repo: the Hotspot menu offers it, and asking for it shows the update under way
  await page.click("#ibtn");
  await page.click("#hbtn");
  const row = await page.textContent("#hr4");
  if (!row.includes("99.0.0 available")) fail("dmrdash row doesn't offer the update: " + row);
  await page.click("#updgo");
  await page.waitForSelector("#powdlg[open]");
  if (!(await page.textContent("#powtitle")).includes("99.0.0")) fail("update confirmation lacks the version");
  await page.click("#powok");
  await page.waitForFunction(() => /Updating dmrdash to 99\.0\.0/.test(document.querySelector("#alarm").textContent));
  // last, as it leaves a DMRHost restart pending: the MMDVM.ini editor, with a form edit, a refused value in the text view, then review as a line diff and write
  await page.click("#hbtn");
  await page.click("#cfgopen");
  await page.waitForSelector(".cr");
  if ((await page.content()).includes("s3cret")) fail("the editor shows the password");
  await page.selectOption("#cfgsel", "Modem").catch(() => {});
  if (await page.isVisible(".cfgsec")) await page.click('.cfgsec:has-text("Modem")');
  await page.fill("input[aria-label=RXOffset]", "-400");
  await page.click("#cfgv-text");
  await page.waitForSelector("#cfgta");
  const ta = await page.inputValue("#cfgta");
  await page.fill("#cfgta", ta.replace("ColorCode=1", "ColorCode=16"));
  if (!(await page.isDisabled("#cfgreview"))) fail("review allowed with ColorCode=16");
  await page.fill("#cfgta", ta);
  await page.click("#cfgreview");
  await page.click("#cfgd-d");
  const diff = await page.textContent(".dif");
  if (!diff.includes("RXOffset=-350") || !diff.includes("RXOffset=-400")) fail("line diff lacks the change: " + diff);
  await page.click("#cfgwrite");
  await page.waitForFunction(() => document.querySelector("#cfg").hidden);
  const req = existsSync(join(tmp, "config.request")) ? readFileSync(join(tmp, "config.request"), "utf8") : "";
  if (!req.includes("RXOffset=-400") || req.includes("s3cret") || !req.includes("\"op\": \"apply\"")) fail("config request wrong: " + req);
  await page.waitForFunction(() => S.config.op === "apply");
  if (!process.exitCode) console.log("smoke ok");
} catch (e) {
  fail(e.message);
} finally {
  await browser?.close();
  srv.kill();
}
