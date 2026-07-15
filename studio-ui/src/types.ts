export type Measure = {
  number: number;
  start_tick: number;
  end_tick: number;
  start_seconds: number;
  end_seconds: number;
  beats: number;
  beat_type: number;
};

export type TempoSegment = {
  start_tick: number;
  end_tick: number;
  start_seconds: number;
  duration_seconds: number;
  bpm: number;
  end_bpm: number | null;
  curve: "step" | "linear" | string;
};

export type LoopRange = {
  start_seconds: number;
  end_seconds: number;
};

export type BuildStageStatus = "queued" | "running" | "ready" | "stale" | "blocked" | "failed" | "cancelled" | "unknown";

export type BuildStage = {
  status: BuildStageStatus;
  input_revision?: string;
  output_revision?: string;
  progress?: number;
  reason?: string;
  message?: string;
  updated_at?: string;
};

export type ArtifactIdentity = {
  path?: string;
  sha256?: string;
  version?: string;
  name?: string;
  id?: string;
  program?: string | number;
};

export type EngineBinding = {
  engine?: string;
  host_kind?: string;
  plugin_format?: string;
  renderer?: string | ArtifactIdentity;
  executable?: string;
  version?: string;
  executable_sha256?: string;
  instrument?: string | ArtifactIdentity;
  instrument_path?: string;
  instrument_sha256?: string;
  preset?: string | Record<string, unknown>;
  preset_state?: string | ArtifactIdentity;
  bank?: number;
  program?: number;
  profile?: string | { id?: string; name?: string; family?: string; midi?: Record<string, unknown> };
  state?: string;
  state_sha256?: string;
  cache?: "hit" | "miss" | string;
  latency_samples?: number;
  tail_seconds?: number;
  sample_rate?: number;
  block_size?: number;
  status?: "ready" | "missing" | "failed" | "unknown" | string;
  message?: string;
  articulations?: string[];
  supported_controls?: string[];
};

export type StudioPart = {
  id: string;
  name: string;
  profile: string;
  family: string;
  staff_count: number;
  note_count: number;
  color: string;
  editable?: boolean;
  voice_count?: number;
  voices?: string[];
  engine?: EngineBinding;
  render_status?: BuildStageStatus;
  render_revision?: string;
  articulations?: string[];
  supported_controls?: string[];
  profile_capabilities?: {
    range: {
      absolute_low: string;
      absolute_high: string;
      comfortable_low: string;
      comfortable_high: string;
      transposition: number;
    };
    midi?: { bank_msb: number; bank_lsb: number; program: number };
    articulations: string[];
    keyswitches: string[];
    keyswitch_map?: Record<string, string>;
    performance_parameters: string[];
    performance: Record<string, {
      type: string;
      controller: number | null;
      parameter: string | null;
      minimum: number;
      maximum: number;
      default: number;
    }>;
  };
};

export type ReadyProfileCatalogEntry = {
  id: string;
  name: string;
  family: string;
  source: "built-in" | "project" | string;
  status: "ready";
  range: {
    absolute_low: string;
    absolute_high: string;
    comfortable_low: string;
    comfortable_high: string;
    transposition: number;
  };
  midi: { bank_msb: number; bank_lsb: number; program: number };
  midi_preset: { bank_msb: number; bank_lsb: number; program: number };
  articulations: string[];
  keyswitches: string[];
  keyswitch_map: Record<string, string>;
  performance_parameters: string[];
  performance: Record<string, {
    type: string;
    controller: number | null;
    parameter: string | null;
    minimum: number;
    maximum: number;
    default: number;
  }>;
};

export type ErrorProfileCatalogEntry = {
  id: string;
  source: "built-in" | "project" | string;
  status: "error";
  reason: string;
  diagnostics?: Record<string, unknown>[];
};

export type ProfileCatalogEntry = ReadyProfileCatalogEntry | ErrorProfileCatalogEntry;

