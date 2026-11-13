"""Static HTML Dashboard Generator from JSONL Run Ledgers.

Per amendment #14:
  Renders three progression curves:
    1. Completion Score vs. Cumulative Cloud Cost (CNY)
    2. Completion Score vs. Cumulative Total Cost (Cloud CNY + GPU CNY)
    3. Completion Score vs. Cumulative Wall-Clock Time (seconds)
Plus Cost-Time scatter plot, consultation breakdown, and ledger replay table.
Embedded base64 images: 100% self-contained, no network needed.
"""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from typing import Any
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from tierlab.ledger import DecisionLedger, compute_ba_auc


def load_runs_from_dir(runs_dir: Path | str) -> list[DecisionLedger]:
    p = Path(runs_dir)
    ledgers: list[DecisionLedger] = []
    if p.is_file() and p.suffix == ".jsonl":
        ledgers.append(DecisionLedger(p))
    elif p.is_dir():
        for jsonl_file in sorted(p.rglob("*.jsonl")):
            ledgers.append(DecisionLedger(jsonl_file))
    return ledgers


def generate_three_curves_and_scatter(ledgers: list[DecisionLedger]) -> tuple[str, str, str, str]:
    """Generates base64 PNGs for the three curves required by amendment #14 and scatter plot."""
    if not ledgers:
        return "", "", "", ""

    # Curve 1: Quality vs Cumulative Cloud Cost
    fig1, ax1 = plt.subplots(figsize=(6.5, 4.2), dpi=120)
    for l in ledgers:
        if not l.entries:
            continue
        costs, scores = [0.0], [l.entries[0].score_before]
        cum = 0.0
        for e in l.entries:
            cum += e.cloud_cny
            costs.append(cum)
            scores.append(e.score_after)
        ax1.plot(costs, scores, marker="o", markersize=3, label=f"{l.summary()['run']}")
    ax1.set_title("1. Quality vs. Cumulative Cloud Cost (CNY)", fontsize=11, fontweight="bold")
    ax1.set_xlabel("Cloud Spend (CNY)")
    ax1.set_ylabel("Completion Score")
    ax1.set_ylim(-0.05, 1.05)
    ax1.grid(True, linestyle="--", alpha=0.5)
    ax1.legend(loc="lower right", fontsize=8)
    fig1.tight_layout()
    buf1 = io.BytesIO()
    fig1.savefig(buf1, format="png")
    plt.close(fig1)
    b64_cloud = base64.b64encode(buf1.getvalue()).decode("utf-8")

    # Curve 2: Quality vs Cumulative Total Cost (Cloud CNY + GPU CNY)
    fig2, ax2 = plt.subplots(figsize=(6.5, 4.2), dpi=120)
    for l in ledgers:
        if not l.entries:
            continue
        total_costs, scores = [0.0], [l.entries[0].score_before]
        cum_total = 0.0
        for e in l.entries:
            cum_total += (e.cloud_cny + e.gpu_cny)
            total_costs.append(cum_total)
            scores.append(e.score_after)
        ax2.plot(total_costs, scores, marker="s", markersize=3, label=f"{l.summary()['run']}")
    ax2.set_title("2. Quality vs. Cumulative Total Cost (Cloud + GPU)", fontsize=11, fontweight="bold")
    ax2.set_xlabel("Total Cost: Cloud ¥ + GPU ¥ (CNY)")
    ax2.set_ylabel("Completion Score")
    ax2.set_ylim(-0.05, 1.05)
    ax2.grid(True, linestyle="--", alpha=0.5)
    ax2.legend(loc="lower right", fontsize=8)
    fig2.tight_layout()
    buf2 = io.BytesIO()
    fig2.savefig(buf2, format="png")
    plt.close(fig2)
    b64_total = base64.b64encode(buf2.getvalue()).decode("utf-8")

    # Curve 3: Quality vs Cumulative Wall-Clock Time
    fig3, ax3 = plt.subplots(figsize=(6.5, 4.2), dpi=120)
    for l in ledgers:
        if not l.entries:
            continue
        wall_times, scores = [0.0], [l.entries[0].score_before]
        cum_wall = 0.0
        for e in l.entries:
            cum_wall += e.wall_s
            wall_times.append(cum_wall)
            scores.append(e.score_after)
        ax3.plot(wall_times, scores, marker="^", markersize=3, label=f"{l.summary()['run']}")
    ax3.set_title("3. Quality vs. Cumulative Wall-Clock Time (s)", fontsize=11, fontweight="bold")
    ax3.set_xlabel("Elapsed Wall-Clock (seconds)")
    ax3.set_ylabel("Completion Score")
    ax3.set_ylim(-0.05, 1.05)
    ax3.grid(True, linestyle="--", alpha=0.5)
    ax3.legend(loc="lower right", fontsize=8)
    fig3.tight_layout()
    buf3 = io.BytesIO()
    fig3.savefig(buf3, format="png")
    plt.close(fig3)
    b64_wall = base64.b64encode(buf3.getvalue()).decode("utf-8")

    # Scatter: Frontier Plot
    fig4, ax4 = plt.subplots(figsize=(6.5, 4.2), dpi=120)
    for l in ledgers:
        s = l.summary()
        ax4.scatter(
            s["total_wall_s"],
            s["final_score"],
            s=80 + (s["total_cloud_cny"] * 100),
            alpha=0.8,
            label=f"{s['run']} ({s['policy']})",
        )
    ax4.set_title("Cost-Time-Completion Frontier Scatter", fontsize=11, fontweight="bold")
    ax4.set_xlabel("Total Wall Clock Time (s)")
    ax4.set_ylabel("Final Score")
    ax4.set_ylim(-0.05, 1.05)
    ax4.grid(True, linestyle="--", alpha=0.5)
    ax4.legend(loc="lower right", fontsize=8)
    fig4.tight_layout()
    buf4 = io.BytesIO()
    fig4.savefig(buf4, format="png")
    plt.close(fig4)
    b64_scatter = base64.b64encode(buf4.getvalue()).decode("utf-8")

    return b64_cloud, b64_total, b64_wall, b64_scatter


