import { describe, expect, it } from "vitest";
import { instrumentProfileCommand, midiPresetLabel, stageSummary } from "./EngineStatusPanel";
import type { ProfileCatalogEntry, ReadyProfileCatalogEntry, StudioPart } from "./types";

describe("engine status projection", () => {
  it("summarizes real stage states without inventing readiness", () => {
    expect(stageSummary({
      compile: { status: "ready" },
      render: { status: "stale" },
      mix: { status: "blocked" },
    })).toBe("1 ready, 1 stale, 1 blocked");
  });
});

describe("instrument profile selection", () => {
  const part = { id: "cello", profile: "starter.cello" } as StudioPart;
  const ready = {
    id: "starter.acoustic-grand-piano",
    name: "Acoustic Grand Piano",
    family: "keys",
    source: "built-in",
    status: "ready",
    midi: { bank_msb: 0, bank_lsb: 0, program: 0 },
    midi_preset: { bank_msb: 0, bank_lsb: 0, program: 0 },
    range: { absolute_low: "A0", absolute_high: "C8", comfortable_low: "A1", comfortable_high: "C7", transposition: 0 },
    articulations: [], keyswitches: [], keyswitch_map: {}, performance_parameters: [], performance: {},
  } satisfies ReadyProfileCatalogEntry;
  const broken = { id: "broken.profile", source: "project", status: "error", reason: "invalid range" } satisfies ProfileCatalogEntry;

  it("projects only a ready different profile into the explicit backend command", () => {
    expect(instrumentProfileCommand(part, ready.id, [ready, broken], false)).toEqual({
      type: "update_instrument", part: "cello", changes: { profile: ready.id },
    });
    expect(instrumentProfileCommand(part, part.profile, [ready, broken], false)).toBeNull();
    expect(instrumentProfileCommand(part, broken.id, [ready, broken], false)).toBeNull();
    expect(instrumentProfileCommand(part, ready.id, [ready, broken], true)).toBeNull();
  });

  it("summarizes the authored bank and program without inventing a preset path", () => {
    expect(midiPresetLabel(ready)).toBe("bank 0:0 · program 0");
  });
});
