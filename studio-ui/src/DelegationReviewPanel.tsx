import { Badge, Button, Field, Select, Textarea } from "@fluentui/react-components";
import { ArrowRight, Check, MagicWand, Robot, WarningCircle, X } from "@phosphor-icons/react";
import { useCallback, useEffect, useState } from "react";
import {
  acceptDelegation,
  answerDelegation,
  applyDelegation,
  createDelegation,
  listDelegations,
  rejectDelegation,
  reviseDelegation,
} from "./api";
import type {
  Delegation,
  DelegationProduction,
  DelegationProposalPreview,
  DelegationScoreNote,
} from "./types";

type Gate = { ready: boolean; reason: string | null };

export function proposalApplyGate(task: Delegation, currentRevision = ""): Gate {
  const preview = task.proposal_preview;
  if (task.status !== "proposed") return { ready: false, reason: "The proposal is not awaiting approval." };
  if (!preview) return { ready: false, reason: "No isolated proposal preview was recorded." };
  if (preview.status !== "ready") return { ready: false, reason: `Preview status is ${preview.status}; only a ready preview can be applied.` };
  if (preview.validation?.status !== "ok" || preview.validation?.compiled !== true) {
    return { ready: false, reason: "The preview did not pass the StudioSession validation contract." };
  }
  if (!preview.result_revision) return { ready: false, reason: "The preview has no result revision receipt." };
  if (task.base_revision && preview.base_revision !== task.base_revision) {
    return { ready: false, reason: "The proposal and preview are based on different revisions." };
  }
  if (currentRevision && preview.base_revision !== currentRevision) {
    return { ready: false, reason: "The project changed after this preview was generated." };
  }
  return { ready: true, reason: null };
}

export function delegationDisplayStatus(task: Delegation): string {
  const productionStatus = task.result?.production?.listening?.status;
  if (productionStatus === "revision-requested") return productionStatus;
  return task.status;
}

export function productionListeningGate(task: Delegation, currentRevision = ""): Gate {
  const production = task.result?.production;
  const sourceRevision = task.result?.source_revision;
  if (task.status !== "ready-for-listening" || production?.status !== "ready-for-listening") {
    return { ready: false, reason: productionStateMessage(task) };
  }
  if (!sourceRevision) return { ready: false, reason: "Production is missing its authored source revision." };
  if (currentRevision && sourceRevision !== currentRevision) {
    return { ready: false, reason: "Production is stale because the project revision has changed." };
  }
  const revisions = production.revisions;
  if (!revisions || [revisions.authored_revision, revisions.compiled_revision, revisions.rendered_revision, revisions.mix_revision].some((value) => !value)) {
    return { ready: false, reason: "Production does not have complete compile, render, and mix receipts." };
  }
  return { ready: true, reason: null };
}

export function productionStateMessage(task: Delegation): string {
  const status = task.result?.production?.status ?? task.result?.status ?? task.status;
  const error = task.result?.production?.error?.message;
  if (error) return error;
  switch (status) {
    case "building":
    case "queued":
    case "running": return "Production is rebuilding. Listening approval will unlock after current compile, render, and mix receipts exist.";
    case "rebuild-required": return "The source edit exists, but its production artifacts are stale. Rebuild before making a listening decision.";
    case "build-failed":
    case "failed":
    case "error": return "The production build failed. Inspect the build error, fix it, and rebuild.";
    case "build-cancelled":
    case "cancelled": return "The production build was cancelled and cannot be approved.";
    case "accepted": return "This production revision was accepted after listening.";
    case "revision-requested": return "Listening feedback was returned to the agent for another proposal.";
    default: return `Production is ${status}.`;
  }
}

function statusColor(status: string): "success" | "danger" | "warning" | "informative" {
  if (["applied", "ready", "ready-for-listening", "accepted"].includes(status)) return "success";
  if (["rejected", "failed", "build-failed", "build-cancelled", "error"].includes(status)) return "danger";
  if (["needs-direction", "rebuild-required", "stale", "revision-requested"].includes(status)) return "warning";
  return "informative";
}

function shortRevision(value: string | null | undefined): string {
  return value ? value.slice(0, 12) : "not recorded";
}

function readable(value: string | null | undefined): string {
  return value ? value.replaceAll("-", " ") : "not recorded";
}

function noteLabel(note: DelegationScoreNote | undefined): string {
  if (!note) return "note details not recorded";
  const part = note.part ?? "unknown part";
  const measure = note.measure === undefined ? "?" : note.measure;
  const pitch = note.pitch === undefined ? "unknown pitch" : String(note.pitch);
  return `${part} · M${measure} · ${pitch}`;
}

