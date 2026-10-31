// M5 acceptance in a real browser: project → dataset → cases → publish version → evaluation run executed by
// the separate sample agent → per-case results → linked trace. Backend: app.dev_test_server (traceforge_test).
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "../..");
const PYTHON = path.join(REPO, ".venv", "Scripts", "python.exe");
const EXAMPLE = path.join(REPO, "examples", "sample_agent");
const SHOTS = path.join(HERE, "screenshots");

test("dataset to evaluation to linked trace, end to end", async ({ page }) => {
  const stamp = Date.now();
  await page.goto("/register");
  await page.getByLabel("Name").fill("Eval Engineer");
  await page.getByLabel("Email").fill(`eval-${stamp}@example.com`);
  await page.getByLabel("Password").fill("correct horse battery staple");
  await page.getByRole("button", { name: "Create account" }).click();

  // Project and API key.
  await page.getByRole("button", { name: "New project" }).click();
  await page.getByLabel("Project name").fill(`Support agent ${stamp}`);
  await page.getByLabel("Agent name").fill("support-bot");
  await page.getByRole("button", { name: "Create", exact: true }).click();
  await page.getByLabel("Key name").fill("evaluator");
  await page.getByRole("button", { name: "Create key" }).click();
  const apiKey = (await page.getByTestId("new-api-key").textContent())!.trim();
  await page.getByRole("button", { name: "I have stored it" }).click();

  // Dataset with one case typed in the editor and the rest imported from JSONL.
  await page.getByRole("link", { name: "Datasets" }).click();
  await page.getByRole("button", { name: "New dataset" }).click();
  await page.getByLabel("Name").fill("Support QA");
  await page.getByLabel("Description").fill("Golden customer-support questions");
  await page.getByRole("button", { name: "Create", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Support QA" })).toBeVisible();

  const lines = readFileSync(path.join(EXAMPLE, "support_qa.jsonl"), "utf-8").trim().split("\n");
  const first = JSON.parse(lines[0]);
  await page.getByRole("button", { name: "Add case" }).click();
  const editor = page.getByRole("form", { name: "New case" });
  await editor.getByLabel(/^Key/).fill(first.key);
  await editor.getByLabel(/^Input/).fill(JSON.stringify(first.input));
  await editor.getByLabel(/^Expected output/).fill(first.expected_output);
  await editor.getByLabel(/^Expected sources/).fill(first.expected_sources.join("\n"));
  await editor.getByRole("button", { name: "Add case" }).click();
  await expect(page.locator("tbody tr")).toHaveCount(1);

  await page.getByRole("button", { name: "Import / export" }).click();
  await page.getByLabel("Import content").fill("{bad json\n" + lines.slice(1).join("\n"));
  await page.getByRole("button", { name: "Validate and import" }).click();
  await expect(page.getByRole("alert")).toContainText("line 1: Invalid JSON");
  await page.screenshot({ path: `${SHOTS}/m5-01-import-validation.png` });
  await page.getByLabel("Import content").fill(lines.slice(1).join("\n"));
  await page.getByRole("button", { name: "Validate and import" }).click();
  await expect(page.getByRole("status")).toContainText("Imported 3 case(s); the draft now has 4.");

  await page.getByRole("button", { name: "Draft cases" }).click();
  await expect(page.locator("tbody tr")).toHaveCount(4);
  await page.screenshot({ path: `${SHOTS}/m5-02-draft-cases.png`, fullPage: true });

  // Publish an immutable version.
  await page.getByRole("button", { name: "Versions" }).click();
  await page.getByPlaceholder("Release notes (optional)").fill("baseline");
  await page.getByRole("button", { name: "Publish v1" }).click();
  await expect(page.getByText("v1", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Publish v2" })).toBeDisabled();

  // Create the run and execute it with the sample agent.
  await page.getByRole("button", { name: "Evaluations" }).last().click();
  const form = page.getByRole("form", { name: "New evaluation run" });
  await form.getByLabel("Run name").fill("baseline run");
  await form.getByLabel("Normalized match: Output field").fill("answer");
  await form.getByLabel(/Required fields/).check();
  await form.getByLabel("Required fields: Fields").fill("answer");
  await form.getByRole("button", { name: "Create run" }).click();
  await expect(page.getByText(/traceforge\.evaluate\(/)).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/m5-03-pending-run.png` });
  const runId = page.url().split("/evaluations/")[1].split("?")[0];

  const output = execFileSync(PYTHON, [path.join(EXAMPLE, "evaluate.py"), "--run", runId, "--endpoint", "http://127.0.0.1:8000"], {
    env: { ...process.env, PYTHONPATH: path.join(REPO, "sdk/python/src"), TRACEFORGE_API_KEY: apiKey },
    encoding: "utf-8",
    timeout: 60_000,
  });
  expect(JSON.parse(output).status).toBe("completed");

  // The run page picks up the result on its own.
  await expect(page.getByText("completed", { exact: true }).first()).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("50.0%").first()).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/m5-04-completed-run.png`, fullPage: true });

  // Failed cases: wrong answer, and a retrieval miss.
  await page.getByRole("tab", { name: /Failed/ }).click();
  const results = page.getByRole("list", { name: "Case results" });
  await expect(results.getByRole("button")).toHaveCount(2);
  await results.getByText("warranty-accidental-damage").click();
  const detail = page.getByLabel("Case warranty-accidental-damage");
  await expect(detail).toContainText("Differs even after normalizing");
  await expect(detail).toContainText("Items can be returned within 30 days");
  await page.screenshot({ path: `${SHOTS}/m5-05-failed-case.png`, fullPage: true });

  await results.getByText("shipping-a100").click();
  const shipping = page.getByLabel("Case shipping-a100");
  await expect(shipping).toContainText("Not retrieved: kb/shipping.md");
  await shipping.getByRole("link", { name: /retrieval span/ }).click();

  // The supporting span opens in the Trace Explorer.
  const tree = page.getByRole("tree", { name: "Span timeline" });
  await expect(tree).toBeVisible();
  await expect(page.getByLabel("Span kb-search")).toContainText("RETRIEVAL", { ignoreCase: true });
  await expect(page.getByText("evaluation", { exact: true })).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/m5-06-linked-trace.png`, fullPage: true });

  // History lists the run with its pass rate.
  await page.getByRole("link", { name: "Evaluations" }).click();
  await expect(page.getByRole("link", { name: "baseline run" })).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/m5-07-run-history.png` });
});
