import { useEffect, useRef } from "react";
import { PREPARE_IDS_MESSAGE } from "./editingPolicy";
import { durationFraction, moveNote, noteTarget, resizeNote, snapSeconds, uniqueEvents, updateNote, type SnapDivision } from "./timeline";
import type { EditCommand, LoopRange, Measure, StudioNote, StudioPart } from "./types";

type Props = {
  notes: StudioNote[];
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
  structuralEditing: boolean;
  loopRange: LoopRange | null;
  onLoopRangeChange: (range: LoopRange | null) => void;
};

type Box = { x: number; y: number; width: number; height: number };

type Gesture =
  | { kind: "move"; anchor: StudioNote; notes: StudioNote[]; x: number; y: number }
  | { kind: "resize"; anchor: StudioNote; notes: StudioNote[]; x: number; y: number }
  | { kind: "marquee"; x: number; y: number; x2: number; y2: number; additive: boolean }
  | { kind: "loop"; x: number; x2: number };

const LOW = 24;
const HIGH = 96;
const HEIGHT = 480;
const RESIZE_HANDLE = 7;

export function PianoRollEditor(props: Props) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const gesture = useRef<Gesture | null>(null);

  const redraw = () => {
    const element = canvas.current;
    if (!element) return;
    const context = element.getContext("2d");
    if (!context) return;
    const ratio = window.devicePixelRatio || 1;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    drawRoll(context, props, gesture.current);
  };

  useEffect(() => {
    const element = canvas.current;
    if (!element) return;
    const ratio = window.devicePixelRatio || 1;
    element.width = Math.ceil(props.width * ratio);
    element.height = Math.ceil(HEIGHT * ratio);
    element.style.width = `${props.width}px`;
    element.style.height = `${HEIGHT}px`;
    redraw();
  }, [props.duration, props.loopRange, props.measures, props.notes, props.parts, props.selectedIds, props.visibleParts, props.width]);

  const point = (event: React.PointerEvent<HTMLCanvasElement> | React.MouseEvent<HTMLCanvasElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  };

  const hit = (x: number, y: number) => {
    const candidates = props.notes.filter((note) => props.visibleParts.has(note.part));
    const note = [...candidates].reverse().find((candidate) => intersectsPoint(noteBox(candidate, props.duration, props.width), x, y));
    if (!note) return null;
    const box = noteBox(note, props.duration, props.width);
    return { note, resize: x >= box.x + box.width - RESIZE_HANDLE };
  };

  const selectForPointer = (note: StudioNote, additive: boolean): Set<string> => {
    const next = new Set(props.selectedIds);
    if (additive) {
      if (next.has(note.id)) next.delete(note.id);
      else next.add(note.id);
    } else if (!next.has(note.id)) {
      next.clear();
      next.add(note.id);
    }
    props.onSelectionChange(next, next.has(note.id) ? note.id : null);
    return next;
  };

  return (
    <canvas
      ref={canvas}
      className="piano-canvas"
      aria-label={props.structuralEditing ? "Editable piano roll. Double click to add a note. Drag notes to move, drag the right edge to resize, or Alt-drag to set the playback loop." : "Piano roll. Alt-drag to set the playback loop. Structural editing requires persistent event IDs."}
      aria-disabled={!props.structuralEditing}
      data-loop-start={props.loopRange?.start_seconds}
      data-loop-end={props.loopRange?.end_seconds}
      role="application"
      tabIndex={0}
      onDoubleClick={(event) => {
        if (!props.structuralEditing) {
          props.onUnavailable(PREPARE_IDS_MESSAGE);
          return;
        }
        const { x, y } = point(event);
        if (hit(x, y)) return;
        if (!props.activePart) {
          props.onUnavailable("Choose a track before adding a note.");
          return;
        }
        const target = snapSeconds(props.measures, x / props.width * props.duration, props.snap);
        if (!target) return;
        const part = props.parts.find((item) => item.id === props.activePart);
        if (part?.editable === false) {
          props.onUnavailable("This track does not have persistent event IDs yet.");
          return;
        }
        const pitch = Math.max(0, Math.min(127, HIGH - Math.floor(y / noteHeight())));
        const noteEvent = {
          p: midiName(pitch),
          d: props.snap === "off" ? "1/4" : props.snap,
          vel: 84,
          staff: 1,
        };
        props.onEdit([{
          type: "insert_event",
          part: props.activePart,
          measure: target.measure.number,
          voice: part?.voices?.[0] ?? "v1",
          target_offset_whole: target.offsetFraction,
          event: noteEvent,
        }]);
      }}
      onPointerDown={(event) => {
        const { x, y } = point(event);
        if (event.altKey) {
          gesture.current = { kind: "loop", x, x2: x };
          event.currentTarget.setPointerCapture(event.pointerId);
          redraw();
          return;
        }
        const found = hit(x, y);
        if (!found) {
          if (!event.shiftKey) props.onSelectionChange(new Set(), null);
          gesture.current = { kind: "marquee", x, y, x2: x, y2: y, additive: event.shiftKey };
        } else {
          const selected = selectForPointer(found.note, event.shiftKey);
          if (!props.structuralEditing) {
            gesture.current = null;
            redraw();
            return;
          }
          const selectedNotes = props.notes.filter((note) => selected.has(note.id));
          gesture.current = {
            kind: found.resize ? "resize" : "move",
            anchor: found.note,
            notes: selectedNotes.length ? selectedNotes : [found.note],
            x,
            y,
          };
        }
        event.currentTarget.setPointerCapture(event.pointerId);
        redraw();
      }}
      onPointerMove={(event) => {
        const active = gesture.current;
        if (!active) return;
        const { x, y } = point(event);
        if (active.kind === "marquee") {
          active.x2 = x;
          active.y2 = y;
          redraw();
        } else if (active.kind === "loop") {
          active.x2 = x;
          redraw();
        }
        event.currentTarget.style.cursor = !props.structuralEditing ? "default" : hit(x, y)?.resize ? "ew-resize" : hit(x, y) ? "grab" : "crosshair";
      }}
      onPointerLeave={(event) => { if (!gesture.current) event.currentTarget.style.cursor = "crosshair"; }}
      onPointerUp={(event) => {
        const active = gesture.current;
        gesture.current = null;
        if (!active) return;
        const { x, y } = point(event);
        if (active.kind === "loop") {
          const low = Math.max(0, Math.min(props.width, Math.min(active.x, x)));
          const high = Math.max(0, Math.min(props.width, Math.max(active.x, x)));
          const startRaw = low / props.width * props.duration;
          const endRaw = high / props.width * props.duration;
          const start = snapSeconds(props.measures, startRaw, props.snap)?.seconds ?? startRaw;
          const end = endRaw >= props.duration - 0.001
            ? props.duration
            : snapSeconds(props.measures, endRaw, props.snap)?.seconds ?? endRaw;
          if (end - start > 0.01) props.onLoopRangeChange({ start_seconds: start, end_seconds: end });
          else props.onUnavailable("Drag a wider range to create a playback loop.");
          redraw();
          return;
        }
        if (active.kind === "marquee") {
          const selectionBox = normalizedBox(active.x, active.y, x, y);
          const hits = props.notes
            .filter((note) => props.visibleParts.has(note.part) && intersectsBox(noteBox(note, props.duration, props.width), selectionBox))
            .map((note) => note.id);
          const next = active.additive ? new Set(props.selectedIds) : new Set<string>();
          hits.forEach((id) => next.add(id));
          props.onSelectionChange(next, hits.at(-1) ?? null);
          redraw();
          return;
        }
        const commands: EditCommand[] = [];
        if (active.kind === "resize") {
          const deltaSeconds = (x - active.x) / props.width * props.duration;
          for (const note of uniqueEvents(active.notes)) {
            const duration = durationFraction(note, props.measures, note.end_seconds + deltaSeconds, props.snap);
            if (duration !== note.duration) commands.push(resizeNote(note, duration));
          }
        } else {
          const deltaSeconds = (x - active.x) / props.width * props.duration;
          const pitchDelta = Math.round((active.y - y) / noteHeight());
          for (const note of active.notes) {
            if (pitchDelta) {
              const nextPitch = Math.max(0, Math.min(127, note.pitch + pitchDelta));
              commands.push(updateNote(note, { pitch: midiName(nextPitch) }));
            }
          }
          for (const note of uniqueEvents(active.notes)) {
            const target = snapSeconds(props.measures, note.start_seconds + deltaSeconds, props.snap);
            if (!target) continue;
            if (Math.abs(target.seconds - note.start_seconds) > 0.001) commands.push(moveNote(note, target));
          }
        }
        if (commands.length) props.onEdit(commands);
        redraw();
      }}
      onContextMenu={(event) => {
        event.preventDefault();
        const { x, y } = point(event);
        const found = hit(x, y);
        if (found && !props.structuralEditing) props.onUnavailable(PREPARE_IDS_MESSAGE);
        else if (found) props.onEdit([{ type: "delete_event", ...noteTarget(found.note) }]);
      }}
    />
  );
}

