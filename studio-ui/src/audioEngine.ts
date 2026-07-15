import { versionedMediaUrl } from "./timeline";
import type { StudioModel } from "./types";

export type TrackPreviewState = Record<string, { gain_db: number; pan: number; mute: boolean; solo: boolean }>;

export type MeterLevel = {
  peak: number;
  rms: number;
  peakDb: number;
  rmsDb: number;
  clipped: boolean;
};

export type MeterSnapshot = {
  tracks: Record<string, MeterLevel>;
  master: MeterLevel;
};

type TrackBus = {
  gain: GainNode;
  pan: StereoPannerNode;
  analyser: AnalyserNode;
};

const SILENT_LEVEL: MeterLevel = { peak: 0, rms: 0, peakDb: -96, rmsDb: -96, clipped: false };

export function calculateMeterLevel(samples: Float32Array): MeterLevel {
  if (!samples.length) return { ...SILENT_LEVEL };
  let peak = 0;
  let sum = 0;
  for (const sample of samples) {
    const absolute = Math.abs(sample);
    peak = Math.max(peak, absolute);
    sum += sample * sample;
  }
  const rms = Math.sqrt(sum / samples.length);
  return {
    peak,
    rms,
    peakDb: amplitudeToDb(peak),
    rmsDb: amplitudeToDb(rms),
    clipped: peak >= 0.999,
  };
}

export function resolvePlaybackSource(
  comparison: "current" | "previous",
  hasValidComparison: boolean,
  hasStems: boolean,
  hasCurrentMaster: boolean,
): "previous-master" | "current-stems" | "current-master" | "preview-synth" {
  if (hasValidComparison && hasCurrentMaster) {
    return comparison === "previous" ? "previous-master" : "current-master";
  }
  if (hasStems) return "current-stems";
  if (hasCurrentMaster) return "current-master";
  return "preview-synth";
}