def generate_html_report(runs_dir: Path | str, output_path: Path | str | None = None) -> str:
    ledgers = load_runs_from_dir(runs_dir)
    b64_cloud, b64_total, b64_wall, b64_scatter = generate_three_curves_and_scatter(ledgers)
    summaries = [l.summary() for l in ledgers]

    replay_sections = []
    for l in ledgers:
        rows = []
        for e in l.entries:
            rows.append(f"""
            <tr>
              <td>{e.step}</td>
              <td><span class="badge badge-{e.action}">{e.action}</span></td>
              <td><code>{e.trigger}</code></td>
              <td>{e.score_before:.3f} &rarr; <strong>{e.score_after:.3f}</strong></td>
              <td>{'+' if e.score_after > e.score_before else ''}{e.score_after - e.score_before:.3f}</td>
              <td>¥{e.cloud_cny:.4f}</td>
              <td>¥{e.gpu_cny:.4f}</td>
              <td>{e.wall_s:.2f}s</td>
              <td>{'&#9989;' if e.verified else '&#10060;'}</td>
              <td>{'&#9888;&#65039; Dup' if e.duplicate else '-'}</td>
              <td><code>{e.lesson_id or '-'}</code></td>
            </tr>
            """)
        replay_sections.append(f"""
        <div class="card">
          <h3>Run: {l.summary()['run']} | Policy: {l.summary()['policy']} | Seed: {l.summary()['seed']}</h3>
          <p>
            Final Score: <strong>{l.summary()['final_score']}</strong> |
            Cloud CNY: <strong>¥{l.summary()['total_cloud_cny']:.4f}</strong> |
            GPU CNY: <strong>¥{l.summary()['total_gpu_cny']:.4f}</strong> |
            Wall Time: <strong>{l.summary()['total_wall_s']:.1f}s</strong> |
            BA-AUC: <strong>{l.summary()['ba_auc']:.3f}</strong>
          </p>
          <div class="table-container">
            <table>
              <thead>
                <tr>
                  <th>Step</th><th>Action</th><th>Trigger</th><th>Score</th><th>&Delta;</th>
                  <th>Cloud ¥</th><th>GPU ¥</th><th>Wall s</th><th>Verified</th><th>Dup</th><th>Lesson</th>
                </tr>
              </thead>
              <tbody>
                {''.join(rows)}
              </tbody>
            </table>
          </div>
        </div>
        """)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TierLab Research Dashboard</title>
