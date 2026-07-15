import { Spinner, Text } from "@fluentui/react-components";
import { MusicNoteSimple, WarningCircle } from "@phosphor-icons/react";
import { useEffect, useMemo, useRef, useState } from "react";
import { OpenSheetMusicDisplay } from "opensheetmusicdisplay";
import { secondsAtTick, tickAtSeconds } from "./timeline";
import type { StudioNote, TempoSegment } from "./types";

type Props = {
  scoreUrl: string;
  notes: StudioNote[];
  tempoSegments: TempoSegment[];
  selectedIds: Set<string>;
  currentTime: () => number;
  onSeek: (seconds: number) => void;
  onSelect: (ids: Set<string>, anchorId: string | null) => void;
};

export type ScoreCursorPosition = {
  index: number;
  tick: number;
  seconds: number;
  x: number;
  y: number;
};

const WHOLE_TICKS = 1920;

export function ScoreEditorView(props: Props) {
  const host = useRef<HTMLDivElement>(null);
  const osmd = useRef<OpenSheetMusicDisplay | null>(null);
  const positions = useRef<ScoreCursorPosition[]>([]);
  const cursorIndex = useRef(0);
  const [status, setStatus] = useState<"loading" | "ready" | "empty" | "error">("loading");
  const [message, setMessage] = useState("");
  const selected = useMemo(() => props.notes.filter((note) => props.selectedIds.has(note.id)), [props.notes, props.selectedIds]);

  useEffect(() => {
    if (!host.current) return;
    let active = true;
    let resizeObserver: ResizeObserver | null = null;
    let resizeTimer = 0;
    setStatus("loading");
    const display = new OpenSheetMusicDisplay(host.current, {
      backend: "svg",
      autoResize: true,
      drawTitle: false,
      followCursor: true,
      cursorsOptions: [{ type: 1, color: "#2aa894", alpha: 0.82, follow: true }],
    });
    osmd.current = display;
    const capture = () => {
      if (!active || !host.current) return;
      positions.current = captureCursorPositions(display, host.current, props.tempoSegments);
      cursorIndex.current = 0;
      host.current.dataset.cursorCount = String(positions.current.length);
      host.current.dataset.cursorTick = String(positions.current[0]?.tick ?? 0);
    };
    fetch(props.scoreUrl || "/api/score", { cache: "no-store" })
      .then((response) => {
        if (!response.ok) throw new Error(`Score request failed (${response.status}).`);
        return response.text();
      })
      .then(async (xml) => {
        if (!active) return;
        if (!xml.trim()) {
          setStatus("empty");
          return;
        }
        await display.load(xml);
        if (!active) return;
        display.render();
        display.cursor.show();
        capture();
        resizeObserver = new ResizeObserver(() => {
          window.clearTimeout(resizeTimer);
          resizeTimer = window.setTimeout(capture, 140);
        });
        resizeObserver.observe(host.current!);
        setStatus("ready");
      })
      .catch((reason) => {
        if (!active) return;
        setMessage(reason instanceof Error ? reason.message : String(reason));
        setStatus("error");
      });
    return () => {
      active = false;
      window.clearTimeout(resizeTimer);
      resizeObserver?.disconnect();
      positions.current = [];
      osmd.current = null;
      display.clear();
    };
  }, [props.scoreUrl, props.tempoSegments]);

  useEffect(() => {
    let frame = 0;
    const update = () => {
      const display = osmd.current;
      const element = host.current;
      if (display && element && positions.current.length) {
        const transportTick = tickAtSeconds(props.tempoSegments, props.currentTime());
        const target = cursorIndexAtTick(positions.current, transportTick);
        moveCursor(display, cursorIndex, target);
        const position = positionAtTick(positions.current, transportTick);
        element.dataset.cursorTick = String(position?.tick ?? 0);
        element.dataset.cursorX = String(Math.round(position?.x ?? 0));
        element.dataset.cursorY = String(Math.round(position?.y ?? 0));
        element.dataset.transportTick = String(Math.round(transportTick));
      }
      frame = window.requestAnimationFrame(update);
    };
    frame = window.requestAnimationFrame(update);
    return () => window.cancelAnimationFrame(frame);
  }, [props.currentTime, props.tempoSegments]);

  const selectAt = (event: React.MouseEvent<HTMLDivElement>) => {
    if (!host.current || !positions.current.length) return;
    const rect = host.current.getBoundingClientRect();
    const point = {
      x: event.clientX - rect.left + host.current.scrollLeft,
      y: event.clientY - rect.top + host.current.scrollTop,
    };
    const position = nearestCursorPosition(positions.current, point.x, point.y);
    if (!position) return;
    const noteTick = nearestNoteTick(props.notes, position.tick);
    const candidates = props.notes.filter((note) => note.start_tick === noteTick);
    props.onSeek(position.seconds);
    props.onSelect(new Set(candidates.map((note) => note.id)), candidates[0]?.id ?? null);
    host.current.dataset.lastSeekTick = String(position.tick);
  };

  return (
    <section className="score-shell" aria-label="Synchronized notation editor">
      {status === "loading" && <div className="score-state"><Spinner size="small" /><Text>Rendering MusicXML...</Text></div>}
      {status === "empty" && <div className="score-state"><MusicNoteSimple size={28} /><b>No notation yet</b><Text>Compile the score to create the synchronized notation view.</Text></div>}
      {status === "error" && <div className="score-state score-error"><WarningCircle size={28} /><b>Score could not be opened</b><Text>{message}</Text></div>}
      <div
        ref={host}
        className={`score-host ${status === "ready" ? "is-ready" : ""}`}
        aria-label="MusicXML score. Click the nearest rendered note position to seek its exact transport tick."
        onClick={selectAt}
      />
      {selected.length > 0 && (
        <output className="score-selection" aria-live="polite">
          {selected.length} selected at {selected[0].start_seconds.toFixed(3)}s
        </output>
      )}
    </section>
  );
}

