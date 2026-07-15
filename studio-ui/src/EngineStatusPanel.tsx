import { Badge, Button, Field, ProgressBar, Select, Text, Tooltip } from "@fluentui/react-components";
import { CheckCircle, ClockCountdown, HardDrives, WarningCircle, Waveform } from "@phosphor-icons/react";
import type { ArtifactIdentity, BuildStage, BuildStageStatus, EditCommand, EngineBinding, ProfileCatalogEntry, ReadyProfileCatalogEntry, StudioModel, StudioPart } from "./types";

export function EngineStatusPanel({ model, sourceMode, onCancelJob, onStartJob, onEdit }: { model: StudioModel; sourceMode: "rendered-stems" | "rendered-master" | "preview-synth" | "silent"; onCancelJob?: (id: string) => void; onStartJob?: (kind: "render" | "mix" | "refine") => void; onEdit?: (commands: EditCommand[]) => void }) {
  const build = model.build ?? model.build_state;
  const stages = build?.stages ?? {};
  const activeJob = model.jobs?.find((job) => job.status === "running" || job.status === "queued");
  const bindings = model.engine?.bindings ?? model.build?.engines ?? Object.fromEntries(
    model.parts.filter((part) => part.engine).map((part) => [part.id, part.engine as EngineBinding]),
  );
  const audioStatus = model.media.binding === "aligned"
    ? "ready"
    : model.media.binding === "stale" ? "stale" : "missing";

  return (
    <section className="engine-panel panel" aria-label="Engine and artifact status">
      <header className="panel-heading">
        <span>ENGINE TRUTH</span>
        <StatusBadge status={audioStatus}>{sourceModeLabel(sourceMode)}</StatusBadge>
      </header>
      {onStartJob && (
        <div className="build-actions" aria-label="Build controls">
          <Button size="small" disabled={Boolean(activeJob)} onClick={() => onStartJob("render")}>Rebuild changed</Button>
          <Button size="small" appearance="primary" disabled={Boolean(activeJob) || stages.render?.status !== "ready"} onClick={() => onStartJob("mix")}>Mix master</Button>
          <Button size="small" disabled={Boolean(activeJob)} onClick={() => onStartJob("refine")}>Refresh analysis</Button>
        </div>
      )}
      {activeJob && (
        <div className="job-progress" role="status">
          <div><ClockCountdown size={16} /><b>{activeJob.kind}</b><span>{activeJob.message ?? activeJob.status}</span>{onCancelJob && <Button size="small" appearance="subtle" onClick={() => onCancelJob(activeJob.id)}>Cancel</Button>}</div>
          <ProgressBar value={normalizeProgress(activeJob.progress)} thickness="medium" />
        </div>
      )}
      <div className="build-stages">
        {Object.entries(stages).map(([name, stage]) => (
          <div className="build-stage" key={name}>
            <StatusIcon status={stage.status} />
            <b>{name}</b>
            <StatusBadge status={stage.status}>{stage.status}</StatusBadge>
            {stage.reason && <Tooltip content={stage.reason} relationship="description"><small>{stage.reason}</small></Tooltip>}
          </div>
        ))}
        {!Object.keys(stages).length && (
          <div className="build-stage"><WarningCircle size={16} /><b>Build state</b><small>Legacy project model</small></div>
        )}
      </div>
      <div className="freshness-grid">
        <span>Authored</span><code>{shortHash(build?.authored_revision ?? model.project.authored_revision ?? model.project.revision)}</code>
        <span>Compiled</span><code>{shortHash(build?.compiled_revision ?? model.project.compiled_revision)}</code>
        <span>Rendered</span><code>{shortHash(model.build?.rendered_revision ?? model.media.rendered_revision)}</code>
        <span>Master</span><code>{shortHash(model.media.master?.output_sha ?? model.media.master?.sha256 ?? model.build?.mix_revision)}</code>
      </div>
      {model.refinement && (
        <div className="refinement-status" aria-label="Refinement analysis status">
          <div><b>Refinement analysis</b><StatusBadge status={model.refinement.status}>{model.refinement.status}</StatusBadge></div>
          {model.refinement.url
            ? <a href={model.refinement.url} target="_blank" rel="noreferrer">Open {model.refinement.status === "ready" ? "current" : "stale"} report</a>
            : <small>{model.refinement.reason ?? "No analysis report has been generated."}</small>}
          {model.refinement.reason && model.refinement.url && <small>{model.refinement.reason}</small>}
        </div>
      )}
      {model.media.binding !== "aligned" && (
        <div className="freshness-warning">
          <WarningCircle size={18} weight="fill" />
          <Text>{model.media.stale_reason ?? "The visible score and rendered audio are not from the same project revision."}</Text>
        </div>
      )}
      <div className="engine-bindings">
        {model.parts.map((part) => {
          const binding = bindings[part.id] ?? part.engine;
          return (
            <details className="engine-receipt" data-part-id={part.id} key={part.id}>
              <summary className="engine-row">
                <i style={{ background: part.color }} />
                <span><b>{part.name}</b><small>{engineDescription(binding, part.profile)}</small></span>
                <StatusBadge status={part.render_status ?? binding?.status ?? "unknown"}>{part.render_status ?? binding?.status ?? "unknown"}</StatusBadge>
              </summary>
              <ReceiptDetail binding={binding} part={part} catalog={model.profile_catalog ?? []} activeJob={Boolean(activeJob)} editable={Boolean(model.capabilities.edit_instrument && onEdit)} onProfile={(profile) => {
                const command = instrumentProfileCommand(part, profile, model.profile_catalog ?? [], Boolean(activeJob));
                if (command && onEdit) onEdit([command]);
              }} />
            </details>
          );
        })}
      </div>
    </section>
  );
}