function drawRoll(context: CanvasRenderingContext2D, props: Props, active: Gesture | null) {
  context.clearRect(0, 0, props.width, HEIGHT);
  context.fillStyle = "#15191b";
  context.fillRect(0, 0, props.width, HEIGHT);
  for (let pitch = LOW; pitch <= HIGH; pitch++) {
    const y = pitchY(pitch);
    const black = [1, 3, 6, 8, 10].includes(pitch % 12);
    context.fillStyle = black ? "#111416" : "#171c1e";
    context.fillRect(0, y, props.width, noteHeight());
    context.strokeStyle = pitch % 12 === 0 ? "#394246" : "#20272a";
    context.beginPath(); context.moveTo(0, y); context.lineTo(props.width, y); context.stroke();
  }
  for (const measure of props.measures) {
    const x = measure.start_seconds / props.duration * props.width;
    context.strokeStyle = "#526064";
    context.lineWidth = 1;
    context.beginPath(); context.moveTo(x, 0); context.lineTo(x, HEIGHT); context.stroke();
    for (let beat = 1; beat < measure.beats; beat++) {
      const beatX = x + (measure.end_seconds - measure.start_seconds) / props.duration * props.width * beat / measure.beats;
      context.strokeStyle = "#293236";
      context.beginPath(); context.moveTo(beatX, 0); context.lineTo(beatX, HEIGHT); context.stroke();
    }
  }
  const loop = active?.kind === "loop"
    ? { start_seconds: Math.min(active.x, active.x2) / props.width * props.duration, end_seconds: Math.max(active.x, active.x2) / props.width * props.duration }
    : props.loopRange;
  if (loop) {
    const start = Math.max(0, loop.start_seconds / props.duration * props.width);
    const end = Math.min(props.width, loop.end_seconds / props.duration * props.width);
    context.fillStyle = "rgba(63, 193, 172, 0.10)";
    context.fillRect(start, 0, Math.max(1, end - start), HEIGHT);
    context.strokeStyle = "#64d0bd";
    context.lineWidth = 1;
    context.beginPath(); context.moveTo(start + 0.5, 0); context.lineTo(start + 0.5, HEIGHT); context.stroke();
    context.beginPath(); context.moveTo(end - 0.5, 0); context.lineTo(end - 0.5, HEIGHT); context.stroke();
  }
  const colors = new Map(props.parts.map((part) => [part.id, part.color]));
  for (const note of props.notes) {
    if (!props.visibleParts.has(note.part) || note.pitch < LOW || note.pitch > HIGH) continue;
    const box = noteBox(note, props.duration, props.width);
    const selected = props.selectedIds.has(note.id);
    context.globalAlpha = selected ? 1 : 0.84;
    context.fillStyle = note.out_of_range || note.capability_error
      ? "#d67878"
      : selected ? "#edf8f5" : colors.get(note.part) ?? "#4fc4b2";
    context.fillRect(box.x + 1, box.y + 1, Math.max(3, box.width - 2), box.height - 2);
    context.fillStyle = selected ? "#3fc1ac" : "rgba(20, 26, 28, 0.7)";
    context.fillRect(box.x + box.width - RESIZE_HANDLE, box.y + 1, Math.min(RESIZE_HANDLE - 1, box.width), box.height - 2);
    if (note.expression) {
      context.fillStyle = "#172021";
      context.fillRect(box.x + 3, box.y + box.height - 4, Math.max(1, box.width - 6), 2);
    }
  }
  context.globalAlpha = 1;
  if (active?.kind === "marquee") {
    const box = normalizedBox(active.x, active.y, active.x2, active.y2);
    context.fillStyle = "rgba(63, 193, 172, 0.12)";
    context.strokeStyle = "#64d0bd";
    context.fillRect(box.x, box.y, box.width, box.height);
    context.strokeRect(box.x + 0.5, box.y + 0.5, box.width, box.height);
  }
}

function noteBox(note: StudioNote, duration: number, width: number): Box {
  return {
    x: note.start_seconds / duration * width,
    y: pitchY(note.pitch),
    width: Math.max(8, (note.end_seconds - note.start_seconds) / duration * width),
    height: noteHeight(),
  };
}

function normalizedBox(x: number, y: number, x2: number, y2: number): Box {
  return { x: Math.min(x, x2), y: Math.min(y, y2), width: Math.abs(x2 - x), height: Math.abs(y2 - y) };
}

function intersectsPoint(box: Box, x: number, y: number): boolean {
  return x >= box.x && x <= box.x + box.width && y >= box.y && y <= box.y + box.height;
}

function intersectsBox(left: Box, right: Box): boolean {
  return left.x <= right.x + right.width && left.x + left.width >= right.x && left.y <= right.y + right.height && left.y + left.height >= right.y;
}

function noteHeight() { return HEIGHT / (HIGH - LOW + 1); }
function pitchY(pitch: number) { return (HIGH - pitch) * noteHeight(); }
function midiName(midi: number) {
  const names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
  return `${names[midi % 12]}${Math.floor(midi / 12) - 1}`;
}
