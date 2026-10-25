// Browser smoke test: the page renders without script errors, and the PIN dialog gates a change.
//   node tests/smoke.mjs            (needs playwright; CHROMIUM=/path/to/chromium to use a system browser)
import { spawn } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT || "playwright");
const here = dirname(fileURLToPath(import.meta.url));
const tmp = mkdtempSync(join(tmpdir(), "dmrdash-"));
writeFileSync(join(tmp, "conf.json"), JSON.stringify({ device_id: 268000000, api_key: "", pin: "4321" }));
const port = 18000 + Math.floor(Math.random() * 1000);
const srv = spawn("python3", [join(here, "serve.py"), String(port)], {
  env: { ...process.env, DMRDASH_CONF: join(tmp, "conf.json"), STATE_DIRECTORY: tmp }, stdio: "inherit" });

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
  if (!process.exitCode) console.log("smoke ok");
} catch (e) {
  fail(e.message);
} finally {
  await browser?.close();
  srv.kill();
}
