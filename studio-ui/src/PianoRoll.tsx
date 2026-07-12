import { useEffect, useRef } from "react";
import type { Measure, StudioNote, StudioPart } from "./types";

type Props = {
  notes: StudioNote[];
  parts: StudioPart[];
  measures: Measure[];
  duration: number;
  width: number;
  selectedId: string | null;
  visibleParts: Set<string>;
  onSelect: (note: StudioNote | null) => void;
  onEdit: (commands: Record<string, unknown>[]) => void;
};

const LOW = 24;
const HIGH = 96;
const HEIGHT = 430;

export function PianoRoll(props: Props) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const drag = useRef<{ note: StudioNote; x: number; y: number } | null>(null);

  useEffect(() => {
    const element = canvas.current;
    if (!element) return;
    const ratio = window.devicePixelRatio || 1;
    element.width = props.width * ratio;
    element.height = HEIGHT * ratio;
    element.style.width = `${props.width}px`;
    element.style.height = `${HEIGHT}px`;
    const context = element.getContext("2d")!;
    context.scale(ratio, ratio);
    drawRoll(context, props);
  }, [props]);

  const point = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  };

  const hit = (x: number, y: number) => {
    const candidates = props.notes.filter((note) => props.visibleParts.has(note.part));
    return [...candidates].reverse().find((note) => {
      const box = noteBox(note, props.duration, props.width);
      return x >= box.x && x <= box.x + box.width && y >= box.y && y <= box.y + box.height;
    });
  };

  return (
    <canvas
      ref={canvas}
      className="piano-canvas"
      aria-label="Editable piano roll"
      onPointerDown={(event) => {
        const { x, y } = point(event);
        const note = hit(x, y);
        props.onSelect(note ?? null);
        if (note) {
          drag.current = { note, x, y };
          event.currentTarget.setPointerCapture(event.pointerId);
        }
      }}
      onPointerUp={(event) => {
        const active = drag.current;
        drag.current = null;
        if (!active) return;
        const { x, y } = point(event);
        const pitchDelta = Math.round((active.y - y) / noteHeight());
        const newPitch = Math.max(0, Math.min(127, active.note.pitch + pitchDelta));
        const targetTime = Math.max(0, Math.min(props.duration, (x / props.width) * props.duration));
        const measure = props.measures.find(
          (item) => targetTime >= item.start_seconds && targetTime < item.end_seconds,
        );
        const commands: Record<string, unknown>[] = [];
        if (newPitch !== active.note.pitch) {
          commands.push({
            type: "update_note",
            part: active.note.part,
            measure: active.note.measure,
            voice: active.note.voice,
            event_index: active.note.event_index,
            pitch_index: active.note.pitch_index,
            changes: { pitch: midiName(newPitch) },
          });
        }
        if (measure?.number === active.note.measure) {
          const measureLength = measure.beats / measure.beat_type;
          const ratio = (targetTime - measure.start_seconds) / (measure.end_seconds - measure.start_seconds);
          const snapped = Math.round(ratio * measureLength * 16) / 16;
          const original = (active.note.start_seconds - measure.start_seconds) /
            (measure.end_seconds - measure.start_seconds) * measureLength;
          if (Math.abs(snapped - original) > 0.001) {
            commands.push({
              type: "move_event",
              part: active.note.part,
              measure: active.note.measure,
              voice: active.note.voice,
              event_index: active.note.event_index,
              target_offset_whole: snapped.toString(),
            });
          }
        }
        if (commands.length) props.onEdit(commands);
      }}
    />
  );
}

function drawRoll(context: CanvasRenderingContext2D, props: Props) {
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
    context.strokeStyle = "#4b575b";
    context.lineWidth = 1;
    context.beginPath(); context.moveTo(x, 0); context.lineTo(x, HEIGHT); context.stroke();
    for (let beat = 1; beat < measure.beats; beat++) {
      const beatX = x + (measure.end_seconds - measure.start_seconds) / props.duration * props.width * beat / measure.beats;
      context.strokeStyle = "#273034";
      context.beginPath(); context.moveTo(beatX, 0); context.lineTo(beatX, HEIGHT); context.stroke();
    }
  }
  const colors = new Map(props.parts.map((part) => [part.id, part.color]));
  for (const note of props.notes) {
    if (!props.visibleParts.has(note.part) || note.pitch < LOW || note.pitch > HIGH) continue;
    const box = noteBox(note, props.duration, props.width);
    context.fillStyle = note.id === props.selectedId ? "#f4f7f6" : colors.get(note.part) ?? "#4fc4b2";
    context.globalAlpha = note.id === props.selectedId ? 1 : 0.86;
    context.fillRect(box.x + 1, box.y + 1, Math.max(3, box.width - 2), box.height - 2);
    if (note.expression) {
      context.fillStyle = "#172021";
      context.fillRect(box.x + 3, box.y + box.height - 4, Math.max(1, box.width - 6), 2);
    }
  }
  context.globalAlpha = 1;
}

function noteBox(note: StudioNote, duration: number, width: number) {
  return {
    x: note.start_seconds / duration * width,
    y: pitchY(note.pitch),
    width: Math.max(4, (note.end_seconds - note.start_seconds) / duration * width),
    height: noteHeight(),
  };
}

function noteHeight() { return HEIGHT / (HIGH - LOW + 1); }
function pitchY(pitch: number) { return (HIGH - pitch) * noteHeight(); }
function midiName(midi: number) {
  const names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
  return `${names[midi % 12]}${Math.floor(midi / 12) - 1}`;
}
