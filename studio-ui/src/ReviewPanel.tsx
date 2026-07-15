import { Badge, Text } from "@fluentui/react-components";
import { ArrowsLeftRight, CheckCircle, GitDiff, WarningCircle } from "@phosphor-icons/react";
import { comparisonGainDb } from "./audioEngine";
import type { MasterReport, SourceImpact, StudioModel } from "./types";

type Comparison = "current" | "previous";

export type MasterVerdict = {
  loudnessDelta: number | null;
  loudnessWithinTolerance: boolean | null;
  peakHeadroom: number | null;
  peakWithinCeiling: boolean | null;
  rangeDelta: number | null;
};

export function masterVerdict(report: MasterReport | undefined): MasterVerdict {
  const loudnessDelta = difference(report?.integrated_lufs, report?.target_lufs);
  const peakHeadroom = difference(report?.true_peak_ceiling_dbtp, report?.true_peak_dbtp);
  return {
    loudnessDelta,
    loudnessWithinTolerance: loudnessDelta === null
      ? null
      : Math.abs(loudnessDelta) <= (report?.loudness_tolerance_lu ?? 0.5),
    peakHeadroom,
    peakWithinCeiling: peakHeadroom === null ? null : peakHeadroom >= -0.01,
    rangeDelta: difference(report?.loudness_range_lu, report?.loudness_range_target_lu),
  };
}

export function sourceImpactLabels(impact: SourceImpact): { files: string[]; measures: string[] } {
  return {
    files: impact.files.map((file) => file.path),
    measures: impact.measures.map((item) => `${item.part} M${item.measure}`),
  };
}

