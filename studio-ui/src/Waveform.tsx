import { useEffect, useRef } from "react";
import type { PeakMedia } from "./types";

export function Waveform({ media, width, color = "#4fc4b2" }: { media: PeakMedia | null; width: number; color?: string }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const element = canvas.current;
    if (!element) return;
    const ratio = window.devicePixelRatio || 1;
    const height = 92;
    element.width = width * ratio;
    element.height = height * ratio;
    element.style.width = `${width}px`;
    element.style.height = `${height}px`;
    const context = element.getContext("2d")!;
    context.scale(ratio, ratio);
    context.fillStyle = "#121618";
    context.fillRect(0, 0, width, height);
    context.strokeStyle = "#252d30";
    context.beginPath(); context.moveTo(0, height / 2); context.lineTo(width, height / 2); context.stroke();
    if (!media) {
      context.fillStyle = "#708084";
      context.font = "12px system-ui";
      context.fillText("Render stems to inspect the waveform", 16, 28);
      return;
    }
    context.fillStyle = color;
    context.globalAlpha = 0.72;
    const step = width / media.peaks.length;
    media.peaks.forEach(([low, high], index) => {
      const top = height / 2 - high * height * 0.45;
      const bottom = height / 2 - low * height * 0.45;
      context.fillRect(index * step, top, Math.max(1, step + 0.3), Math.max(1, bottom - top));
    });
    context.globalAlpha = 1;
  }, [media, width, color]);
  return <canvas ref={canvas} className="waveform-canvas" aria-label="Aligned audio waveform" />;
}