export function DelegationPanel({ onApplied, currentRevision = "" }: { onApplied: () => void; currentRevision?: string }) {
  const [tasks, setTasks] = useState<Delegation[]>([]);
  const [goal, setGoal] = useState("");
  const [context, setContext] = useState("");
  const [autonomy, setAutonomy] = useState("review");
  const [busy, setBusy] = useState(false);
  const [actingTask, setActingTask] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [listeningNotes, setListeningNotes] = useState<Record<string, string>>({});
  const refresh = useCallback(() => listDelegations().then(setTasks).catch((error) => setMessage(String(error))), []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(refresh, 2500);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const act = async (task: Delegation, operation: () => Promise<Delegation>, success: string, refreshModel = false) => {
    setActingTask(task.id);
    try {
      await operation();
      setMessage(success);
      await refresh();
      if (refreshModel) onApplied();
    } catch (error) {
      setMessage(String(error));
    } finally {
      setActingTask(null);
    }
  };

  return <section className="delegate panel" aria-label="Delegate work to AI">
    <header className="delegate-hero"><Robot size={28} weight="duotone" /><div><b>Delegate to your music agent</b><span>Describe the musical result. The agent handles notes, orchestration and production details.</span></div></header>
    <div className="delegate-form">
      <Field label="What should change?"><Textarea resize="vertical" value={goal} onChange={(_, data) => setGoal(data.value)} placeholder="Make the second half more emotionally intense, keep the opening intimate, and preserve the cello melody." /></Field>
      <Field label="Useful context (optional)"><Textarea resize="vertical" value={context} onChange={(_, data) => setContext(data.value)} placeholder="Reference mood, instruments to keep, or anything you dislike." /></Field>
      <div className="delegate-action-row"><Field label="Control level"><Select value={autonomy} onChange={(_, data) => setAutonomy(data.value)}><option value="review">Review before applying</option><option value="safe-auto">Apply safe edits automatically</option></Select></Field><Button appearance="primary" icon={<MagicWand />} disabled={!goal.trim() || busy} onClick={async () => {
        setBusy(true);
        try {
          const task = await createDelegation(goal, autonomy, context);
          setGoal(""); setContext("");
          setMessage(task.autonomy === "review" ? "Request queued. The agent will return a reviewable proposal." : "Request queued for safe automatic editing.");
          await refresh();
        } catch (error) { setMessage(String(error)); } finally { setBusy(false); }
      }}>Delegate</Button></div>
    </div>
    {message && <div className="delegate-message" role="status">{message}</div>}
    <div className="task-list">
      {tasks.slice(0, 6).map((task) => {
        const displayStatus = delegationDisplayStatus(task);
        const applyGate = proposalApplyGate(task, currentRevision);
        const listeningGate = productionListeningGate(task, currentRevision);
        const production = task.result?.production;
        const isActing = actingTask === task.id;
        return <article className={`task task-${displayStatus}`} data-task-id={task.id} key={task.id}>
          <div className="task-top"><Badge appearance="tint" color={statusColor(displayStatus)}>{displayStatus}</Badge><time>{new Date(task.created_at).toLocaleString()}</time></div>
          <b>{task.goal}</b>
          {currentRevision && task.base_revision && task.base_revision !== currentRevision && <div className="task-stale"><WarningCircle size={16} weight="fill" /><span>This request was based on an older project revision. Ask the agent to rebase it before applying.</span></div>}
          {task.status === "pending" && displayStatus !== "revision-requested" && <p>Waiting for the connected LedgerLine agent. You can keep editing while it works.</p>}
          {task.status === "needs-direction" && <div className="direction-request">
            <b>The agent needs your direction</b>
            {(task.questions ?? task.proposal?.questions ?? []).map((question) => <p key={question}>{question}</p>)}
            <Field label="Your answer"><Textarea resize="vertical" value={answers[task.id] ?? ""} onChange={(_, data) => setAnswers((current) => ({ ...current, [task.id]: data.value }))} placeholder="Describe the result you want in plain language." /></Field>
            <Button appearance="primary" icon={<ArrowRight />} disabled={!answers[task.id]?.trim() || isActing} onClick={() => void act(task, () => answerDelegation(task, answers[task.id]), "Direction sent. The agent can continue from this checkpoint.")}>Send direction</Button>
          </div>}
          {(task.error || (task.status === "failed" && task.message)) && <div className="task-stale"><WarningCircle size={16} weight="fill" /><span>{task.error ?? task.message}</span></div>}
          {task.proposal && <div className="proposal"><p>{task.proposal.summary}</p>{task.proposal.reasoning && <small>{task.proposal.reasoning}</small>}<span>{task.proposal.actions.length} reversible edit actions</span>{task.proposal.listening_check && <em>Listening check: {Array.isArray(task.proposal.listening_check) ? task.proposal.listening_check.join(" · ") : task.proposal.listening_check}</em>}</div>}
          {task.proposal_preview && <ProposalPreview preview={task.proposal_preview} />}
          {task.status === "proposed" && <>
            {!applyGate.ready && <div className="proposal-blocked" role="alert"><WarningCircle size={16} weight="fill" /><span>{applyGate.reason}</span></div>}
            <div className="task-buttons"><Button appearance="primary" icon={<Check />} disabled={!applyGate.ready || isActing} onClick={() => void act(task, () => applyDelegation(task), "Proposal applied. Production receipts are now being refreshed.", true)}>Apply reviewed proposal</Button><Button appearance="subtle" icon={<X />} disabled={isActing} onClick={() => void act(task, () => rejectDelegation(task), "Proposal rejected.")}>Reject</Button></div>
          </>}
          {production && <ProductionReview task={task} production={production} gate={listeningGate} note={listeningNotes[task.id] ?? ""} setNote={(value) => setListeningNotes((current) => ({ ...current, [task.id]: value }))} busy={isActing} onAccept={() => void act(task, () => acceptDelegation(task, listeningNotes[task.id] ?? ""), "Production accepted after listening.", true)} onRevise={() => void act(task, () => reviseDelegation(task, listeningNotes[task.id] ?? ""), "Listening feedback sent. The agent will prepare another proposal.", true)} />}
        </article>;
      })}
      {!tasks.length && <div className="empty-task">No delegated work yet. Start with a plain-language musical goal.</div>}
    </div>
  </section>;
}

function ProposalPreview({ preview }: { preview: DelegationProposalPreview }) {
  const { impact, score_diff: score, yaml_diff: yaml } = preview;
  return <details className="proposal-review" open>
    <summary><span>Verified proposal preview</span><Badge appearance="outline" color={preview.status === "ready" ? "success" : "warning"}>{preview.status}</Badge></summary>
    <div className="preview-receipts">
      <Receipt label="Validation" value={`${preview.validation.status} · ${preview.validation.contract ?? "contract not recorded"}`} />
      <Receipt label="Commands" value={`${preview.command_count} · ${preview.command_types.join(", ") || "none"}`} />
      <Receipt label="Base revision" value={shortRevision(preview.base_revision)} title={preview.base_revision} />
      <Receipt label="Result revision" value={shortRevision(preview.result_revision)} title={preview.result_revision} />
    </div>
    <div className="preview-counts" aria-label="Proposal impact counts">
      <Count label="Files" value={impact.counts.files} /><Count label="Parts" value={impact.counts.parts} /><Count label="Measures" value={impact.counts.measures} /><Count label="Aspects" value={impact.counts.aspects} /><Count label="Score changes" value={score.counts.total} />
    </div>
    <div className="impact-summary">
      <b>Source impact</b>
      <span>{impact.files.map((file) => file.path).join(", ") || "No authored files"}</span>
      <small>{impact.measures.map((item) => `${item.part} M${item.measure}`).join(" · ") || "No measure range"}</small>
      <small>{[...impact.aspects, ...impact.fields].filter((value, index, all) => all.indexOf(value) === index).join(" · ") || "No fields changed"}</small>
    </div>
    <details className="score-diff" open>
      <summary>Score diff · +{score.counts.added} −{score.counts.removed} Δ{score.counts.changed}</summary>
      {!score.identity.complete && <div className="preview-warning">{score.identity.fallback_count} notes use fallback identity; inspect these changes carefully.</div>}
      <ScoreGroup label="Added" kind="added" notes={score.added.map((note) => ({ note }))} />
      <ScoreGroup label="Removed" kind="removed" notes={score.removed.map((note) => ({ note }))} />
      <ScoreGroup label="Changed" kind="changed" notes={score.changed.map((change) => ({ note: change.after ?? change.before, detail: change.changed_fields.join(", ") }))} />
      {score.counts.total === 0 && <span className="diff-empty">No score-note changes; this proposal affects another authored layer.</span>}
    </details>
    <details className="yaml-diff">
      <summary>Bounded YAML diff · {yaml.line_count} lines / {yaml.byte_count} bytes</summary>
      {yaml.truncated && <div className="preview-warning">Diff was bounded at {yaml.truncated_at_file ?? "the configured limit"}. Omitted: {yaml.omitted_files?.join(", ") || "additional content"}.</div>}
      <pre aria-label="Proposal YAML unified diff">{yaml.text || "No YAML text changes."}</pre>
    </details>
  </details>;
}

function ScoreGroup({ label, kind, notes }: { label: string; kind: string; notes: { note?: DelegationScoreNote; detail?: string }[] }) {
  if (!notes.length) return null;
  return <div className={`score-change-group score-change-${kind}`}><b>{label}</b>{notes.slice(0, 12).map((item, index) => <span key={`${kind}-${item.note?.event_id ?? item.note?.id ?? index}`}><i>{kind === "added" ? "+" : kind === "removed" ? "−" : "Δ"}</i>{noteLabel(item.note)}{item.detail && <small>{item.detail}</small>}</span>)}{notes.length > 12 && <small>+{notes.length - 12} more changes in the bounded preview</small>}</div>;
}

function ProductionReview({ task, production, gate, note, setNote, busy, onAccept, onRevise }: { task: Delegation; production: DelegationProduction; gate: Gate; note: string; setNote: (value: string) => void; busy: boolean; onAccept: () => void; onRevise: () => void }) {
  const revisions = production.revisions;
  const checks = production.listening_checks ?? production.listening?.checks ?? [];
  const status = delegationDisplayStatus(task);
  const ab = production.ab;
  return <section className={`production-review production-${status}`} aria-label="Production listening review">
    <header><div><b>Production listening checkpoint</b><span>{productionStateMessage(task)}</span></div><Badge appearance="outline" color={statusColor(status)}>{status}</Badge></header>
    <div className="production-revisions">
      <Receipt label="Authored" value={shortRevision(revisions?.authored_revision ?? production.build?.source_revision)} title={revisions?.authored_revision ?? production.build?.source_revision ?? undefined} />
      <Receipt label="Compiled" value={shortRevision(revisions?.compiled_revision ?? production.build?.compiled_revision)} title={revisions?.compiled_revision ?? production.build?.compiled_revision ?? undefined} />
      <Receipt label="Rendered" value={shortRevision(revisions?.rendered_revision ?? production.build?.rendered_revision)} title={revisions?.rendered_revision ?? production.build?.rendered_revision ?? undefined} />
      <Receipt label="Mix" value={shortRevision(revisions?.mix_revision ?? production.build?.mix_revision)} title={revisions?.mix_revision ?? production.build?.mix_revision ?? undefined} />
    </div>
    {production.build?.stages && <div className="production-stages">{Object.entries(production.build.stages).map(([name, stage]) => <span key={name}><b>{name}</b><Badge size="small" appearance="tint" color={statusColor(stage.status ?? "unknown")}>{stage.status ?? "unknown"}</Badge>{stage.reason && <small>{stage.reason}</small>}</span>)}</div>}
    <div className={`ab-evidence ${ab?.available ? "is-available" : "is-unavailable"}`}>
      <b>A/B evidence</b>
      {ab?.available ? <><span>Current {shortRevision(ab.current?.sha256)} vs previous {shortRevision(ab.previous?.sha256)}</span><small>Level match: {readable(ab.level_matching)} · current {ab.current?.integrated_lufs ?? "?"} LUFS / previous {ab.previous?.integrated_lufs ?? "?"} LUFS</small></> : <><span>Unavailable: {readable(ab?.unavailable_reason)}</span><small>{ab?.detail ?? "Approval remains possible only for the recorded current production; no comparison is implied."}</small></>}
    </div>
    <div className="listening-checks"><b>Listening checks</b>{checks.length ? <ol>{checks.map((check) => <li key={check}>{check}</li>)}</ol> : <span>No listening checks were recorded. Judge balance, phrasing, transitions, and artifacts before accepting.</span>}</div>
    {!gate.ready && !["accepted", "revision-requested"].includes(status) && <div className="production-blocked" role="alert"><WarningCircle size={16} weight="fill" /><span>{gate.reason}</span></div>}
    {gate.ready && <div className="listening-decision">
      <Field label="Listening note / revision feedback"><Textarea aria-label={`Listening note for ${task.id}`} resize="vertical" value={note} onChange={(_, data) => setNote(data.value)} placeholder="Record what you heard. Acceptance notes are optional; revision requests require specific feedback." /></Field>
      <div className="task-buttons"><Button appearance="primary" icon={<Check />} disabled={busy} onClick={onAccept}>Accept production</Button><Button appearance="secondary" icon={<ArrowRight />} disabled={busy || !note.trim()} onClick={onRevise}>Request revision</Button></div>
    </div>}
    {status === "accepted" && <div className="decision-record"><Check size={17} weight="bold" /><span>Accepted {task.acceptance?.accepted_at ? new Date(task.acceptance.accepted_at).toLocaleString() : "after listening"}{task.acceptance?.note ? ` · ${task.acceptance.note}` : ""}</span></div>}
    {status === "revision-requested" && <div className="decision-record"><ArrowRight size={17} /><span>{production.listening?.feedback ?? "Revision feedback was sent to the agent."}</span></div>}
  </section>;
}

function Receipt({ label, value, title }: { label: string; value: string; title?: string }) {
  return <span className="preview-receipt" title={title}><small>{label}</small><code>{value}</code></span>;
}

function Count({ label, value }: { label: string; value: number }) {
  return <span><b>{value}</b><small>{label}</small></span>;
}
