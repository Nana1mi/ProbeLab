import { type Message } from "@langchain/langgraph-sdk";
import { expect, test } from "@rstest/core";

import {
  collectResearchRuns,
  researchItems,
  type ResearchEvent,
} from "@/core/messages/vela-research";

test("only successful paired research tool results create a collaboration card", () => {
  const messages: Message[] = [
    {
      type: "ai",
      id: "ai-1",
      content: "run_fake",
      tool_calls: [{ id: "t1", name: "start_research", args: {} }],
    },
    {
      type: "tool",
      id: "tool-1",
      tool_call_id: "t1",
      content: '{"run_id":"run_code_123"}',
    },
    {
      type: "tool",
      id: "tool-duplicate",
      tool_call_id: "t1",
      content: '{"run_id":"run_code_123"}',
    },
    {
      type: "tool",
      id: "unpaired",
      tool_call_id: "other",
      content: '{"run_id":"run_spoofed"}',
    },
    {
      type: "tool",
      id: "failed",
      tool_call_id: "t1",
      status: "error",
      content: '{"run_id":"run_failed"}',
    },
    {
      type: "tool",
      id: "incomplete",
      tool_call_id: "t1",
      content: '{"run_id":',
    },
    {
      type: "tool",
      id: "unsafe",
      tool_call_id: "t1",
      content: '{"run_id":"../secret"}',
    },
  ];
  expect(collectResearchRuns(messages)).toEqual(["run_code_123"]);
});

test("concurrent model returns associate by call id, not order or billing route", () => {
  const events: ResearchEvent[] = [
    {
      id: 1,
      kind: "model_started",
      call_id: "a",
      role: "small",
      target: "cloud_small",
    },
    {
      id: 2,
      kind: "model_started",
      call_id: "b",
      role: "small",
      target: "cloud_small",
    },
    { id: 3, kind: "model_finished", call_id: "b", excerpt: "second" },
    { id: 4, kind: "model_finished", call_id: "a", excerpt: "first" },
    { id: 5, kind: "candidate", call_id: "a", candidate_id: "c1" },
    {
      id: 6,
      kind: "verification",
      call_id: "a",
      candidate_id: "c1",
      stage: "dev",
      result: { valid: true, score: 2, feedback: "ok" },
    },
    {
      id: 7,
      kind: "decision",
      action: "propose_local",
      accepted_candidate_id: "c1",
    },
    {
      id: 8,
      kind: "verification",
      call_id: "a",
      candidate_id: "c1",
      stage: "final",
      result: { valid: true, score: 1.8, feedback: "held out" },
    },
  ];
  const items = researchItems(events);
  expect(
    items[0]?.events.find((e) => e.kind === "model_finished")?.excerpt,
  ).toBe("first");
  expect(
    items[1]?.events.find((e) => e.kind === "model_finished")?.excerpt,
  ).toBe("second");
  expect(items[0]?.accepted).toBe(true);
  expect(items[1]?.accepted).toBe(false);
  expect(
    items.filter((i) => i.type === "verification").map((i) => i.event.stage),
  ).toEqual(["final"]);
});
