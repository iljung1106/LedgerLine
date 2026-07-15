import { describe, expect, it } from "vitest";
import { processorSummary, updateMixNodeCommand } from "./Mixer";

describe("structured mixer command projection", () => {
  it("targets track, bus and master nodes without reverting to MIDI program data", () => {
    expect(updateMixNodeCommand({ type: "bus", id: "strings" }, { output: "master", gain_db: -2 })).toEqual({
      type: "update_mix_node",
      node_type: "bus",
      node: "strings",
      changes: { output: "master", gain_db: -2 },
    });
  });

  it("summarizes processor state from effective backend values", () => {
    expect(processorSummary({ type: "eq", highpass_hz: 40, bands: [{ frequency_hz: 300, gain_db: -2, q: 1 }] })).toContain("1 band");
    expect(processorSummary({ type: "compressor", threshold_db: -18, ratio: 2.5, attack_ms: 20, release_ms: 200, makeup_db: 0, knee_db: 2 })).toBe("-18 dB · 2.5:1");
  });
});
