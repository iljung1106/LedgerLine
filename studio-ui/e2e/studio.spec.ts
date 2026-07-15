import { expect, test, type Page } from "@playwright/test";
import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";

type StudioNote = {
  event_id?: string;
  event_index: number;
  id: string;
  measure: number;
  part: string;
  pitch_index: number;
  pitch: number;
  start_seconds: number;
  end_seconds: number;
  velocity: number;
  voice: string;
};

type StudioModel = {
  csrf_token: string;
  history: { can_redo: boolean; can_undo: boolean };
  notes: StudioNote[];
  parts: { id: string; name: string; profile: string }[];
  project: { revision: string; title: string };
  transport: { duration_seconds: number };
  mix: { format?: number; tracks: Record<string, { gain_db: number }> };
  media: { stems: { part: string; kind?: string; spectrogram_url?: string | null }[] };
};

const repository = path.resolve(import.meta.dirname, "..", "..");
const project = path.join(repository, ".cache", "studio-e2e", "nocturne");

test("production Studio stays synchronized while editing, reviewing, and delegating", async ({ page }) => {
  test.setTimeout(70_000);
  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  const initialModelResponse = page.waitForResponse((response) => (
    response.request().method() === "GET" && response.url().endsWith("/api/model")
  ));
  await page.goto("/");
  const loadedResponse = await initialModelResponse;
  expect(loadedResponse.ok()).toBeTruthy();
  const loadedModel = await loadedResponse.json() as StudioModel;
  await expect(page.getByText("LedgerLine Studio", { exact: true })).toBeVisible();
  await expect(page.getByText(loadedModel.project.title, { exact: true })).toBeVisible();

  const transport = page.locator(".transport-buttons");
  const playPauseButton = transport.getByRole("button", { name: /^(Play|Pause)/ });
  const stopButton = transport.getByRole("button", { name: "Stop" });
  const undoButton = transport.getByRole("button", { name: /^Undo/ });
  const redoButton = transport.getByRole("button", { name: /^Redo/ });
  await expect(transport.locator("button")).toHaveCount(4);
  await expect(page.getByRole("tab", { name: "Guide" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Edit" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Score" })).toBeVisible();
  const engineStatus = page.getByRole("region", { name: "Engine and artifact status" });
  await expect(engineStatus).toBeVisible();
  await expect(engineStatus.getByText("authored", { exact: true })).toBeVisible();
  await expect(engineStatus.getByText("compile", { exact: true })).toBeVisible();
  await expect(engineStatus.getByRole("button", { name: "Refresh analysis" })).toBeVisible();

  await page.getByRole("tab", { name: "Edit" }).click();
  await expect(page.getByRole("application", { name: /^Editable piano roll/ })).toBeVisible();
  await expect(page.getByRole("application", { name: "Velocity editor" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Mixer" })).toBeVisible();
  await expect(transport.locator("button")).toHaveCount(4);

  const initial = await getModel(page);
  const note = [...initial.notes]
    .filter((item) => item.event_id && item.pitch >= 24 && item.pitch <= 96)
    .sort((left, right) => left.start_seconds - right.start_seconds)[0];
  expect(note, "the isolated fixture should have persistent event IDs").toBeTruthy();
  const nextVelocity = note!.velocity >= 120 ? note!.velocity - 7 : note!.velocity + 7;
  const pianoRoll = page.getByRole("application", { name: /^Editable piano roll/ });
  await pianoRoll.scrollIntoViewIfNeeded();
  const rollBox = await pianoRoll.boundingBox();
  expect(rollBox).toBeTruthy();
  const noteX = rollBox!.x + note!.start_seconds / initial.transport.duration_seconds * rollBox!.width + 3;
  const noteY = rollBox!.y + (96 - note!.pitch + 0.5) / 73 * rollBox!.height;
  await page.mouse.click(noteX, noteY);
  const inspector = page.locator(".inspector");
  const velocityInput = inspector.getByLabel("Velocity", { exact: true });
  await expect(velocityInput).toHaveValue(String(note!.velocity));
  await velocityInput.fill(String(nextVelocity));
  const noteWrite = page.waitForResponse((response) => response.request().method() === "POST" && response.url().endsWith("/api/commands"));
  await inspector.getByRole("button", { name: "Apply note changes" }).click();
  expect((await noteWrite).ok()).toBeTruthy();

  await expect.poll(async () => (await getModel(page)).project.revision).not.toBe(initial.project.revision);
  await expect.poll(async () => velocityFor(page, note!.id)).toBe(nextVelocity);
  await expect(undoButton).toBeEnabled({ timeout: 6_000 });
  await expect(engineStatus.getByText("stale", { exact: true }).first()).toBeVisible({ timeout: 6_000 });

  const undoWrite = page.waitForResponse((response) => response.request().method() === "POST" && response.url().endsWith("/api/undo"));
  await undoButton.click();
  expect((await undoWrite).ok()).toBeTruthy();
  await expect.poll(async () => velocityFor(page, note!.id)).toBe(note!.velocity);
  await expect(redoButton).toBeEnabled();
  const redoWrite = page.waitForResponse((response) => response.request().method() === "POST" && response.url().endsWith("/api/redo"));
  await redoButton.click();
  expect((await redoWrite).ok()).toBeTruthy();
  await expect.poll(async () => velocityFor(page, note!.id)).toBe(nextVelocity);

  const afterNote = await getModel(page);
  expect(afterNote.mix.format).toBe(2);
  const track = afterNote.parts.find((part) => afterNote.mix.tracks[part.id]);
  expect(track).toBeTruthy();
  const currentGain = afterNote.mix.tracks[track!.id].gain_db;
  const nextGain = currentGain >= 23.5 ? currentGain - 0.5 : currentGain + 0.5;
  await page.locator(".mix-editor-heading select").selectOption(`track:${track!.id}`);
  const gainInput = page.locator(".mix-node-editor .mix-field").filter({ hasText: "Gain" }).first().locator("input");
  await gainInput.fill(String(nextGain));
  const mixWrite = page.waitForResponse((response) => response.request().method() === "POST" && response.url().endsWith("/api/commands"));
  await gainInput.press("Tab");
  expect((await mixWrite).ok()).toBeTruthy();
  await expect.poll(async () => (await getModel(page)).mix.tracks[track!.id].gain_db).toBe(nextGain);

  const stem = afterNote.media.stems.find((item) => item.part === track!.id) ?? afterNote.media.stems[0];
  expect(stem).toBeTruthy();
  await page.getByLabel("Analysis source").selectOption(`stem:${stem!.part}`);
  await expect(page.locator(`.waveform-shell[data-media-kind="stem"][data-media-part="${stem!.part}"]`)).toBeVisible();
  await expect(page.locator(".spectrogram-empty")).toContainText("No spectrogram is available");

  await pianoRoll.scrollIntoViewIfNeeded();
  const loopBox = await pianoRoll.boundingBox();
  expect(loopBox).toBeTruthy();
  await page.keyboard.down("Alt");
  await page.mouse.move(loopBox!.x + loopBox!.width * 0.06, loopBox!.y + 36);
  await page.mouse.down();
  await page.mouse.move(loopBox!.x + loopBox!.width * 0.14, loopBox!.y + 36, { steps: 6 });
  await page.mouse.up();
  await page.keyboard.up("Alt");
  await expect.poll(async () => Number(await pianoRoll.getAttribute("data-loop-end"))).toBeGreaterThan(0);
  const loopStart = Number(await pianoRoll.getAttribute("data-loop-start"));
  const loopEnd = Number(await pianoRoll.getAttribute("data-loop-end"));
  expect(loopEnd).toBeGreaterThan(loopStart);
  await expect(page.getByRole("button", { name: "Clear loop" })).toBeVisible();
  await playPauseButton.click();
  await expect.poll(async () => page.locator(".time-readout").textContent()).not.toBe("0:00.000");
  await stopButton.click();
  await page.getByRole("button", { name: "Clear loop" }).click();

  await page.getByRole("tab", { name: "Score" }).click();
  const score = page.locator(".score-host.is-ready");
  await expect(score).toBeVisible({ timeout: 15_000 });
  await expect.poll(async () => Number(await score.getAttribute("data-cursor-count")), { timeout: 15_000 }).toBeGreaterThan(1);
  await playPauseButton.click();
  await expect.poll(async () => Number(await score.getAttribute("data-transport-tick"))).toBeGreaterThan(0);
  await expect.poll(async () => Number(await score.getAttribute("data-cursor-tick"))).toBeGreaterThan(0);
  await playPauseButton.click();
  const cursorX = Number(await score.getAttribute("data-cursor-x"));
  const cursorY = Number(await score.getAttribute("data-cursor-y"));
  await score.click({ position: { x: cursorX, y: cursorY } });
  await expect.poll(async () => Number(await score.getAttribute("data-last-seek-tick"))).toBeGreaterThan(0);
  await expect.poll(async () => {
    const sought = Number(await score.getAttribute("data-last-seek-tick"));
    const transportTick = Number(await score.getAttribute("data-transport-tick"));
    return Math.abs(sought - transportTick);
  }).toBeLessThanOrEqual(1);
  await stopButton.click();
  await expect(transport.locator("button")).toHaveCount(4);

  const celloReceipt = engineStatus.locator('details.engine-receipt[data-part-id="cello"]');
  await celloReceipt.locator("summary").click();
  const profileSelect = celloReceipt.getByLabel("Profile / MIDI preset for Cello");
  await expect(profileSelect).toHaveValue("starter.cello");
  await expect(celloReceipt).toContainText("Changing the profile makes this stem and the master stale");
  const profileWrite = page.waitForRequest((request) => request.method() === "POST" && request.url().endsWith("/api/commands"));
  await profileSelect.selectOption("starter.acoustic-grand-piano");
  const profileRequest = await profileWrite;
  const profilePayload = profileRequest.postDataJSON() as { commands: Record<string, unknown>[] };
  expect(profilePayload.commands).toEqual([{ type: "update_instrument", part: "cello", changes: { profile: "starter.acoustic-grand-piano" } }]);
  await expect.poll(async () => (await getModel(page)).parts.find((part) => part.id === "cello")?.profile).toBe("starter.acoustic-grand-piano");

  await page.getByRole("tab", { name: "Guide" }).click();
  const createResponse = page.waitForResponse((response) => (
    response.request().method() === "POST" && response.url().endsWith("/api/delegations")
  ));
  await page.getByLabel("What should change?").fill("Keep the opening intimate and clarify the cello response.");
  await page.getByRole("button", { name: "Delegate" }).click();
  const task = await (await createResponse).json();
  await expect(page.getByText("pending", { exact: true }).first()).toBeVisible();

  const taskPath = path.join(project, ".ledgerline", "delegations", `${task.id}.json`);
  const queued = JSON.parse(await readFile(taskPath, "utf8"));
  queued.status = "needs-direction";
  queued.questions = ["Should the climax stay restrained or become openly dramatic?"];
  queued.proposal = {
    summary: "Direction is required before changing the climax.",
    actions: [],
    questions: queued.questions,
  };
  await writeFile(taskPath, `${JSON.stringify(queued, null, 2)}\n`, "utf8");

  await page.reload();
  await expect(page.getByText("The agent needs your direction", { exact: true })).toBeVisible();
  await expect(page.getByText(queued.questions[0], { exact: true })).toBeVisible();
  await page.getByLabel("Your answer").fill("Stay restrained, but widen the harmony and increase inner motion.");
  const answerResponse = page.waitForResponse((response) => (
    response.request().method() === "POST" && response.url().endsWith(`/api/delegations/${task.id}/answer`)
  ));
  await page.getByRole("button", { name: "Send direction" }).click();
  const answered = await (await answerResponse).json();
  expect(answered.status).toBe("pending");
  expect(answered.answers.at(-1).text).toContain("Stay restrained");
  await expect(page.getByText("Direction sent. The agent can continue from this checkpoint.")).toBeVisible();

  const reviewRevision = (await getModel(page)).project.revision;
  let mockedTask = reviewableTask(answered, reviewRevision);
  await page.route("**/api/delegations", async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ schema_version: "1", status: "ok", tasks: [mockedTask] }) });
  });
  const reviewTask = page.locator(`[data-task-id="${task.id}"]`);
  await expect(reviewTask.getByText("Verified proposal preview", { exact: true })).toBeVisible();
  await expect(reviewTask.getByText("StudioSession.apply", { exact: false })).toBeVisible();
  await expect(reviewTask.getByText("Score diff · +0 −0 Δ1", { exact: true })).toBeVisible();
  await reviewTask.getByText(/^Bounded YAML diff/).click();
  await expect(reviewTask.getByLabel("Proposal YAML unified diff")).toContainText("parts/cello.yaml");

  let applyPayload: Record<string, unknown> | null = null;
  await page.route(`**/api/delegations/${task.id}/apply`, async (route) => {
    applyPayload = route.request().postDataJSON() as Record<string, unknown>;
    mockedTask = buildingTask(mockedTask, reviewRevision);
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(mockedTask) });
  });
  const applyRequest = page.waitForRequest((request) => request.method() === "POST" && request.url().endsWith(`/api/delegations/${task.id}/apply`));
  await reviewTask.getByRole("button", { name: "Apply reviewed proposal" }).click();
  await applyRequest;
  expect(applyPayload).toEqual({ token: "e2e-preview-token" });
  await expect(page.getByText("Proposal applied. Production receipts are now being refreshed.")).toBeVisible();
  await expect(reviewTask.getByRole("region", { name: "Production listening review" })).toContainText("Production is rebuilding");

  mockedTask = readyListeningTask(mockedTask, reviewRevision);
  const listeningTask = page.locator(`[data-task-id="${task.id}"]`);
  const listeningReview = listeningTask.getByRole("region", { name: "Production listening review" });
  await expect(listeningReview).toContainText("ready-for-listening");
  await expect(listeningReview).toContainText("A/B evidence");
  await expect(listeningReview).toContainText("Confirm the cello response remains clear.");
  await expect(listeningReview.getByRole("button", { name: "Accept production" })).toBeEnabled();
  await expect(listeningReview.getByRole("button", { name: "Request revision" })).toBeDisabled();

  let revisePayload: Record<string, unknown> | null = null;
  await page.route(`**/api/delegations/${task.id}/revise`, async (route) => {
    revisePayload = route.request().postDataJSON() as Record<string, unknown>;
    mockedTask = revisionRequestedTask(mockedTask, reviewRevision, String(revisePayload.feedback));
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(mockedTask) });
  });
  await listeningReview.getByLabel(`Listening note for ${task.id}`).fill("Keep the response clear, but let the cadence breathe longer.");
  const reviseRequest = page.waitForRequest((request) => request.method() === "POST" && request.url().endsWith(`/api/delegations/${task.id}/revise`));
  await listeningReview.getByRole("button", { name: "Request revision" }).click();
  await reviseRequest;
  expect(revisePayload).toEqual({ feedback: "Keep the response clear, but let the cadence breathe longer." });
  await expect(listeningTask.getByText("revision-requested", { exact: true }).first()).toBeVisible();

  mockedTask = readyListeningTask(mockedTask, reviewRevision);
  const acceptTask = page.locator(`[data-task-id="${task.id}"]`);
  const acceptReview = acceptTask.getByRole("region", { name: "Production listening review" });
  let acceptPayload: Record<string, unknown> | null = null;
  await page.route(`**/api/delegations/${task.id}/accept`, async (route) => {
    acceptPayload = route.request().postDataJSON() as Record<string, unknown>;
    mockedTask = acceptedTask(mockedTask, reviewRevision, String(acceptPayload.note));
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(mockedTask) });
  });
  await acceptReview.getByLabel(`Listening note for ${task.id}`).fill("Approved: balance and phrase direction are coherent.");
  const acceptRequest = page.waitForRequest((request) => request.method() === "POST" && request.url().endsWith(`/api/delegations/${task.id}/accept`));
  await acceptReview.getByRole("button", { name: "Accept production" }).click();
  await acceptRequest;
  expect(acceptPayload).toEqual({ note: "Approved: balance and phrase direction are coherent." });
  await expect(acceptTask.getByText("accepted", { exact: true }).first()).toBeVisible();
  await expect(acceptTask).toContainText("Approved: balance and phrase direction are coherent.");

  expect(consoleErrors).toEqual([]);
  expect(pageErrors).toEqual([]);
});

