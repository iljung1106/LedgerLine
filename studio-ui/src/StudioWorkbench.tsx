import {
  Badge,
  Button,
  Checkbox,
  FluentProvider,
  Select,
  Tab,
  TabList,
  Text,
  Tooltip,
  webDarkTheme,
  webLightTheme,
} from "@fluentui/react-components";
import {
  ArrowCounterClockwise,
  ArrowUUpLeft,
  ArrowUUpRight,
  ArrowsOutSimple,
  Copy,
  Eye,
  EyeSlash,
  Minus,
  Pause,
  Play,
  Plus,
  SlidersHorizontal,
  Stop,
  Trash,
  WarningCircle,
} from "@phosphor-icons/react";
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { applyCommands, cancelJob, loadModel, loadStatus, redo, startJob, undo } from "./api";
import { AudioEngine, type MeterSnapshot, type TrackPreviewState } from "./audioEngine";
import { DelegationPanel } from "./DelegationReviewPanel";
import { EngineStatusPanel } from "./EngineStatusPanel";
import { PREPARE_IDS_MESSAGE, structuralEditingAvailable } from "./editingPolicy";
import { Inspector } from "./Inspector";
import { Mixer } from "./Mixer";
import { PerformanceLane } from "./PerformanceLane";
import { PianoRollEditor } from "./PianoRollEditor";
import { ReviewPanel } from "./ReviewPanel";
import { deleteNotes, duplicateNotes, quantizeNotes, snapSeconds, type SnapDivision } from "./timeline";
import type { EditCommand, LaneKind, StudioModel, StudioNote } from "./types";
import { Waveform } from "./Waveform";

type ViewMode = "guide" | "edit" | "score";
type SourceMode = "rendered-stems" | "rendered-master" | "preview-synth" | "silent";

const EMPTY_METERS: MeterSnapshot = {
  tracks: {},
  master: { peak: 0, rms: 0, peakDb: -96, rmsDb: -96, clipped: false },
};

const ScoreEditorView = lazy(() => import("./ScoreEditorView").then((module) => ({ default: module.ScoreEditorView })));

