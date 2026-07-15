import { describe, expect, it } from "vitest";
import {
  delegationDisplayStatus,
  productionListeningGate,
  productionStateMessage,
  proposalApplyGate,
} from "./DelegationReviewPanel";
import type { Delegation, DelegationProposalPreview } from "./types";

const BASE = "a".repeat(64);
const RESULT = "b".repeat(64);

const preview: DelegationProposalPreview = {
  schema_version: "1",
  status: "ready",
  base_revision: BASE,
  result_revision: RESULT,
  command_count: 1,
  command_types: ["update_note"],
  validation: { status: "ok", contract: "StudioSession.apply", compiled: true },
  impact: {
    changed: true,
    files: [{ path: "parts/piano.yaml" }],
    parts: ["piano"],
    measures: [{ part: "piano", measure: 2 }],
    aspects: ["dynamics"],
    targets: ["part:piano:measure:2"],
    fields: ["velocity"],
    counts: { files: 1, parts: 1, measures: 1, aspects: 1, targets: 1, fields: 1 },
  },
  yaml_diff: {
    format: "unified-yaml",
    text: "--- a/parts/piano.yaml\n+++ b/parts/piano.yaml",
    files: ["parts/piano.yaml"],
    truncated: false,
    line_count: 2,
    byte_count: 55,
    limits: { max_files: 12, max_lines: 400, max_bytes: 65_536 },
  },
  score_diff: {
    identity: { scheme: "authored-event-id+pitch-index", complete: true, fallback_count: 0 },
    added: [],
    removed: [],
    changed: [],
    counts: { added: 0, removed: 0, changed: 0, total: 0 },
  },
};

function task(overrides: Partial<Delegation> = {}): Delegation {
  return {
    id: "task-1",
    status: "proposed",
    goal: "Shape the phrase",
    context: "",
    constraints: [],
    autonomy: "review",
    proposal: { summary: "Shape phrase", actions: [{ type: "update_note" }] },
    proposal_preview: preview,
    approval_token: "token",
    created_at: "2026-07-15T00:00:00Z",
    updated_at: "2026-07-15T00:00:00Z",
    base_revision: BASE,
    ...overrides,
  };
}

describe("delegation review gates", () => {
  it("allows apply only for a validated isolated preview on the current revision", () => {
    expect(proposalApplyGate(task(), BASE)).toEqual({ ready: true, reason: null });
    expect(proposalApplyGate(task({ proposal_preview: null }), BASE).ready).toBe(false);
    expect(proposalApplyGate(task(), RESULT)).toMatchObject({ ready: false });
    expect(proposalApplyGate(task({ proposal_preview: { ...preview, status: "no-actions" } }), BASE).reason).toContain("only a ready preview");
  });

  it("requires complete revision-bound production receipts before listening decisions", () => {
    const ready = task({
      status: "ready-for-listening",
      result: {
        status: "ready-for-listening",
        source_revision: RESULT,
        production: {
          status: "ready-for-listening",
          revisions: {
            authored_revision: RESULT,
            compiled_revision: "c".repeat(64),
            rendered_revision: "d".repeat(64),
            mix_revision: "e".repeat(64),
          },
        },
      },
    });
    expect(productionListeningGate(ready, RESULT)).toEqual({ ready: true, reason: null });
    expect(productionListeningGate(ready, BASE).reason).toContain("stale");
    const incomplete = structuredClone(ready);
    incomplete.result!.production!.revisions!.mix_revision = null;
    expect(productionListeningGate(incomplete, RESULT).reason).toContain("complete");
  });

  it("surfaces revision-requested and production error truth", () => {
    const revised = task({
      status: "pending",
      result: { production: { status: "revision-requested", listening: { status: "revision-requested", feedback: "Lighter cadence" } } },
    });
    expect(delegationDisplayStatus(revised)).toBe("revision-requested");
    const failed = task({ result: { production: { status: "failed", error: { message: "Renderer exited 1" } } } });
    expect(productionStateMessage(failed)).toBe("Renderer exited 1");
  });
});