function ReceiptDetail({ binding, part, catalog, activeJob, editable, onProfile }: { binding: EngineBinding | undefined; part: StudioPart; catalog: ProfileCatalogEntry[]; activeJob: boolean; editable: boolean; onProfile: (profile: string) => void }) {
  const renderer = identity(binding?.renderer, binding?.executable, binding?.executable_sha256, binding?.version);
  const instrument = identity(binding?.instrument, binding?.instrument_path, binding?.instrument_sha256);
  const preset = identity(binding?.preset_state ?? binding?.preset ?? binding?.state, binding?.state, binding?.state_sha256);
  const profile = typeof binding?.profile === "object" ? binding.profile : undefined;
  const capabilities = part.profile_capabilities;
  const performance = capabilities
    ? Object.entries(capabilities.performance).map(([name, value]) => {
      const target = value.controller === null ? value.parameter ?? "native" : `CC${value.controller}`;
      return `${name} · ${target} · ${value.minimum}–${value.maximum}`;
    })
    : [];
  const readyProfiles = catalog.filter((item): item is ReadyProfileCatalogEntry => item.status === "ready");
  const errorProfiles = catalog.filter((item) => item.status === "error");
  const selected = readyProfiles.find((item) => item.id === part.profile);
  return <div className="receipt-detail" aria-label={`${part.name} render receipt details`}>
    <div className="profile-editor">
      <Field label="Profile / MIDI preset">
        <Select aria-label={`Profile / MIDI preset for ${part.name}`} value={part.profile} disabled={!editable || activeJob || !readyProfiles.length} onChange={(_, data) => onProfile(data.value)}>
          {!selected && <option value={part.profile} disabled>{part.profile} · current profile not in catalog</option>}
          {readyProfiles.map((profile) => <option key={profile.id} value={profile.id} disabled={profile.id === part.profile}>{profile.name} · {profile.family} · {midiPresetLabel(profile)}</option>)}
          {errorProfiles.map((profile) => <option key={profile.id} value={profile.id} disabled>Unavailable · {profile.id} · {profile.reason}</option>)}
        </Select>
      </Field>
      <div className="profile-summary">
        <b>{selected?.name ?? part.profile}</b>
        <span>{selected ? `${selected.source} · ${selected.family} · ${midiPresetLabel(selected)}` : "Current profile metadata is not available in the catalog."}</span>
        <small>{selected ? `Absolute ${selected.range.absolute_low}–${selected.range.absolute_high} · comfort ${selected.range.comfortable_low}–${selected.range.comfortable_high}` : "Range not recorded"}</small>
      </div>
      <div className="profile-rebuild-note"><WarningCircle size={15} weight="fill" /><span>Changing the profile makes this stem and the master stale. Rebuild before listening approval.</span></div>
      {activeJob && <small className="profile-disabled-reason">Profile changes are locked while a build job is active.</small>}
      {errorProfiles.length > 0 && <details className="profile-errors"><summary>{errorProfiles.length} unavailable profile{errorProfiles.length === 1 ? "" : "s"}</summary>{errorProfiles.map((profile) => <span key={profile.id}><b>{profile.id}</b><small>{profile.reason}</small></span>)}</details>}
    </div>
    <ReceiptRow label="Engine" value={binding?.engine} />
    <ReceiptRow label="Host / format" value={joinRecorded(binding?.host_kind, binding?.plugin_format)} />
    <ReceiptRow label="Renderer path" value={renderer.path} path />
    <ReceiptRow label="Renderer version" value={renderer.version} />
    <ReceiptRow label="Renderer SHA-256" value={renderer.sha256} mono />
    <ReceiptRow label="Instrument path" value={instrument.path} path />
    <ReceiptRow label="Instrument SHA-256" value={instrument.sha256} mono />
    <ReceiptRow label="Preset / state path" value={preset.path} path />
    <ReceiptRow label="Preset / state SHA-256" value={preset.sha256} mono />
    <ReceiptRow label="Receipt profile" value={joinRecorded(profile?.id, profile?.name, profile?.family) ?? (typeof binding?.profile === "string" ? binding.profile : undefined)} />
    <ReceiptRow label="Authored profile" value={part.profile} />
    <ReceiptRow label="Latency / tail" value={binding ? `${recorded(binding.latency_samples, "samples")} / ${recorded(binding.tail_seconds, "s")}` : undefined} />
    <ReceiptRow label="Cache" value={binding?.cache} />
    <ReceiptRow label="Sample rate / block" value={binding ? `${recorded(binding.sample_rate, "Hz")} / ${recorded(binding.block_size, "samples")}` : undefined} />
    <ReceiptRow label="Absolute range" value={capabilities ? `${capabilities.range.absolute_low}–${capabilities.range.absolute_high}` : undefined} />
    <ReceiptRow label="Comfort range" value={capabilities ? `${capabilities.range.comfortable_low}–${capabilities.range.comfortable_high}` : undefined} />
    <ReceiptRow label="Articulations" value={declaredList(capabilities?.articulations)} />
    <ReceiptRow label="Keyswitches" value={capabilities ? declaredList(Object.entries(capabilities.keyswitch_map ?? {}).map(([name, pitch]) => `${name} (${pitch})`)) : undefined} />
    <ReceiptRow label="Performance" value={capabilities ? declaredList(performance) : undefined} />
  </div>;
}

