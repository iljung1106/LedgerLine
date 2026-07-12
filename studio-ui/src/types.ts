export type Measure = {
  number: number;
  start_tick: number;
  end_tick: number;
  start_seconds: number;
  end_seconds: number;
  beats: number;
  beat_type: number;
};

export type StudioPart = {
  id: string;
  name: string;
  profile: string;
  family: string;
  staff_count: number;
  note_count: number;
  color: string;
};

export type StudioNote = {
  id: string;
  part: string;
  measure: number;
  voice: string;
  event_index: number;
  pitch_index: number;
  pitch: number;
  written_pitch: string;
  start_tick: number;
  end_tick: number;
  start_seconds: number;
  end_seconds: number;
  duration: string;
  velocity: number;
  dynamic: string | null;
  articulation: string | null;
  staff: number;
  expression: boolean;
};

export type PeakMedia = {
  url: string;
  path: string;
  duration_seconds: number;
  sample_rate: number;
  channels: number;
  sample_width: number;
  peaks: [number, number][];
};

export type StudioModel = {
  schema_version: string;
  status: string;
  csrf_token: string;
  project: {
    root: string;
    title: string;
    revision: string;
    measures: number;
    duration_seconds: number;
    sample_rate: number;
  };
  transport: {
    duration_seconds: number;
    tempo_segments: { start_seconds: number; bpm: number }[];
    measures: Measure[];
  };
  parts: StudioPart[];
  notes: StudioNote[];
  mix: {
    tracks: Record<string, { gain_db: number; pan: number; output: string; sends: Record<string, number>; inserts: Record<string, unknown>[] }>;
    buses: Record<string, unknown>;
    master: Record<string, unknown>;
  };
  media: {
    master: PeakMedia | null;
    stems: (PeakMedia & { part: string })[];
    spectrogram_url: string | null;
    binding: "aligned" | "midi-only" | "stale";
  };
  score: { url: string; format: string };
  capabilities: Record<string, boolean>;
  history: { can_undo: boolean; can_redo: boolean };
};

export type Delegation = {
  id: string;
  status: string;
  goal: string;
  context: string;
  constraints: string[];
  autonomy: "review" | "safe-auto";
  proposal: null | {
    summary: string;
    reasoning?: string;
    actions: Record<string, unknown>[];
    listening_check?: string;
    questions?: string[];
  };
  approval_token: string | null;
  created_at: string;
  updated_at: string;
  result?: Record<string, unknown> | null;
  agent_command?: string;
};

export type EditCommand = Record<string, unknown> & { type: string };
