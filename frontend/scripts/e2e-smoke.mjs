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

await step("a task and a note can be linked and the backlink shows on the task", async () => {
  const post = async (path, data) => (await page.request.post(`${base}/api${path}`, { headers: { "X-Jarvis": "1" }, data })).json();
  const task = await post("/m/tasks", { title: "E2E zadanie" });
  const note = await post("/m/notes", { content: "# E2E notatka\nszczegóły", tags: ["e2e"] });
  await post("/hub/links", { a: `tasks:${task.id}`, b: `notes:${note.id}` });
  await page.goto(`${base}/#/tasks?id=${task.id}`, { waitUntil: "networkidle" });
  await page.locator(".related").getByText("E2E notatka").waitFor({ timeout: 15000 });
});

await step("tasks with a due date show in the calendar and can be ticked", async () => {
  const due = new Date(); due.setHours(23, 0, 0, 0);
  await (await page.request.post(`${base}/api/m/tasks`, { headers: { "X-Jarvis": "1" }, data: { title: "E2E dzisiaj", due: due.toISOString() } })).json();
  await page.goto(`${base}/#/calendar`, { waitUntil: "networkidle" });
  await page.locator(".task-list li", { hasText: "E2E dzisiaj" }).locator("input[type=checkbox]").click({ timeout: 15000 });
  await page.getByText("Zrobione: E2E dzisiaj").first().waitFor({ timeout: 15000 });
});

await browser.close();
const real = errors.filter((e) => !/favicon|manifest/.test(e));
if (real.length) {
  console.error("browser errors:\n" + real.join("\n"));
  process.exit(1);
}
console.log("e2e smoke passed");