<style>
  :root {{
    --bg: #0f172a;
    --card-bg: #1e293b;
    --text: #f8fafc;
    --text-muted: #94a3b8;
    --border: #334155;
    --primary: #38bdf8;
    --success: #4ade80;
  }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    margin: 0;
    padding: 24px;
    line-height: 1.5;
  }}
  .container {{ max-width: 1300px; margin: 0 auto; }}
  .header {{ margin-bottom: 24px; border-bottom: 1px solid var(--border); padding-bottom: 16px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(400px, 1fr)); gap: 20px; margin-bottom: 24px; }}
  .card {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px; padding: 18px; margin-bottom: 20px; }}
  .card img {{ width: 100%; height: auto; border-radius: 4px; }}
  .table-container {{ overflow-x: auto; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; text-align: left; }}
  th, td {{ padding: 8px 10px; border-bottom: 1px solid var(--border); }}
  th {{ background: #0f172a; color: var(--text-muted); }}
  code {{ font-family: ui-monospace, Menlo, monospace; font-size: 12px; color: #a5f3fc; }}
  .badge {{ display: inline-block; padding: 2px 6px; border-radius: 4px; font-size: 11px; font-weight: 600; text-transform: uppercase; }}
  .badge-propose_local {{ background: #0284c7; color: white; }}
  .badge-widen {{ background: #7c3aed; color: white; }}
  .badge-lesson_hit {{ background: #059669; color: white; }}
  .badge-hint {{ background: #d97706; color: white; }}
  .badge-solve {{ background: #dc2626; color: white; }}
  .badge-no_advice_retry {{ background: #0d9488; color: white; }}
  .badge-final_eval {{ background: #4f46e5; color: white; }}
  .badge-stop {{ background: #475569; color: white; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>TierLab Research Agent Dashboard</h1>
    <p style="color: var(--text-muted);">
      Specification #14 Three Curves: Quality vs Cloud Spend | Quality vs Total Cost | Quality vs Wall Time
    </p>
  </div>

  <h2>Three Core Performance Curves (Amendment #14)</h2>
  <div class="grid">
    <div class="card">
      {f'<img src="data:image/png;base64,{b64_cloud}" alt="Quality vs Cloud Spend" />' if b64_cloud else '<p>No data</p>'}
    </div>
    <div class="card">
      {f'<img src="data:image/png;base64,{b64_total}" alt="Quality vs Total Cost" />' if b64_total else '<p>No data</p>'}
    </div>
    <div class="card">
      {f'<img src="data:image/png;base64,{b64_wall}" alt="Quality vs Wall Time" />' if b64_wall else '<p>No data</p>'}
    </div>
    <div class="card">
      {f'<img src="data:image/png;base64,{b64_scatter}" alt="Frontier Scatter" />' if b64_scatter else '<p>No data</p>'}
    </div>
  </div>

  <h2>Run Ledger Summaries</h2>
  <div class="card table-container">
    <table>
      <thead>
        <tr>
          <th>Run</th><th>Policy</th><th>Seed</th><th>Steps</th><th>Final Score</th>
          <th>Cloud ¥</th><th>GPU ¥</th><th>Wall s</th><th>BA-AUC</th><th>Lesson Hits</th><th>Dups</th>
        </tr>
      </thead>
      <tbody>
        {''.join(f"<tr><td><strong>{s['run']}</strong></td><td>{s['policy']}</td><td>{s['seed']}</td><td>{s['total_steps']}</td><td><strong>{s['final_score']}</strong></td><td>¥{s['total_cloud_cny']:.4f}</td><td>¥{s['total_gpu_cny']:.4f}</td><td>{s['total_wall_s']:.1f}s</td><td>{s['ba_auc']:.3f}</td><td>{s['lesson_hits']}</td><td>{s.get('duplicates_skipped', 0)}</td></tr>" for s in summaries)}
      </tbody>
    </table>
  </div>

  <h2>Decision Ledger Replay</h2>
  {''.join(replay_sections)}
</div>
</body>
</html>
"""
    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html, encoding="utf-8")

    return html
