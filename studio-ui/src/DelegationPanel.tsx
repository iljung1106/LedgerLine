import { Badge, Button, Field, Select, Textarea } from "@fluentui/react-components";
import { Check, MagicWand, Robot, X } from "@phosphor-icons/react";
import { useEffect, useState } from "react";
import { applyDelegation, createDelegation, listDelegations, rejectDelegation } from "./api";
import type { Delegation } from "./types";

export function DelegationPanel({ onApplied }: { onApplied: () => void }) {
  const [tasks, setTasks] = useState<Delegation[]>([]);
  const [goal, setGoal] = useState("");
  const [context, setContext] = useState("");
  const [autonomy, setAutonomy] = useState("review");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const refresh = () => listDelegations().then(setTasks).catch((error) => setMessage(String(error)));
  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 2500);
    return () => window.clearInterval(timer);
  }, []);
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
    {message && <div className="delegate-message">{message}</div>}
    <div className="task-list">
      {tasks.slice(0, 6).map((task) => <article className={`task task-${task.status}`} key={task.id}>
        <div className="task-top"><Badge appearance="tint" color={task.status === "applied" ? "success" : task.status === "rejected" ? "danger" : "informative"}>{task.status}</Badge><time>{new Date(task.created_at).toLocaleString()}</time></div>
        <b>{task.goal}</b>
        {task.status === "pending" && <p>Waiting for the connected LedgerLine agent. You can keep editing while it works.</p>}
        {task.proposal && <div className="proposal"><p>{task.proposal.summary}</p>{task.proposal.reasoning && <small>{task.proposal.reasoning}</small>}<span>{task.proposal.actions.length} reversible edit actions</span>{task.proposal.listening_check && <em>Listening check: {task.proposal.listening_check}</em>}</div>}
        {task.status === "proposed" && <div className="task-buttons"><Button appearance="primary" icon={<Check />} onClick={async () => { await applyDelegation(task); await refresh(); onApplied(); }}>Apply proposal</Button><Button appearance="subtle" icon={<X />} onClick={async () => { await rejectDelegation(task); await refresh(); }}>Reject</Button></div>}
      </article>)}
      {!tasks.length && <div className="empty-task">No delegated work yet. Start with a plain-language musical goal.</div>}
    </div>
  </section>;
}