function reviewableTask(base: Record<string, unknown>, revision: string) {
  return {
    ...base,
    status: "proposed",
    base_revision: revision,
    approval_token: "e2e-preview-token",
    proposal: {
      summary: "Clarify the cello response without changing the opening.",
      reasoning: "A small velocity shape preserves the notes and improves the handoff.",
      actions: [{ type: "update_note", event_id: "evt-e2e", changes: { velocity: 84 } }],
      listening_check: ["Confirm the cello response remains clear."],
    },
    proposal_preview: {
      schema_version: "1",
      status: "ready",
      base_revision: revision,
      result_revision: "b".repeat(64),
      command_count: 1,
      command_types: ["update_note"],
      validation: { status: "ok", contract: "StudioSession.apply", compiled: true },
      impact: {
        changed: true,
        files: [{ path: "parts/cello.yaml", before_sha256: "1".repeat(64), after_sha256: "2".repeat(64) }],
        parts: ["cello"],
        measures: [{ part: "cello", measure: 4 }],
        aspects: ["dynamics"],
        targets: ["part:cello:measure:4"],
        fields: ["velocity"],
        counts: { files: 1, parts: 1, measures: 1, aspects: 1, targets: 1, fields: 1 },
      },
      yaml_diff: {
        format: "unified-yaml",
        text: "--- a/parts/cello.yaml\n+++ b/parts/cello.yaml\n@@ -1 +1 @@\n-  vel: 76\n+  vel: 84",
        files: ["parts/cello.yaml"], included_files: ["parts/cello.yaml"], omitted_files: [], truncated: false,
        line_count: 5, byte_count: 91, limits: { max_files: 12, max_lines: 400, max_bytes: 65_536, context_lines: 3 },
      },
      score_diff: {
        identity: { scheme: "authored-event-id+pitch-index", complete: true, fallback_count: 0 },
        added: [], removed: [],
        changed: [{ event_id: "evt-e2e", pitch_index: 0, part: "cello", measure: 4, changed_fields: ["velocity"], before: { part: "cello", measure: 4, pitch: 48, velocity: 76 }, after: { part: "cello", measure: 4, pitch: 48, velocity: 84 } }],
        counts: { added: 0, removed: 0, changed: 1, total: 1 },
      },
    },
  };
}

