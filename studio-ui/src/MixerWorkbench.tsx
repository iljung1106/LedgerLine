import { Button, Input, Select, Slider, Tooltip } from "@fluentui/react-components";
import { ArrowDown, ArrowUp, Plus, SpeakerHigh, SpeakerSlash, Trash } from "@phosphor-icons/react";
import { useEffect, useMemo, useState } from "react";
import type { MeterLevel, MeterSnapshot, TrackPreviewState } from "./audioEngine";
import type { EditCommand, EqBand, MixNode, MixProcessor, StudioModel } from "./types";

export type MixNodeRef = { type: "track" | "bus" | "master"; id: string };

export function updateMixNodeCommand(ref: MixNodeRef, changes: Record<string, unknown>): EditCommand {
  return { type: "update_mix_node", node_type: ref.type, node: ref.id, changes };
}

export function Mixer({ model, state, meters, onChange, onEdit }: {
  model: StudioModel;
  state: TrackPreviewState;
  meters?: MeterSnapshot;
  onChange: (part: string, patch: Partial<TrackPreviewState[string]>) => void;
  onEdit: (commands: EditCommand[]) => void;
}) {
  const liveMeters = meters ?? { tracks: {}, master: { peak: 0, rms: 0, peakDb: -96, rmsDb: -96, clipped: false } };
  const refs = useMemo<MixNodeRef[]>(() => [
    ...model.parts.filter((part) => model.mix.tracks[part.id]).map((part) => ({ type: "track" as const, id: part.id })),
    ...Object.keys(model.mix.buses).map((id) => ({ type: "bus" as const, id })),
    { type: "master" as const, id: "master" },
  ], [model.mix.buses, model.mix.tracks, model.parts]);
  const [selectedKey, setSelectedKey] = useState(() => nodeKey(refs[0] ?? { type: "master", id: "master" }));
  const selected = refs.find((ref) => nodeKey(ref) === selectedKey) ?? refs[0] ?? { type: "master" as const, id: "master" };
  const structured = model.mix.format === 2 && model.capabilities.edit_mix_graph !== false;

  useEffect(() => {
    if (!refs.some((ref) => nodeKey(ref) === selectedKey)) setSelectedKey(nodeKey(refs[0] ?? { type: "master", id: "master" }));
  }, [refs, selectedKey]);

  const writeTrack = (part: string, field: "gain_db" | "pan", value: number) => {
    if (structured) onEdit([updateMixNodeCommand({ type: "track", id: part }, { [field]: value })]);
    else onEdit([{ type: "update_mix", part, changes: { [field]: value } }]);
  };

  return <section className="mixer panel" aria-label="Mixer">
    <header className="panel-heading"><span>MIXER</span><small>preview meters · source-backed controls</small></header>
    <div className="mixer-strips">
      {model.parts.map((part) => {
        const authored = model.mix.tracks[part.id] ?? defaultNode();
        const track = state[part.id] ?? { gain_db: authored.gain_db, pan: authored.pan, mute: false, solo: false };
        const level = liveMeters.tracks[part.id];
        return <div className={`channel ${selected.type === "track" && selected.id === part.id ? "is-selected" : ""}`} key={part.id}>
          <button className="channel-select" aria-label={`Edit ${part.name} mix`} onClick={() => setSelectedKey(nodeKey({ type: "track", id: part.id }))}>
            <span style={{ background: part.color }} />{part.name}
          </button>
          <Meter level={level} label={`${part.name} level`} />
          <label>GAIN <strong>{track.gain_db.toFixed(1)}</strong></label>
          <Slider vertical min={-36} max={12} step={0.5} value={track.gain_db}
            onChange={(_, data) => onChange(part.id, { gain_db: data.value })}
            onPointerUp={() => writeTrack(part.id, "gain_db", track.gain_db)}
            onKeyUp={(event) => { if (isSliderKey(event.key)) writeTrack(part.id, "gain_db", track.gain_db); }} />
          <label>PAN <strong>{track.pan.toFixed(2)}</strong></label>
          <Slider min={-1} max={1} step={0.05} value={track.pan}
            onChange={(_, data) => onChange(part.id, { pan: data.value })}
            onPointerUp={() => writeTrack(part.id, "pan", track.pan)}
            onKeyUp={(event) => { if (isSliderKey(event.key)) writeTrack(part.id, "pan", track.pan); }} />
          <div className="channel-buttons">
            <Tooltip content={track.mute ? "Unmute preview" : "Mute preview"} relationship="label">
              <Button size="small" appearance={track.mute ? "primary" : "subtle"} icon={track.mute ? <SpeakerSlash /> : <SpeakerHigh />}
                onClick={() => onChange(part.id, { mute: !track.mute })} />
            </Tooltip>
            <Button size="small" appearance={track.solo ? "primary" : "subtle"} onClick={() => onChange(part.id, { solo: !track.solo })}>S</Button>
          </div>
        </div>;
      })}
      <button className={`channel master-channel ${selected.type === "master" ? "is-selected" : ""}`} onClick={() => setSelectedKey("master:master")}>
        <Meter level={liveMeters.master} label="Master level" />
        <b>MASTER</b><small>{formatDb(liveMeters.master.peakDb)} peak</small><small>{formatDb(liveMeters.master.rmsDb)} RMS</small>
      </button>
    </div>

    <div className="mix-editor-heading">
      <label><span>Edit node</span><Select size="small" value={selectedKey} onChange={(_, data) => setSelectedKey(data.value)}>
        {refs.map((ref) => <option key={nodeKey(ref)} value={nodeKey(ref)}>{nodeLabel(ref, model)}</option>)}
      </Select></label>
      <small>{structured ? "Changes write mix.yaml and require a fresh render." : "Legacy mix format: only track gain and pan can be written."}</small>
    </div>
    {structured && <MixNodeEditor key={selectedKey} model={model} nodeRef={selected} onEdit={onEdit} />}
  </section>;
}

