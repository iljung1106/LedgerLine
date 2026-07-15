import { describe, expect, it } from "vitest";
import { deleteNotes, fraction, moveNote, noteTarget, quantizeNotes, secondsAtTick, snapSeconds, tickAtSeconds, versionedMediaUrl } from "./timeline";
import type { Measure, PeakMedia, StudioNote } from "./types";

const measures: Measure[] = [
  { number: 1, start_tick: 0, end_tick: 1920, start_seconds: 0, end_seconds: 2, beats: 4, beat_type: 4 },
  { number: 2, start_tick: 1920, end_tick: 3840, start_seconds: 2, end_seconds: 4, beats: 4, beat_type: 4 },
];

const note: StudioNote = {
  id: "piano:1:v1:0:0",
  event_id: "event-12",
  part: "piano",
  measure: 1,
  voice: "v1",
  event_index: 0,
  pitch_index: 0,
  pitch: 60,
  written_pitch: "C4",
  start_tick: 0,
  end_tick: 480,
  start_seconds: 0,
  end_seconds: 0.5,
  duration: "1/4",
  velocity: 80,
  dynamic: "mf",
  articulation: null,
  staff: 1,
  pitch_cents: 0,
  expression: null,
};

describe("timeline edit contract", () => {
  it("snaps time inside a measure to the selected whole-note grid", () => {
    const point = snapSeconds(measures, 0.74, "1/8");
    expect(point?.measure.number).toBe(1);
    expect(point?.offsetFraction).toBe("3/8");
    expect(point?.seconds).toBeCloseTo(0.75);
  });

  it("uses a legacy command for same-measure movement", () => {
    const target = snapSeconds(measures, 1, "1/8")!;
    expect(moveNote(note, target)).toMatchObject({
      type: "move_event",
      id: "event-12",
      part: "piano",
      measure: 1,
      target_offset_whole: "1/2",
    });
  });

  it("uses persistent IDs while retaining legacy coordinates", () => {
    expect(deleteNotes([note])[0]).toMatchObject({
      type: "delete_event",
      id: "event-12",
      event_index: 0,
      pitch_index: 0,
    });
  });

  it("does not send a virtual display ID to legacy projects", () => {
    const target = noteTarget({ ...note, event_id: undefined });
    expect(target).not.toHaveProperty("event_id");
    expect(target).not.toHaveProperty("id");
    expect(target).toMatchObject({ part: "piano", measure: 1, voice: "v1", event_index: 0 });
  });

  it("deduplicates chord pitches for event-level deletion", () => {
    const chordPitch = { ...note, id: "event-12:1", pitch_index: 1, pitch: 64 };
    expect(deleteNotes([note, chordPitch])).toHaveLength(1);
  });

  it("quantizes through compatible move_event commands", () => {
    const offGrid = { ...note, start_seconds: 0.63 };
    expect(quantizeNotes([offGrid], measures, "1/8")[0]).toMatchObject({
      type: "move_event",
      target_offset_whole: "3/8",
    });
  });

  it("reduces approximate fractions", () => {
    expect(fraction(0.5)).toBe("1/2");
    expect(fraction(0.1875)).toBe("3/16");
  });

  it("cache-busts rendered media using its output hash", () => {
    const media = { url: "/media/master.wav", output_sha: "abc123", peaks: [] } as unknown as PeakMedia;
    expect(versionedMediaUrl(media)).toBe("/media/master.wav?v=abc123");
  });

  it("does not duplicate an existing media version query", () => {
    const media = { url: "/media/master.wav?v=abc123", sha256: "abc123", peaks: [] } as unknown as PeakMedia;
    expect(versionedMediaUrl(media)).toBe("/media/master.wav?v=abc123");
  });

  it("round-trips ticks through the backend linear-tempo-ramp contract", () => {
    const segments = [{
      start_tick: 0,
      end_tick: 1920,
      start_seconds: 0,
      duration_seconds: 60 / 15 * Math.log(2),
      bpm: 60,
      end_bpm: 120,
      curve: "linear",
    }];
    const seconds = secondsAtTick(segments, 960);
    expect(seconds).toBeCloseTo(4 * Math.log(1.5), 8);
    expect(tickAtSeconds(segments, seconds)).toBeCloseTo(960, 7);
  });
});
