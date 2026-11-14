"use client";

// VELA native chat component. Model text is rendered as text, never executable HTML.
import { type Message } from "@langchain/langgraph-sdk";
import {
  BotIcon,
  CloudIcon,
  GitBranchIcon,
  Loader2Icon,
  ShieldCheckIcon,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { useI18n } from "@/core/i18n/hooks";
import { researchCopy } from "@/core/i18n/locales/vela-research";
import {
  collectResearchRuns,
  researchItems,
  type ResearchEvent,
  type ResearchItem,
  type ResearchPayload,
} from "@/core/messages/vela-research";
import { cn } from "@/lib/utils";

type Copy = (typeof researchCopy)["zh-CN"];
const active = (status: string) => status === "queued" || status === "running";
const endpoint = (runId: string) =>
  `/vela-proxy/runs/${encodeURIComponent(runId)}/collaboration`;
const metric = (value: number | undefined, digits = 4) =>
  value === undefined
    ? "—"
    : value.toLocaleString(undefined, { maximumFractionDigits: digits });
const label = (copy: Copy, key: string | undefined) =>
  key && Object.hasOwn(copy, key)
    ? copy[key as keyof Copy]
    : (key ?? copy.decision);

async function readJSON<T>(url: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(url, { signal, cache: "no-store" });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return (await response.json()) as T;
}

export function ResearchCollaborationRuns({
  messages,
}: {
  messages: Message[];
}) {
  const runs = useMemo(() => collectResearchRuns(messages), [messages]);
  return runs.map((runId) => (
    <ResearchCollaboration key={runId} runId={runId} />
  ));
}

export function ResearchCollaboration({ runId }: { runId: string }) {
  const { locale } = useI18n();
  const copy = researchCopy[locale === "zh-CN" ? "zh-CN" : "en-US"];
  const [snapshot, setSnapshot] = useState<ResearchPayload | null>(null);
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let disposed = false;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let offset = 0;
    const events = new Map<number, ResearchEvent>();
    setSnapshot(null);
    setError(false);
    async function poll() {
      try {
        let data: ResearchPayload;
        do {
          data = await readJSON<ResearchPayload>(
            `${endpoint(runId)}?offset=${offset}&limit=100`,
            controller.signal,
          );
          if (disposed) return;
          for (const event of data.events) events.set(event.id, event);
          if (
            data.next_offset < offset ||
            (data.next_offset === offset && offset < data.total)
          )
            throw new Error("Invalid cursor");
          offset = data.next_offset;
          setSnapshot({
            ...data,
            events: [...events.values()].sort((a, b) => a.id - b.id),
          });
          setError(false);
        } while (offset < data.total);
        if (active(data.status))
          timer = setTimeout(() => {
            void poll();
          }, 1500);
      } catch {
        if (!disposed) {
          setError(true);
          timer = setTimeout(() => {
            void poll();
          }, 5000);
        }
      }
    }
    void poll();
    return () => {
      disposed = true;
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [runId, retry]);
  const items = useMemo(() => {
    if (!snapshot) return [];
    return researchItems(
      snapshot.legacy
        ? snapshot.legacy_entries.map((e, i) => ({
            ...e,
            id: i + 1,
            kind: "decision",
            legacy: true,
          }))
        : snapshot.events,
    );
  }, [snapshot]);
  return (
    <section
      className="border-border bg-card my-3 w-full min-w-0 rounded-xl border"
      data-testid="vela-collaboration"
    >
      <header className="border-border flex items-start justify-between gap-3 border-b p-4">
        <div>
          <h3 className="flex items-center gap-2 text-base font-medium">
            <GitBranchIcon className="size-4" />
            {copy.title}
          </h3>
          <p className="text-muted-foreground mt-1 text-sm">{copy.subtitle}</p>
        </div>
        <span className="bg-secondary shrink-0 rounded-full px-2 py-1 text-sm">
          {label(copy, snapshot?.status ?? "running")}
        </span>
      </header>
      <div className="space-y-3 p-4">
        {snapshot?.backend === "mock" && (
          <p className="border-border bg-secondary rounded-md border p-2 text-sm">
            {copy.mock}
          </p>
        )}
        {snapshot?.legacy && (
          <p className="border-border bg-secondary rounded-md border p-2 text-sm">
            {copy.legacy}
          </p>
        )}
        <div className="grid grid-cols-2 gap-3">
          {(["small", "strong"] as const).map((role) => (
            <div key={role} className="bg-secondary/60 min-w-0 rounded-lg p-3">
              <div className="flex items-center gap-2 text-sm">
                {role === "small" ? (
                  <BotIcon className="size-4" />
                ) : (
                  <CloudIcon className="size-4" />
                )}
                {copy[role]}
              </div>
              <p className="mt-1 text-xl font-medium">
                {metric(snapshot?.stats?.[role].calls)}{" "}
                <span className="text-muted-foreground text-sm font-normal">
                  {copy.calls}
                </span>
              </p>
              <p
                className="text-muted-foreground mt-1 truncate text-sm"
                title={snapshot?.stats?.[role].models.join(", ")}
              >
                {snapshot?.stats?.[role].models.length
                  ? snapshot.stats[role].models.join(", ")
                  : "—"}
              </p>
              {snapshot?.stats && (
                <p className="text-muted-foreground text-sm">
                  ¥{metric(snapshot.stats[role].cost_cny, 6)}
                </p>
              )}
            </div>
          ))}
        </div>
        {error && (
          <div
            role="alert"
            className="text-destructive flex items-center justify-between text-sm"
          >
            {copy.error}
            <Button
              variant="outline"
              size="sm"
              onClick={() => setRetry((v) => v + 1)}
            >
              {copy.retry}
            </Button>
          </div>
        )}
        {!snapshot && !error && (
          <p role="status" className="text-muted-foreground text-sm">
            {copy.loading}
          </p>
        )}
        <ol
          className="max-h-[680px] space-y-3 overflow-auto pr-1"
          aria-label={copy.title}
        >
          {items.map((item) => (
            <ResearchCard
              key={`${runId}-${item.id}`}
              item={item}
              runId={runId}
              copy={copy}
              running={active(snapshot?.status ?? "")}
            />
          ))}
        </ol>
        {snapshot && !items.length && (
          <p className="text-muted-foreground text-sm">{copy.noData}</p>
        )}
        {snapshot?.summary && (
          <div className="border-border grid grid-cols-2 gap-3 border-t pt-3 text-sm sm:grid-cols-3">
            <div>
              {copy.finalScore}
              <p className="mt-1 font-mono">
                {!active(snapshot.status)
                  ? `${metric(snapshot.summary.final_score)}${snapshot.task === "code" ? "×" : ""}`
                  : "—"}
              </p>
            </div>
            <div>
              {copy.totalCost}
              <p className="mt-1 font-mono">
                ¥
                {metric(
                  snapshot.stats
                    ? snapshot.stats.small.cost_cny +
                        snapshot.stats.strong.cost_cny
                    : snapshot.summary.total_cloud_cny,
                  6,
                )}
              </p>
            </div>
            <div>
              {copy.wall}
              <p className="mt-1 font-mono">
                {!active(snapshot.status)
                  ? `${metric(snapshot.summary.total_wall_s, 2)} s`
                  : "—"}
              </p>
            </div>
          </div>
        )}
        <p className="text-muted-foreground text-sm">{copy.costNote}</p>
        {snapshot &&
          !active(snapshot.status) &&
          snapshot.status === "completed" && (
            <a
              className="text-sm underline underline-offset-4"
              href={`/vela-proxy/runs/${encodeURIComponent(runId)}/report`}
              target="_blank"
              rel="noreferrer"
            >
              {copy.report} ↗
            </a>
          )}
      </div>
    </section>
  );
}

function ResearchCard({
  item,
  runId,
  copy,
  running,
}: {
  item: ResearchItem;
  runId: string;
  copy: Copy;
  running: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [details, setDetails] = useState<ResearchEvent[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [retry, setRetry] = useState(0);
  const event = item.event;
  const ids = item.events.map((e) => e.id).join(",");
  useEffect(() => {
    if (!open || event.legacy) return;
    const controller = new AbortController();
    let disposed = false;
    setDetails(null);
    setFailed(false);
    void Promise.all(
      ids
        .split(",")
        .map((id) =>
          readJSON<ResearchEvent>(
            `${endpoint(runId)}/${id}`,
            controller.signal,
          ),
        ),
    )
      .then((data) => {
        if (!disposed) setDetails(data);
      })
      .catch(() => {
        if (!disposed) setFailed(true);
      });
    return () => {
      disposed = true;
      controller.abort();
    };
  }, [open, ids, runId, retry, event.legacy]);
  const finished = item.events.find((e) => e.kind === "model_finished");
  const modelError = item.events.find((e) => e.kind === "model_failed");
  const checks = item.events.filter(
    (e) => e.kind === "verification" && e.stage !== "final",
  );
  const lastCheck = checks.at(-1)?.result;
  const final = item.type === "verification" ? event.result : undefined;
  const feedback = lastCheck?.feedback ?? final?.feedback;
  const title =
    item.type === "model"
      ? `${event.role === "strong" ? copy.strong : copy.small} · ${label(copy, event.phase ?? "candidate")}`
      : item.type === "verification"
        ? event.stage === "final"
          ? copy.final
          : copy.baseline
        : label(copy, event.action);
  const status = item.accepted
    ? copy.accepted
    : modelError
      ? copy.failed
      : lastCheck?.valid === false || final?.valid === false
        ? copy.rejected
        : final?.valid
          ? copy.checked
          : finished
            ? copy.returned
            : item.type === "model"
              ? running
                ? copy.pending
                : copy.incomplete
              : "";
  return (
    <li className="border-border overflow-hidden rounded-lg border">
      <details open={open} onToggle={(e) => setOpen(e.currentTarget.open)}>
        <summary
          className="hover:bg-accent focus-visible:outline-ring cursor-pointer p-3 text-sm focus-visible:outline-2 focus-visible:outline-offset-2"
          aria-label={`${copy.details}: ${title} ${event.step ?? 0} ${item.id}`}
        >
          <span className="inline-flex w-[calc(100%-1.5rem)] flex-wrap items-center justify-between gap-2 align-middle">
            <span className="font-medium">{title}</span>
            <span
              className={cn(
                "text-muted-foreground",
                (lastCheck?.valid === false || modelError) &&
                  "text-destructive",
              )}
            >
              {status}
            </span>
          </span>
          <div className="text-muted-foreground mt-1 flex flex-wrap gap-x-3 gap-y-1">
            <span>
              {copy.round} {event.step ?? 0}
            </span>
            {event.model && <span className="break-all">{event.model}</span>}
            {finished && (
              <span>
                {metric(finished.elapsed_s, 2)} s · ¥
                {metric(finished.cost_cny, 6)}
              </span>
            )}
            {event.guidance_ref && <span>{copy.usingHint}</span>}
          </div>
          {feedback && (
            <p className="text-muted-foreground mt-2 break-words">{feedback}</p>
          )}
          {event.trigger && (
            <p className="text-muted-foreground mt-1 font-mono break-all">
              {event.trigger}
            </p>
          )}
        </summary>
        {open && (
          <div className="border-border space-y-3 border-t p-3 text-sm">
            {event.legacy ? (
              <pre className="max-h-80 overflow-auto break-words whitespace-pre-wrap">
                {JSON.stringify(event, null, 2)}
              </pre>
            ) : failed ? (
              <div role="alert">
                {copy.error}
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setRetry((v) => v + 1)}
                >
                  {copy.retry}
                </Button>
              </div>
            ) : !details ? (
              <p role="status" className="flex items-center gap-2">
                <Loader2Icon className="size-4 animate-spin" />
                {copy.loading}
              </p>
            ) : (
              details.map((detail) => (
                <div key={detail.id}>
                  {detail.messages && (
                    <>
                      <h4 className="font-medium">{copy.task}</h4>
                      <pre className="bg-secondary/60 mt-2 max-h-72 overflow-auto rounded-md p-3 break-words whitespace-pre-wrap">
                        {detail.messages
                          .map((m) => `[${m.role}]\n${m.content}`)
                          .join("\n\n")}
                      </pre>
                    </>
                  )}
                  {detail.output !== undefined && (
                    <>
                      <h4 className="font-medium">{copy.output}</h4>
                      <pre className="bg-secondary/60 mt-2 max-h-96 overflow-auto rounded-md p-3 break-words whitespace-pre-wrap">
                        {detail.output}
                      </pre>
                    </>
                  )}
                  {detail.code !== undefined && (
                    <>
                      <h4 className="font-medium">{copy.code}</h4>
                      <pre className="bg-secondary/60 mt-2 max-h-96 overflow-auto rounded-md p-3">
                        {detail.code}
                      </pre>
                    </>
                  )}
                  {detail.result && (
                    <div className="border-border rounded-md border p-3">
                      <h4 className="flex items-center gap-2 font-medium">
                        <ShieldCheckIcon className="size-4" />
                        {detail.verifier}
                      </h4>
                      <p className="mt-1 break-words">
                        {detail.result.feedback}
                      </p>
                      {detail.result.failure_signature && (
                        <p className="text-destructive mt-1 font-mono break-all">
                          {detail.result.failure_signature}
                        </p>
                      )}
                      <details className="mt-2">
                        <summary className="cursor-pointer">
                          {copy.evidence}
                        </summary>
                        <pre className="mt-2 max-h-72 overflow-auto break-words whitespace-pre-wrap">
                          {JSON.stringify(detail.result.evidence, null, 2)}
                        </pre>
                      </details>
                    </div>
                  )}
                  {detail.kind === "decision" && (
                    <pre className="max-h-72 overflow-auto break-words whitespace-pre-wrap">
                      {JSON.stringify(detail, null, 2)}
                    </pre>
                  )}
                </div>
              ))
            )}
          </div>
        )}
      </details>
    </li>
  );
}