export type StudioExpression = {
  pitch_cents: number;
  curves: Record<string, { at: number; value: number }[]>;
  gestures: ({ type: string } & Record<string, string | number>)[];
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
  pitch_cents: number;
  expression: StudioExpression | null;
  event_id?: string;
  editable?: boolean;
  out_of_range?: boolean;
  capability_error?: string | null;
};

export type StudioControl = {
  id: string;
  control_id?: string;
  control_index?: number;
  point_id?: string;
  point_index?: number;
  lane?: string;
  part?: string;
  measure?: number;
  voice?: string;
  staff?: number;
  kind: "cc" | "pedal" | "pitch_bend" | "tempo" | "articulation" | "keyswitch" | "automation" | string;
  controller?: number;
  semantic?: string;
  value: number | string | boolean;
  start_tick?: number;
  start_seconds: number;
  end_seconds?: number;
  curve?: "step" | "linear" | "bezier" | string;
  articulation?: string;
  lane_target?: string;
  unit?: string;
  lane_interpolation?: string;
  editable?: boolean;
};

export type StudioTempoPoint = {
  source_index: number;
  at: string;
  seconds: number;
  bpm: number;
  ramp?: {
    to: string;
    bpm: number;
    curve: string;
  };
};

export type PeakMedia = {
  url: string;
  path: string;
  duration_seconds: number;
  sample_rate: number;
  channels: number;
  sample_width: number;
  peaks: [number, number][];
  output_sha?: string;
  sha256?: string;
  version?: string;
  project_revision?: string;
  artifact_revision?: string;
  freshness?: "ready" | "stale" | "missing" | "failed" | string;
  stale_reason?: string;
  status?: "ready" | "stale" | "missing" | "failed" | string;
  kind?: "master" | "stem" | "previous-master";
  label?: string;
  spectrogram_url?: string | null;
  source_revision?: string;
  measurement?: LoudnessMeasurement;
};

export type LoudnessMeasurement = {
  integrated_lufs?: number;
  true_peak_dbtp?: number;
  loudness_range_lu?: number;
  [key: string]: unknown;
};

export type EqBand = {
  frequency_hz: number;
  gain_db: number;
  q: number;
};

export type EqProcessor = {
  type: "eq";
  highpass_hz?: number;
  lowpass_hz?: number;
  bands: EqBand[];
};

export type CompressorProcessor = {
  type: "compressor";
  threshold_db: number;
  ratio: number;
  attack_ms: number;
  release_ms: number;
  makeup_db: number;
  knee_db: number;
};

export type ReverbProcessor = {
  type: "reverb";
  in_gain: number;
  out_gain: number;
  delays_ms: string;
  decays: string;
};

export type MixProcessor = EqProcessor | CompressorProcessor | ReverbProcessor;

export type MixNode = {
  gain_db: number;
  pan: number;
  output: string;
  sends: Record<string, number>;
  inserts: MixProcessor[];
};

export type MasterMixNode = {
  gain_db: number;
  target_lufs: number;
  true_peak_ceiling_db: number;
  loudness_range_lu: number;
  loudness_tolerance_lu: number;
  inserts: MixProcessor[];
};

export type MasterReport = {
  status: BuildStageStatus | string;
  bound_to_current_revision: boolean;
  source_revision: string | null;
  output_sha256?: string | null;
  target_lufs: number;
  true_peak_ceiling_dbtp: number;
  loudness_range_target_lu: number;
  loudness_tolerance_lu: number;
  integrated_lufs: number | null;
  true_peak_dbtp: number | null;
  loudness_range_lu: number | null;
  premaster_measurement?: LoudnessMeasurement | null;
  final_measurement?: LoudnessMeasurement | null;
};

export type ABGainAdjustment = {
  current: number;
  previous: number;
  requested_previous: number | null;
  bounds: [number, number];
  limited: boolean;
  peak_limited: boolean;
};

