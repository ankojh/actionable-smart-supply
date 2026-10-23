import { type ReactNode, useEffect, useMemo, useState } from "react";

import { type ArtifactRef, type RuntimeEvent, type Step, type Timeline, errorMessage, get } from "../api";
import { clock, duration, shortId, stamp } from "../format";
import { useEvents } from "../queries";
import { Lamp } from "./Lamp";
import { Meters, STATE_WORD } from "./Line";
import { ServerLog } from "./ServerLog";
import { useSheet } from "./Sheet";
import { useToast } from "./Toasts";

const CHARGE_WORDS: Record<string, [string, string]> = {
  model_calls: ["model call", "model calls"],
  tool_calls: ["tool call", "tool calls"],
  specialist_work_orders: ["work order", "work orders"],
  scenario_evaluations: ["evaluation", "evaluations"],
  evidence_refreshes: ["refresh", "refreshes"],
  revision_rounds: ["revision", "revisions"],
  candidates: ["candidate", "candidates"],
};

export function chargeText(charge: Step["charge"]) {
  return Object.entries(charge)
    .filter(([, n]) => n)
    .map(([key, n]) => `${n} ${CHARGE_WORDS[key]?.[n === 1 ? 0 : 1] ?? key}`)
    .join(", ");
}

export function useArtifactViewer(runId: string) {
  const sheet = useSheet();
  return (ref: ArtifactRef) =>
    sheet({
      title: ref.kind,
      subtitle: `artifact ${ref.artifact_id} · sha256 ${ref.content_sha256.slice(0, 16)}…`,
      filename: `${ref.kind}-${ref.artifact_id.slice(0, 12)}.json`,
      load: () => get(`/api/graph/runs/${runId}/artifacts/${ref.artifact_id}`),
    });
}

export function ArtifactChips({ runId, artifacts }: { runId: string; artifacts: ArtifactRef[] }) {
  const view = useArtifactViewer(runId);
  if (!artifacts.length) return null;
  return (
    <span className="artifact-chips">
      {artifacts.map((ref) => (
        <button
          key={ref.artifact_id}
          className="artifact-chip"
          title="Open this artifact"
          onClick={(event) => {
            event.stopPropagation();
            view(ref);
          }}
        >
          {ref.kind} {shortId(ref.artifact_id)}
        </button>
      ))}
    </span>
  );
}

function stepState(step: Step) {
  if (step.attention) return "attention";
  if (step.type === "milestone") {
    if (step.tone === "error") return "failed";
    if (step.tone === "attention") return "waiting";
    if (step.tone === "warn") return "waiting";
    return "idle";
  }
  return step.outcome === "completed" ? "done" : step.outcome;
}

function download(name: string, value: unknown) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}