function MixNodeEditor({ model, nodeRef, onEdit }: { model: StudioModel; nodeRef: MixNodeRef; onEdit: (commands: EditCommand[]) => void }) {
  const node = nodeRef.type === "track" ? model.mix.tracks[nodeRef.id]
    : nodeRef.type === "bus" ? model.mix.buses[nodeRef.id]
    : model.mix.master;
  if (!node) return <div className="mix-empty">This mix node no longer exists.</div>;
  const busIds = Object.keys(model.mix.buses);
  const inserts = node.inserts ?? [];
  const patchNode = (changes: Record<string, unknown>) => onEdit([updateMixNodeCommand(nodeRef, changes)]);

  return (
    <div className="mix-node-editor">
      <div className="mix-node-title"><b>{nodeLabel(nodeRef, model)}</b><span>{nodeRef.type}</span></div>
      <div className="mix-field-grid">
        <CommitNumber label="Gain" unit="dB" value={numberOr(node.gain_db, 0)} min={-120} max={24} step={0.5} onCommit={(value) => patchNode({ gain_db: value })} />
        {nodeRef.type !== "master" && <>
          <CommitNumber label="Pan" value={numberOr((node as MixNode).pan, 0)} min={-1} max={1} step={0.05} onCommit={(value) => patchNode({ pan: value })} />
          <label className="mix-field"><span>Output</span><Select size="small" value={(node as MixNode).output} onChange={(_, data) => patchNode({ output: data.value })}>
            <option value="master">master</option>
            {busIds.filter((id) => !(nodeRef.type === "bus" && id === nodeRef.id)).map((id) => <option key={id} value={id}>{id}</option>)}
          </Select></label>
        </>}
        {nodeRef.type === "master" && <>
          <CommitNumber label="Target" unit="LUFS" value={numberOr(model.mix.master.target_lufs, -16)} min={-70} max={0} step={0.5} onCommit={(value) => patchNode({ target_lufs: value })} />
          <CommitNumber label="Peak ceiling" unit="dBTP" value={numberOr(model.mix.master.true_peak_ceiling_db, -1)} min={-20} max={0} step={0.1} onCommit={(value) => patchNode({ true_peak_ceiling_db: value })} />
          <CommitNumber label="LRA objective" unit="LU" value={numberOr(model.mix.master.loudness_range_lu, 11)} min={0.01} max={70} step={0.5} onCommit={(value) => patchNode({ loudness_range_lu: value })} />
          <CommitNumber label="LUFS tolerance" unit="LU" value={numberOr(model.mix.master.loudness_tolerance_lu, 0.5)} min={0} max={10} step={0.1} onCommit={(value) => patchNode({ loudness_tolerance_lu: value })} />
        </>}
      </div>

      {nodeRef.type !== "master" && <SendEditor nodeRef={nodeRef} node={node as MixNode} busIds={busIds} onEdit={onEdit} />}

      <section className="insert-editor" aria-label={`${nodeRef.id} insert chain`}>
        <header><b>Insert chain</b><span>{inserts.length} processor{inserts.length === 1 ? "" : "s"}</span></header>
        {inserts.map((processor, index) => (
          <ProcessorEditor key={`${index}-${processor.type}`} processor={processor} index={index} count={inserts.length} nodeRef={nodeRef} onEdit={onEdit} />
        ))}
        {!inserts.length && <p>No processors. The signal routes directly to its output.</p>}
        <AddProcessor nodeRef={nodeRef} onEdit={onEdit} />
      </section>
    </div>
  );
}