export function instrumentProfileCommand(part: StudioPart, profileId: string, catalog: ProfileCatalogEntry[], activeJob: boolean): EditCommand | null {
  if (activeJob || profileId === part.profile) return null;
  if (!catalog.some((profile) => profile.id === profileId && profile.status === "ready")) return null;
  return { type: "update_instrument", part: part.id, changes: { profile: profileId } };
}

export function midiPresetLabel(profile: ReadyProfileCatalogEntry): string {
  const preset = profile.midi_preset ?? profile.midi;
  return `bank ${preset.bank_msb}:${preset.bank_lsb} · program ${preset.program}`;
}

function ReceiptRow({ label, value, mono = false, path = false }: { label: string; value: unknown; mono?: boolean; path?: boolean }) {
  const text = value === undefined || value === null || value === "" ? "not recorded" : String(value);
  return <div className="receipt-row"><span>{label}</span><code className={`${mono ? "is-mono" : ""} ${path ? "is-path" : ""}`}>{text}</code></div>;
}

function identity(value: unknown, fallbackPath?: string, fallbackSha?: string, fallbackVersion?: string): ArtifactIdentity {
  if (typeof value === "string") return { path: value, sha256: fallbackSha, version: fallbackVersion };
  if (value && typeof value === "object") {
    const item = value as ArtifactIdentity;
    return { ...item, path: item.path ?? fallbackPath, sha256: item.sha256 ?? fallbackSha, version: item.version ?? fallbackVersion };
  }
  return { path: fallbackPath, sha256: fallbackSha, version: fallbackVersion };
}

