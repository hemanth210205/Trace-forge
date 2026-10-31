// M7 acceptance in a real browser: completed evaluation → experiment (baseline vs candidate on the same dataset
// version) executed by the sample agent → comparison → supporting traces → hypothesis validation → human decision.
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
const ENV = (key: string) => ({ ...process.env, PYTHONPATH: path.join(REPO, "sdk/python/src"), TRACEFORGE_API_KEY: key });

test("experiment compares baseline and candidate and records a human decision", async ({ page }) => {
  const stamp = Date.now();
  await page.goto("/register");
  await page.getByLabel("Name").fill("Experiment Engineer");
  await page.getByLabel("Email").fill(`exp-${stamp}@example.com`);
  await page.getByLabel("Password").fill("correct horse battery staple");
  await page.getByRole("button", { name: "Create account" }).click();
  await page.getByRole("button", { name: "New project" }).click();
  await page.getByLabel("Project name").fill(`Support agent ${stamp}`);
  await page.getByLabel("Agent name").fill("support-bot");
  await page.getByRole("button", { name: "Create", exact: true }).click();
  await page.getByLabel("Key name").fill("runner");
  await page.getByRole("button", { name: "Create key" }).click();
  const apiKey = (await page.getByTestId("new-api-key").textContent())!.trim();
  await page.getByRole("button", { name: "I have stored it" }).click();

  // Dataset v1 and a completed source evaluation (sample agent, default configuration).
  await page.getByRole("link", { name: "Datasets" }).click();
  await page.getByRole("button", { name: "New dataset" }).click();
  await page.getByLabel("Name").fill("Support QA");
  await page.getByRole("button", { name: "Create", exact: true }).click();
  await page.getByRole("button", { name: "Import / export" }).click();
  await page.getByLabel("Import content").fill(readFileSync(path.join(EXAMPLE, "support_qa.jsonl"), "utf-8"));
  await page.getByRole("button", { name: "Validate and import" }).click();
  await page.getByRole("button", { name: "Versions" }).click();
  await page.getByRole("button", { name: "Publish v1" }).click();
  await expect(page.getByRole("button", { name: "Publish v2" })).toBeDisabled();
  await page.getByRole("button", { name: "Evaluations" }).last().click();
  const runForm = page.getByRole("form", { name: "New evaluation run" });
  await runForm.getByLabel("Run name").fill("production config");
  await runForm.getByLabel("Normalized match: Output field").fill("answer");
  await runForm.getByRole("button", { name: "Create run" }).click();
  await expect(page.getByText(/traceforge\.evaluate\(/)).toBeVisible();
  const sourceRun = page.url().split("/evaluations/")[1].split("?")[0];
  execFileSync(PYTHON, [path.join(EXAMPLE, "evaluate.py"), "--run", sourceRun, "--endpoint", "http://127.0.0.1:8000"], { env: ENV(apiKey) });
  await expect(page.getByText("completed", { exact: true }).first()).toBeVisible({ timeout: 15_000 });

  // Diagnose, then test the shipping retrieval hypothesis with an experiment.
  await page.getByRole("button", { name: "Diagnose failures" }).click();
  const shipping = page.getByRole("region", { name: "Diagnosis of case shipping-a100" });
  const hypothesis = shipping.getByRole("article", { name: "Hypothesis Retrieval failure" });
  await expect(hypothesis).toContainText("Unvalidated hypothesis", { timeout: 15_000 });
  await hypothesis.getByRole("link", { name: "Test with an experiment →" }).click();
  const form = page.getByRole("form", { name: "New experiment" });
  await expect(form).toContainText("Support QA v1");
  await form.getByLabel("Name").fill("stemmed retriever");
  await form.getByLabel("Description").fill("Does stemming fix the shipping retrieval miss?");
  await form.getByRole("button", { name: "Create experiment" }).click();
  await expect(page.getByRole("heading", { name: "stemmed retriever" })).toBeVisible();
  await expect(page.getByRole("region", { name: "What differs" })).toContainText('"stemmed"');
  await expect(page.getByText(/traceforge\.run_experiment\(/)).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/m7-01-pending-experiment.png`, fullPage: true });
  const experimentUrl = page.url();
  const experimentId = experimentUrl.split("/experiments/")[1];

  // Replay both arms with the sample agent; the page picks up the comparison.
  const output = execFileSync(PYTHON, [path.join(EXAMPLE, "experiment.py"), "--experiment", experimentId, "--endpoint", "http://127.0.0.1:8000"],
    { env: ENV(apiKey), encoding: "utf-8", timeout: 60_000 });
  expect(JSON.parse(output).status).toBe("completed");
  const metrics = page.getByRole("table", { name: "Metrics" });
  await expect(metrics).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("Candidate improves")).toBeVisible();
  await expect(metrics).toContainText("+25.0 pts");
  await expect(metrics.getByText("Latency (mean)").locator("..")).toContainText("4 paired case(s)");
  const perCase = page.getByRole("table", { name: "Per-case comparison" });
  const shippingRow = perCase.getByRole("row").filter({ hasText: "shipping-a100" });
  await expect(shippingRow).toContainText("Fixed");
  await expect(perCase.getByRole("row").filter({ hasText: "warranty-accidental-damage" })).toContainText("Still failing");
  await page.screenshot({ path: `${SHOTS}/m7-02-comparison.png`, fullPage: true });

  // Supporting trace of the candidate arm.
  await shippingRow.getByRole("link", { name: "candidate trace" }).click();
  await page.getByRole("tree", { name: "Span timeline" }).getByText("kb-search").click();
  await expect(page.getByLabel("Span kb-search")).toContainText("kb/shipping.md");
  await page.screenshot({ path: `${SHOTS}/m7-03-candidate-trace.png`, fullPage: true });
  await page.goto(experimentUrl);

  // Validation needs the experiment's evidence; promotion needs a human decision.
  await page.getByRole("button", { name: "Mark hypothesis validated" }).click();
  await expect(page.getByText("Validated by this experiment")).toBeVisible();
  const decision = page.getByRole("region", { name: "Promotion decision" });
  await expect(decision.getByRole("button", { name: "Approve promotion" })).toBeDisabled();
  await decision.getByLabel(/Rationale/).fill("Stemming fixes the shipping retrieval miss with no regressions.");
  await decision.getByRole("button", { name: "Approve promotion" }).click();
  await expect(decision.getByRole("list", { name: "Decision history" })).toContainText("Approved by Experiment Engineer");
  await page.screenshot({ path: `${SHOTS}/m7-04-decision.png`, fullPage: true });

  // The diagnosis now shows the validated cause, linked to the experiment.
  await page.goto(`/projects/${page.url().split("/projects/")[1].split("/")[0]}/diagnoses`);
  await page.getByRole("link", { name: "production config" }).click();
  await expect(page.getByRole("region", { name: "Diagnosis of case shipping-a100" })).toContainText("Retrieval failure — validated by an experiment");
});