function SendEditor({ nodeRef, node, busIds, onEdit }: { nodeRef: MixNodeRef; node: MixNode; busIds: string[]; onEdit: (commands: EditCommand[]) => void }) {
  const sends = Object.entries(node.sends ?? {});
  const available = busIds.filter((id) => id !== nodeRef.id && !(id in (node.sends ?? {})));
  const [newBus, setNewBus] = useState(available[0] ?? "");
  useEffect(() => { if (!available.includes(newBus)) setNewBus(available[0] ?? ""); }, [available, newBus]);
  const commandBase = { node_type: nodeRef.type, node: nodeRef.id };
  return <section className="send-editor" aria-label={`${nodeRef.id} sends`}>
    <header><b>Sends</b><span>{sends.length}</span></header>
    {sends.map(([bus, gain]) => <div className="send-row" key={bus}>
      <span>→ {bus}</span>
      <CommitNumber compact label={`${bus} send`} unit="dB" value={gain} min={-120} max={24} step={0.5} onCommit={(gain_db) => onEdit([{ type: "set_mix_send", ...commandBase, bus, gain_db }])} />
      <Tooltip content={`Delete send to ${bus}`} relationship="label"><Button size="small" appearance="subtle" icon={<Trash />} onClick={() => onEdit([{ type: "delete_mix_send", ...commandBase, bus }])} /></Tooltip>
    </div>)}
    {!sends.length && <p>No parallel sends.</p>}
    {available.length > 0 && <div className="send-add">
      <Select size="small" aria-label="Send destination" value={newBus} onChange={(_, data) => setNewBus(data.value)}>{available.map((id) => <option key={id}>{id}</option>)}</Select>
      <Button size="small" icon={<Plus />} disabled={!newBus} onClick={() => onEdit([{ type: "set_mix_send", ...commandBase, bus: newBus, gain_db: -12 }])}>Add -12 dB</Button>
    </div>}
  </section>;
}

function ProcessorEditor({ processor, index, count, nodeRef, onEdit }: {
  processor: MixProcessor;
  index: number;
  count: number;
  nodeRef: MixNodeRef;
  onEdit: (commands: EditCommand[]) => void;
}) {
  const base = { node_type: nodeRef.type, node: nodeRef.id, insert_index: index };
  const patch = (changes: Record<string, unknown>) => onEdit([{ type: "update_mix_insert", ...base, changes }]);
  return <details className="processor-card">
    <summary><span><b>{processor.type.toUpperCase()}</b><small>{processorSummary(processor)}</small></span><span className="processor-actions" onClick={(event) => event.preventDefault()}>
      <Tooltip content="Move processor up" relationship="label"><Button size="small" appearance="subtle" icon={<ArrowUp />} disabled={index === 0} onClick={() => onEdit([{ type: "reorder_mix_insert", ...base, to_index: index - 1 }])} /></Tooltip>
      <Tooltip content="Move processor down" relationship="label"><Button size="small" appearance="subtle" icon={<ArrowDown />} disabled={index === count - 1} onClick={() => onEdit([{ type: "reorder_mix_insert", ...base, to_index: index + 1 }])} /></Tooltip>
      <Tooltip content="Delete processor (Undo is available)" relationship="label"><Button size="small" appearance="subtle" icon={<Trash />} onClick={() => onEdit([{ type: "delete_mix_insert", ...base }])} /></Tooltip>
    </span></summary>
    <div className="processor-fields">
      {processor.type === "eq" && <EqFields processor={processor} onPatch={patch} />}
      {processor.type === "compressor" && <>
        <CommitNumber label="Threshold" unit="dB" value={processor.threshold_db} min={-100} max={0} step={0.5} onCommit={(value) => patch({ threshold_db: value })} />
        <CommitNumber label="Ratio" unit=":1" value={processor.ratio} min={1} max={20} step={0.1} onCommit={(value) => patch({ ratio: value })} />
        <CommitNumber label="Attack" unit="ms" value={processor.attack_ms} min={0.01} max={2000} step={1} onCommit={(value) => patch({ attack_ms: value })} />
        <CommitNumber label="Release" unit="ms" value={processor.release_ms} min={0.01} max={9000} step={5} onCommit={(value) => patch({ release_ms: value })} />
        <CommitNumber label="Makeup" unit="dB" value={processor.makeup_db} min={-24} max={24} step={0.5} onCommit={(value) => patch({ makeup_db: value })} />
        <CommitNumber label="Knee" unit="dB" value={processor.knee_db} min={0} max={40} step={0.5} onCommit={(value) => patch({ knee_db: value })} />
      </>}
      {processor.type === "reverb" && <>
        <CommitNumber label="Input gain" value={processor.in_gain} min={0} max={1} step={0.05} onCommit={(value) => patch({ in_gain: value })} />
        <CommitNumber label="Output gain" value={processor.out_gain} min={0} max={1} step={0.05} onCommit={(value) => patch({ out_gain: value })} />
        <CommitText label="Delays" unit="ms, pipe-separated" value={processor.delays_ms} onCommit={(value) => patch({ delays_ms: value })} />
        <CommitText label="Decays" unit="pipe-separated" value={processor.decays} onCommit={(value) => patch({ decays: value })} />
      </>}
    </div>
  </details>;
}

