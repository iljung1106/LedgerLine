import { useEffect, useMemo, useRef } from "react";
import { clamp, noteTarget, snapSeconds, uniqueEvents, updateNote, type SnapDivision } from "./timeline";
import type { EditCommand, LaneKind, Measure, StudioControl, StudioNote, StudioPart, StudioTempoPoint } from "./types";

type Props = {
  lane: LaneKind;
  notes: StudioNote[];
  controls: StudioControl[];
  tempoPoints: StudioTempoPoint[];
  parts: StudioPart[];
  measures: Measure[];
  duration: number;
  width: number;
  selectedIds: Set<string>;
  visibleParts: Set<string>;
  activePart: string | null;
  snap: SnapDivision;
  onSelectionChange: (ids: Set<string>, anchorId: string | null) => void;
  onEdit: (commands: EditCommand[]) => void;
  onUnavailable: (message: string) => void;
};

type EditablePoint = {
  id: string;
  seconds: number;
  value: number;
  note?: StudioNote;
  control?: StudioControl;
  index?: number;
};

const HEIGHT = 128;
const FALLBACK_ARTICULATIONS = ["staccato", "tenuto", "accent", "marcato"];

export function PerformanceLane(props: Props) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const drag = useRef<EditablePoint | null>(null);
  const points = useMemo(
    () => lanePoints(props),
    [props.activePart, props.controls, props.lane, props.notes, props.parts, props.tempoPoints, props.visibleParts],
  );
  const availability = laneWriteAvailability(props.parts, props.activePart, props.lane);

  useEffect(() => {
    const element = canvas.current;
    if (!element) return;
    const dpr = window.devicePixelRatio || 1;
    element.width = Math.ceil(props.width * dpr);
    element.height = Math.ceil(HEIGHT * dpr);
    element.style.width = `${props.width}px`;
    element.style.height = `${HEIGHT}px`;
    const context = element.getContext("2d");
    if (!context) return;
    context.setTransform(dpr, 0, 0, dpr, 0, 0);
    drawLane(context, props, points);
  }, [points, props.duration, props.lane, props.parts, props.selectedIds, props.width]);

  const pointer = (event: React.PointerEvent<HTMLCanvasElement> | React.MouseEvent<HTMLCanvasElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  };

  const nearest = (x: number): EditablePoint | null => {
    const threshold = Math.max(7, props.width / Math.max(80, points.length * 5));
    return points.reduce<EditablePoint | null>((best, item) => {
      const distance = Math.abs(item.seconds / props.duration * props.width - x);
      if (distance > threshold) return best;
      if (!best) return item;
      return distance < Math.abs(best.seconds / props.duration * props.width - x) ? item : best;
    }, null);
  };

  const writeValue = (point: EditablePoint, value: number, seconds = point.seconds): EditCommand[] => {
    if (props.lane === "velocity" && point.note) {
      const selected = props.selectedIds.has(point.note.id)
        ? props.notes.filter((note) => props.selectedIds.has(note.id))
        : [point.note];
      return selected.map((note) => updateNote(note, { velocity: Math.round(value) }));
    }
    if (props.lane === "pitch_cents" && point.note) {
      const selected = props.selectedIds.has(point.note.id)
        ? props.notes.filter((note) => props.selectedIds.has(note.id))
        : [point.note];
      return uniqueEvents(selected).map((note) => ({
        type: "update_event",
        ...noteTarget(note),
        changes: { pitch_cents: Math.round(clamp(value, -200, 200) * 10) / 10 },
      }));
    }
    if (props.lane === "tempo") {
      return [{ type: "update_tempo", index: point.index ?? 0, changes: { bpm: Math.round(value * 10) / 10 } }];
    }
    if (props.lane === "articulation" && point.note) {
      const vocabulary = articulationVocabulary(props);
      const current = vocabulary.indexOf(point.note.articulation ?? null);
      const articulation = vocabulary[(current + 1) % vocabulary.length];
      return [updateNote(point.note, { articulation })];
    }
    if (point.control) {
      const target = snapSeconds(props.measures, seconds, props.snap);
      const at = target ? anchorAt(target.measure, target.seconds) : undefined;
      if (props.lane.startsWith("automation:")) {
        const targetFields = automationTarget(point.control);
        const changes: Record<string, unknown> = { value: Math.round(value * 1000) / 1000 };
        if (at && !point.control.point_id) changes.at = at;
        const commands: EditCommand[] = [{ type: "update_point", lane: point.control.lane, ...targetFields, changes }];
        if (at && point.control.point_id && Math.abs(seconds - point.seconds) > 0.001) {
          commands.push({ type: "move_point", lane: point.control.lane, ...targetFields, at });
        }
        return commands;
      }
      const changes: Record<string, unknown> = props.lane === "pedal"
        ? { action: value >= 96 ? "down" : value >= 32 ? "change" : "up" }
        : props.lane === "keyswitch"
          ? { name: keyswitchVocabulary(props)[Math.round(clamp(value, 0, Math.max(0, keyswitchVocabulary(props).length - 1)))] }
          : { value: Math.round(value) };
      if (at && Math.abs(seconds - point.seconds) > 0.001) changes.at = at;
      return [{
        type: "update_control",
        part: point.control.part ?? props.activePart,
        ...controlTarget(point.control),
        changes,
      }];
    }
    return [];
  };

  const insertPoint = (x: number, y: number) => {
    if (!availability.writable) {
      props.onUnavailable(availability.reason);
      return;
    }
    const target = snapSeconds(props.measures, x / props.width * props.duration, props.snap);
    if (!target) return;
    const value = valueAtY(props.lane, y, points, keyswitchVocabulary(props).length);
    const at = anchorAt(target.measure, target.seconds);
    if (props.lane === "tempo") {
      props.onEdit([{ type: "insert_tempo", tempo: { at, bpm: Math.round(value * 10) / 10 } }]);
      return;
    }
    if (props.lane === "pitch_cents" || props.lane === "velocity") {
      props.onUnavailable("Select an existing note marker to edit this note-level value.");
      return;
    }
    if (props.lane.startsWith("automation:")) {
      const lane = props.lane.slice("automation:".length);
      const interpolation = points[0]?.control?.lane_interpolation ?? "linear";
      props.onEdit([{ type: "insert_point", lane, point: { at, value: Math.round(value * 1000) / 1000, curve: interpolation } }]);
      return;
    }
    if (!props.activePart) {
      props.onUnavailable("Choose a track before adding a performance event.");
      return;
    }
    if (props.lane === "articulation") {
      props.onUnavailable("Select a note, then click its articulation marker or use the inspector.");
      return;
    }
    const keyswitch = keyswitchVocabulary(props);
    const control = props.lane === "pedal"
      ? { at, type: "pedal", action: value >= 64 ? "down" : "up" }
      : props.lane === "keyswitch"
        ? { at, type: "keyswitch", name: keyswitch[Math.round(clamp(value, 0, Math.max(0, keyswitch.length - 1)))], velocity: 64, duration: "1/32" }
      : { at, type: "cc", controller: props.lane === "cc1" ? 1 : 11, value: Math.round(value) };
    props.onEdit([{ type: "insert_control", part: props.activePart, control }]);
  };

  return (
    <div className="performance-lane" data-lane={props.lane} data-write-enabled={availability.writable}>
      <canvas
        ref={canvas}
        className="lane-canvas"
        aria-label={`${laneLabel(props.lane)} editor`}
        aria-disabled={!availability.writable}
        role="application"
        tabIndex={0}
        onDoubleClick={(event) => {
          const { x, y } = pointer(event);
          const point = nearest(x);
          if (props.lane === "articulation" && point) props.onEdit(writeValue(point, point.value));
          else insertPoint(x, y);
        }}
        onPointerDown={(event) => {
          const { x } = pointer(event);
          const point = nearest(x);
          if (point && !availability.writable) {
            props.onUnavailable(availability.reason);
            return;
          }
          drag.current = point;
          if (point?.note) props.onSelectionChange(new Set([point.note.id]), point.note.id);
          if (point) event.currentTarget.setPointerCapture(event.pointerId);
        }}
        onPointerUp={(event) => {
          const point = drag.current;
          drag.current = null;
          if (!point) return;
          const { x, y } = pointer(event);
          const commands = writeValue(point, valueAtY(props.lane, y, points, keyswitchVocabulary(props).length), x / props.width * props.duration);
          if (commands.length) props.onEdit(commands);
        }}
        onContextMenu={(event) => {
          event.preventDefault();
          const { x } = pointer(event);
          const point = nearest(x);
          if (point?.control && props.lane.startsWith("automation:")) {
            props.onEdit([{ type: "delete_point", lane: point.control.lane, ...automationTarget(point.control) }]);
          } else if (point?.control && props.lane !== "tempo") {
            if (!availability.writable) props.onUnavailable(availability.reason);
            else props.onEdit([{ type: "delete_control", part: point.control.part ?? props.activePart, ...controlTarget(point.control) }]);
          } else if (props.lane === "tempo" && point && (point.index ?? 0) > 0) {
            props.onEdit([{ type: "delete_tempo", index: point.index }]);
          }
        }}
      />
      {!availability.writable && <span className="lane-disabled-reason" role="status">Read only: {availability.reason}</span>}
    </div>
  );
}