function joinRecorded(...values: unknown[]): string | undefined {
  const recorded = values.filter((value) => value !== undefined && value !== null && value !== "").map(String);
  return recorded.length ? recorded.join(" · ") : undefined;
}

function recorded(value: unknown, unit: string): string {
  return value === undefined || value === null ? "not recorded" : `${value} ${unit}`;
}

function declaredList(values: string[] | undefined): string | undefined {
  if (!values) return undefined;
  return values.length ? values.join(", ") : "none declared";
}

function StatusIcon({ status }: { status: BuildStageStatus | string }) {
  if (status === "ready") return <CheckCircle className="status-ready" size={16} weight="fill" />;
  if (status === "running" || status === "queued") return <ClockCountdown className="status-busy" size={16} />;
  if (status === "stale" || status === "blocked" || status === "failed") return <WarningCircle className="status-warning" size={16} weight="fill" />;
  return <HardDrives size={16} />;
}

function StatusBadge({ status, children }: { status: BuildStageStatus | string; children: React.ReactNode }) {
  const color = status === "ready"
    ? "success"
    : status === "failed" ? "danger" : status === "running" || status === "queued" ? "informative" : "warning";
  return <Badge appearance="tint" color={color}>{children}</Badge>;
}

function engineDescription(binding: EngineBinding | undefined, profile: string): string {
  if (!binding) return `${profile} / no render receipt`;
  const engine = binding.engine ?? identityLabel(binding.renderer) ?? "unknown engine";
  const instrument = identityLabel(binding.preset_state) ?? identityLabel(binding.preset) ?? identityLabel(binding.instrument) ?? binding.instrument_path ?? profile;
  return `${engine} / ${instrument}`;
}

function identityLabel(value: unknown): string | undefined {
  if (typeof value === "string" && value) return value;
  if (!value || typeof value !== "object") return undefined;
  const item = value as Record<string, unknown>;
  const candidate = item.name ?? item.id ?? item.path ?? item.program;
  if (typeof candidate !== "string" && typeof candidate !== "number") return undefined;
  const text = String(candidate);
  return text.includes("\\") || text.includes("/") ? text.split(/[\\/]/).at(-1) : text;
}

function sourceModeLabel(mode: "rendered-stems" | "rendered-master" | "preview-synth" | "silent"): React.ReactNode {
  if (mode === "rendered-stems") return <><Waveform size={13} /> rendered stems</>;
  if (mode === "rendered-master") return <><Waveform size={13} /> rendered master</>;
  if (mode === "preview-synth") return "preview synth";
  return "no audio";
}

function shortHash(value: unknown): string {
  return typeof value === "string" && value ? value.slice(0, 10) : "not ready";
}

function normalizeProgress(value: number | undefined): number | undefined {
  if (value === undefined) return undefined;
  return value > 1 ? Math.max(0, Math.min(1, value / 100)) : Math.max(0, Math.min(1, value));
}

export function stageSummary(stages: Record<string, BuildStage>): string {
  const counts = Object.values(stages).reduce<Record<string, number>>((result, stage) => {
    result[stage.status] = (result[stage.status] ?? 0) + 1;
    return result;
  }, {});
  return Object.entries(counts).map(([status, count]) => `${count} ${status}`).join(", ");
}