export function comparisonGainDb(model: StudioModel, comparison: "current" | "previous"): number {
  if (comparison !== "previous") return 0;
  const contract = model.review?.ab ?? model.media.ab;
  if (!contract?.available || contract.playback_policy.level_matching === "none") return 0;
  const value = contract.playback_policy.gain_adjustment_db.previous;
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

export function comparisonSourcesReady(contractAvailable: boolean, hasCurrentMaster: boolean, hasPreviousMaster: boolean): boolean {
  return contractAvailable && hasCurrentMaster && hasPreviousMaster;
}

export class AudioEngine {
  private context: AudioContext | null = null;
  private buffers = new Map<string, AudioBuffer>();
  private masterBuffers = new Map<"current" | "previous", AudioBuffer>();
  private sources = new Map<string, AudioBufferSourceNode>();
  private buses = new Map<string, TrackBus>();
  private masterGain: GainNode | null = null;
  private masterAnalyser: AnalyserNode | null = null;
  private synthNodes: OscillatorNode[] = [];
  private startedAt = 0;
  private offset = 0;
  private playing = false;
  private model: StudioModel | null = null;
  private loadController: AbortController | null = null;
  private decodeFailures: string[] = [];
  private playbackMode: "rendered-stems" | "rendered-master" | "preview-synth" | "silent" = "silent";

  async load(model: StudioModel): Promise<void> {
    this.stop();
    this.model = model;
    this.context ??= new AudioContext();
    this.loadController?.abort();
    this.loadController = new AbortController();
    this.buffers.clear();
    this.masterBuffers.clear();
    this.decodeFailures = [];
    const results = await Promise.allSettled(
      model.media.stems.map(async (stem) => {
        const response = await fetch(versionedMediaUrl(stem), { signal: this.loadController?.signal });
        if (!response.ok) throw new Error(`${stem.part}: ${response.status}`);
        const data = await response.arrayBuffer();
        this.buffers.set(stem.part, await this.context!.decodeAudioData(data.slice(0)));
      }),
    );
    results.forEach((result, index) => {
      if (result.status === "rejected" && result.reason?.name !== "AbortError") {
        this.decodeFailures.push(`${model.media.stems[index]?.part ?? "stem"}: ${String(result.reason)}`);
      }
    });
    const masters = [
      ["current", model.media.master],
      ["previous", model.media.previous_master],
    ] as const;
    const masterResults = await Promise.allSettled(masters.map(async ([kind, media]) => {
      if (!media) return;
      const response = await fetch(versionedMediaUrl(media), { signal: this.loadController?.signal });
      if (!response.ok) throw new Error(`${kind} master: ${response.status}`);
      const data = await response.arrayBuffer();
      this.masterBuffers.set(kind, await this.context!.decodeAudioData(data.slice(0)));
    }));
    masterResults.forEach((result, index) => {
      if (result.status === "rejected" && result.reason?.name !== "AbortError") {
        this.decodeFailures.push(`${masters[index][0]} master: ${String(result.reason)}`);
      }
    });
  }

  updateModel(model: StudioModel): void {
    this.model = model;
  }

  async play(offset: number, settings: TrackPreviewState, comparison: "current" | "previous" = "current"): Promise<void> {
    if (!this.context || !this.model) return;
    this.stop();
    await this.context.resume();
    this.createGraph();
    this.offset = Math.max(0, offset);
    this.startedAt = this.context.currentTime;
    this.playing = true;
    const playbackSource = resolvePlaybackSource(
      comparison,
      this.comparisonAvailable(),
      Boolean(this.buffers.size),
      this.masterBuffers.has("current"),
    );
    this.playbackMode = playbackSource === "current-stems"
      ? "rendered-stems"
      : playbackSource === "preview-synth" ? "preview-synth" : "rendered-master";
    if (playbackSource === "previous-master") {
      this.playMaster("previous", comparisonGainDb(this.model, "previous"));
    } else if (playbackSource === "current-stems") {
      for (const [part, buffer] of this.buffers) {
        const source = this.context.createBufferSource();
        source.buffer = buffer;
        source.connect(this.busFor(part).gain);
        this.sources.set(part, source);
        source.start(0, Math.min(this.offset, Math.max(0, buffer.duration - 0.001)));
      }
      this.setMix(settings);
    } else if (playbackSource === "current-master") {
      this.playMaster("current");
    } else {
      this.schedulePreviewSynth(settings);
    }
  }

  stop(): void {
    for (const source of this.sources.values()) {
      try { source.stop(); } catch { /* already stopped */ }
      source.disconnect();
    }
    for (const source of this.synthNodes) {
      try { source.stop(); } catch { /* already stopped */ }
      source.disconnect();
    }
    for (const bus of this.buses.values()) {
      bus.gain.disconnect();
      bus.pan.disconnect();
      bus.analyser.disconnect();
    }
    this.masterGain?.disconnect();
    this.masterAnalyser?.disconnect();
    this.sources.clear();
    this.buses.clear();
    this.synthNodes = [];
    this.masterGain = null;
    this.masterAnalyser = null;
    this.playing = false;
  }

  currentTime(): number {
    if (!this.playing || !this.context) return this.offset;
    return this.offset + (this.context.currentTime - this.startedAt);
  }

  isPlaying(): boolean { return this.playing; }

  sourceMode(): "rendered-stems" | "rendered-master" | "preview-synth" | "silent" {
    if (this.playing) return this.playbackMode;
    if (this.comparisonAvailable()) return "rendered-master";
    if (this.buffers.size) return "rendered-stems";
    if (this.masterBuffers.has("current")) return "rendered-master";
    if (this.model?.notes.length) return "preview-synth";
    return "silent";
  }

  failures(): string[] { return [...this.decodeFailures]; }

  comparisonAvailable(): boolean {
    const contract = this.model?.review?.ab ?? this.model?.media.ab;
    return comparisonSourcesReady(Boolean(contract?.available), this.masterBuffers.has("current"), this.masterBuffers.has("previous"));
  }

  setMix(settings: TrackPreviewState): void {
    if (!this.context) return;
    const soloed = Object.values(settings).some((item) => item.solo);
    for (const [part, bus] of this.buses) {
      const item = settings[part];
      const audible = item && !item.mute && (!soloed || item.solo);
      const amplitude = audible ? Math.pow(10, item.gain_db / 20) : 0;
      bus.gain.gain.setTargetAtTime(amplitude, this.context.currentTime, 0.015);
      bus.pan.pan.setTargetAtTime(item?.pan ?? 0, this.context.currentTime, 0.015);
    }
  }

  meters(): MeterSnapshot {
    const tracks: Record<string, MeterLevel> = {};
    for (const [part, bus] of this.buses) tracks[part] = readAnalyser(bus.analyser);
    return {
      tracks,
      master: this.masterAnalyser ? readAnalyser(this.masterAnalyser) : { ...SILENT_LEVEL },
    };
  }

  private createGraph(): void {
    if (!this.context) return;
    this.masterGain = this.context.createGain();
    this.masterAnalyser = this.context.createAnalyser();
    configureAnalyser(this.masterAnalyser);
    this.masterGain.connect(this.masterAnalyser).connect(this.context.destination);
  }

  private busFor(part: string): TrackBus {
    const existing = this.buses.get(part);
    if (existing) return existing;
    if (!this.context || !this.masterGain) throw new Error("Audio graph is not ready.");
    const gain = this.context.createGain();
    const pan = this.context.createStereoPanner();
    const analyser = this.context.createAnalyser();
    configureAnalyser(analyser);
    gain.connect(pan).connect(analyser).connect(this.masterGain);
    const bus = { gain, pan, analyser };
    this.buses.set(part, bus);
    return bus;
  }

  private playMaster(kind: "current" | "previous", gainDb = 0): void {
    if (!this.context || !this.masterGain) return;
    const buffer = this.masterBuffers.get(kind);
    if (!buffer) return;
    const source = this.context.createBufferSource();
    source.buffer = buffer;
    this.masterGain.gain.value = Math.pow(10, gainDb / 20);
    source.connect(this.masterGain);
    this.sources.set(`__${kind}_master__`, source);
    source.start(0, Math.min(this.offset, Math.max(0, buffer.duration - 0.001)));
  }

  private schedulePreviewSynth(settings: TrackPreviewState): void {
    if (!this.context || !this.model) return;
    const soloed = Object.values(settings).some((item) => item.solo);
    for (const note of this.model.notes) {
      if (note.end_seconds <= this.offset) continue;
      const track = settings[note.part];
      if (!track || track.mute || (soloed && !track.solo)) continue;
      const oscillator = this.context.createOscillator();
      const gain = this.context.createGain();
      const start = this.context.currentTime + Math.max(0, note.start_seconds - this.offset);
      const end = this.context.currentTime + Math.max(0.03, note.end_seconds - this.offset);
      oscillator.frequency.value = 440 * Math.pow(2, (note.pitch - 69) / 12);
      oscillator.type = "triangle";
      const level = (note.velocity / 127) * 0.08;
      gain.gain.setValueAtTime(0.0001, start);
      gain.gain.exponentialRampToValueAtTime(Math.max(0.0002, level), start + 0.015);
      gain.gain.setValueAtTime(Math.max(0.0002, level), Math.max(start + 0.016, end - 0.04));
      gain.gain.exponentialRampToValueAtTime(0.0001, end);
      oscillator.connect(gain).connect(this.busFor(note.part).gain);
      oscillator.start(start);
      oscillator.stop(end + 0.01);
      this.synthNodes.push(oscillator);
    }
    this.setMix(settings);
  }
}

function configureAnalyser(analyser: AnalyserNode): void {
  analyser.fftSize = 2048;
  analyser.smoothingTimeConstant = 0.35;
}

function readAnalyser(analyser: AnalyserNode): MeterLevel {
  const samples = new Float32Array(analyser.fftSize);
  analyser.getFloatTimeDomainData(samples);
  return calculateMeterLevel(samples);
}

function amplitudeToDb(value: number): number {
  return value <= 0.0000158 ? -96 : 20 * Math.log10(value);
}
