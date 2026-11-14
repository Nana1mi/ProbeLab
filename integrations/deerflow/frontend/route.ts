import { getServerSideUser } from "@/core/auth/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
type Context = { params: Promise<{ path?: string[] }> };

async function proxy(request: Request, context: Context): Promise<Response> {
  const auth = await getServerSideUser();
  if (auth.tag !== "authenticated") {
    return Response.json(
      { detail: "Please sign in to the workspace." },
      { status: 401 },
    );
  }
  const origin = request.headers.get("origin");
  if (
    request.method === "POST" &&
    origin &&
    origin !== new URL(request.url).origin
  ) {
    return Response.json(
      { detail: "Cross-origin writes are disabled." },
      { status: 403 },
    );
  }
  const { path = [] } = await context.params;
  const allowed = path.every(
    (p) => /^[A-Za-z0-9_.-]+$/.test(p) && p !== "." && p !== "..",
  );
  const route = path.join("/");
  const readable =
    route === "" ||
    route === "health" ||
    route === "runs" ||
    /^ui\/(style\.css|app\.js)$/.test(route) ||
    /^runs\/[A-Za-z0-9_-]+(\/(ledger|report|collaboration(?:\/[0-9]+)?))?$/.test(
      route,
    );
  if (
    !allowed ||
    !readable ||
    (request.method === "POST" && route !== "runs")
  ) {
    return Response.json({ detail: "Unknown VELA endpoint." }, { status: 404 });
  }
  try {
    const base = new URL(
      process.env.TIERLAB_SERVER_URL ?? "http://127.0.0.1:8000",
    );
    const target = new URL(route || "./", `${base.href.replace(/\/$/, "")}/`);
    target.search = new URL(request.url).search;
    const upstream = await fetch(target, {
      method: request.method,
      body: request.method === "POST" ? await request.text() : undefined,
      headers:
        request.method === "POST"
          ? { "Content-Type": "application/json" }
          : undefined,
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.timeout(30000),
    });
    return new Response(upstream.body, {
      status: upstream.status,
      headers: {
        "Content-Type":
          upstream.headers.get("content-type") ?? "application/json",
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
      },
    });
  } catch {
    return Response.json(
      { detail: "VELA service is unavailable. Check TIERLAB_SERVER_URL." },
      { status: 502 },
    );
  }
}

export const GET = proxy;
export const POST = proxy;