export type ABContract = {
  schema_version: string;
  available: boolean;
  unavailable_reason: string | null;
  selection_mode: "exclusive" | string;
  default_selection: "current" | "previous";
  playback_policy: {
    simultaneous_playback: boolean;
    stop_before_switch: boolean;
    crossfade_ms: number;
    level_matching: "integrated-lufs" | "none" | string;
    gain_adjustment_db: ABGainAdjustment;
  };
  alignment: {
    start_seconds: number;
    common_duration_seconds: number | null;
  };
  current: PeakMedia | null;
  previous: PeakMedia | null;
};

export type SourceImpact = {
  changed: boolean;
  files: {
    path: string;
    before_sha256: string | null;
    after_sha256: string | null;
  }[];
  parts: string[];
  measures: {
    part: string;
    measure: number;
  }[];
  aspects: string[];
  targets: string[];
  fields: string[];
};

export type StudioReview = {
  schema_version: string;
  status: "none" | "current" | "superseded" | string;
  current_revision: string;
  transaction_matches_current_revision?: boolean;
  latest_transaction: null | {
    id?: string;
    operation?: string;
    created_at?: string;
    from_revision?: string;
    to_revision?: string;
    command_count?: number;
    command_types?: string[];
  };
  impact: SourceImpact;
  ab?: ABContract;
};

export type StudioJob = {
  id: string;
  kind: string;
  status: BuildStageStatus;
  progress?: number;
  message?: string;
  parts?: string[];
  created_at?: string;
  updated_at?: string;
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
    authored_revision?: string;
    compiled_revision?: string;
    prepared_ids?: boolean;
  };
  transport: {
    duration_seconds: number;
    tempo_segments: TempoSegment[];
    measures: Measure[];
    musical_duration_seconds?: number;
    render_duration_seconds?: number;
  };
  parts: StudioPart[];
  profile_catalog: ProfileCatalogEntry[];
  notes: StudioNote[];
  controls?: StudioControl[];
  tempo?: StudioTempoPoint[];
  automation?: StudioControl[];
  mix: {
    format?: number;
    tracks: Record<string, MixNode>;
    buses: Record<string, MixNode>;
    master: MasterMixNode;
    authored?: Record<string, unknown>;
    source?: {
      path: string;
      sha256: string;
      authored_revision: string;
    };
    master_report?: MasterReport;
  };
  media: {
    master: PeakMedia | null;
    stems: (PeakMedia & { part: string })[];
    spectrogram_url: string | null;
    binding: "aligned" | "midi-only" | "stale";
    project_revision?: string;
    rendered_revision?: string;
    refinement_revision?: string;
    version?: string;
    stale_parts?: string[];
    stale_reason?: string;
    previous_master?: PeakMedia | null;
    ab?: ABContract;
  };
  review?: StudioReview;
  refinement?: {
    status: BuildStageStatus | string;
    reason?: string | null;
    authored_revision?: string | null;
    report_status?: string | null;
    gates?: Record<string, unknown> | null;
    output_sha256?: string | null;
    url?: string | null;
  };
  score: { url: string; format: string };
  capabilities: Record<string, boolean>;
  history: { can_undo: boolean; can_redo: boolean };
  build_state?: {
    project_revision?: string;
    authored_revision?: string;
    compiled_revision?: string;
    stages: Record<string, BuildStage>;
  };
  build?: {
    schema_version?: string;
    status?: string;
    authored_revision?: string;
    compiled_revision?: string | null;
    rendered_revision?: string | null;
    mix_revision?: string | null;
    stages?: Record<string, BuildStage & { parts?: Record<string, BuildStage>; revision?: string; compiled_revision?: string }>;
    engines?: Record<string, EngineBinding>;
  };
  jobs?: StudioJob[];
  engine?: {
    status?: "ready" | "degraded" | "missing" | "unknown" | string;
    message?: string;
    bindings?: Record<string, EngineBinding>;
  };
};

export type DelegationPreviewImpact = {
  changed: boolean;
  files: { path: string; before_sha256?: string | null; after_sha256?: string | null }[];
  parts: string[];
  measures: { part: string; measure: number }[];
  aspects: string[];
  targets: string[];
  fields: string[];
  counts: {
    files: number;
    parts: number;
    measures: number;
    aspects: number;
    targets: number;
    fields: number;
  };
};

