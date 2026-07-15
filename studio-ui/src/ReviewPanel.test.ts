import { describe, expect, it } from "vitest";
import { masterVerdict, sourceImpactLabels } from "./ReviewPanel";
import type { MasterReport } from "./types";

describe("master objective projection", () => {
  it("compares revision-bound actual measurements with authored objectives", () => {
    const report: MasterReport = {
      status: "ready",
      bound_to_current_revision: true,
      source_revision: "revision",
      target_lufs: -16,
      true_peak_ceiling_dbtp: -1,
      loudness_range_target_lu: 10,
      loudness_tolerance_lu: 0.5,
      integrated_lufs: -16.2,
      true_peak_dbtp: -1.3,
      loudness_range_lu: 7.5,
    };
    expect(masterVerdict(report)).toEqual({
      loudnessDelta: -0.1999999999999993,
      loudnessWithinTolerance: true,
      peakHeadroom: 0.30000000000000004,
      peakWithinCeiling: true,
      rangeDelta: -2.5,
    });
  });

  it("does not invent verdicts before a master is measured", () => {
    expect(masterVerdict(undefined)).toEqual({
      loudnessDelta: null,
      loudnessWithinTolerance: null,
      peakHeadroom: null,
      peakWithinCeiling: null,
      rangeDelta: null,
    });
  });
});

describe("source impact projection", () => {
  it("renders backend file and part/measure objects as readable labels", () => {
    expect(sourceImpactLabels({
      changed: true,
      files: [{ path: "parts/piano.yaml", before_sha256: "a", after_sha256: "b" }],
      parts: ["piano"],
      measures: [{ part: "piano", measure: 7 }],
      aspects: ["pitch"],
      targets: ["part:piano"],
      fields: ["parts/piano.yaml.measures.7"],
    })).toEqual({ files: ["parts/piano.yaml"], measures: ["piano M7"] });
  });
});