function lanePoints(props: Props): EditablePoint[] {
  if (props.lane === "velocity" || props.lane === "articulation" || props.lane === "pitch_cents") {
    const vocabulary = articulationVocabulary(props);
    return props.notes
      .filter((note) => props.visibleParts.has(note.part))
      .map((note) => ({
        id: note.id,
        seconds: note.start_seconds,
        value: props.lane === "velocity"
          ? note.velocity
          : props.lane === "pitch_cents" ? note.pitch_cents : Math.max(1, vocabulary.indexOf(note.articulation ?? null)) * 25,
        note,
      }));
  }
  if (props.lane === "tempo") {
    return props.tempoPoints.map((tempo) => ({ id: `tempo-${tempo.source_index}`, seconds: tempo.seconds, value: tempo.bpm, index: tempo.source_index }));
  }
  if (props.lane.startsWith("automation:")) {
    const lane = props.lane.slice("automation:".length);
    return props.controls
      .filter((control) => control.kind === "automation" && control.lane === lane && (!control.part || props.visibleParts.has(control.part)))
      .map((control) => ({
        id: control.id,
        seconds: control.start_seconds,
        value: typeof control.value === "number" ? control.value : 0,
        control,
      }));
  }
  const keyswitches = keyswitchVocabulary(props);
  return props.controls
    .filter((control) => {
      if (control.part && !props.visibleParts.has(control.part)) return false;
      if (props.lane === "cc1") return control.kind === "cc" && control.controller === 1;
      if (props.lane === "cc11") return control.kind === "cc" && control.controller === 11;
      if (props.lane === "keyswitch") return control.kind === "keyswitch";
      return props.lane === "pedal" && (control.kind === "pedal" || control.controller === 64);
    })
    .map((control) => ({
      id: control.id,
      seconds: control.start_seconds,
      value: props.lane === "keyswitch" ? Math.max(0, keyswitches.indexOf(String(control.semantic ?? control.value))) : controlValue(control),
      control,
    }));
}

