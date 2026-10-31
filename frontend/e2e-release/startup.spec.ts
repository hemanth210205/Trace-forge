// Local release: a fresh browser session against `python -m app.serve` serving the production build.
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

const SHOTS = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "e2e", "screenshots");

test("one server runs the console and the API from a single origin", async ({ page, request }) => {
  // Every failed response and script error, except the console's expected "not signed in" probe.
  const problems: string[] = [];
  page.on("response", (response) => {
    const url = new URL(response.url());
    const expected = response.status() === 401 && url.pathname === "/api/v1/auth/me";
    if (response.status() >= 400 && !expected) problems.push(`${response.status()} ${url.pathname}`);
  });
  page.on("console", (message) => {
    if (message.type() === "error" && !message.text().startsWith("Failed to load resource")) problems.push(message.text());
  });
  page.on("pageerror", (error) => problems.push(error.message));

  // Fresh session: protected routes go to sign-in.
  await page.goto("/");
  await expect(page).toHaveURL(/\/login$/);

  const stamp = Date.now();
  const email = `release-${stamp}@example.com`;
  await page.goto("/register");
  await page.getByLabel("Name").fill("Release Check");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("correct horse battery staple");
  await page.getByRole("button", { name: "Create account" }).click();
  await page.getByRole("button", { name: "New project" }).click();
  await page.getByLabel("Project name").fill(`Release ${stamp}`);
  await page.getByLabel("Agent name").fill("support-bot");
  await page.getByRole("button", { name: "Create", exact: true }).click();
  await expect(page.getByRole("navigation", { name: "Project" })).toBeVisible();

  // Client-side routes survive a full reload (server returns the app shell, the session cookie persists).
  await page.getByRole("link", { name: "Datasets" }).click();
  const datasets = page.url();
  await page.reload();
  await expect(page).toHaveURL(datasets);
  await expect(page.getByRole("button", { name: "New dataset" })).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/release-01-served-console.png`, fullPage: true });

  // Sign out and back in on the same origin (login's Origin check trusts the serving origin).
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login$/);
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("correct horse battery staple");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("button", { name: "New project" })).toBeVisible();

  // The API keeps its own behaviour next to the console.
  const missing = await request.get("/api/v1/does-not-exist");
  expect(missing.status()).toBe(404);
  expect((await missing.json()).error.code).toBe("not_found");
  expect((await request.get("/api/v1/health")).ok()).toBeTruthy();
  expect((await request.get("/api/docs")).ok()).toBeTruthy();

  expect(problems).toEqual([]);  // includes Content-Security-Policy violations
});
