import { afterEach, expect, test, rs } from "@rstest/core";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";

rs.mock("@/core/i18n/hooks", () => ({ useI18n: () => ({ locale: "en-US" }) }));
import { ResearchCollaboration } from "@/components/workspace/messages/research-collaboration";

const originalFetch = globalThis.fetch;
afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
});
const payload = (run: string) => ({
  run_id: run,
  status: "completed",
  backend: "real",
  legacy: false,
  stats: {
    small: { calls: 7, cost_cny: 0.1, models: ["small-api"] },
    strong: { calls: 2, cost_cny: 0.02, models: ["strong-api"] },
  },
  events: [],
  legacy_entries: [],
  next_offset: 0,
  total: 0,
});

test("late data from a previous run cannot replace the current run", async () => {
  let finishOld: (response: Response) => void = () => {
    throw new Error("Old request not started");
  };
  const signals: AbortSignal[] = [];
  globalThis.fetch = rs.fn((url: RequestInfo | URL, options?: RequestInit) => {
    const address =
      typeof url === "string" ? url : url instanceof URL ? url.href : url.url;
    if (address.includes("old_run")) {
      signals.push(options!.signal!);
      return new Promise<Response>((resolve) => {
        finishOld = resolve;
      });
    }
    return Promise.resolve(new Response(JSON.stringify(payload("new_run"))));
  }) as typeof fetch;
  const view = render(<ResearchCollaboration runId="old_run" />);
  view.rerender(<ResearchCollaboration runId="new_run" />);
  await screen.findByText("small-api");
  await act(async () => {
    finishOld(
      new Response(
        JSON.stringify({
          ...payload("old_run"),
          stats: {
            small: { calls: 999, cost_cny: 0, models: ["stale-model"] },
            strong: { calls: 0, cost_cny: 0, models: [] },
          },
        }),
      ),
    );
  });
  expect(screen.queryByText("stale-model")).toBeNull();
  expect(signals[0]?.aborted).toBe(true);
});

test("unmount cancels pending polling and does not issue another request", async () => {
  let signal: AbortSignal | null = null;
  const fetcher = rs.fn((_url: RequestInfo | URL, options?: RequestInit) => {
    signal = options!.signal!;
    return Promise.resolve(
      new Response(JSON.stringify({ ...payload("run"), status: "running" })),
    );
  });
  globalThis.fetch = fetcher as typeof fetch;
  const view = render(<ResearchCollaboration runId="run" />);
  await screen.findByText("small-api");
  view.unmount();
  expect((signal as AbortSignal | null)?.aborted).toBe(true);
  await new Promise((resolve) => setTimeout(resolve, 1600));
  expect(fetcher).toHaveBeenCalledTimes(1);
});

test("history with no trace never guesses model-size counts", async () => {
  globalThis.fetch = rs.fn(() =>
    Promise.resolve(
      new Response(
        JSON.stringify({ ...payload("legacy"), legacy: true, stats: null }),
      ),
    ),
  ) as typeof fetch;
  render(<ResearchCollaboration runId="legacy" />);
  await waitFor(() =>
    expect(screen.getByText(/historical run did not retain/)).toBeTruthy(),
  );
  expect(screen.queryByText("small-api")).toBeNull();
});