export default function StudioWorkbench() {
  const [model, setModel] = useState<StudioModel | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [anchorId, setAnchorId] = useState<string | null>(null);
  const [activePart, setActivePart] = useState<string | null>(null);
  const [visibleParts, setVisibleParts] = useState<Set<string>>(new Set());
  const [view, setView] = useState<ViewMode>("guide");
  const [lane, setLane] = useState<LaneKind>("velocity");
  const [snap, setSnap] = useState<SnapDivision>("1/16");
  const [zoom, setZoom] = useState(1);
  const [dark, setDark] = useState(true);
  const [playing, setPlaying] = useState(false);
  const [loop, setLoop] = useState(false);
  const [loopRange, setLoopRange] = useState<{ start_seconds: number; end_seconds: number } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [timeLabel, setTimeLabel] = useState("0:00.000");
  const [trackState, setTrackState] = useState<TrackPreviewState>({});
  const [meters, setMeters] = useState<MeterSnapshot>(EMPTY_METERS);
  const [sourceMode, setSourceMode] = useState<SourceMode>("silent");
  const [comparison, setComparison] = useState<"current" | "previous">("current");
  const [comparisonAvailable, setComparisonAvailable] = useState(false);
  const [analysisSource, setAnalysisSource] = useState("master");
  const engine = useRef(new AudioEngine());
  const playhead = useRef(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const modelRef = useRef<StudioModel | null>(null);
  const loadedMediaKey = useRef("");
  const clipboard = useRef<StudioNote[]>([]);

  const selection = useMemo(
    () => model?.notes.filter((note) => selectedIds.has(note.id)) ?? [],
    [model, selectedIds],
  );
  const selectedNote = useMemo(
    () => selection.find((note) => note.id === anchorId) ?? selection[0] ?? null,
    [selection, anchorId],
  );
  const duration = model?.transport.duration_seconds ?? 1;
  const timelineWidth = useMemo(() => Math.max(1280, Math.ceil(duration * 68)) * zoom, [duration, zoom]);
  const structuralEditing = model ? structuralEditingAvailable(model) : false;

  const loadAudio = useCallback(async (next: StudioModel) => {
    const key = mediaKey(next);
    engine.current.updateModel(next);
    if (key === loadedMediaKey.current) {
      const available = engine.current.comparisonAvailable();
      setComparisonAvailable(available);
      if (!available) setComparison("current");
      return;
    }
    loadedMediaKey.current = key;
    await engine.current.load(next);
    setPlaying(false);
    setMeters(EMPTY_METERS);
    setSourceMode(engine.current.sourceMode());
    const available = engine.current.comparisonAvailable();
    setComparisonAvailable(available);
    if (!available) setComparison("current");
    const failures = engine.current.failures();
    if (failures.length) setError(`Some rendered stems could not be decoded: ${failures.join(", ")}`);
  }, []);

  const acceptModel = useCallback(async (next: StudioModel, loadMedia = true) => {
    modelRef.current = next;
    setModel(next);
    setTrackState((current) => ensureTrackState(next, current));
    setVisibleParts((current) => current.size ? new Set([...current].filter((part) => next.parts.some((item) => item.id === part))) : new Set(next.parts.map((part) => part.id)));
    setActivePart((current) => current && next.parts.some((part) => part.id === current) ? current : next.parts[0]?.id ?? null);
    setAnalysisSource((current) => {
      if (current === "master" && next.media.master) return current;
      if (current.startsWith("stem:") && next.media.stems.some((stem) => `stem:${stem.part}` === current)) return current;
      return next.media.master ? "master" : next.media.stems[0] ? `stem:${next.media.stems[0].part}` : "master";
    });
    setSelectedIds((current) => new Set([...current].filter((id) => next.notes.some((note) => note.id === id))));
    if (loadMedia) await loadAudio(next);
  }, [loadAudio]);

  const hydrate = useCallback(async () => {
    try {
      const [loaded, status] = await Promise.all([loadModel(), loadStatus().catch(() => null)]);
      const next = status ? { ...loaded, ...status } : loaded;
      await acceptModel(next);
      setError("");
    } catch (reason) {
      setError(readableError(reason));
    } finally {
      setLoading(false);
    }
  }, [acceptModel]);

  useEffect(() => { hydrate(); }, [hydrate]);

  useEffect(() => {
    const timer = window.setInterval(async () => {
      try {
        const [loaded, status] = await Promise.all([loadModel(), loadStatus().catch(() => null)]);
        const next = status ? { ...loaded, ...status } : loaded;
        const current = modelRef.current;
        const changed = !current
          || current.project.revision !== next.project.revision
          || mediaKey(current) !== mediaKey(next);
        await acceptModel(next, changed);
      } catch (reason) {
        setError(readableError(reason));
      }
    }, 2500);
    return () => window.clearInterval(timer);
  }, [acceptModel]);

  useEffect(() => {
    let frame = 0;
    let meterFrame = 0;
    let labelFrame = 0;
    const tick = () => {
      const currentDuration = modelRef.current?.transport.duration_seconds ?? 1;
      if (engine.current.isPlaying()) {
        playhead.current = Math.min(currentDuration, engine.current.currentTime());
        const repeatEnd = loop && loopRange ? loopRange.end_seconds : currentDuration;
        if (playhead.current >= repeatEnd - 0.02) {
          if (loop) {
            const repeatStart = loopRange?.start_seconds ?? 0;
            engine.current.play(repeatStart, trackState, comparison).catch((reason) => setError(readableError(reason)));
            playhead.current = repeatStart;
          } else {
            engine.current.stop();
            setPlaying(false);
          }
        }
      }
      const ratio = Math.max(0, Math.min(1, playhead.current / currentDuration));
      rootRef.current?.style.setProperty("--playhead-ratio", `${ratio}`);
      if (++labelFrame % 4 === 0) setTimeLabel(formatTime(playhead.current));
      if (++meterFrame % 6 === 0) setMeters(engine.current.meters());
      frame = window.requestAnimationFrame(tick);
    };
    frame = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(frame);
  }, [comparison, loop, loopRange, trackState]);

  const submit = useCallback(async (commands: EditCommand[]) => {
    const current = modelRef.current;
    if (!current || !commands.length) return;
    try {
      const response = await applyCommands(commands, current.project.revision);
      await acceptModel(response.model);
      setError("");
    } catch (reason) {
      setError(readableError(reason));
    }
  }, [acceptModel]);

  const performUndo = useCallback(async (kind: "undo" | "redo") => {
    try {
      const response = kind === "undo" ? await undo() : await redo();
      await acceptModel(response.model);
    } catch (reason) {
      setError(readableError(reason));
    }
  }, [acceptModel]);

  const startStop = useCallback(async () => {
    const current = modelRef.current;
    if (!current) return;
    if (playing) {
      engine.current.stop();
      setPlaying(false);
      setMeters(EMPTY_METERS);
      return;
    }
    try {
      const start = loop && loopRange && (playhead.current < loopRange.start_seconds || playhead.current >= loopRange.end_seconds)
        ? loopRange.start_seconds
        : playhead.current;
      playhead.current = start;
      await engine.current.play(start, trackState, comparison);
      setSourceMode(engine.current.sourceMode());
      setPlaying(true);
    } catch (reason) {
      setError(readableError(reason));
    }
  }, [comparison, loop, loopRange, playing, trackState]);

  const stop = useCallback(() => {
    engine.current.stop();
    playhead.current = 0;
    setPlaying(false);
    setMeters(EMPTY_METERS);
  }, []);

  const seek = useCallback((seconds: number) => {
    playhead.current = Math.max(0, Math.min(modelRef.current?.transport.duration_seconds ?? 0, seconds));
    if (engine.current.isPlaying()) engine.current.play(playhead.current, trackState, comparison).catch((reason) => setError(readableError(reason)));
  }, [comparison, trackState]);
  const currentTime = useCallback(() => playhead.current, []);

  const switchComparison = useCallback((next: "current" | "previous") => {
    if (next === "previous" && !comparisonAvailable) return;
    setComparison(next);
    setSourceMode(next === "previous" ? "rendered-master" : engine.current.sourceMode());
    if (engine.current.isPlaying()) {
      engine.current.play(playhead.current, trackState, next).catch((reason) => setError(readableError(reason)));
    }
  }, [comparisonAvailable, trackState]);

  const setTrack = useCallback((part: string, patch: Partial<TrackPreviewState[string]>) => {
    setTrackState((current) => {
      const next = { ...current, [part]: { ...current[part], ...patch } };
      engine.current.setMix(next);
      return next;
    });
  }, []);

  const cancelBuild = useCallback(async (id: string) => {
    try {
      await cancelJob(id);
      const status = await loadStatus();
      if (status && modelRef.current) await acceptModel({ ...modelRef.current, ...status }, false);
    } catch (reason) {
      setError(readableError(reason));
    }
  }, [acceptModel]);

  const requestBuild = useCallback(async (kind: "render" | "mix" | "refine") => {
    try {
      await startJob(kind);
      const status = await loadStatus();
      if (status && modelRef.current) await acceptModel({ ...modelRef.current, ...status }, false);
    } catch (reason) {
      setError(readableError(reason));
    }
  }, [acceptModel]);

  const copySelected = useCallback(() => {
    if (!selection.length) return;
    clipboard.current = selection.map((note) => ({ ...note }));
  }, [selection]);

  const pasteSelected = useCallback(() => {
    if (!structuralEditing) { setError(PREPARE_IDS_MESSAGE); return; }
    const current = modelRef.current;
    if (!current || !clipboard.current.length) return;
    const first = Math.min(...clipboard.current.map((note) => note.start_seconds));
    const commands = clipboard.current.flatMap((note) => {
      const target = snapSeconds(current.transport.measures, playhead.current + note.start_seconds - first, snap);
      if (!target) return [];
      const command = duplicateNotes([note], target.offsetFraction)[0];
      return [{ ...command, target_measure: target.measure.number, target_voice: note.voice }];
    });
    submit(commands);
  }, [snap, structuralEditing, submit]);

  const deleteSelected = useCallback(() => {
    if (!structuralEditing) { setError(PREPARE_IDS_MESSAGE); return; }
    submit(deleteNotes(selection));
  }, [selection, structuralEditing, submit]);
  const quantizeSelected = useCallback(() => {
    if (!structuralEditing) { setError(PREPARE_IDS_MESSAGE); return; }
    const measures = modelRef.current?.transport.measures ?? [];
    submit(quantizeNotes(selection, measures, snap));
  }, [selection, snap, structuralEditing, submit]);

  useEffect(() => {
    const listener = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.closest("input, textarea, select, [contenteditable='true']")) return;
      const modifier = event.ctrlKey || event.metaKey;
      if (event.code === "Space") { event.preventDefault(); startStop(); }
      else if (event.key === "Delete" || event.key === "Backspace") { event.preventDefault(); deleteSelected(); }
      else if (modifier && event.key.toLowerCase() === "c") { event.preventDefault(); copySelected(); }
      else if (modifier && event.key.toLowerCase() === "v") { event.preventDefault(); pasteSelected(); }
      else if (modifier && event.key.toLowerCase() === "z" && event.shiftKey) { event.preventDefault(); performUndo("redo"); }
      else if (modifier && event.key.toLowerCase() === "z") { event.preventDefault(); performUndo("undo"); }
      else if (modifier && event.key.toLowerCase() === "a") {
        event.preventDefault();
        const current = modelRef.current;
        if (current) setSelectedIds(new Set(current.notes.filter((note) => visibleParts.has(note.part)).map((note) => note.id)));
      } else if (event.key.toLowerCase() === "q") { event.preventDefault(); quantizeSelected(); }
    };
    window.addEventListener("keydown", listener);
    return () => window.removeEventListener("keydown", listener);
  }, [copySelected, deleteSelected, pasteSelected, performUndo, quantizeSelected, startStop, visibleParts]);

  if (loading) return <LoadingState dark={dark} />;
  if (!model) return <FailureState dark={dark} message={error || "Studio project could not be loaded."} onRetry={hydrate} />;
  if (!model.parts.length) return <EmptyProjectState dark={dark} title={model.project.title} />;

  const activeMedia = analysisSource === "master"
    ? model.media.master
    : model.media.stems.find((stem) => `stem:${stem.part}` === analysisSource) ?? null;
  const activeSpectrogram = activeMedia?.spectrogram_url
    ?? (analysisSource === "master" ? model.media.spectrogram_url : null);
  const controls = [...(model.controls ?? []), ...(model.automation ?? [])];
  const automationLanes = [...new Map((model.automation ?? []).filter((point) => point.lane).map((point) => [point.lane!, point])).values()];
  const selectedAutomation = lane.startsWith("automation:")
    ? automationLanes.find((point) => point.lane === lane.slice("automation:".length))
    : undefined;

  return (
    <FluentProvider theme={dark ? webDarkTheme : webLightTheme}>
      <main ref={rootRef} className={`studio-shell ${dark ? "theme-dark" : "theme-light"}`}>
        <header className="transport">
          <div className="brand"><span>LedgerLine Studio</span><b>{model.project.title}</b></div>
          <div className="transport-buttons">
            <Tooltip content={playing ? "Pause (Space)" : "Play (Space)"} relationship="label"><Button icon={playing ? <Pause /> : <Play />} appearance="primary" onClick={startStop} /></Tooltip>
            <Tooltip content="Stop" relationship="label"><Button icon={<Stop />} onClick={stop} /></Tooltip>
            <Tooltip content="Undo (Ctrl+Z)" relationship="label"><Button icon={<ArrowUUpLeft />} disabled={!model.history.can_undo} onClick={() => performUndo("undo")} /></Tooltip>
            <Tooltip content="Redo (Ctrl+Shift+Z)" relationship="label"><Button icon={<ArrowUUpRight />} disabled={!model.history.can_redo} onClick={() => performUndo("redo")} /></Tooltip>
          </div>
          <time className="time-readout">{timeLabel}</time>
          <div className="comparison-toggle" role="group" aria-label="Current and previous render comparison">
            <Button size="small" appearance={comparison === "current" ? "primary" : "subtle"} onClick={() => switchComparison("current")}>A Current</Button>
            <Tooltip content={comparisonAvailable ? "Play the archived pre-edit master" : "No revision-matched previous master is available"} relationship="description"><Button size="small" appearance={comparison === "previous" ? "primary" : "subtle"} disabled={!comparisonAvailable} onClick={() => switchComparison("previous")}>B Previous</Button></Tooltip>
          </div>
          <div className="transport-options">
            <Checkbox checked={loop} onChange={(_, data) => setLoop(Boolean(data.checked))} label="Loop" />
            <Checkbox checked={!dark} onChange={(_, data) => setDark(!Boolean(data.checked))} label="Light" />
          </div>
          <TabList selectedValue={view} onTabSelect={(_, data) => setView(data.value as ViewMode)}>
            <Tab value="guide" icon={<ArrowCounterClockwise />}>Guide</Tab>
            <Tab value="edit" icon={<SlidersHorizontal />}>Edit</Tab>
            <Tab value="score">Score</Tab>
          </TabList>
        </header>
        {error && <div className="error-bar" role="alert"><WarningCircle size={17} weight="fill" /><span>{error}</span><Button appearance="subtle" size="small" onClick={() => setError("")}>Dismiss</Button></div>}
        {view !== "guide" && (
          <nav className="edit-toolbar" aria-label="Timeline editing controls">
            <div className="toolbar-group">
              <Tooltip content="Copy selection (Ctrl+C)" relationship="label"><Button size="small" icon={<Copy />} disabled={!selection.length} onClick={copySelected} /></Tooltip>
              <Tooltip content={structuralEditing ? "Delete selection" : "Run prepare-ids before deleting events"} relationship="label"><Button size="small" icon={<Trash />} disabled={!selection.length || !structuralEditing} onClick={deleteSelected} /></Tooltip>
              <Button size="small" disabled={!selection.length || snap === "off" || !structuralEditing} onClick={quantizeSelected}>Quantize</Button>
              <Badge appearance="outline">{selection.length} selected</Badge>
            </div>
            <label className="compact-field"><span>Snap</span><Select size="small" value={snap} onChange={(_, data) => setSnap(data.value as SnapDivision)}><option value="off">Off</option><option value="1/4">1/4</option><option value="1/8">1/8</option><option value="1/16">1/16</option><option value="1/32">1/32</option></Select></label>
            {view === "edit" && <label className="compact-field"><span>Lane</span><Select size="small" value={lane} onChange={(_, data) => setLane(data.value as LaneKind)}>
              <option value="velocity">Velocity</option>
              <option value="pitch_cents">Pitch cents</option>
              <option value="cc1">CC1 modulation</option>
              <option value="cc11">CC11 expression</option>
              <option value="pedal">CC64 pedal</option>
              <option value="keyswitch">Keyswitch</option>
              <option value="tempo">Tempo</option>
              <option value="articulation">Articulation</option>
              {automationLanes.map((point) => <option key={point.lane} value={`automation:${point.lane}`}>Automation · {point.lane_target ?? point.semantic ?? point.lane}</option>)}
            </Select></label>}
            {view === "edit" && selectedAutomation && <label className="compact-field"><span>Curve</span><Select size="small" value={selectedAutomation.lane_interpolation ?? selectedAutomation.curve ?? "linear"} onChange={(_, data) => submit([{ type: "set_curve", lane: selectedAutomation.lane, curve: data.value }])}>
              <option value="step">Step</option><option value="linear">Linear</option><option value="smooth">Smooth</option><option value="exponential">Exponential</option><option value="bezier">Bezier</option>
            </Select></label>}
            {view === "edit" && <label className="compact-field"><span>Analysis source</span><Select size="small" value={analysisSource} onChange={(_, data) => setAnalysisSource(data.value)}>
              {model.media.master && <option value="master">Master</option>}
              {model.media.stems.map((stem) => <option key={stem.part} value={`stem:${stem.part}`}>{model.parts.find((part) => part.id === stem.part)?.name ?? stem.part} stem</option>)}
            </Select></label>}
            {view === "edit" && loopRange && <div className="toolbar-group loop-range-readout"><Badge appearance="outline">Loop {formatTime(loopRange.start_seconds)}–{formatTime(loopRange.end_seconds)}</Badge><Button size="small" onClick={() => { setLoopRange(null); setLoop(false); }}>Clear loop</Button></div>}
            <div className="toolbar-group zoom-controls">
              <Tooltip content="Zoom out" relationship="label"><Button size="small" icon={<Minus />} onClick={() => setZoom((value) => Math.max(0.5, value - 0.25))} /></Tooltip>
              <span>{Math.round(zoom * 100)}%</span>
              <Tooltip content="Zoom in" relationship="label"><Button size="small" icon={<Plus />} onClick={() => setZoom((value) => Math.min(4, value + 0.25))} /></Tooltip>
              <Tooltip content="Fit project" relationship="label"><Button size="small" icon={<ArrowsOutSimple />} onClick={() => setZoom(1)} /></Tooltip>
            </div>
            <small className="shortcut-hint">Double click adds. Right click removes. Q quantizes.</small>
          </nav>
        )}
        {view === "edit" && !structuralEditing && (
          <section className="id-gate-banner" role="status" aria-label="Structural editing requires persistent IDs">
            <WarningCircle size={18} weight="fill" />
            <div><b>Prepare persistent IDs before structural editing</b><span>Listening and safe inspector edits remain available. Review the dry-run and backup before applying.</span></div>
            <code>ledgerline prepare-ids &quot;{model.project.root}&quot; --dry-run</code>
          </section>
        )}
        <section className="studio-body">
          <section className="workspace">
            {view === "guide" && (
              <div className="guide-grid">
                <DelegationPanel onApplied={hydrate} currentRevision={model.project.revision} />
                <section className="project-brief panel">
                  <header className="panel-heading"><span>PROJECT</span><small>{model.project.measures} measures / {formatTime(duration)}</small></header>
                  <div className="part-list">
                    {model.parts.map((part) => <button key={part.id} className={`part-row ${activePart === part.id ? "is-active" : ""}`} onClick={() => { setActivePart(part.id); setView("edit"); }}>
                      <i style={{ background: part.color }} />
                      <span>{part.name}</span>
                      <small>{part.family} / {part.note_count} notes</small>
                    </button>)}
                  </div>
                  <div className="guide-copy"><Text>Describe a musical result, review the agent proposal, then inspect the exact engine render in Edit or Score.</Text></div>
                </section>
              </div>
            )}
            {view !== "guide" && (
              <div className="timeline-wrap">
                <div className="ruler-shell">
                  <div className="ruler-spacer" />
                  <div className="ruler" style={{ width: timelineWidth }} onClick={(event) => {
                    const rect = event.currentTarget.getBoundingClientRect();
                    seek((event.clientX - rect.left) / rect.width * duration);
                  }}>
                    {model.transport.measures.map((measure) => <button key={measure.number} style={{ left: `${measure.start_seconds / duration * 100}%` }} onClick={(event) => { event.stopPropagation(); seek(measure.start_seconds); }}>M{measure.number}</button>)}
                    <div className="playhead" />
                  </div>
                </div>
                <div className="timeline-scroll">
                  <div className="timeline-stage" style={{ width: timelineWidth + 178 }}>
                    <div className="track-rail">
                      {model.parts.map((part) => {
                        const visible = visibleParts.has(part.id);
                        return <div key={part.id} className={`track-row ${activePart === part.id ? "is-active" : ""}`}>
                          <button className="track-select" onClick={() => setActivePart(part.id)}><i style={{ background: part.color }} /><span>{part.name}</span><small>{part.render_status ?? "unknown"}</small></button>
                          <Tooltip content={visible ? "Hide notes" : "Show notes"} relationship="label"><Button size="small" appearance="subtle" icon={visible ? <Eye /> : <EyeSlash />} onClick={() => setVisibleParts((current) => { const next = new Set(current); if (next.has(part.id)) next.delete(part.id); else next.add(part.id); return next; })} /></Tooltip>
                        </div>;
                      })}
                    </div>
                    <div className="lanes" style={{ width: timelineWidth }}>
                      {view === "edit" && <>
                        <PianoRollEditor notes={model.notes} parts={model.parts} measures={model.transport.measures} duration={duration} width={timelineWidth} selectedIds={selectedIds} visibleParts={visibleParts} activePart={activePart} snap={snap} structuralEditing={structuralEditing} loopRange={loopRange} onLoopRangeChange={(range) => { setLoopRange(range); setLoop(Boolean(range)); }} onSelectionChange={(ids, anchor) => { setSelectedIds(ids); setAnchorId(anchor); }} onEdit={submit} onUnavailable={setError} />
                        <PerformanceLane lane={lane} notes={model.notes} controls={controls} tempoPoints={model.tempo ?? []} parts={model.parts} measures={model.transport.measures} duration={duration} width={timelineWidth} selectedIds={selectedIds} visibleParts={visibleParts} activePart={activePart} snap={snap} onSelectionChange={(ids, anchor) => { setSelectedIds(ids); setAnchorId(anchor); }} onEdit={submit} onUnavailable={setError} />
                        <Waveform media={activeMedia} width={timelineWidth} onSeek={seek} />
                        {activeSpectrogram
                          ? <img className="spectrogram" alt={`${activeMedia?.label ?? "Audio"} spectrogram aligned to the project timeline`} src={activeSpectrogram} />
                          : <div className="spectrogram-empty" style={{ width: timelineWidth }}>No spectrogram is available for {activeMedia?.label ?? "this source"}; its waveform remains aligned to the score.</div>}
                      </>}
                      {view === "score" && <Suspense fallback={<div className="score-state"><Text>Loading notation renderer...</Text></div>}><ScoreEditorView scoreUrl={model.score.url} notes={model.notes} tempoSegments={model.transport.tempo_segments} selectedIds={selectedIds} currentTime={currentTime} onSeek={seek} onSelect={(ids, anchor) => { setSelectedIds(ids); setAnchorId(anchor); }} /></Suspense>}
                      <div className="playhead timeline-playhead" />
                    </div>
                  </div>
                </div>
              </div>
            )}
          </section>
          <aside className="right-rail">
            <EngineStatusPanel model={model} sourceMode={sourceMode} onCancelJob={cancelBuild} onStartJob={requestBuild} onEdit={submit} />
            <Inspector note={selectedNote} articulations={model.parts.find((part) => part.id === selectedNote?.part)?.articulations ?? model.parts.find((part) => part.id === selectedNote?.part)?.engine?.articulations} structuralEditing={structuralEditing} onEdit={submit} />
            {view === "edit" && <>
              <ReviewPanel model={model} comparison={comparison} />
              <Mixer model={model} state={trackState} meters={meters} onChange={setTrack} onEdit={submit} />
            </>}
          </aside>
        </section>
      </main>
    </FluentProvider>
  );
}

