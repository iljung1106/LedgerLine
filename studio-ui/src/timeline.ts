import type { EditCommand, Measure, PeakMedia, StudioNote, TempoSegment } from "./types";

export type SnapDivision = "off" | "1/4" | "1/8" | "1/16" | "1/32";

export type TimelinePoint = {
  measure: Measure;
  seconds: number;
  offsetWhole: number;
  offsetFraction: string;
};

const SNAP_WHOLE: Record<Exclude<SnapDivision, "off">, number> = {
  "1/4": 1 / 4,
  "1/8": 1 / 8,
  "1/16": 1 / 16,
  "1/32": 1 / 32,
};

const TICKS_PER_QUARTER = 480;

export function secondsAtTick(segments: TempoSegment[], tick: number): number {
  if (!segments.length) return 0;
  const bounded = Math.max(segments[0].start_tick, Math.min(tick, segments.at(-1)!.end_tick));
  const segment = segments.find((item) => bounded <= item.end_tick) ?? segments.at(-1)!;
  const quarters = (bounded - segment.start_tick) / TICKS_PER_QUARTER;
  const totalQuarters = (segment.end_tick - segment.start_tick) / TICKS_PER_QUARTER;
  const target = segment.end_bpm ?? segment.bpm;
  if (segment.curve !== "linear" || target === segment.bpm || totalQuarters === 0) {
    return segment.start_seconds + quarters * 60 / segment.bpm;
  }
  const slope = (target - segment.bpm) / totalQuarters;
  const current = segment.bpm + slope * quarters;
  return segment.start_seconds + 60 / slope * Math.log(current / segment.bpm);
}

export function tickAtSeconds(segments: TempoSegment[], seconds: number): number {
  if (!segments.length) return 0;
  const final = segments.at(-1)!;
  const bounded = Math.max(segments[0].start_seconds, Math.min(seconds, final.start_seconds + final.duration_seconds));
  const segment = segments.find((item) => bounded <= item.start_seconds + item.duration_seconds + 1e-9) ?? final;
  const elapsed = Math.max(0, bounded - segment.start_seconds);
  const totalQuarters = (segment.end_tick - segment.start_tick) / TICKS_PER_QUARTER;
  const target = segment.end_bpm ?? segment.bpm;
  let quarters: number;
  if (segment.curve !== "linear" || target === segment.bpm || totalQuarters === 0) {
    quarters = elapsed * segment.bpm / 60;
  } else {
    const slope = (target - segment.bpm) / totalQuarters;
    const current = segment.bpm * Math.exp(elapsed * slope / 60);
    quarters = (current - segment.bpm) / slope;
  }
  return Math.max(segment.start_tick, Math.min(segment.end_tick, segment.start_tick + quarters * TICKS_PER_QUARTER));
}

export function clamp(value: number, low: number, high: number): number {
  return Math.max(low, Math.min(high, value));
}

export function measureAtSeconds(measures: Measure[], seconds: number): Measure | null {
  if (!measures.length) return null;
  return measures.find((item) => seconds >= item.start_seconds && seconds < item.end_seconds)
    ?? (seconds >= measures[measures.length - 1].end_seconds ? measures[measures.length - 1] : measures[0]);
}

export function snapSeconds(measures: Measure[], seconds: number, division: SnapDivision): TimelinePoint | null {
  const measure = measureAtSeconds(measures, seconds);
  if (!measure) return null;
  const durationSeconds = Math.max(0.000001, measure.end_seconds - measure.start_seconds);
  const measureWhole = measure.beats / measure.beat_type;
  const rawWhole = clamp((seconds - measure.start_seconds) / durationSeconds, 0, 1) * measureWhole;
  const snappedWhole = division === "off"
    ? rawWhole
    : Math.round(rawWhole / SNAP_WHOLE[division]) * SNAP_WHOLE[division];
  const boundedWhole = clamp(snappedWhole, 0, Math.max(0, measureWhole - 1 / 1024));
  return {
    measure,
    seconds: measure.start_seconds + (boundedWhole / measureWhole) * durationSeconds,
    offsetWhole: boundedWhole,
    offsetFraction: fraction(boundedWhole),
  };
}

