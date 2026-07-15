import type { Delegation, EditCommand, StudioJob, StudioModel } from "./types";

let token = "";

export async function loadModel(): Promise<StudioModel> {
  const response = await fetch("/api/model", { cache: "no-store" });
  if (!response.ok) throw new Error("Studio project could not be loaded.");
  const model = (await response.json()) as StudioModel;
  token = model.csrf_token;
  return model;
}

export async function loadStatus(): Promise<Pick<StudioModel, "build" | "jobs"> | null> {
  const response = await fetch("/api/status", { cache: "no-store" });
  if (response.status === 404) return null;
  if (!response.ok) throw new Error("Studio build status could not be loaded.");
  const status = await response.json() as { build?: StudioModel["build"]; jobs?: StudioModel["jobs"] };
  return { build: status.build, jobs: status.jobs };
}

async function post<T>(url: string, body: unknown = {}): Promise<T> {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-LedgerLine-Token": token },
    body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok || data.status === "error") throw new Error(data.message || "Studio command failed.");
  return data as T;
}

export async function applyCommands(commands: EditCommand[], revision: string) {
  return post<{ model: StudioModel }>("/api/commands", { commands, revision });
}

export async function undo() {
  return post<{ model: StudioModel }>("/api/undo");
}

export async function redo() {
  return post<{ model: StudioModel }>("/api/redo");
}

export async function cancelJob(id: string) {
  return post<{ id: string; status: string }>(`/api/jobs/${id}/cancel`);
}

export async function startJob(kind: "render" | "mix" | "refine" | "build", payload: Record<string, unknown> = {}) {
  return post<StudioJob>("/api/jobs", { kind, payload, coalesce: true });
}

export async function listDelegations(): Promise<Delegation[]> {
  const response = await fetch("/api/delegations", { cache: "no-store" });
  if (!response.ok) throw new Error("Delegations could not be loaded.");
  return ((await response.json()) as { tasks: Delegation[] }).tasks;
}

export async function createDelegation(goal: string, autonomy: string, context: string) {
  return post<Delegation>("/api/delegations", { goal, autonomy, context, constraints: [] });
}

export async function applyDelegation(task: Delegation) {
  return post<Delegation>(`/api/delegations/${task.id}/apply`, { token: task.approval_token });
}

export async function rejectDelegation(task: Delegation) {
  return post<Delegation>(`/api/delegations/${task.id}/reject`, { reason: "Rejected in Studio" });
}

export async function answerDelegation(task: Delegation, answer: string) {
  return post<Delegation>(`/api/delegations/${task.id}/answer`, { answer, answers: [answer] });
}

export async function acceptDelegation(task: Delegation, note: string) {
  return post<Delegation>(`/api/delegations/${task.id}/accept`, { note });
}

export async function reviseDelegation(task: Delegation, feedback: string) {
  return post<Delegation>(`/api/delegations/${task.id}/revise`, { feedback });
}