export function ActivityLog({
  runId,
  timeline,
  focus,
  onFocus,
}: {
  runId: string;
  timeline: Timeline;
  focus: string | null;
  onFocus: (station: string | null) => void;
}) {
  const [layer, setLayer] = useState<string>("all");
  const [calls, setCalls] = useState(false);
  const [problems, setProblems] = useState(false);
  const [view, setView] = useState<"steps" | "timeline" | "raw" | "server">("steps");
  const raw = view === "raw";
  const [open, setOpen] = useState<Set<string>>(new Set());
  const sheet = useSheet();
  const toast = useToast();
  const events = useEvents(runId, raw, timeline.active);

  const children = useMemo(() => {
    const map = new Map<string, Step[]>();
    for (const step of timeline.steps) {
      if (step.parent) map.set(step.parent, [...(map.get(step.parent) ?? []), step]);
    }
    return map;
  }, [timeline.steps]);

  const stations = Object.fromEntries(timeline.stations.map((s) => [s.key, s]));
  const stages = Object.fromEntries(timeline.stages.map((s) => [s.key, s]));
  const isProblem = (step: Step) =>
    step.attention || ["failed", "interrupted"].includes(step.outcome) || ["warn", "error"].includes(step.tone ?? "");
  const visible = timeline.steps.filter((step) => {
    if (step.parent) return false;
    if (layer !== "all" && step.layer !== layer) return false;
    if (focus && step.layer !== focus) return false;
    if (problems && !isProblem(step)) return false;
    return true;
  });

  // Opening a stage from the progress bar scrolls to it in the Steps view.
  useEffect(() => {
    if (focus && view === "steps") document.getElementById("stage-" + focus)?.scrollIntoView({ block: "start" });
  }, [focus, view]);
  const layers = timeline.layers.filter((l) => timeline.steps.some((s) => s.layer === l.key));

  const showStep = (step: Step) =>
    sheet({
      title: step.label,
      subtitle: `${step.actor} · events ${step.events.join(", ")}`,
      filename: `step-${step.events[0] ?? step.id}.json`,
      body: (
        <dl className="kv" style={{ marginBottom: "1rem" }}>
          <dt>Station</dt>
          <dd>{stations[step.station]?.label ?? step.station}</dd>
          <dt>Actor</dt>
          <dd>
            {step.actor}
            {step.role && <span className="muted mono"> {step.role}</span>}
          </dd>
          {step.work_order_id && (
            <>
              <dt>Work order</dt>
              <dd className="mono">{step.work_order_id}</dd>
            </>
          )}
          <dt>Outcome</dt>
          <dd>
            {step.outcome}
            {step.attention && " (needs a look)"}
          </dd>
          {step.detail && (
            <>
              <dt>Result</dt>
              <dd>{step.detail}</dd>
            </>
          )}
          <dt>Started</dt>
          <dd>{stamp(step.started_at)}</dd>
          {step.ended_at && (
            <>
              <dt>Ended</dt>
              <dd>
                {stamp(step.ended_at)} ({duration(step.duration_ms)})
              </dd>
            </>
          )}
          {chargeText(step.charge) && (
            <>
              <dt>Budget used</dt>
              <dd>{chargeText(step.charge)}</dd>
            </>
          )}
          {step.error && (
            <>
              <dt>Error</dt>
              <dd className="mono">{step.error}</dd>
            </>
          )}
          {step.artifacts.length > 0 && (
            <>
              <dt>Artifacts</dt>
              <dd>
                <ArtifactChips runId={runId} artifacts={step.artifacts} />
              </dd>
            </>
          )}
          <dt>Raw events</dt>
          <dd className="muted small">Below, exactly as recorded by the runtime ledger.</dd>
        </dl>
      ),
      load: async () => {
        const all = await get<RuntimeEvent[]>(`/api/graph/runs/${runId}/events`);
        const wanted = new Set([...step.events, ...(children.get(step.id) ?? []).flatMap((c) => c.events)]);
        return all.filter((e) => wanted.has(e.sequence));
      },
    });

  const exportLog = async () => {
    try {
      const all = await get<RuntimeEvent[]>(`/api/graph/runs/${runId}/events`);
      download(`linekeeper-${runId.slice(0, 8)}-log.json`, { run_id: runId, timeline, events: all });
    } catch (error) {
      toast(errorMessage(error), "bad");
    }
  };

  const row = (step: Step, child = false) => {
    const kids = children.get(step.id) ?? [];
    const expanded = calls ? !open.has(step.id) : open.has(step.id);
    const total: Step["charge"] = { ...step.charge };
    if (!child) {
      for (const kid of kids) {
        for (const [key, n] of Object.entries(kid.charge)) {
          const k = key as keyof Step["charge"];
          total[k] = (total[k] ?? 0) + (n ?? 0);
        }
      }
    }
    return (
      <li key={step.id}>
        <div
          className={`log-row tone-${step.tone ?? "plain"}${child ? " child" : ""}`}
          onClick={() => showStep(step)}
          style={{ cursor: "pointer" }}
        >
          <Lamp state={stepState(step)} />
          <span className="when">{clock(step.started_at)}</span>
          <span className="who">{child ? "" : step.actor}</span>
          <span className="what">
            <span className={"what-label" + (isProblem(step) ? " problem" : "")}>
              {step.label}
              {step.error && !step.label.startsWith("Failed") && <span className="mono"> ({step.error})</span>}
            </span>
            {step.detail && <span className={"what-detail" + (step.attention ? " problem" : "")}>{step.detail}</span>}
            {(chargeText(total) || step.artifacts.length > 0 || kids.length > 0) && (
              <span className="row">
                {chargeText(total) && <span className="charge">{chargeText(total)}</span>}
                {!child && <ArtifactChips runId={runId} artifacts={step.artifacts} />}
                {!child && kids.length > 0 && (
                  <button
                    className="expand"
                    aria-expanded={expanded}
                    onClick={(event) => {
                      event.stopPropagation();
                      setOpen((set) => {
                        const next = new Set(set);
                        if (next.has(step.id)) next.delete(step.id);
                        else next.add(step.id);
                        return next;
                      });
                    }}
                  >
                    {expanded ? "Hide" : "Show"} {kids.length === 1 ? "call" : `${kids.length} calls`}
                  </button>
                )}
              </span>
            )}
          </span>
          <span className="dur">{step.outcome === "running" ? "working" : duration(step.duration_ms)}</span>
        </div>
        {expanded && kids.length > 0 && <ul className="log">{kids.map((kid) => row(kid, true))}</ul>}
      </li>
    );
  };

  return (
    <div className="tabpanel">
      <div className="log-tools">
        <div className="chips" role="group" aria-label="Log view">
          <button className="chip" aria-pressed={view === "steps"} onClick={() => setView("steps")}>
            Steps
          </button>
          <button className="chip" aria-pressed={view === "timeline"} onClick={() => setView("timeline")}>
            Timeline
          </button>
          <button className="chip" aria-pressed={view === "raw"} onClick={() => setView("raw")}>
            Raw events
          </button>
          <button className="chip" aria-pressed={view === "server"} onClick={() => setView("server")}>
            Server log
          </button>
        </div>
        <span style={{ flex: 1 }} />
        <button className="btn small" onClick={exportLog}>
          Download log
        </button>
      </div>
      {view === "timeline" && (
        <section className="panel panel-pad stack" aria-label="Run budget">
          <p className="small muted">Run budget: what this run has used against its hard limits.</p>
          <Meters timeline={timeline} />
        </section>
      )}
      {view === "timeline" && (
        <div className="log-tools">
          <div className="chips" role="group" aria-label="Filter by layer">
            <button className="chip" aria-pressed={layer === "all"} onClick={() => setLayer("all")}>
              All layers
            </button>
            {layers.map((l) => (
              <button key={l.key} className="chip" aria-pressed={layer === l.key} onClick={() => setLayer(l.key)}>
                {l.label}
              </button>
            ))}
          </div>
          {focus && (
            <button className="chip" aria-pressed onClick={() => onFocus(null)} title="Clear stage filter">
              {stages[focus]?.label ?? focus} ×
            </button>
          )}
          <span style={{ flex: 1 }} />
          <label className="row small">
            <input type="checkbox" checked={calls} onChange={(e) => setCalls(e.target.checked)} />
            Show model and tool calls
          </label>
          <label className="row small">
            <input type="checkbox" checked={problems} onChange={(e) => setProblems(e.target.checked)} />
            Problems only
          </label>
        </div>
      )}

      {view === "server" && <ServerLog runId={runId} live={timeline.active} />}

      {view === "steps" && (
        <StepsView timeline={timeline} focus={focus} problems={problems} setProblems={setProblems} row={row} isProblem={isProblem} />
      )}

      {view === "timeline" && (
        <section className="panel panel-pad" aria-label="Activity log">
          {visible.length === 0 ? (
            <p className="muted">Nothing matches these filters yet.</p>
          ) : (
            <ul className="log">{visible.map((step) => row(step))}</ul>
          )}
        </section>
      )}

      {raw && (
        <section className="panel panel-pad" aria-label="Raw runtime events">
          {events.isLoading && <p className="skeleton">Loading events…</p>}
          {events.error && <p className="callout bad">{errorMessage(events.error)}</p>}
          {events.data && (
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th className="num">#</th>
                    <th>Time</th>
                    <th>Kind</th>
                    <th>Role</th>
                    <th>Outcome</th>
                    <th>Artifacts</th>
                    <th className="num">Model calls</th>
                  </tr>
                </thead>
                <tbody>
                  {events.data
                    .filter((e) => (focus ? stepForEvent(timeline, e.sequence)?.layer === focus : true))
                    .map((event) => (
                      <tr
                        key={event.event_id}
                        style={{ cursor: "pointer" }}
                        onClick={() =>
                          sheet({ title: `Event ${event.sequence}: ${event.kind}`, data: event, filename: `event-${event.sequence}.json` })
                        }
                      >
                        <td className="num">{event.sequence}</td>
                        <td className="nowrap">{clock(event.started_at)}</td>
                        <td className="mono">{event.kind}</td>
                        <td className="mono">{event.role ?? ""}</td>
                        <td>
                          {event.outcome}
                          {event.error && <span className="mono"> {event.error}</span>}
                        </td>
                        <td>
                          <ArtifactChips runId={runId} artifacts={event.artifacts} />
                        </td>
                        <td className="num">{event.usage?.model_calls ?? ""}</td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}
    </div>
  );
}

function stepForEvent(timeline: Timeline, sequence: number) {
  return timeline.steps.find((s) => s.events.includes(sequence));
}

/** Stages, each with its substeps (stations); a substep opens to its operations and calls. */
function StepsView({
  timeline,
  focus,
  problems,
  setProblems,
  row,
  isProblem,
}: {
  timeline: Timeline;
  focus: string | null;
  problems: boolean;
  setProblems: (value: boolean) => void;
  row: (step: Step) => ReactNode;
  isProblem: (step: Step) => boolean;
}) {
  const stations = Object.fromEntries(timeline.stations.map((s) => [s.key, s]));
  return (
    <>
      <div className="log-tools">
        <p className="small muted" style={{ flex: 1 }}>
          Each stage of the assessment and the substeps it ran. Open a substep to see every operation and model call.
        </p>
        <label className="row small">
          <input type="checkbox" checked={problems} onChange={(e) => setProblems(e.target.checked)} />
          Problems only
        </label>
      </div>
      <ol className="stage-list">
        {timeline.stages.map((stage, index) => {
          const substeps = stage.stations
            .map((key) => stations[key])
            .filter((station) => !problems || ["failed", "interrupted", "attention"].includes(station.state));
          if (problems && !substeps.length) return null;
          return (
            <li key={stage.key} id={"stage-" + stage.key} className={"panel stage-block" + (focus === stage.key ? " focused" : "")}>
              <div className="stage-block-head">
                <span className={"stage-num " + stage.state} aria-hidden="true">
                  {index + 1}
                </span>
                <div className="stack" style={{ gap: "0.15rem" }}>
                  <div className="row">
                    <h3>{stage.label}</h3>
                    <span className={"pill " + stage.state}>{STATE_WORD[stage.state]}</span>
                  </div>
                  <span className="small muted">{stage.purpose}</span>
                  {stage.detail && <span className="small">{stage.detail}</span>}
                </div>
              </div>
              <ul className="substeps">
                {substeps.map((station) => {
                  const steps = timeline.steps.filter(
                    (s) => !s.parent && s.station === station.key && (!problems || isProblem(s)),
                  );
                  const open = ["failed", "interrupted", "attention", "running", "waiting"].includes(station.state);
                  return (
                    <li key={station.key}>
                      <details className="substep" open={open || undefined}>
                        <summary>
                          <Lamp state={station.state} />
                          <span className="substep-name">{station.label}</span>
                          <span className={"substep-detail" + (["failed", "attention"].includes(station.state) ? " problem" : "")}>
                            {station.detail ?? (station.state === "idle" ? "Not started" : STATE_WORD[station.state])}
                          </span>
                          <span className="substep-meta">
                            {station.passes > 1 && `${station.passes} passes · `}
                            {station.busy_ms ? duration(station.busy_ms) + " · " : ""}
                            {station.actor}
                          </span>
                        </summary>
                        {steps.length ? (
                          <ul className="log">{steps.map((step) => row(step))}</ul>
                        ) : (
                          <p className="small muted substep-empty">Nothing recorded yet.</p>
                        )}
                      </details>
                    </li>
                  );
                })}
              </ul>
            </li>
          );
        })}
      </ol>
    </>
  );
}
