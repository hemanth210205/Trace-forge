// M8 in a real browser: unknown/foreign projects are a clear dead end, and the console fits a phone-width screen.
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test, type Page } from "@playwright/test";

const SHOTS = path.join(path.dirname(fileURLToPath(import.meta.url)), "screenshots");

async function noHorizontalScroll(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
}

test("unknown projects are not rendered and the console fits a phone screen", async ({ page }) => {
  const stamp = Date.now();
  await page.goto("/register");
  await page.getByLabel("Name").fill("Reliability Engineer");
  await page.getByLabel("Email").fill(`rel-${stamp}@example.com`);
  await page.getByLabel("Password").fill("correct horse battery staple");
  await page.getByRole("button", { name: "Create account" }).click();
  await page.getByRole("button", { name: "New project" }).click();
  await page.getByLabel("Project name").fill(`Phone check ${stamp}`);
  await page.getByLabel("Agent name").fill("support-bot");
  await page.getByRole("button", { name: "Create", exact: true }).click();
  await expect(page.getByRole("navigation", { name: "Project" })).toBeVisible();
  const projectUrl = page.url();

  await page.goto("/projects/00000000-0000-4000-8000-000000000000/traces");
  await expect(page.getByText("Project not found")).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Project" })).toHaveCount(0);
  await page.getByRole("link", { name: "Back to projects" }).click();
  await expect(page).toHaveURL(/\/$/);

  await page.setViewportSize({ width: 375, height: 800 });
  for (const tab of ["traces", "datasets", "evaluations", "diagnoses", "experiments", "keys"]) {
    await page.goto(projectUrl.replace(/\/projects\/([^/]+)\/.*/, `/projects/$1/${tab}`));
    await expect(page.getByRole("navigation", { name: "Project" })).toBeVisible();
    await noHorizontalScroll(page);
  }
  await page.screenshot({ path: `${SHOTS}/m8-01-phone-experiments.png`, fullPage: true });
});
