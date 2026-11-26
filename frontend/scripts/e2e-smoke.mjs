// Short end-to-end check (CI and local): first-run setup in the UI -> dashboard -> Settings -> backup now.
// Needs a fresh core (empty database, JARVIS_SETUP_TOKEN set) and the frontend serving /api through to it.
//   JARVIS_URL=http://localhost:5173 JARVIS_SETUP_TOKEN=… node scripts/e2e-smoke.mjs
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const base = process.env.JARVIS_URL ?? "http://localhost:5173";
const token = process.env.JARVIS_SETUP_TOKEN ?? "";
const password = "e2e-password-123456";

const browser = await chromium.launch();
const page = await browser.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
page.on("console", (m) => m.type() === "error" && errors.push(`console: ${m.text()}`));

const step = async (name, fn) => {
  try {
    await fn();
    console.log(`ok   ${name}`);
  } catch (e) {
    console.error(`FAIL ${name}: ${e.message}`);
    await page.screenshot({ path: "e2e-failure.png", fullPage: true }).catch(() => undefined);
    await browser.close();
    process.exit(1);
  }
};

await step("first-run setup form creates the account", async () => {
  await page.goto(base, { waitUntil: "networkidle" });
  const inputs = page.locator("form.login input");
  await inputs.nth(0).fill(token);
  await inputs.nth(1).fill("owner");
  await inputs.nth(2).fill(password);
  await page.locator("form.login button.primary").click();
  await page.locator(".page-head h1").first().waitFor({ timeout: 15000 });
});

await step("dashboard shows tiles and the 2FA reminder", async () => {
  await page.locator(".notice", { hasText: "dwuetapowa" }).waitFor({ timeout: 15000 });
  await page.locator(".tile").first().waitFor({ timeout: 15000 });
});

await step("settings: backup now", async () => {
  await page.goto(`${base}/#/settings`, { waitUntil: "networkidle" });
  const card = page.locator("section.card", { hasText: "Kopie zapasowe" });
  await card.getByRole("button", { name: "Zrób kopię teraz" }).first().click();
  await card.locator(".backup-list li").first().waitFor({ timeout: 15000 });
});

await step("diagnostics page loads", async () => {
  await page.goto(`${base}/#/debug`, { waitUntil: "networkidle" });
  await page.getByText("Kopie zapasowe bazy").first().waitFor({ timeout: 15000 });
});

await browser.close();
const real = errors.filter((e) => !/favicon|manifest/.test(e));
if (real.length) {
  console.error("browser errors:\n" + real.join("\n"));
  process.exit(1);
}
console.log("e2e smoke passed");
