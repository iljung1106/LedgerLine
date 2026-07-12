import type { Delegation, EditCommand, StudioModel } from "./types";

let token = "";

export async function loadModel(): Promise<StudioModel> {
  const response = await fetch("/api/model", { cache: "no-store" });
  if (!response.ok) throw new Error("Studio project could not be loaded.");
  const model = (await response.json()) as StudioModel;
  token = model.csrf_token;
  return model;
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
