"""Local web UI for TradingAgents reports and analysis workflow."""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.llm_clients.model_catalog import MODEL_OPTIONS
from tradingagents.report_index import build_report_index, write_report_index


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORT_ROOT = PROJECT_ROOT / "reports"
WEB_PROVIDER_OPTIONS = [
    ("OpenAI", "openai"),
    ("Google", "google"),
    ("Anthropic", "anthropic"),
    ("xAI", "xai"),
    ("DeepSeek", "deepseek"),
    ("Qwen", "qwen"),
    ("GLM", "glm"),
    ("MiniMax", "minimax"),
    ("NVIDIA NIM", "nvidia"),
    ("Ollama", "ollama"),
]


class TradingAgentsHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_html(render_home(REPORT_ROOT))
            return
        if parsed.path == "/api/reports":
            write_report_index(REPORT_ROOT)
            self._send_json(build_report_index(REPORT_ROOT))
            return
        if parsed.path == "/file":
            query = parse_qs(parsed.query)
            target = Path(query.get("path", [""])[0])
            if not _is_allowed_report_path(target):
                self.send_error(403)
                return
            if not target.exists() or not target.is_file():
                self.send_error(404)
                return
            self._send_file(target)
            return
        self.send_error(404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/run/value-discover":
            subprocess.Popen(
                [sys.executable, "-m", "tradingagents.value_discover"],
                cwd=PROJECT_ROOT,
                stdout=(REPORT_ROOT / "value_discover.web.log").open("ab"),
                stderr=subprocess.STDOUT,
            )
            self.send_response(303)
            self.send_header("Location", "/?started=1")
            self.end_headers()
            return
        if parsed.path == "/run/stock-analysis":
            length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(length).decode("utf-8")
            form = parse_qs(body)
            ticker = form.get("ticker", [""])[0].strip().upper()
            analysis_date = form.get("analysis_date", [""])[0].strip()
            llm_provider = form.get("llm_provider", [DEFAULT_CONFIG["llm_provider"]])[0].strip()
            quick_model = form.get("quick_model", [DEFAULT_CONFIG["quick_think_llm"]])[0].strip()
            deep_model = form.get("deep_model", [DEFAULT_CONFIG["deep_think_llm"]])[0].strip()
            backend_url = form.get("backend_url", [""])[0].strip()
            if not ticker:
                self.send_error(400, "Ticker is required")
                return
            command = [
                sys.executable,
                "-m",
                "tradingagents.stock_analysis",
                ticker,
            ]
            if analysis_date:
                command.extend(["--date", analysis_date])
            if llm_provider:
                command.extend(["--provider", llm_provider])
            if quick_model:
                command.extend(["--quick-model", quick_model])
            if deep_model:
                command.extend(["--deep-model", deep_model])
            if backend_url:
                command.extend(["--backend-url", backend_url])
            subprocess.Popen(
                command,
                cwd=PROJECT_ROOT,
                stdout=(REPORT_ROOT / "stock_analysis.web.log").open("ab"),
                stderr=subprocess.STDOUT,
            )
            self.send_response(303)
            self.send_header("Location", "/?stock_started=1")
            self.end_headers()
            return
        self.send_error(404)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("TradingAgentsWeb: " + fmt % args + "\n")

    def _send_html(self, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_json(self, payload: dict) -> None:
        encoded = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_file(self, path: Path) -> None:
        content_type = "text/plain; charset=utf-8"
        if path.suffix == ".html":
            content_type = "text/html; charset=utf-8"
        elif path.suffix == ".json":
            content_type = "application/json; charset=utf-8"
        elif path.suffix == ".csv":
            content_type = "text/csv; charset=utf-8"
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def render_home(report_root: Path) -> str:
    write_report_index(report_root)
    index = build_report_index(report_root)
    latest = index.get("latest_value_discover")
    latest_stock = index.get("latest_stock_analysis")
    rows = []
    for run in index.get("value_discover_runs", []):
        rows.append(
            "<tr>"
            f"<td>{_e(run['date'])}</td>"
            f"<td>{_link(run.get('value_discover_markdown'), 'Markdown')}</td>"
            f"<td>{_link(run.get('value_discover_csv'), 'CSV')}</td>"
            f"<td>{_link(run.get('public_equity_markdown'), 'Public Equity')}</td>"
            f"<td>{_link(run.get('public_equity_payload'), 'Payload')}</td>"
            f"<td>{_link(run.get('llm_summary'), 'LLM Summary')}</td>"
            "</tr>"
        )
    stock_rows = []
    for run in index.get("stock_analysis_runs", []):
        report = run.get("complete_report") or run.get("error_report")
        stock_rows.append(
            "<tr>"
            f"<td>{_e(run.get('ticker', ''))}</td>"
            f"<td>{_e(run.get('analysis_date', ''))}</td>"
            f"<td>{_status_badge(run.get('status', ''))}</td>"
            f"<td>{_e(run.get('llm_provider') or '')}<br><span class='muted'>{_e(_short(run.get('deep_think_llm') or '', 32))}</span></td>"
            f"<td>{_e(_short(run.get('decision') or run.get('error') or ''))}</td>"
            f"<td>{_link(report, 'Open')}</td>"
            f"<td>{_link(run.get('status_json'), 'Status')}</td>"
            "</tr>"
        )
    latest_panel = render_latest_panel(latest)
    stock_panel = render_stock_panel(latest_stock)
    today = dt.date.today().isoformat()
    provider_options = _provider_options(DEFAULT_CONFIG["llm_provider"])
    quick_options = _model_options("quick", DEFAULT_CONFIG["llm_provider"], DEFAULT_CONFIG["quick_think_llm"])
    deep_options = _model_options("deep", DEFAULT_CONFIG["llm_provider"], DEFAULT_CONFIG["deep_think_llm"])
    started = "Run started. Refresh in a minute for the new report." if "started=1" in "" else ""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TradingAgents Reports</title>
  <style>
    :root {{ color-scheme: light; --ink:#172033; --muted:#5b6475; --line:#d9dee8; --bg:#f6f7fb; --panel:#ffffff; --accent:#2563eb; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: var(--ink); background: var(--bg); }}
    header {{ padding: 18px 24px; border-bottom: 1px solid var(--line); background: var(--panel); display:flex; align-items:center; justify-content:space-between; gap:16px; }}
    h1 {{ font-size: 20px; margin: 0; letter-spacing:0; }}
    h2 {{ margin-top:0; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 22px; }}
    .toolbar {{ display:flex; gap:10px; align-items:center; }}
    .analysis-form {{ display:grid; grid-template-columns: minmax(120px, .7fr) minmax(150px, .8fr) minmax(150px, .9fr) minmax(220px, 1.4fr) minmax(220px, 1.4fr) auto; gap:12px; align-items:end; }}
    label span {{ color:var(--muted); display:block; font-size:12px; margin-bottom:4px; }}
    input, select {{ width:100%; border:1px solid var(--line); border-radius:6px; padding:9px 10px; font:inherit; background:white; }}
    button, .button {{ border:1px solid var(--accent); background:var(--accent); color:white; border-radius:6px; padding:8px 12px; font-weight:600; cursor:pointer; text-decoration:none; white-space:nowrap; }}
    button:disabled {{ opacity:.58; cursor:progress; }}
    .secondary {{ background:white; color:var(--accent); }}
    .panel {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:16px; margin-bottom:18px; }}
    .grid {{ display:grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap:12px; }}
    .metric {{ border:1px solid var(--line); border-radius:6px; padding:12px; min-height:76px; }}
    .metric span {{ color:var(--muted); display:block; font-size:12px; }}
    .metric strong {{ display:block; font-size:22px; margin-top:6px; }}
    table {{ width:100%; border-collapse:collapse; background:white; }}
    th, td {{ text-align:left; padding:10px 8px; border-bottom:1px solid var(--line); vertical-align:top; }}
    th {{ color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.04em; }}
    a {{ color:var(--accent); text-decoration:none; }}
    .muted {{ color:var(--muted); }}
    .steps {{ display:grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap:8px; margin-top:14px; }}
    .step {{ border:1px solid var(--line); border-radius:6px; padding:10px; min-height:68px; }}
    .step strong {{ display:block; font-size:13px; margin-top:4px; }}
    .badge {{ display:inline-flex; align-items:center; border-radius:999px; padding:2px 8px; font-size:12px; font-weight:700; text-transform:uppercase; }}
    .status-running {{ background:#dbeafe; color:#1d4ed8; }}
    .status-ok {{ background:#dcfce7; color:#166534; }}
    .status-error {{ background:#fee2e2; color:#991b1b; }}
    .status-pending, .status-queued {{ background:#eef2f7; color:#475569; }}
    .events {{ margin:12px 0 0; padding-left:18px; color:var(--muted); max-height:140px; overflow:auto; }}
    @media (max-width: 1060px) {{ .analysis-form {{ grid-template-columns:1fr 1fr; }} .steps {{ grid-template-columns:1fr 1fr; }} }}
    @media (max-width: 780px) {{ header {{ display:block; }} .toolbar {{ margin-top:12px; }} .grid {{ grid-template-columns:1fr 1fr; }} .analysis-form {{ grid-template-columns:1fr; }} .steps {{ grid-template-columns:1fr; }} table {{ font-size:12px; }} }}
  </style>
</head>
<body>
  <header>
    <h1>TradingAgents Reports</h1>
    <div class="toolbar">
      <form method="post" action="/run/value-discover"><button type="submit">Run Value Discover</button></form>
      <a class="button secondary" href="/api/reports">API</a>
    </div>
  </header>
  <main>
    <section class="panel">
      <h2>Run Stock Analysis</h2>
      <form method="post" action="/run/stock-analysis" class="analysis-form" data-analysis-form>
        <label>
          <span>Ticker</span>
          <input name="ticker" placeholder="NVDA" required maxlength="16">
        </label>
        <label>
          <span>Analysis date</span>
          <input name="analysis_date" type="date" value="{today}">
        </label>
        <label>
          <span>Provider</span>
          <select name="llm_provider">{provider_options}</select>
        </label>
        <label>
          <span>Quick model</span>
          <select name="quick_model">{quick_options}</select>
        </label>
        <label>
          <span>Deep model</span>
          <select name="deep_model">{deep_options}</select>
        </label>
        <button type="submit" data-analysis-button>Analyze Stock</button>
      </form>
      <p class="muted">Runs in the background and writes a full Markdown report bundle. Each run gets its own unique folder.</p>
    </section>
    {stock_panel}
    {latest_panel}
    <section class="panel">
      <h2>Stock Analysis History</h2>
      <table>
        <thead><tr><th>Ticker</th><th>Date</th><th>Status</th><th>Model</th><th>Decision/Error</th><th>Report</th><th>Status JSON</th></tr></thead>
        <tbody>{''.join(stock_rows) or '<tr><td colspan="7">No stock analysis runs found.</td></tr>'}</tbody>
      </table>
    </section>
    <section class="panel">
      <h2>Report History</h2>
      <table>
        <thead><tr><th>Date</th><th>Shortlist</th><th>CSV</th><th>Public Equity</th><th>Payload</th><th>LLM</th></tr></thead>
        <tbody>{''.join(rows) or '<tr><td colspan="6">No reports found.</td></tr>'}</tbody>
      </table>
    </section>
    <p class="muted">Outputs are research support, not financial advice or order recommendations.</p>
  </main>
  <script>
    const form = document.querySelector("[data-analysis-form]");
    if (form) {{
      const provider = form.querySelector("select[name='llm_provider']");
      const syncModel = (select) => {{
        if (!provider || !select) return;
        const current = select.selectedOptions[0];
        if (current && current.dataset.provider === provider.value) return;
        const next = Array.from(select.options).find((option) => option.dataset.provider === provider.value);
        if (next) next.selected = true;
      }};
      if (provider) {{
        provider.addEventListener("change", () => {{
          syncModel(form.querySelector("select[name='quick_model']"));
          syncModel(form.querySelector("select[name='deep_model']"));
        }});
      }}
      form.addEventListener("submit", () => {{
        const button = form.querySelector("[data-analysis-button]");
        if (button) {{
          button.disabled = true;
          button.textContent = "Starting...";
        }}
      }});
    }}
    if (document.querySelector(".status-running, .status-queued")) {{
      setTimeout(() => window.location.reload(), 5000);
    }}
  </script>
</body>
</html>"""


def render_stock_panel(latest: dict | None) -> str:
    if not latest:
        return "<section class='panel'><h2>Latest Stock Analysis</h2><p>No stock analysis runs found.</p></section>"
    report = latest.get("complete_report") or latest.get("error_report")
    steps = "".join(
        "<div class='step'>"
        f"{_status_badge(step.get('status', 'pending'))}"
        f"<strong>{_e(step.get('label', step.get('id', 'Step')))}</strong>"
        f"<span class='muted'>{_e(_short(step.get('error') or step.get('report_path') or '', 42))}</span>"
        "</div>"
        for step in latest.get("steps", [])
    )
    events = "".join(
        f"<li>{_e(_short(event.get('message', ''), 120))}</li>"
        for event in latest.get("events", [])[-8:]
    )
    steps_html = f"<div class='steps'>{steps}</div>" if steps else ""
    events_html = f"<ol class='events'>{events}</ol>" if events else ""
    return (
        "<section class='panel'>"
        f"<h2>Latest Stock Analysis: {_e(latest.get('ticker', ''))}</h2>"
        "<div class='grid'>"
        f"<div class='metric'><span>Status</span><strong>{_status_badge(latest.get('status', 'unknown'))}</strong></div>"
        f"<div class='metric'><span>Analysis Date</span><strong>{_e(latest.get('analysis_date', ''))}</strong></div>"
        f"<div class='metric'><span>Model</span><strong>{_e(latest.get('llm_provider') or 'N/A')}</strong><span>{_e(_short(latest.get('deep_think_llm') or '', 38))}</span></div>"
        f"<div class='metric'><span>Report</span><strong>{_link(report, 'Open')}</strong></div>"
        "</div>"
        f"{steps_html}"
        f"{events_html}"
        "</section>"
    )


def render_latest_panel(latest: dict | None) -> str:
    if not latest:
        return "<section class='panel'><h2>Latest Run</h2><p>No Value Discover runs found.</p></section>"
    payload_path = latest.get("public_equity_payload")
    payload = _load_json(Path(payload_path)) if payload_path else None
    metrics = ""
    if payload:
        for item in payload.get("snapshot", []):
            metrics += (
                "<div class='metric'>"
                f"<span>{_e(item.get('label', 'Metric'))}</span>"
                f"<strong>{_e(item.get('value', 'N/A'))}</strong>"
                f"<span>{_e(item.get('unit', ''))}</span>"
                "</div>"
            )
    fallback_metric = "<div class='metric'><span>Status</span><strong>Indexed</strong></div>"
    return (
        "<section class='panel'>"
        f"<h2>Latest Run: {_e(latest['date'])}</h2>"
        "<div class='grid'>"
        f"{metrics or fallback_metric}"
        "</div>"
        "<p>"
        f"{_link(latest.get('value_discover_markdown'), 'Open shortlist')} &nbsp; "
        f"{_link(latest.get('public_equity_markdown'), 'Open Public Equity triage')} &nbsp; "
        f"{_link(latest.get('llm_summary'), 'Open LLM summary')}"
        "</p>"
        "</section>"
    )


def run(host: str, port: int) -> None:
    write_report_index(REPORT_ROOT)
    server = ThreadingHTTPServer((host, port), TradingAgentsHandler)
    print(f"TradingAgents web UI: http://{host}:{port}")
    server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve TradingAgents reports UI")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    run(args.host, args.port)


def _is_allowed_report_path(path: Path) -> bool:
    try:
        resolved = path.resolve()
        return resolved.is_file() and resolved.is_relative_to(REPORT_ROOT.resolve())
    except OSError:
        return False


def _link(path: str | None, label: str) -> str:
    if not path:
        return "<span class='muted'>N/A</span>"
    return f"<a href='/file?path={html.escape(path, quote=True)}'>{_e(label)}</a>"


def _load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


def _short(value: object, limit: int = 80) -> str:
    text = str(value).replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _provider_options(selected: str) -> str:
    options = []
    for label, value in WEB_PROVIDER_OPTIONS:
        if value not in MODEL_OPTIONS:
            continue
        options.append(
            f"<option value='{_e(value)}'{' selected' if value == selected else ''}>{_e(label)}</option>"
        )
    return "".join(options)


def _model_options(mode: str, selected_provider: str, selected_model: str) -> str:
    groups = []
    for provider_label, provider in WEB_PROVIDER_OPTIONS:
        mode_options = MODEL_OPTIONS.get(provider, {}).get(mode)
        if not mode_options:
            continue
        options = []
        for label, value in mode_options:
            if value == "custom":
                continue
            selected = value == selected_model and provider == selected_provider
            options.append(
                f"<option value='{_e(value)}' data-provider='{_e(provider)}'{' selected' if selected else ''}>{_e(label)}</option>"
            )
        groups.append(f"<optgroup label='{_e(provider_label)}'>{''.join(options)}</optgroup>")
    return "".join(groups)


def _status_badge(status: object) -> str:
    normalized = str(status or "unknown").lower()
    class_name = normalized if normalized in {"running", "ok", "error", "pending", "queued"} else "pending"
    return f"<span class='badge status-{_e(class_name)}'>{_e(normalized)}</span>"


if __name__ == "__main__":
    main()
