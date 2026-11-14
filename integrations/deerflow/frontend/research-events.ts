// VELA native collaboration: factual model events, never inferred from billing location.
import { type Message } from "@langchain/langgraph-sdk";

export interface ResearchEvent {
  id: number;
  kind: string;
  step?: number;
  call_id?: string;
  candidate_id?: string;
  role?: "small" | "strong";
  model?: string;
  phase?: string;
  target?: string;
  guidance_ref?: string;
  stage?: string;
  verifier?: string;
  action?: string;
  trigger?: string;
  error?: string;
  elapsed_s?: number;
  cost_cny?: number;
  score_before?: number;
  score_after?: number;
  accepted_candidate_id?: string;
  excerpt?: string;
  output?: string;
  code?: string;
  messages?: { role: string; content: string }[];
  result?: {
    valid: boolean;
    score: number;
    feedback: string;
    failure_signature?: string;
    evidence?: unknown;
  };
  legacy?: boolean;
  extra?: Record<string, unknown>;
}

export interface ResearchPayload {
  run_id: string;
  task?: string;
  status: string;
  backend?: string;
  legacy: boolean;
  stats: Record<
    "small" | "strong",
    { calls: number; cost_cny: number; models: string[] }
  > | null;
  events: ResearchEvent[];
  legacy_entries: ResearchEvent[];
  next_offset: number;
  total: number;
  summary?: {
    final_score: number;
    total_cloud_cny: number;
    total_wall_s: number;
    stop_reason: string;
  };
}

export interface ResearchItem {
  id: string;
  type: "model" | "verification" | "decision";
  event: ResearchEvent;
  events: ResearchEvent[];
  accepted: boolean;
}

export function collectResearchRuns(messages: Message[]): string[] {
  const pending = new Map<string, string>();
  const runs = new Set<string>();
  for (const message of messages) {
    if (message.type === "ai") {
      for (const call of message.tool_calls ?? []) {
        if (call.id) pending.set(call.id, call.name);
      }
    }
    if (
      message.type !== "tool" ||
      !message.tool_call_id ||
      message.status === "error" ||
      pending.get(message.tool_call_id) !== "start_research"
    )
      continue;
    const text =
      typeof message.content === "string"
        ? message.content
        : message.content
            .filter((b) => b.type === "text")
            .map((b) => ("text" in b ? b.text : ""))
            .join("");
    try {
      const data: unknown = JSON.parse(text);
      if (
        typeof data === "object" &&
        data !== null &&
        "run_id" in data &&
        typeof data.run_id === "string" &&
        /^[A-Za-z0-9_-]{1,180}$/.test(data.run_id)
      ) {
        runs.add(data.run_id);
      }
    } catch {
      /* Unfinished streamed tool results become parseable on a later render. */
    }
  }
  return [...runs];
}

export function researchItems(events: ResearchEvent[]): ResearchItem[] {
  const accepted = new Set(
    events
      .filter((e) => e.kind === "decision" && e.action !== "final_eval")
      .map((e) => e.accepted_candidate_id)
      .filter(Boolean),
  );
  const items: ResearchItem[] = [];
  const verificationGroups = new Map<string, ResearchItem>();
  for (const event of events) {
    if (event.kind === "model_started") {
      const related = events.filter((e) => e.call_id === event.call_id);
      items.push({
        id: `model-${event.call_id}`,
        type: "model",
        event,
        events: related,
        accepted: related.some((e) =>
          Boolean(e.candidate_id && accepted.has(e.candidate_id)),
        ),
      });
    } else if (
      event.kind === "verification" &&
      (!event.call_id || event.stage === "final")
    ) {
      const id = `${event.stage}-${event.candidate_id}`;
      let item = verificationGroups.get(id);
      if (!item) {
        item = { id, type: "verification", event, events: [], accepted: false };
        verificationGroups.set(id, item);
        items.push(item);
      }
      item.events.push(event);
      item.event = event;
    } else if (event.kind === "decision" && event.action !== "final_eval") {
      items.push({
        id: `decision-${event.id}`,
        type: "decision",
        event,
        events: [event],
        accepted: false,
      });
    }
  }
  return items;
}
