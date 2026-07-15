import { describe, expect, it } from "vitest";
import { laneWriteAvailability } from "./PerformanceLane";
import type { StudioPart } from "./types";

const part: StudioPart = {
  id: "cello",
  name: "Cello",
  profile: "starter.cello",
  family: "strings",
  staff_count: 1,
  note_count: 4,
  color: "#4fc4b2",
  profile_capabilities: {
    range: {
      absolute_low: "C2",
      absolute_high: "C6",
      comfortable_low: "G2",
      comfortable_high: "G5",
      transposition: 0,
    },
    articulations: ["legato"],
    keyswitches: [],
    performance_parameters: ["expression"],
    performance: {
      expression: { type: "cc", controller: 11, parameter: null, minimum: 0, maximum: 127, default: 0.65 },
    },
  },
};

describe("profile-gated performance lane writes", () => {
  it("allows only controllers declared by the active profile", () => {
    expect(laneWriteAvailability([part], "cello", "cc11").writable).toBe(true);
    expect(laneWriteAvailability([part], "cello", "cc1")).toEqual({
      writable: false,
      reason: "Profile starter.cello does not declare CC1. Existing events remain visible.",
    });
  });

  it("keeps keyswitch writing disabled without an authored vocabulary", () => {
    expect(laneWriteAvailability([part], "cello", "keyswitch").writable).toBe(false);
    expect(laneWriteAvailability([{ ...part, profile_capabilities: { ...part.profile_capabilities!, keyswitches: ["pizzicato"] } }], "cello", "keyswitch").writable).toBe(true);
  });

  it("allows project automation without requiring an active instrument", () => {
    expect(laneWriteAvailability([], null, "automation:master-fade").writable).toBe(true);
  });
});