function buildingTask(base: Record<string, unknown>, revision: string) {
  return {
    ...base,
    status: "building",
    approval_token: null,
    result: {
      status: "building",
      source_revision: revision,
      production: {
        status: "building", job_id: "e2e-build", revisions: { authored_revision: revision },
        build: { source_revision: revision, stages: { compile: { status: "ready" }, render: { status: "running" }, mix: { status: "blocked" } } },
        ab: { available: false, unavailable_reason: "production-not-ready" },
        listening_checks: ["Confirm the cello response remains clear."], listening: { status: "waiting-for-build" }, error: null,
      },
    },
  };
}

function readyListeningTask(base: Record<string, unknown>, revision: string) {
  return {
    ...base,
    status: "ready-for-listening",
    result: {
      status: "ready-for-listening",
      source_revision: revision,
      production: {
        status: "ready-for-listening",
        revisions: { authored_revision: revision, compiled_revision: "c".repeat(64), rendered_revision: "d".repeat(64), mix_revision: "e".repeat(64) },
        build: { source_revision: revision, compiled_revision: "c".repeat(64), rendered_revision: "d".repeat(64), mix_revision: "e".repeat(64), stages: { compile: { status: "ready" }, render: { status: "ready" }, mix: { status: "ready" } } },
        ab: { available: true, source_revision: revision, level_matching: "integrated-lufs", current: { sha256: "e".repeat(64), integrated_lufs: -16.1 }, previous: { sha256: "f".repeat(64), integrated_lufs: -16.0 } },
        listening_checks: ["Confirm the cello response remains clear."], listening: { status: "pending", checks: ["Confirm the cello response remains clear."] }, error: null,
      },
    },
  };
}