export function ReviewPanel({ model, comparison }: { model: StudioModel; comparison: Comparison }) {
  const report = model.mix.master_report;
  const verdict = masterVerdict(report);
  const review = model.review;
  const impact = review?.impact;
  const impactLabels = impact ? sourceImpactLabels(impact) : { files: [], measures: [] };
  const transaction = review?.latest_transaction;
  const ab = review?.ab ?? model.media.ab;
  const appliedGain = comparisonGainDb(model, comparison);
  const previousGain = ab?.playback_policy.gain_adjustment_db.previous ?? 0;

  return (
    <section className="review-panel panel" aria-label="Master, comparison and source review">
      <header className="panel-heading">
        <span>REVIEW TRUTH</span>
        <Badge appearance="tint" color={report?.bound_to_current_revision ? "success" : "warning"}>
          {report?.bound_to_current_revision ? "current master" : report?.status ?? "not measured"}
        </Badge>
      </header>

      <div className="master-review-grid">
        <ReviewMetric
          label="Integrated"
          objective={formatNumber(report?.target_lufs, " LUFS")}
          actual={formatNumber(report?.integrated_lufs, " LUFS")}
          detail={verdict.loudnessDelta === null ? "render to measure" : `${signed(verdict.loudnessDelta)} LU from target`}
          state={verdict.loudnessWithinTolerance}
        />
        <ReviewMetric
          label="True peak"
          objective={`≤ ${formatNumber(report?.true_peak_ceiling_dbtp, " dBTP")}`}
          actual={formatNumber(report?.true_peak_dbtp, " dBTP")}
          detail={verdict.peakHeadroom === null ? "render to measure" : `${signed(verdict.peakHeadroom)} dB headroom`}
          state={verdict.peakWithinCeiling}
        />
        <ReviewMetric
          label="Loudness range"
          objective={formatNumber(report?.loudness_range_target_lu, " LU")}
          actual={formatNumber(report?.loudness_range_lu, " LU")}
          detail={verdict.rangeDelta === null ? "render to measure" : `${signed(verdict.rangeDelta)} LU vs objective`}
          state={null}
        />
      </div>

      <div className="ab-review">
        <div className="review-row-title">
          <ArrowsLeftRight size={16} />
          <b>A/B playback</b>
          <Badge appearance="outline">{comparison === "current" ? "A listening" : "B listening"}</Badge>
        </div>
        {!ab && <Text size={200}>No revision-bound comparison contract is available.</Text>}
        {ab && !ab.available && (
          <Text size={200}>B is unavailable: {humanize(ab.unavailable_reason ?? "comparison is not ready")}.</Text>
        )}
        {ab?.available && (
          <>
            <Text size={200}>
              {ab.playback_policy.level_matching === "integrated-lufs"
                ? `B playback ${signed(previousGain)} dB${comparison === "previous" ? ` (applied now: ${signed(appliedGain)} dB)` : ""}.`
                : "A and B play at their raw rendered levels; no loudness match is available."}
            </Text>
            <div className="ab-measurements">
              <span>A {formatNumber(ab.current?.measurement?.integrated_lufs, " LUFS")}</span>
              <span>B raw {formatNumber(ab.previous?.measurement?.integrated_lufs, " LUFS")}</span>
              {ab.playback_policy.gain_adjustment_db.peak_limited && <Badge color="warning">peak-limited match</Badge>}
              {!ab.playback_policy.gain_adjustment_db.peak_limited && ab.playback_policy.gain_adjustment_db.limited && <Badge color="warning">bounded match</Badge>}
            </div>
          </>
        )}
      </div>

      <div className="source-impact">
        <div className="review-row-title">
          <GitDiff size={16} />
          <b>Latest source impact</b>
          <Badge appearance="outline" color={review?.status === "current" ? "success" : "warning"}>{review?.status ?? "none"}</Badge>
        </div>
        {!transaction || !impact?.changed ? (
          <Text size={200}>No source-changing Studio transaction is recorded for this revision.</Text>
        ) : (
          <>
            <Text size={200}>
              {humanize(transaction.operation ?? "edit")} · {transaction.command_count ?? transaction.command_types?.length ?? 0} command(s)
            </Text>
            <div className="impact-chips" aria-label="Affected source areas">
              {(impact.aspects ?? []).map((item) => <Badge key={`aspect-${item}`} color="informative">{item}</Badge>)}
              {(impact.parts ?? []).map((item) => <Badge key={`part-${item}`} appearance="outline">{item}</Badge>)}
              {impactLabels.measures.map((item) => <Badge key={`measure-${item}`} appearance="outline">{item}</Badge>)}
            </div>
            <details className="impact-details">
              <summary>Exact targets and fields</summary>
              <dl>
                <dt>Targets</dt><dd>{joinOrNone(impact.targets)}</dd>
                <dt>Fields</dt><dd>{joinOrNone(impact.fields)}</dd>
                <dt>Files</dt><dd>{joinOrNone(impactLabels.files)}</dd>
                <dt>Commands</dt><dd>{joinOrNone(transaction.command_types)}</dd>
              </dl>
            </details>
          </>
        )}
      </div>
    </section>
  );
}

function ReviewMetric({ label, objective, actual, detail, state }: {
  label: string;
  objective: string;
  actual: string;
  detail: string;
  state: boolean | null;
}) {
  return (
    <div className="review-metric">
      <span>{label}</span>
      <dl><dt>Target</dt><dd>{objective}</dd><dt>Actual</dt><dd>{actual}</dd></dl>
      <small className={state === false ? "metric-fail" : state === true ? "metric-pass" : ""}>
        {state === true && <CheckCircle size={12} weight="fill" />}
        {state === false && <WarningCircle size={12} weight="fill" />}
        {detail}
      </small>
    </div>
  );
}

function difference(left: number | null | undefined, right: number | null | undefined): number | null {
  return typeof left === "number" && Number.isFinite(left) && typeof right === "number" && Number.isFinite(right)
    ? left - right
    : null;
}

function formatNumber(value: number | null | undefined, suffix: string): string {
  return typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(1)}${suffix}` : "not measured";
}

function signed(value: number): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(1)}`;
}

function humanize(value: string): string {
  return value.replace(/[-_]+/g, " ");
}

function joinOrNone(values: (string | number)[] | undefined): string {
  return values?.length ? values.join(", ") : "none";
}