function drawLane(context: CanvasRenderingContext2D, props: Props, points: EditablePoint[]) {
  context.clearRect(0, 0, props.width, HEIGHT);
  context.fillStyle = "#111719";
  context.fillRect(0, 0, props.width, HEIGHT);
  context.strokeStyle = "#283236";
  context.fillStyle = "#859497";
  context.font = "10px ui-monospace, monospace";
  for (const ratio of [0.25, 0.5, 0.75]) {
    const y = HEIGHT - 10 - ratio * (HEIGHT - 24);
    context.beginPath(); context.moveTo(0, y); context.lineTo(props.width, y); context.stroke();
  }
  const colors = new Map(props.parts.map((part) => [part.id, part.color]));
  const scale = laneScale(props.lane, points, keyswitchVocabulary(props).length);
  for (const point of points) {
    const x = point.seconds / props.duration * props.width;
    const ratio = clamp((point.value - scale.min) / Math.max(1, scale.max - scale.min), 0, 1);
    const y = HEIGHT - 10 - ratio * (HEIGHT - 24);
    context.fillStyle = point.note && props.selectedIds.has(point.note.id)
      ? "#edf8f5"
      : point.note ? colors.get(point.note.part) ?? "#3fc1ac" : "#52c8b5";
    if (props.lane === "articulation" || props.lane === "keyswitch") {
      context.fillRect(x - 3, y - 3, 7, 7);
      const marker = point.note?.articulation ?? point.control?.semantic;
      if (marker) context.fillText(String(marker).slice(0, 12), x + 6, y + 3);
    } else {
      context.fillRect(x - 1.5, y, 3, HEIGHT - 10 - y);
      context.beginPath(); context.arc(x, y, 4, 0, Math.PI * 2); context.fill();
    }
  }
  context.fillStyle = "#8d9b9e";
  context.fillText(laneLabel(props.lane), 10, 15);
  if (!points.length) context.fillText("Double click to add an event", 10, HEIGHT - 14);
}