function EqFields({ processor, onPatch }: { processor: Extract<MixProcessor, { type: "eq" }>; onPatch: (changes: Record<string, unknown>) => void }) {
  const bands = processor.bands ?? [];
  const updateBand = (index: number, changes: Partial<EqBand>) => onPatch({ bands: bands.map((band, bandIndex) => bandIndex === index ? { ...band, ...changes } : band) });
  return <>
    {processor.highpass_hz === undefined
      ? <Button size="small" icon={<Plus />} onClick={() => onPatch({ highpass_hz: 40 })}>High-pass 40 Hz</Button>
      : <CommitNumber label="High-pass" unit="Hz" value={processor.highpass_hz} min={10} max={24000} step={5} onCommit={(value) => onPatch({ highpass_hz: value })} />}
    {processor.lowpass_hz === undefined
      ? <Button size="small" icon={<Plus />} onClick={() => onPatch({ lowpass_hz: 18000 })}>Low-pass 18 kHz</Button>
      : <CommitNumber label="Low-pass" unit="Hz" value={processor.lowpass_hz} min={10} max={24000} step={50} onCommit={(value) => onPatch({ lowpass_hz: value })} />}
    <div className="eq-bands">
      {bands.map((band, index) => <div className="eq-band" key={index}>
        <b>Band {index + 1}</b>
        <CommitNumber compact label={`Band ${index + 1} frequency`} unit="Hz" value={band.frequency_hz} min={10} max={24000} step={10} onCommit={(value) => updateBand(index, { frequency_hz: value })} />
        <CommitNumber compact label={`Band ${index + 1} gain`} unit="dB" value={band.gain_db} min={-24} max={24} step={0.5} onCommit={(value) => updateBand(index, { gain_db: value })} />
        <CommitNumber compact label={`Band ${index + 1} Q`} value={band.q} min={0.05} max={30} step={0.05} onCommit={(value) => updateBand(index, { q: value })} />
        <Button size="small" appearance="subtle" icon={<Trash />} aria-label={`Delete EQ band ${index + 1}`} onClick={() => onPatch({ bands: bands.filter((_, bandIndex) => bandIndex !== index) })} />
      </div>)}
      <Button size="small" icon={<Plus />} onClick={() => onPatch({ bands: [...bands, { frequency_hz: 1000, gain_db: 0, q: 1 }] })}>Add EQ band</Button>
    </div>
  </>;
}

