import { describe, expect, it } from "vitest";
import { readableError } from "./StudioWorkbench";
import { structuralEditingAvailable } from "./editingPolicy";
import type { StudioModel } from "./types";

describe("Studio error translation", () => {
  it("explains revision conflicts without hiding that no edit was applied", () => {
    expect(readableError(new Error("expected revision abc; found def"))).toContain("source is safe");
    expect(readableError(new Error("expected revision abc; found def"))).toContain("latest revision");
  });

  it("explains server capability mismatches", () => {
    expect(readableError("unsupported Studio command: insert_event")).toContain("does not support");
  });
});

describe("persistent ID editing gate", () => {
  it("keeps structural edits locked until the model explicitly confirms prepared IDs", () => {
    const model = {
      project: { prepared_ids: false },
      capabilities: { move_within_measure: true, resize_with_validation: true },
    } as unknown as StudioModel;
    expect(structuralEditingAvailable(model)).toBe(false);
    model.project.prepared_ids = true;
    expect(structuralEditingAvailable(model)).toBe(true);
    model.capabilities.resize_with_validation = false;
    expect(structuralEditingAvailable(model)).toBe(false);
  });
});