function laneScale(lane: LaneKind, points: EditablePoint[], keyswitchCount = 0): { min: number; max: number } {
  if (lane === "tempo") {
    const values = points.map((point) => point.value);
    const low = Math.min(60, ...values);
    const high = Math.max(180, ...values);
    return { min: Math.max(1, low - 10), max: high + 10 };
  }
  if (lane === "pitch_cents") return { min: -200, max: 200 };
  if (lane === "keyswitch") return { min: 0, max: Math.max(1, keyswitchCount - 1, ...points.map((point) => point.value)) };
  if (lane.startsWith("automation:")) {
    const values = points.map((point) => point.value);
    if (!values.length) return { min: 0, max: 1 };
    const low = Math.min(...values);
    const high = Math.max(...values);
    const padding = Math.max(0.1, (high - low) * 0.15);
    return { min: low - padding, max: high + padding };
  }
  return { min: 0, max: 127 };
}

function valueAtY(lane: LaneKind, y: number, points: EditablePoint[], keyswitchCount = 0): number {
  const normalized = clamp((HEIGHT - 10 - y) / (HEIGHT - 24), 0, 1);
  if (lane === "tempo") return 30 + normalized * 210;
  const scale = laneScale(lane, points, keyswitchCount);
  if (lane === "pitch_cents" || lane === "keyswitch" || lane.startsWith("automation:")) {
    return scale.min + normalized * (scale.max - scale.min);
  }
  return 1 + normalized * 126;
}

function controlValue(control: StudioControl): number {
  if (typeof control.value === "number") return control.value;
  const action = control.semantic ?? String(control.value);
  return action === "down" ? 127 : action === "change" ? 64 : 0;
}

function anchorAt(measure: Measure, seconds: number): string {
  const ratio = clamp((seconds - measure.start_seconds) / Math.max(0.000001, measure.end_seconds - measure.start_seconds), 0, 0.999999);
  const beat = 1 + ratio * measure.beats;
  const rounded = Math.round(beat * 16) / 16;
  return `${measure.number}:${Number.isInteger(rounded) ? rounded : rounded.toFixed(4).replace(/0+$/, "")}`;
}

function laneLabel(lane: LaneKind): string {
  if (lane.startsWith("automation:")) return `Automation · ${lane.slice("automation:".length)}`;
  switch (lane) {
    case "velocity": return "Velocity";
    case "pitch_cents": return "Pitch cents";
    case "cc1": return "CC1 Modulation";
    case "cc11": return "CC11 Expression";
    case "pedal": return "CC64 Pedal";
    case "keyswitch": return "Keyswitch";
    case "tempo": return "Tempo";
    case "articulation": return "Articulation";
    default: return "Automation";
  }
}

function controlTarget(control: StudioControl): { control_id?: string; control_index?: number } {
  if (control.control_id) return { control_id: control.control_id };
  return { control_index: control.control_index };
}

function automationTarget(control: StudioControl): { point_id?: string; point_index?: number } {
  if (control.point_id) return { point_id: control.point_id };
  return { point_index: control.point_index };
}

function articulationVocabulary(props: Props): (string | null)[] {
  const part = props.parts.find((item) => item.id === props.activePart);
  const authored = part?.articulations ?? part?.engine?.articulations;
  return [null, ...new Set(authored?.length ? authored : FALLBACK_ARTICULATIONS)];
}

function keyswitchVocabulary(props: Props): string[] {
  return props.parts.find((item) => item.id === props.activePart)?.profile_capabilities?.keyswitches ?? [];
}

export function laneWriteAvailability(parts: StudioPart[], activePart: string | null, lane: LaneKind): { writable: boolean; reason: string } {
  if (lane === "tempo" || lane.startsWith("automation:")) return { writable: true, reason: "" };
  const part = parts.find((item) => item.id === activePart);
  if (!part) return { writable: false, reason: "Choose an active track before writing this lane." };
  if (lane === "keyswitch") {
    const vocabulary = part.profile_capabilities?.keyswitches ?? [];
    return vocabulary.length
      ? { writable: true, reason: "" }
      : { writable: false, reason: `Profile ${part.profile} declares no keyswitch vocabulary.` };
  }
  if (lane === "cc1" || lane === "cc11") {
    const controller = lane === "cc1" ? 1 : 11;
    const bindings = Object.values(part.profile_capabilities?.performance ?? {});
    return bindings.some((binding) => binding.type === "cc" && binding.controller === controller)
      ? { writable: true, reason: "" }
      : { writable: false, reason: `Profile ${part.profile} does not declare CC${controller}. Existing events remain visible.` };
  }
  return { writable: true, reason: "" };
}
