import { useEffect, useRef } from "react";
import { OpenSheetMusicDisplay } from "opensheetmusicdisplay";

export function ScoreView({ currentTime, onSeek }: { currentTime: () => number; onSeek: (seconds: number) => void }) {
  const host = useRef<HTMLDivElement>(null);
  const osmd = useRef<OpenSheetMusicDisplay | null>(null);
  const onsets = useRef<number[]>([]);
  const cursorIndex = useRef(0);

  useEffect(() => {
    if (!host.current) return;
    const display = new OpenSheetMusicDisplay(host.current, {
      backend: "svg",
      autoResize: true,
      drawTitle: false,
      followCursor: true,
      cursorsOptions: [{ type: 1, color: "#2aa894", alpha: 0.82, follow: true }],
    });
    osmd.current = display;
    Promise.all([fetch("/api/score").then((response) => response.text()), fetch("/api/model").then((response) => response.json())])
      .then(async ([xml, model]) => {
        await display.load(xml);
        display.render();
        display.cursor.show();
        onsets.current = [...new Set<number>(model.notes.map((note: { start_seconds: number }) => note.start_seconds))].sort((a, b) => a - b);
      });
    const timer = window.setInterval(() => {
      const cursor = display.cursor;
      const time = currentTime();
      let target = 0;
      while (target + 1 < onsets.current.length && onsets.current[target + 1] <= time) target++;
      if (target < cursorIndex.current) { cursor.reset(); cursorIndex.current = 0; }
      while (cursorIndex.current < target) { cursor.next(); cursorIndex.current++; }
    }, 120);
    return () => { window.clearInterval(timer); osmd.current = null; };
  }, [currentTime]);

  return (
    <div className="score-shell" onDoubleClick={() => onSeek(currentTime())}>
      <div ref={host} className="score-host" aria-label="Interactive MusicXML score" />
    </div>
  );
}
