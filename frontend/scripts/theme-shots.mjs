// Screenshots of app pages in EVERY installed theme, desktop and phone, plus basic layout checks.
// Every new or changed module must pass this before it is done (see CLAUDE.md, skill jarvis-design).
//
//   JARVIS_URL=http://localhost:5173 JARVIS_USER=owner JARVIS_PASSWORD=... \
//     node scripts/theme-shots.mjs [out-dir] [route,route,...]
//
// Routes default to: dashboard, every enabled module, system, settings. Needs Playwright + Chromium
// (`npm i -D playwright && npx playwright install chromium` on your machine; preinstalled in Claude's cloud sandbox).
// Exit code 1 when a page overflows horizontally on the phone, shows a JS error, or fails to load.
import { readFileSync, mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
let chromium;
try {
  ({ chromium } = require("playwright"));
} catch {
  console.error("Playwright is not installed: npm i -D playwright && npx playwright install chromium");
  process.exit(2);
}

const here = dirname(fileURLToPath(import.meta.url));
const themes = JSON.parse(readFileSync(join(here, "..", "public", "themes", "index.json"), "utf8"));
const base = (process.env.JARVIS_URL ?? "http://localhost:5173").replace(/\/$/, "");
const out = process.argv[2] ?? "theme-shots";
const { JARVIS_USER: user, JARVIS_PASSWORD: password } = process.env;
if (!user || !password) {
  console.error("Set JARVIS_USER and JARVIS_PASSWORD (an existing account on JARVIS_URL).");
  process.exit(2);
}
mkdirSync(out, { recursive: true });

const browser = await chromium.launch();
const problems = [];
try {
  const login = await browser.newContext();
  const res = await login.request.post(`${base}/api/auth/login`, { headers: { "X-Jarvis": "1" }, data: { username: user, password } });
  if (!res.ok()) throw new Error(`login failed: HTTP ${res.status()} (accounts with 2FA: use a test account without TOTP)`);
  const state = await login.storageState();
  let routes = process.argv[3]?.split(",").filter(Boolean);
  if (!routes) {
    const mods = await (await login.request.get(`${base}/api/modules`)).json();
    routes = ["dashboard", ...mods.filter((m) => m.status === "ok").map((m) => m.id), "debug", "system", "settings"];
  }
  const sizes = { desktop: { width: 1280, height: 860 }, phone: { width: 390, height: 844 } };
  for (const theme of themes) {
    for (const [size, viewport] of Object.entries(sizes)) {
      const ctx = await browser.newContext({ viewport, storageState: state });
      await ctx.addInitScript((id) => localStorage.setItem("jarvis.theme", id), theme.id);
      const page = await ctx.newPage();
      const errors = [];
      page.on("pageerror", (e) => errors.push(e.message));
      for (const route of routes) {
        errors.length = 0;
        await page.goto(`${base}/#/${route}`, { waitUntil: "networkidle" });
        await page.waitForFunction((id) => document.documentElement.dataset.theme === id, theme.id, { timeout: 5000 })
          .catch(() => problems.push(`${theme.id}/${route}: theme not applied`));
        await page.waitForTimeout(250);
        const file = join(out, `${theme.id}--${route}--${size}.png`);
        await page.screenshot({ path: file, fullPage: true });
        if (size === "phone" && (await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)))
          problems.push(`${theme.id}/${route}/phone: horizontal overflow`);
        for (const e of errors) problems.push(`${theme.id}/${route}/${size}: JS error: ${e}`);
      }
      await ctx.close();
    }
  }
  console.log(`Saved ${themes.length} themes × ${routes.length} routes × 2 sizes to ${out}/`);
} finally {
  await browser.close();
}
if (problems.length) {
  console.error("Problems:\n  " + problems.join("\n  "));
  process.exit(1);
}
