// M6 acceptance in a real browser: failed evaluation → Diagnose → evidence-backed hypotheses → supporting span
// in the Trace Explorer; plus a run without traces → insufficient evidence. Backend: app.dev_test_server.
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test, type Page } from "@playwright/test";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "../..");
const PYTHON = path.join(REPO, ".venv", "Scripts", "python.exe");
const EXAMPLE = path.join(REPO, "examples", "sample_agent");
const SHOTS = path.join(HERE, "screenshots");
const ENV = (key: string) => ({ ...process.env, PYTHONPATH: path.join(REPO, "sdk/python/src"), TRACEFORGE_API_KEY: key });

async function createRun(page: Page, name: string): Promise<string> {
  await page.getByRole("button", { name: "Evaluations" }).last().click();
  const form = page.getByRole("form", { name: "New evaluation run" });
  await form.getByLabel("Run name").fill(name);
  await form.getByLabel("Normalized match: Output field").fill("answer");
  await form.getByRole("button", { name: "Create run" }).click();
  await expect(page.getByText(/traceforge\.evaluate\(/)).toBeVisible();
  return page.url().split("/evaluations/")[1].split("?")[0];
}

test("failed evaluation to evidence-backed diagnosis to supporting span", async ({ page }) => {
  const stamp = Date.now();
  await page.goto("/register");
  await page.getByLabel("Name").fill("Diagnosis Engineer");
  await page.getByLabel("Email").fill(`diag-${stamp}@example.com`);
  await page.getByLabel("Password").fill("correct horse battery staple");
  await page.getByRole("button", { name: "Create account" }).click();
  await page.getByRole("button", { name: "New project" }).click();
  await page.getByLabel("Project name").fill(`Support agent ${stamp}`);
  await page.getByLabel("Agent name").fill("support-bot");
  await page.getByRole("button", { name: "Create", exact: true }).click();
  await page.getByLabel("Key name").fill("evaluator");
  await page.getByRole("button", { name: "Create key" }).click();
  const apiKey = (await page.getByTestId("new-api-key").textContent())!.trim();
  await page.getByRole("button", { name: "I have stored it" }).click();

  await page.getByRole("link", { name: "Datasets" }).click();
  await page.getByRole("button", { name: "New dataset" }).click();
  await page.getByLabel("Name").fill("Support QA");
  await page.getByRole("button", { name: "Create", exact: true }).click();
  await page.getByRole("button", { name: "Import / export" }).click();
  await page.getByLabel("Import content").fill(readFileSync(path.join(EXAMPLE, "support_qa.jsonl"), "utf-8"));
  await page.getByRole("button", { name: "Validate and import" }).click();
  await expect(page.getByRole("status")).toContainText("Imported 4 case(s)");
  await page.getByRole("button", { name: "Versions" }).click();
  await page.getByRole("button", { name: "Publish v1" }).click();
  await expect(page.getByRole("button", { name: "Publish v2" })).toBeDisabled();
  const datasetUrl = page.url().split("?")[0];

  // Execute the run with the sample agent, then diagnose it from the run page.
  const runId = await createRun(page, "baseline run");
  execFileSync(PYTHON, [path.join(EXAMPLE, "evaluate.py"), "--run", runId, "--endpoint", "http://127.0.0.1:8000"], { env: ENV(apiKey), timeout: 60_000 });
  await expect(page.getByText("completed", { exact: true }).first()).toBeVisible({ timeout: 15_000 });
  await page.getByRole("button", { name: "Diagnose failures" }).click();
  await expect(page.getByRole("heading", { name: "Diagnosis of baseline run" })).toBeVisible();
  await expect(page.getByText("completed", { exact: true }).first()).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText(/No cause is experimentally validated yet/)).toBeVisible();

  const warranty = page.getByRole("region", { name: "Diagnosis of case warranty-accidental-damage" });
  const selection = warranty.getByRole("article", { name: "Hypothesis Context selection failure" });
  await expect(selection).toContainText("Evidence supports");
  await expect(selection).toContainText("Unvalidated hypothesis");
  await expect(selection).toContainText("kb/warranty.md at rank 2");
  await expect(warranty.getByRole("article", { name: "Hypothesis Grounding failure" })).toContainText("Evidence contradicts");
  await expect(warranty).toContainText("None. No cause for this case has been confirmed by an experiment.");

  const shipping = page.getByRole("region", { name: "Diagnosis of case shipping-a100" });
  const retrieval = shipping.getByRole("article", { name: "Hypothesis Retrieval failure" });
  await expect(retrieval).toContainText("not for an incorrect answer");
  await page.screenshot({ path: `${SHOTS}/m6-01-diagnosis.png`, fullPage: true });

  // Follow the supporting span into the Trace Explorer.
  await selection.getByRole("link").filter({ hasText: /^[0-9a-f]{8}$/ }).first().click();
  await expect(page.getByRole("tree", { name: "Span timeline" })).toBeVisible();
  const span = page.getByLabel("Span kb-search");
  await expect(span).toContainText("kb/returns.md");
  await expect(span).toContainText("kb/warranty.md");
  await page.screenshot({ path: `${SHOTS}/m6-02-supporting-span.png`, fullPage: true });

  // A run whose results were submitted without traces: the diagnosis must say the evidence is insufficient.
  await page.goto(datasetUrl);
  const blindRun = await createRun(page, "untraced run");
  const script = [
    "import sys, traceforge",
    "from traceforge._evaluate import _call",
    "c = traceforge.Client(endpoint='http://127.0.0.1:8000')",
    "rid = sys.argv[1]",
    "cases = _call(c, 'GET', f'/sdk/eval-runs/{rid}')['cases']",
    "_call(c, 'POST', f'/sdk/eval-runs/{rid}/results', {'results': [{'case_id': x['case_id'], 'output': {'answer': 'I do not know.'}} for x in cases]})",
    "print(_call(c, 'POST', f'/sdk/eval-runs/{rid}/finish')['status']); c.shutdown()",
  ].join("\n");
  expect(execFileSync(PYTHON, ["-c", script, blindRun], { env: ENV(apiKey), encoding: "utf-8" }).trim()).toBe("completed");
  await expect(page.getByText("completed", { exact: true }).first()).toBeVisible({ timeout: 15_000 });
  await page.getByRole("button", { name: "Diagnose failures" }).click();
  await expect(page.getByText(/4 case\(s\) lack the evidence for any conclusion/)).toBeVisible({ timeout: 15_000 });
  const untraced = page.getByRole("region").filter({ hasText: "no trace linked" }).first();
  await expect(untraced.getByRole("article", { name: "Hypothesis Insufficient evidence" })).toContainText("Inconclusive");
  await expect(untraced).toContainText("confidence n/a");
  await page.screenshot({ path: `${SHOTS}/m6-03-insufficient-evidence.png`, fullPage: true });

  await page.getByRole("link", { name: "Diagnoses" }).click();
  await expect(page.getByRole("link", { name: "baseline run" })).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/m6-04-diagnosis-history.png` });
});
