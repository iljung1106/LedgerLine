import { describe, expect, it } from "vitest";
import { calculateMeterLevel, comparisonGainDb, comparisonSourcesReady, resolvePlaybackSource } from "./audioEngine";
import type { StudioModel } from "./types";

describe("WebAudio meter math", () => {
  it("reports peak and RMS from actual sample amplitudes", () => {
    const level = calculateMeterLevel(new Float32Array([0.5, -0.5, 0.5, -0.5]));
    expect(level.peak).toBeCloseTo(0.5);
    expect(level.rms).toBeCloseTo(0.5);
    expect(level.peakDb).toBeCloseTo(-6.0206, 3);
    expect(level.clipped).toBe(false);
  });

  it("marks full-scale samples as clipped", () => {
    const level = calculateMeterLevel(new Float32Array([0, 1, 0, -1]));
    expect(level.peak).toBe(1);
    expect(level.clipped).toBe(true);
    expect(level.rmsDb).toBeCloseTo(-3.0103, 3);
  });

  it("uses a stable floor for silence", () => {
    const level = calculateMeterLevel(new Float32Array(16));
    expect(level.peakDb).toBe(-96);
    expect(level.rmsDb).toBe(-96);
  });

  it("uses only a real archived master for B comparison", () => {
    expect(resolvePlaybackSource("previous", true, true, true)).toBe("previous-master");
    expect(resolvePlaybackSource("previous", false, true, true)).toBe("current-stems");
    expect(resolvePlaybackSource("current", true, true, true)).toBe("current-master");
    expect(resolvePlaybackSource("current", false, true, true)).toBe("current-stems");
    expect(comparisonSourcesReady(true, false, true)).toBe(false);
    expect(comparisonSourcesReady(true, true, false)).toBe(false);
    expect(comparisonSourcesReady(true, true, true)).toBe(true);
  });

  it("applies only the backend-declared previous-master level match", () => {
    const model = {
      review: {
        ab: {
          available: true,
          playback_policy: {
            level_matching: "integrated-lufs",
            gain_adjustment_db: { previous: 1.9 },
          },
        },
      },
      media: {},
    } as unknown as StudioModel;
    expect(comparisonGainDb(model, "previous")).toBe(1.9);
    expect(comparisonGainDb(model, "current")).toBe(0);
    model.review!.ab!.available = false;
    expect(comparisonGainDb(model, "previous")).toBe(0);
  });
});
