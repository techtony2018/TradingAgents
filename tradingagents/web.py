"""Local web UI for TradingAgents reports and analysis workflow."""

from __future__ import annotations

import argparse
import html
import json
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from tradingagents.report_index import build_report_index, write_report_index


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORT_ROOT = PROJECT_ROOT / "reports"


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
    latest_panel = render_latest_panel(latest)
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
    main {{ max-width: 1180px; margin: 0 auto; padding: 22px; }}
    .toolbar {{ display:flex; gap:10px; align-items:center; }}
    button, .button {{ border:1px solid var(--accent); background:var(--accent); color:white; border-radius:6px; padding:8px 12px; font-weight:600; cursor:pointer; text-decoration:none; }}
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
    @media (max-width: 780px) {{ header {{ display:block; }} .toolbar {{ margin-top:12px; }} .grid {{ grid-template-columns:1fr 1fr; }} table {{ font-size:12px; }} }}
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
    {latest_panel}
    <section class="panel">
      <h2>Report History</h2>
      <table>
        <thead><tr><th>Date</th><th>Shortlist</th><th>CSV</th><th>Public Equity</th><th>Payload</th><th>LLM</th></tr></thead>
        <tbody>{''.join(rows) or '<tr><td colspan="6">No reports found.</td></tr>'}</tbody>
      </table>
    </section>
    <p class="muted">Outputs are research support, not financial advice or order recommendations.</p>
  </main>
</body>
</html>"""


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


if __name__ == "__main__":
    main()