export function captureCursorPositions(display: OpenSheetMusicDisplay, element: HTMLElement, segments: TempoSegment[]): ScoreCursorPosition[] {
  const cursor = display.cursor;
  const previousFollow = display.FollowCursor;
  display.FollowCursor = false;
  cursor.reset();
  cursor.show();
  const result: ScoreCursorPosition[] = [];
  const hostRect = element.getBoundingClientRect();
  for (let index = 0; index < 100_000; index++) {
    cursor.update();
    const tick = Math.round(cursor.Iterator.CurrentSourceTimestamp.RealValue * WHOLE_TICKS);
    const cursorRect = cursor.cursorElement.getBoundingClientRect();
    const position = {
      index,
      tick,
      seconds: secondsAtTick(segments, tick),
      x: cursorRect.left - hostRect.left + element.scrollLeft + cursorRect.width / 2,
      y: cursorRect.top - hostRect.top + element.scrollTop + cursorRect.height / 2,
    };
    const previous = result.at(-1);
    if (!previous || previous.tick !== position.tick || Math.abs(previous.y - position.y) > 1) result.push(position);
    if (cursor.Iterator.EndReached) break;
    const before = cursor.Iterator.CurrentSourceTimestamp.RealValue;
    cursor.next();
    if (cursor.Iterator.EndReached && cursor.Iterator.CurrentSourceTimestamp.RealValue === before) break;
  }
  cursor.reset();
  cursor.show();
  display.FollowCursor = previousFollow;
  return result;
}

export function cursorIndexAtTick(positions: ScoreCursorPosition[], tick: number): number {
  return positionAtTick(positions, tick)?.index ?? 0;
}

function positionAtTick(positions: ScoreCursorPosition[], tick: number): ScoreCursorPosition | null {
  if (!positions.length) return null;
  let low = 0;
  let high = positions.length - 1;
  while (low < high) {
    const middle = Math.ceil((low + high) / 2);
    if (positions[middle].tick <= tick) low = middle;
    else high = middle - 1;
  }
  return positions[low];
}

export function nearestCursorPosition(positions: ScoreCursorPosition[], x: number, y: number): ScoreCursorPosition | null {
  return positions.reduce<ScoreCursorPosition | null>((best, position) => {
    if (!best) return position;
    const distance = (position.x - x) ** 2 + ((position.y - y) * 0.55) ** 2;
    const bestDistance = (best.x - x) ** 2 + ((best.y - y) * 0.55) ** 2;
    return distance < bestDistance ? position : best;
  }, null);
}

function moveCursor(display: OpenSheetMusicDisplay, current: React.MutableRefObject<number>, target: number): void {
  if (target < current.current) {
    display.cursor.reset();
    current.current = 0;
  }
  while (current.current < target && !display.cursor.Iterator.EndReached) {
    display.cursor.next();
    current.current++;
  }
}

function nearestNoteTick(notes: StudioNote[], tick: number): number {
  if (!notes.length) return tick;
  return notes.reduce((best, note) => Math.abs(note.start_tick - tick) < Math.abs(best - tick) ? note.start_tick : best, notes[0].start_tick);
}
