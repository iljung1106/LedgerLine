import type { StudioModel } from "./types";

export type TrackPreviewState = Record<string, { gain_db: number; pan: number; mute: boolean; solo: boolean }>;

export class AudioEngine {
  private context: AudioContext | null = null;
  private buffers = new Map<string, AudioBuffer>();
  private sources = new Map<string, AudioBufferSourceNode>();
  private gains = new Map<string, GainNode>();
  private panners = new Map<string, StereoPannerNode>();
  private synthNodes: OscillatorNode[] = [];
  private startedAt = 0;
  private offset = 0;
  private playing = false;
  private model: StudioModel | null = null;

  async load(model: StudioModel): Promise<void> {
    this.model = model;
    this.context ??= new AudioContext();
    this.buffers.clear();
    await Promise.all(
      model.media.stems.map(async (stem) => {
        const response = await fetch(stem.url);
        const data = await response.arrayBuffer();
        this.buffers.set(stem.part, await this.context!.decodeAudioData(data));
      }),
    );
  }

  async play(offset: number, settings: TrackPreviewState): Promise<void> {
    if (!this.context || !this.model) return;
    this.stop();
    await this.context.resume();
    this.offset = Math.max(0, offset);
    this.startedAt = this.context.currentTime;
    this.playing = true;
    if (this.buffers.size) {
      for (const [part, buffer] of this.buffers) {
        const source = this.context.createBufferSource();
        const gain = this.context.createGain();
        const pan = this.context.createStereoPanner();
        source.buffer = buffer;
        source.connect(gain).connect(pan).connect(this.context.destination);
        this.sources.set(part, source);
        this.gains.set(part, gain);
        this.panners.set(part, pan);
        source.start(0, Math.min(this.offset, buffer.duration));
      }
      this.setMix(settings);
    } else {
      this.schedulePreviewSynth(settings);
    }
  }

  stop(): void {
    for (const source of this.sources.values()) {
      try { source.stop(); } catch { /* already stopped */ }
    }
    for (const source of this.synthNodes) {
      try { source.stop(); } catch { /* already stopped */ }
    }
    this.sources.clear();
    this.gains.clear();
    this.panners.clear();
    this.synthNodes = [];
    this.playing = false;
  }

  currentTime(): number {
    if (!this.playing || !this.context) return this.offset;
    return this.offset + (this.context.currentTime - this.startedAt);
  }

  isPlaying(): boolean { return this.playing; }

  setMix(settings: TrackPreviewState): void {
    const soloed = Object.values(settings).some((item) => item.solo);
    for (const [part, gainNode] of this.gains) {
      const item = settings[part];
      const audible = item && !item.mute && (!soloed || item.solo);
      const amplitude = audible ? Math.pow(10, item.gain_db / 20) : 0;
      gainNode.gain.setTargetAtTime(amplitude, this.context!.currentTime, 0.015);
      this.panners.get(part)?.pan.setTargetAtTime(item?.pan ?? 0, this.context!.currentTime, 0.015);
    }
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
      const pan = this.context.createStereoPanner();
      const start = this.context.currentTime + Math.max(0, note.start_seconds - this.offset);
      const end = this.context.currentTime + Math.max(0.03, note.end_seconds - this.offset);
      oscillator.frequency.value = 440 * Math.pow(2, (note.pitch - 69) / 12);
      oscillator.type = "triangle";
      const level = Math.pow(10, track.gain_db / 20) * (note.velocity / 127) * 0.08;
      gain.gain.setValueAtTime(0.0001, start);
      gain.gain.exponentialRampToValueAtTime(Math.max(0.0002, level), start + 0.015);
      gain.gain.setValueAtTime(Math.max(0.0002, level), Math.max(start + 0.016, end - 0.04));
      gain.gain.exponentialRampToValueAtTime(0.0001, end);
      pan.pan.value = track.pan;
      oscillator.connect(gain).connect(pan).connect(this.context.destination);
      oscillator.start(start);
      oscillator.stop(end + 0.01);
      this.synthNodes.push(oscillator);
    }
  }
}
