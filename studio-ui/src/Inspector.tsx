import { Button, Field, Input, Select } from "@fluentui/react-components";
import { useEffect, useState } from "react";
import type { StudioNote } from "./types";

const articulations = ["", "staccato", "tenuto", "accent", "marcato", "legato"];

export function Inspector({ note, onEdit }: { note: StudioNote | null; onEdit: (commands: Record<string, unknown>[]) => void }) {
  const [pitch, setPitch] = useState(""); const [velocity, setVelocity] = useState("80");
  const [duration, setDuration] = useState("1/4"); const [articulation, setArticulation] = useState("");
  useEffect(() => { if (note) { setPitch(note.written_pitch); setVelocity(String(note.velocity)); setDuration(note.duration); setArticulation(note.articulation ?? ""); } }, [note]);
  if (!note) return <aside className="inspector panel"><header className="panel-heading">INSPECTOR</header><div className="empty-state"><b>Select a note</b><span>Click a note in the piano roll to edit pitch, velocity, duration and articulation.</span></div></aside>;
  const target = { part: note.part, measure: note.measure, voice: note.voice, event_index: note.event_index };
  return <aside className="inspector panel">
    <header className="panel-heading"><span>NOTE INSPECTOR</span><small>{note.part} / M{note.measure} / {note.voice}</small></header>
    <div className="inspector-fields">
      <Field label="Written pitch"><Input value={pitch} onChange={(_, d) => setPitch(d.value)} /></Field>
      <Field label="Velocity"><Input type="number" min={1} max={127} value={velocity} onChange={(_, d) => setVelocity(d.value)} /></Field>
      <Field label="Duration"><Input value={duration} onChange={(_, d) => setDuration(d.value)} /></Field>
      <Field label="Articulation"><Select value={articulation} onChange={(_, d) => setArticulation(d.value)}>{articulations.map((item) => <option key={item} value={item}>{item || "none"}</option>)}</Select></Field>
      <Button appearance="primary" onClick={() => {
        const changes: Record<string, unknown> = { pitch, velocity: Number(velocity), articulation: articulation || null };
        const commands: Record<string, unknown>[] = [{ type: "update_note", ...target, pitch_index: note.pitch_index, changes }];
        if (duration !== note.duration) commands.push({ type: "resize_event", ...target, duration });
        onEdit(commands);
      }}>Apply note changes</Button>
    </div>
    <div className="inspector-readout"><span>START</span><b>{note.start_seconds.toFixed(3)}s</b><span>END</span><b>{note.end_seconds.toFixed(3)}s</b><span>STAFF</span><b>{note.staff}</b><span>EXPRESSION</span><b>{note.expression ? "yes" : "no"}</b></div>
  </aside>;
}