export type DelegationScoreNote = {
  id?: string;
  event_id?: string;
  pitch_index?: number;
  part?: string;
  measure?: number;
  voice?: string;
  pitch?: number | string;
  start?: string;
  duration?: string;
  velocity?: number;
  articulation?: string | null;
  [field: string]: unknown;
};

export type DelegationProposalPreview = {
  schema_version: string;
  status: "ready" | "no-actions" | string;
  base_revision: string;
  result_revision: string;
  command_count: number;
  command_types: string[];
  validation: {
    status: string;
    contract?: string;
    compiled?: boolean;
  };
  impact: DelegationPreviewImpact;
  yaml_diff: {
    format: string;
    text: string;
    files: string[];
    included_files?: string[];
    omitted_files?: string[];
    truncated: boolean;
    truncated_at_file?: string | null;
    line_count: number;
    byte_count: number;
    limits: {
      max_files: number;
      max_lines: number;
      max_bytes: number;
      context_lines?: number;
    };
  };
  score_diff: {
    identity: {
      scheme: string;
      complete: boolean;
      fallback_count: number;
      fallback_scheme?: string;
    };
    added: DelegationScoreNote[];
    removed: DelegationScoreNote[];
    changed: {
      id?: string;
      event_id?: string;
      pitch_index?: number;
      part?: string;
      measure?: number;
      changed_fields: string[];
      before?: DelegationScoreNote;
      after?: DelegationScoreNote;
    }[];
    counts: { added: number; removed: number; changed: number; total: number };
  };
};

export type DelegationProduction = {
  status?: string;
  job_id?: string | null;
  job?: StudioJob | null;
  build?: {
    source_revision?: string | null;
    compiled_revision?: string | null;
    rendered_revision?: string | null;
    mix_revision?: string | null;
    stages?: Record<string, { status?: string; reason?: string }>;
  };
  revisions?: {
    authored_revision?: string | null;
    compiled_revision?: string | null;
    rendered_revision?: string | null;
    mix_revision?: string | null;
  };
  ab?: {
    available?: boolean;
    unavailable_reason?: string | null;
    checked_at?: string | null;
    source_revision?: string | null;
    level_matching?: string | null;
    current?: { source_revision?: string | null; sha256?: string | null; integrated_lufs?: number | null } | null;
    previous?: { source_revision?: string | null; sha256?: string | null; integrated_lufs?: number | null } | null;
    detail?: string;
  };
  listening_checks?: string[];
  listening?: {
    status?: string;
    checks?: string[];
    reason?: string;
    note?: string;
    feedback?: string;
    accepted_at?: string;
    requested_at?: string;
    revision?: string;
  };
  error?: { type?: string; message?: string } | null;
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
  proposal_preview?: DelegationProposalPreview | null;
  approval_token: string | null;
  created_at: string;
  updated_at: string;
  result?: ({
    status?: string;
    source_revision?: string;
    source?: Record<string, unknown>;
    production?: DelegationProduction;
  } & Record<string, unknown>) | null;
  agent_command?: string;
  questions?: string[];
  answers?: string[];
  base_revision?: string;
  message?: string;
  error?: string;
  accepted_at?: string;
  accepted_revision?: string;
  acceptance?: { note?: string; accepted_at?: string; revision?: string };
  listening_history?: {
    action: "accept" | "revise" | string;
    note?: string;
    feedback?: string;
    accepted_at?: string;
    requested_at?: string;
    revision?: string;
  }[];
};

export type EditCommand = Record<string, unknown> & { type: string };

export type LaneKind = "velocity" | "pitch_cents" | "cc1" | "cc11" | "pedal" | "keyswitch" | "tempo" | "articulation" | `automation:${string}`;

export type TimelineSelection = {
  noteIds: Set<string>;
  controlIds: Set<string>;
  anchorId: string | null;
};