function LoadingState({ dark }: { dark: boolean }) {
  return <FluentProvider theme={dark ? webDarkTheme : webLightTheme}><main className="loading-shell"><div className="loading-transport" /><div className="loading-grid"><div /><div /><div /></div><span>Opening the score, engine receipts and media...</span></main></FluentProvider>;
}

function FailureState({ dark, message, onRetry }: { dark: boolean; message: string; onRetry: () => void }) {
  return <FluentProvider theme={dark ? webDarkTheme : webLightTheme}><main className="full-state"><WarningCircle size={34} weight="fill" /><h1>LedgerLine Studio could not open this project</h1><p>{message}</p><Button appearance="primary" onClick={onRetry}>Retry</Button></main></FluentProvider>;
}

function EmptyProjectState({ dark, title }: { dark: boolean; title: string }) {
  return <FluentProvider theme={dark ? webDarkTheme : webLightTheme}><main className="full-state"><h1>{title}</h1><p>This project has no tracks. Ask the music agent to create a draft, then reopen Studio.</p></main></FluentProvider>;
}

function ensureTrackState(model: StudioModel, current: TrackPreviewState): TrackPreviewState {
  return Object.fromEntries(model.parts.map((part) => {
    const mix = model.mix.tracks[part.id] ?? { gain_db: 0, pan: 0 };
    return [part.id, { gain_db: Number(mix.gain_db ?? 0), pan: Number(mix.pan ?? 0), mute: current[part.id]?.mute ?? false, solo: current[part.id]?.solo ?? false }];
  }));
}

function mediaKey(model: StudioModel): string {
  return [
    model.media.version,
    model.media.rendered_revision,
    model.media.master?.output_sha ?? model.media.master?.sha256 ?? model.media.master?.url,
    model.media.previous_master?.output_sha ?? model.media.previous_master?.sha256 ?? model.media.previous_master?.url,
    ...model.media.stems.map((stem) => stem.output_sha ?? stem.sha256 ?? stem.url),
  ].join("|");
}

function formatTime(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const rest = seconds - minutes * 60;
  return `${minutes}:${rest.toFixed(3).padStart(6, "0")}`;
}

export function readableError(reason: unknown): string {
  const raw = reason instanceof Error ? reason.message : String(reason);
  const lower = raw.toLowerCase();
  if (lower.includes("revision") || lower.includes("stale") || lower.includes("conflict")) {
    return "The project changed after this edit was prepared. Studio refreshed nothing, so your source is safe. Reload and apply the edit again to the latest revision.";
  }
  if (lower.includes("unsupported studio command")) {
    return "This project server does not support that editor action yet. Update LedgerLine or use the note inspector for the compatible edit.";
  }
  return raw;
}
