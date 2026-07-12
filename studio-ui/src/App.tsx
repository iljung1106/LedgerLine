import {
  Button,
  Checkbox,
  FluentProvider,
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
  Pause,
  Play,
  SlidersHorizontal,
  Stop,
} from "@phosphor-icons/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { applyCommands, loadModel, redo, undo } from "./api";
import { AudioEngine, type TrackPreviewState } from "./audioEngine";
import { DelegationPanel } from "./DelegationPanel";
import { Inspector } from "./Inspector";
import { Mixer } from "./Mixer";
import { PianoRoll } from "./PianoRoll";
import { ScoreView } from "./ScoreView";
import type { EditCommand, StudioModel, StudioNote } from "./types";
import { VelocityLane } from "./VelocityLane";
import { Waveform } from "./Waveform";

type ViewMode = "guide" | "edit" | "score";

const timelineWidth = 1440;

export default function App() {
  const [model, setModel] = useState<StudioModel | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [view, setView] = useState<ViewMode>("guide");
  const [dark, setDark] = useState(true);
  const [playing, setPlaying] = useState(false);
  const [loop, setLoop] = useState(false);
  const [error, setError] = useState("");
  const [timeLabel, setTimeLabel] = useState("0:00.000");
  const [trackState, setTrackState] = useState<TrackPreviewState>({});
  const engine = useRef(new AudioEngine());
  const playhead = useRef(0);
  const rootRef = useRef<HTMLDivElement>(null);

  const selectedNote = useMemo(
    () => model?.notes.find((note) => note.id === selectedId) ?? null,
    [model, selectedId],
  );
  const visibleParts = useMemo(
    () => new Set(Object.entries(trackState).filter(([, item]) => !item.mute).map(([part]) => part)),
    [trackState],
  );

  const hydrate = useCallback(async () => {
    const next = await loadModel();
    setModel(next);
    setTrackState((current) => ensureTrackState(next, current));
    await engine.current.load(next);
  }, []);

  useEffect(() => {
    hydrate().catch((reason) => setError(String(reason)));
  }, [hydrate]);

  useEffect(() => {
    let frame = 0;
    const tick = () => {
      const duration = model?.transport.duration_seconds ?? 1;
      if (engine.current.isPlaying()) {
        playhead.current = Math.min(duration, engine.current.currentTime());
        if (playhead.current >= duration - 0.02) {
          if (loop) {
            engine.current.play(0, trackState).catch((reason) => setError(String(reason)));
            playhead.current = 0;
          } else {
            engine.current.stop();
            setPlaying(false);
          }
        }
      }
      const ratio = Math.max(0, Math.min(1, playhead.current / duration));
      rootRef.current?.style.setProperty("--playhead-ratio", `${ratio}`);
      setTimeLabel(formatTime(playhead.current));
      frame = window.requestAnimationFrame(tick);
    };
    frame = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(frame);
  }, [loop, model, trackState]);

  const submit = useCallback(async (commands: Record<string, unknown>[]) => {
    if (!model) return;
    try {
      const response = await applyCommands(commands as EditCommand[], model.project.revision);
      setModel(response.model);
      setTrackState((current) => ensureTrackState(response.model, current));
      await engine.current.load(response.model);
    } catch (reason) {
      setError(String(reason));
    }
  }, [model]);

  const performUndo = async (kind: "undo" | "redo") => {
    try {
      const response = kind === "undo" ? await undo() : await redo();
      setModel(response.model);
      setTrackState((current) => ensureTrackState(response.model, current));
      await engine.current.load(response.model);
    } catch (reason) {
      setError(String(reason));
    }
  };

  const startStop = async () => {
    if (!model) return;
    if (playing) {
      engine.current.stop();
      setPlaying(false);
      return;
    }
    await engine.current.play(playhead.current, trackState);
    setPlaying(true);
  };

  const stop = () => {
    engine.current.stop();
    playhead.current = 0;
    setPlaying(false);
  };

  const seek = (seconds: number) => {
    playhead.current = Math.max(0, Math.min(model?.transport.duration_seconds ?? 0, seconds));
    if (playing) engine.current.play(playhead.current, trackState).catch((reason) => setError(String(reason)));
  };

  const setTrack = (part: string, patch: Partial<TrackPreviewState[string]>) => {
    setTrackState((current) => {
      const next = { ...current, [part]: { ...current[part], ...patch } };
      engine.current.setMix(next);
      return next;
    });
  };

  if (!model) {
    return <FluentProvider theme={webDarkTheme}><main className="loading">Loading LedgerLine Studio...</main></FluentProvider>;
  }

  const duration = model.transport.duration_seconds;
  const masterWave = model.media.master;

  return (
    <FluentProvider theme={dark ? webDarkTheme : webLightTheme}>
      <main ref={rootRef} className={`studio-shell ${dark ? "theme-dark" : "theme-light"}`}>
        <header className="transport">
          <div className="brand"><span>LedgerLine Studio</span><b>{model.project.title}</b></div>
          <div className="transport-buttons">
            <Tooltip content={playing ? "Pause" : "Play"} relationship="label">
              <Button icon={playing ? <Pause /> : <Play />} appearance="primary" onClick={startStop} />
            </Tooltip>
            <Tooltip content="Stop" relationship="label"><Button icon={<Stop />} onClick={stop} /></Tooltip>
            <Tooltip content="Undo" relationship="label"><Button icon={<ArrowUUpLeft />} disabled={!model.history.can_undo} onClick={() => performUndo("undo")} /></Tooltip>
            <Tooltip content="Redo" relationship="label"><Button icon={<ArrowUUpRight />} disabled={!model.history.can_redo} onClick={() => performUndo("redo")} /></Tooltip>
          </div>
          <time className="time-readout">{timeLabel}</time>
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
        {error && <div className="error-bar" onClick={() => setError("")}>{error}</div>}
        <section className="studio-body">
          <section className="workspace">
            {view === "guide" && (
              <div className="guide-grid">
                <DelegationPanel onApplied={hydrate} />
                <section className="project-brief panel">
                  <header className="panel-heading"><span>PROJECT</span><small>{model.project.measures} measures / {formatTime(duration)}</small></header>
                  <div className="part-list">
                    {model.parts.map((part) => <label key={part.id} className="part-row">
                      <i style={{ background: part.color }} />
                      <span>{part.name}</span>
                      <small>{part.family} / {part.note_count} notes</small>
                    </label>)}
                  </div>
                  <div className="guide-copy">
                    <Text>Ask for musical outcomes: brighter chorus, cleaner bass, more intimate opening, fewer dense chords, or better balance. The agent receives the score model, edit API and validation contract.</Text>
                  </div>
                </section>
              </div>
            )}
            {view !== "guide" && (
              <div className="timeline-wrap">
                <div className="ruler" style={{ width: timelineWidth }}>
                  {model.transport.measures.map((measure) => <button key={measure.number} style={{ left: `${measure.start_seconds / duration * 100}%` }} onClick={() => seek(measure.start_seconds)}>M{measure.number}</button>)}
                  <div className="playhead" />
                </div>
                <div className="timeline-scroll">
                  <div className="timeline-stage" style={{ width: timelineWidth }}>
                    <div className="track-rail">
                      {model.parts.map((part) => <button key={part.id} className={!trackState[part.id]?.mute ? "active" : ""} onClick={() => setTrack(part.id, { mute: !trackState[part.id]?.mute })}>
                        <i style={{ background: part.color }} />{part.name}
                      </button>)}
                    </div>
                    <div className="lanes">
                      {view === "edit" && <>
                        <PianoRoll notes={model.notes} parts={model.parts} measures={model.transport.measures} duration={duration} width={timelineWidth} selectedId={selectedId} visibleParts={visibleParts} onSelect={(note) => setSelectedId(note?.id ?? null)} onEdit={submit} />
                        <VelocityLane notes={model.notes} parts={model.parts} duration={duration} width={timelineWidth} visibleParts={visibleParts} />
                        <Waveform media={masterWave} width={timelineWidth} />
                        {model.media.spectrogram_url && <img className="spectrogram" alt="Aligned spectrum" src={model.media.spectrogram_url} />}
                      </>}
                      {view === "score" && <ScoreView currentTime={() => playhead.current} onSeek={seek} />}
                      <div className="playhead timeline-playhead" />
                    </div>
                  </div>
                </div>
              </div>
            )}
          </section>
          <aside className="right-rail">
            <Inspector note={selectedNote} onEdit={submit} />
            {view === "edit" && <Mixer model={model} state={trackState} onChange={setTrack} onWrite={(part, field, value) => submit([{ type: "update_mix", part, changes: { [field]: value } }])} />}
          </aside>
        </section>
      </main>
    </FluentProvider>
  );
}

function ensureTrackState(model: StudioModel, current: TrackPreviewState): TrackPreviewState {
  return Object.fromEntries(model.parts.map((part) => {
    const mix = model.mix.tracks[part.id] ?? { gain_db: 0, pan: 0 };
    return [part.id, { gain_db: Number(mix.gain_db ?? 0), pan: Number(mix.pan ?? 0), mute: current[part.id]?.mute ?? false, solo: current[part.id]?.solo ?? false }];
  }));
}

function formatTime(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const rest = seconds - minutes * 60;
  return `${minutes}:${rest.toFixed(3).padStart(6, "0")}`;
}