export function durationFraction(note: StudioNote, measures: Measure[], targetSeconds: number, division: SnapDivision): string {
  const measure = measures.find((item) => item.number === note.measure) ?? measureAtSeconds(measures, note.start_seconds);
  if (!measure) return note.duration;
  const secondsPerWhole = (measure.end_seconds - measure.start_seconds) / (measure.beats / measure.beat_type);
  let whole = Math.max(1 / 128, (targetSeconds - note.start_seconds) / Math.max(0.000001, secondsPerWhole));
  if (division !== "off") whole = Math.max(SNAP_WHOLE[division], Math.round(whole / SNAP_WHOLE[division]) * SNAP_WHOLE[division]);
  return fraction(whole);
}

export function noteTarget(note: StudioNote): Record<string, unknown> {
  const target: Record<string, unknown> = {
    part: note.part,
    measure: note.measure,
    voice: note.voice,
    event_index: note.event_index,
    pitch_index: note.pitch_index,
  };
  if (note.event_id) {
    target.id = note.event_id;
    target.event_id = note.event_id;
  }
  return target;
}

export function updateNote(note: StudioNote, changes: Record<string, unknown>): EditCommand {
  return { type: "update_note", ...noteTarget(note), changes };
}

export function moveNote(note: StudioNote, target: TimelinePoint): EditCommand {
  if (target.measure.number === note.measure) {
    return { type: "move_event", ...noteTarget(note), target_offset_whole: target.offsetFraction };
  }
  return {
    type: "move_event",
    ...noteTarget(note),
    target_measure: target.measure.number,
    target_offset_whole: target.offsetFraction,
  };
}

export function resizeNote(note: StudioNote, duration: string): EditCommand {
  return { type: "resize_event", ...noteTarget(note), duration };
}

export function deleteNotes(notes: StudioNote[]): EditCommand[] {
  return uniqueEvents(notes).map((note) => ({ type: "delete_event", ...noteTarget(note) }));
}

export function duplicateNotes(notes: StudioNote[], offsetWhole = "1/16"): EditCommand[] {
  return uniqueEvents(notes).map((note) => ({
    type: "duplicate_event",
    ...noteTarget(note),
    target_measure: note.measure,
    target_voice: note.voice,
    target_offset_whole: offsetWhole,
  }));
}

export function quantizeNotes(notes: StudioNote[], measures: Measure[], division: SnapDivision, strength = 1): EditCommand[] {
  if (division === "off" || !notes.length) return [];
  return uniqueEvents(notes).flatMap((note) => {
    const snapped = snapSeconds(measures, note.start_seconds, division);
    if (!snapped) return [];
    const targetSeconds = note.start_seconds + (snapped.seconds - note.start_seconds) * clamp(strength, 0, 1);
    const target = snapSeconds(measures, targetSeconds, strength >= 0.999 ? division : "off");
    return target && Math.abs(target.seconds - note.start_seconds) > 0.001 ? [moveNote(note, target)] : [];
  });
}

export function eventKey(note: StudioNote): string {
  return note.event_id ?? `${note.part}:${note.measure}:${note.voice}:${note.event_index}`;
}

export function uniqueEvents(notes: StudioNote[]): StudioNote[] {
  const seen = new Set<string>();
  return notes.filter((note) => {
    const key = eventKey(note);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export function versionedMediaUrl(media: PeakMedia): string {
  const version = media.output_sha ?? media.sha256 ?? media.version ?? media.artifact_revision ?? media.project_revision;
  if (!version) return media.url;
  if (/[?&]v=/.test(media.url)) return media.url;
  const separator = media.url.includes("?") ? "&" : "?";
  return `${media.url}${separator}v=${encodeURIComponent(version)}`;
}

export function fraction(value: number, maxDenominator = 128): string {
  if (!Number.isFinite(value)) return "0";
  if (Math.abs(value) < 1e-9) return "0";
  let bestNumerator = Math.round(value);
  let bestDenominator = 1;
  let bestError = Math.abs(value - bestNumerator);
  for (let denominator = 2; denominator <= maxDenominator; denominator++) {
    const numerator = Math.round(value * denominator);
    const error = Math.abs(value - numerator / denominator);
    if (error < bestError) {
      bestError = error;
      bestNumerator = numerator;
      bestDenominator = denominator;
    }
  }
  const divisor = gcd(Math.abs(bestNumerator), bestDenominator);
  return `${bestNumerator / divisor}/${bestDenominator / divisor}`;
}

function gcd(left: number, right: number): number {
  while (right) [left, right] = [right, left % right];
  return left || 1;
}
