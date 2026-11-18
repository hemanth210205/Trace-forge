// M4 acceptance in a real browser: UI-created project and API key -> the separate sample agent sends traces
// through the SDK -> the Trace Explorer shows and inspects them. Backend: app.dev_test_server (traceforge_test).
import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test, type Page } from "@playwright/test";

const HERE = path.dirname(fileURLToPath(import.meta.url));

const REPO = path.resolve(HERE, "../..");
const PYTHON = path.join(REPO, ".venv", "Scripts", "python.exe");
const SHOTS = path.join(HERE, "screenshots");
const PASSWORD = "correct horse battery staple";

async function register(page: Page, email: string, name: string) {
  await page.goto("/register");
  await page.getByLabel("Name").fill(name);
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("heading", { name: "Projects" })).toBeVisible();
}

test("SDK traces from the sample agent are visible and inspectable in the Trace Explorer", async ({ page, browser }) => {
  const stamp = Date.now();
  await register(page, `e2e-${stamp}@example.com`, "E2E Engineer");
  await page.screenshot({ path: `${SHOTS}/01-projects-empty.png` });

  // Project + API key through the UI.
  await page.getByRole("button", { name: "New project" }).click();
  await page.getByLabel("Project name").fill(`Support agent ${stamp}`);
  await page.getByLabel("Agent name").fill("support-bot");
  await page.getByRole("button", { name: "Create", exact: true }).click();
  await expect(page.getByRole("heading", { name: "API keys" })).toBeVisible();
  const projectId = page.url().split("/projects/")[1].split("/")[0];
  await page.getByLabel("Key name").fill("sample-agent");
  await page.getByRole("button", { name: "Create key" }).click();
  const apiKey = (await page.getByTestId("new-api-key").textContent())!.trim();
  expect(apiKey).toMatch(/^tf_[0-9a-f]{8}_/);
  await page.screenshot({ path: `${SHOTS}/02-api-key-created.png` });

  // The separate sample agent sends real traces with that key.
  const output = execFileSync(PYTHON, [path.join(REPO, "examples/sample_agent/agent.py"), "--endpoint", "http://127.0.0.1:8000"], {
    env: { ...process.env, PYTHONPATH: path.join(REPO, "sdk/python/src"), TRACEFORGE_API_KEY: apiKey },
    encoding: "utf-8",
    timeout: 60_000,
  });
  expect(output).toContain('"dropped_items": 0');

  // Trace list.
  await page.getByRole("button", { name: "I have stored it" }).click();
  await page.getByRole("link", { name: "Traces" }).click();
  const rows = page.locator("tbody tr");
  await expect(rows).toHaveCount(3);
  await expect(page.getByText("3 traces · end of results")).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/03-trace-list.png` });

  // Filter to the trace whose tool span failed, open it, inspect the error.
  await page.getByLabel("Spans with errors").check();
  await expect(rows).toHaveCount(1);
  await rows.first().getByRole("link").click();
  const tree = page.getByRole("tree", { name: "Span timeline" });
  await expect(tree.getByRole("treeitem")).toHaveCount(4);
  await expect(page.getByLabel("Trace details")).toContainText("Z999");
  await tree.getByText("order-lookup").click();
  const toolPanel = page.getByLabel("Span order-lookup");
  await expect(toolPanel).toContainText("LookupError");
  await expect(toolPanel).toContainText("order Z999 not found");
  await expect(toolPanel).toContainText("Tool call");
  await page.screenshot({ path: `${SHOTS}/04-trace-detail-tool-error.png`, fullPage: true });

  // LLM span: model, tokens and cost as reported by the SDK.
  await tree.getByText("compose-answer").click();
  const llmPanel = page.getByLabel("Span compose-answer");
  await expect(llmPanel).toContainText("sample-template-llm");
  await expect(llmPanel).toContainText(/\d+ in · \d+ out · \d+ total/);
  await page.screenshot({ path: `${SHOTS}/05-trace-detail-llm.png`, fullPage: true });

  // Back to the (still filtered) list, clear filters, and open the refund question's trace.
  await page.getByRole("link", { name: "Back to Traces" }).click();
  await page.getByRole("button", { name: "Clear filters" }).click();
  await expect(rows).toHaveCount(3);
  for (let i = 0; i < 3; i++) {
    await rows.nth(i).getByRole("link").click();
    await expect(page.getByLabel("Trace details")).toContainText("question");
    if (await page.getByLabel("Trace details").getByText("refunds").count()) break;
    await page.getByRole("link", { name: "Back to Traces" }).click();
  }
  await tree.getByText("kb-search").click();
  const kbPanel = page.getByLabel("Span kb-search");
  await expect(kbPanel).toContainText("Retrieved documents");
  await expect(kbPanel).toContainText("kb/returns.md");
  await expect(kbPanel).not.toContainText("Model call");
  await page.screenshot({ path: `${SHOTS}/06-trace-detail-retrieval.png`, fullPage: true });

  // Redaction done by the SDK is what the UI shows.
  await tree.getByText("support-agent").click();
  await page.getByRole("button", { name: "Close ✕" }).click();
  await expect(page.getByLabel("Trace details")).toContainText("[REDACTED]");
  await expect(page.getByLabel("Trace details")).not.toContainText("fake-key-123");

  // Mobile layout.
  await page.setViewportSize({ width: 390, height: 844 });
  await tree.getByText("compose-answer").click();
  await page.screenshot({ path: `${SHOTS}/07-mobile-trace-detail.png`, fullPage: true });

  // Another user cannot see this project's traces (backend ownership check -> 404 -> UI error).
  const other = await browser.newContext();
  const otherPage = await other.newPage();
  await register(otherPage, `e2e-other-${stamp}@example.com`, "Someone Else");
  await otherPage.goto(`/projects/${projectId}/traces`);
  await expect(otherPage.getByRole("alert")).toContainText("Project not found");
  await otherPage.screenshot({ path: `${SHOTS}/08-other-user-denied.png` });
  await other.close();
});