function revisionRequestedTask(base: Record<string, unknown>, revision: string, feedback: string) {
  const ready = readyListeningTask(base, revision);
  return { ...ready, status: "pending", proposal: null, proposal_preview: null, result: { ...ready.result, status: "revision-requested", production: { ...ready.result.production, status: "revision-requested", listening: { status: "revision-requested", feedback, revision } } } };
}

function acceptedTask(base: Record<string, unknown>, revision: string, note: string) {
  const ready = readyListeningTask(base, revision);
  const acceptedAt = "2026-07-15T01:00:00Z";
  return { ...ready, status: "accepted", accepted_revision: revision, accepted_at: acceptedAt, acceptance: { note, accepted_at: acceptedAt, revision }, result: { ...ready.result, status: "accepted", production: { ...ready.result.production, status: "accepted", listening: { status: "accepted", note, accepted_at: acceptedAt, revision } } } };
}

async function getModel(page: Page): Promise<StudioModel> {
  return page.evaluate(async () => {
    const response = await fetch("/api/model", { cache: "no-store" });
    if (!response.ok) throw new Error(`model request failed: ${response.status}`);
    return response.json();
  });
}

async function velocityFor(page: Page, id: string): Promise<number | undefined> {
  return (await getModel(page)).notes.find((note) => note.id === id)?.velocity;
}
