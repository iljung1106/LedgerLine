import { afterEach, describe, expect, it, vi } from "vitest";
import { acceptDelegation, reviseDelegation } from "./api";
import type { Delegation } from "./types";

const task = { id: "task-http" } as Delegation;

afterEach(() => vi.unstubAllGlobals());

describe("delegation listening API", () => {
  it.each([
    ["accept", () => acceptDelegation(task, "Approved balance"), { note: "Approved balance" }],
    ["revise", () => reviseDelegation(task, "Reduce the cadence weight"), { feedback: "Reduce the cadence weight" }],
  ])("posts the %s decision to its CSRF-protected task endpoint", async (action, invoke, body) => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ id: task.id, status: "ok" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    await invoke();

    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, options] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`/api/delegations/${task.id}/${action}`);
    expect(options.method).toBe("POST");
    expect(options.headers).toMatchObject({ "Content-Type": "application/json", "X-LedgerLine-Token": expect.any(String) });
    expect(JSON.parse(String(options.body))).toEqual(body);
  });
});