function AddProcessor({ nodeRef, onEdit }: { nodeRef: MixNodeRef; onEdit: (commands: EditCommand[]) => void }) {
  const [kind, setKind] = useState<MixProcessor["type"]>("eq");
  return <div className="processor-add">
    <Select size="small" aria-label="Processor type" value={kind} onChange={(_, data) => setKind(data.value as MixProcessor["type"])}>
      <option value="eq">EQ</option><option value="compressor">Compressor</option><option value="reverb">Reverb</option>
    </Select>
    <Button size="small" icon={<Plus />} onClick={() => onEdit([{ type: "add_mix_insert", node_type: nodeRef.type, node: nodeRef.id, processor: { type: kind } }])}>Add insert</Button>
  </div>;
}

function CommitNumber({ label, unit, value, min, max, step, compact = false, onCommit }: {
  label: string;
  unit?: string;
  value: number;
  min: number;
  max: number;
  step: number;
  compact?: boolean;
  onCommit: (value: number) => void;
}) {
  const [draft, setDraft] = useState(String(value));
  useEffect(() => setDraft(String(value)), [value]);
  const commit = () => {
    const parsed = Number(draft);
    if (!Number.isFinite(parsed) || parsed < min || parsed > max) { setDraft(String(value)); return; }
    if (parsed !== value) onCommit(parsed);
  };
  return <label className={`mix-field ${compact ? "is-compact" : ""}`}><span>{label}</span><Input size="small" type="number" min={min} max={max} step={step} value={draft}
    contentAfter={unit ? <small>{unit}</small> : undefined}
    onChange={(_, data) => setDraft(data.value)}
    onBlur={commit}
    onKeyDown={(event) => { if (event.key === "Enter") event.currentTarget.blur(); else if (event.key === "Escape") { setDraft(String(value)); event.currentTarget.blur(); } }} /></label>;
}

function CommitText({ label, unit, value, onCommit }: { label: string; unit?: string; value: string; onCommit: (value: string) => void }) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  const commit = () => {
    const trimmed = draft.trim();
    if (!trimmed) { setDraft(value); return; }
    if (trimmed !== value) onCommit(trimmed);
  };
  return <label className="mix-field"><span>{label}</span><Input size="small" value={draft} contentAfter={unit ? <small>{unit}</small> : undefined}
    onChange={(_, data) => setDraft(data.value)} onBlur={commit}
    onKeyDown={(event) => { if (event.key === "Enter") event.currentTarget.blur(); else if (event.key === "Escape") { setDraft(value); event.currentTarget.blur(); } }} /></label>;
}

function Meter({ level, label }: { level?: MeterLevel; label: string }) {
  const peak = level?.peakDb ?? -96;
  const rms = level?.rmsDb ?? -96;
  return (
    <div className={`channel-meter ${level?.clipped ? "is-clipped" : ""}`} role="meter" aria-label={label} aria-valuemin={-96} aria-valuemax={0} aria-valuenow={Math.max(-96, peak)}>
      <i className="meter-rms" style={{ height: `${dbPercent(rms)}%` }} />
      <i className="meter-peak" style={{ bottom: `${dbPercent(peak)}%` }} />
    </div>
  );
}

export function processorSummary(processor: MixProcessor): string {
  if (processor.type === "eq") return `${processor.bands?.length ?? 0} band${processor.bands?.length === 1 ? "" : "s"}${processor.highpass_hz ? ` · HP ${processor.highpass_hz} Hz` : ""}`;
  if (processor.type === "compressor") return `${numberOr(processor.threshold_db, -18)} dB · ${numberOr(processor.ratio, 3)}:1`;
  return `${processor.delays_ms ?? "40|55"} ms`;
}

function nodeKey(ref: MixNodeRef): string { return `${ref.type}:${ref.id}`; }

function nodeLabel(ref: MixNodeRef, model: StudioModel): string {
  if (ref.type === "master") return "Master";
  if (ref.type === "bus") return `Bus · ${ref.id}`;
  return `Track · ${model.parts.find((part) => part.id === ref.id)?.name ?? ref.id}`;
}

function defaultNode(): MixNode { return { gain_db: 0, pan: 0, output: "master", sends: {}, inserts: [] }; }
function numberOr(value: unknown, fallback: number): number { return typeof value === "number" && Number.isFinite(value) ? value : fallback; }
function isSliderKey(key: string): boolean { return ["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End", "PageUp", "PageDown"].includes(key); }
function dbPercent(db: number): number { return Math.max(0, Math.min(100, (db + 72) / 72 * 100)); }
function formatDb(db: number): string { return db <= -95 ? "-inf" : `${db.toFixed(1)} dB`; }
