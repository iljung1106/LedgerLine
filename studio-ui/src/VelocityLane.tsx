import { useEffect, useRef } from "react";
import type { StudioNote, StudioPart } from "./types";

export function VelocityLane({ notes, parts, duration, width, visibleParts }: {
  notes: StudioNote[]; parts: StudioPart[]; duration: number; width: number; visibleParts: Set<string>;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const element = canvas.current;
    if (!element) return;
    const dpr = window.devicePixelRatio || 1;
    element.width = width * dpr; element.height = 92 * dpr;
    element.style.width = `${width}px`; element.style.height = "92px";
    const context = element.getContext("2d")!; context.scale(dpr, dpr);
    context.fillStyle = "#111719"; context.fillRect(0, 0, width, 92);
    context.strokeStyle = "#273135";
    for (const level of [32, 64, 96]) { const y = 91 - level / 127 * 86; context.beginPath(); context.moveTo(0, y); context.lineTo(width, y); context.stroke(); }
    const colors = new Map(parts.map((part) => [part.id, part.color]));
    for (const note of notes) {
      if (!visibleParts.has(note.part)) continue;
      const x = note.start_seconds / duration * width;
      const height = note.velocity / 127 * 86;
      context.fillStyle = colors.get(note.part) ?? "#39b8a3";
      context.fillRect(x, 91 - height, 2.5, height);
    }
  }, [notes, parts, duration, width, visibleParts]);
  return <canvas ref={canvas} className="lane-canvas" aria-label="Note velocity lane" />;
}
