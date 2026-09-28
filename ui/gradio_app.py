import gradio as gr
import requests
import json
import os
import time
from html import escape


# Gradio calls the API in the same server process, including on Render.
API_BASE = f"http://localhost:{os.getenv('PORT') or '8000'}/api/v1"


def submit_research(question: str, depth: str) -> str:
    """Submit research question and return run_id."""
    try:
        resp = requests.post(
            f"{API_BASE}/research",
            json={"question": question, "depth": depth, "output_format": "report"},
            timeout=10
        )
        data = resp.json()
        return data.get("run_id", "error")
    except Exception as e:
        return f"error: {str(e)}"


def poll_status(run_id: str) -> str:
    """Poll run status."""
    if run_id.startswith("error"):
        return run_id

    try:
        resp = requests.get(f"{API_BASE}/status/{run_id}", timeout=5)
        return resp.json().get("status", "unknown")
    except:
        return "error"


def get_report(run_id: str) -> tuple:
    """Fetch final report - returns (markdown, structured_json)."""
    if not run_id or run_id.startswith("error"):
        return "No run ID available", {}

    try:
        resp = requests.get(f"{API_BASE}/report/{run_id}", timeout=10)
        if resp.status_code == 404:
            return "Report not ready yet. Check status.", {}
        data = resp.json()

        # Extract markdown report
        markdown = data.get("report_markdown", "No report available")

        # Build structured JSON from the report data
        structured = {
            "run_id": data.get("run_id", ""),
            "question": data.get("question", ""),
            "quality_score": data.get("quality_score"),
            "total_latency_ms": data.get("total_latency_ms"),
            "chunks_retrieved": data.get("chunks_retrieved", 0),
            "sources": data.get("sources", []),
            "token_usage": data.get("token_usage", {})
        }

        return markdown, structured
    except Exception as e:
        return f"Error fetching report: {str(e)}", {}


def to_json_code(structured: dict) -> str:
    """Render a dict as pretty-printed JSON text for gr.Code(language='json')."""
    return json.dumps(structured, indent=2, ensure_ascii=False)


def download_pdf(run_id: str):
    """Fetch the PDF from the API and save it to a temp file for gr.DownloadButton."""
    if not run_id or run_id.startswith("error"):
        return None
    try:
        resp = requests.get(f"{API_BASE}/report/{run_id}/pdf", timeout=30)
        if resp.status_code != 200:
            return None
        import tempfile
        path = f"{tempfile.gettempdir()}/insyfy_report_{run_id}.pdf"
        with open(path, "wb") as f:
            f.write(resp.content)
        return path
    except Exception:
        return None


def send_report_email_ui(run_id: str, to_email: str) -> str:
    """Trigger the email-send endpoint. Returns a status message for the UI."""
    if not run_id or run_id.startswith("error"):
        return "Start or open a report first."
    if not to_email or "@" not in to_email:
        return "Enter a valid email address."
    try:
        resp = requests.post(
            f"{API_BASE}/report/{run_id}/email",
            json={"to_email": to_email},
            timeout=30
        )
        data = resp.json()
        if resp.status_code == 200:
            return data.get("message", "Sent.")
        return data.get("detail", "Failed to send email.")
    except Exception as e:
        return f"Error: {str(e)}"


def stream_events(run_id: str):
    """
    Generator for SSE events, yielding a growing list of chat messages
    (for gr.Chatbot(type="messages")) — each yield includes the FULL
    accumulated history, not just the newest line, so nothing gets
    overwritten as new events arrive.
    """
    messages = []

    def _add(content: str):
        messages.append({"role": "assistant", "content": content})
        return list(messages)

    if run_id.startswith("error"):
        yield _add(f"Error starting research: {run_id}")
        return

    yield _add(f"Run `{run_id}` — connecting to the event stream...")

    try:
        resp = requests.get(
            f"{API_BASE}/stream/{run_id}",
            stream=True,
            timeout=300
        )

        for line in resp.iter_lines():
            if line:
                line = line.decode("utf-8")
                if line.startswith("data: "):
                    try:
                        event = json.loads(line[6:])
                        event_type = event.get("type", "unknown")

                        if event_type == "run_started":
                            yield _add(f"**Research started:** {event.get('question', '')[:120]}")
                        elif event_type == "node_start":
                            yield _add(f"▶️ `{event['node']}` started...")
                        elif event_type == "node_complete":
                            latency = event.get("latency_ms", 0)
                            tokens = event.get("tokens")
                            token_str = f" · {tokens} tokens" if tokens else ""
                            yield _add(f"✅ `{event['node']}` complete — {latency}ms{token_str}")
                        elif event_type == "node_error":
                            yield _add(f"⚠️ `{event.get('node', 'unknown')}` errored: {event.get('message', '')}")
                        elif event_type == "declined":
                            yield _add(f"❌ **Declined:** {event.get('reason', '')}")
                        elif event_type == "done":
                            quality = event.get("quality_score")
                            latency = event.get("total_latency_ms")
                            summary = "🎉 **Pipeline complete!**"
                            if quality:
                                summary += f" Quality: {quality:.2f}"
                            if latency:
                                summary += f" · Total: {latency}ms"
                            yield _add(summary)
                            break
                        elif event_type == "error":
                            msg = f"❌ **Error:** {event.get('error_type', 'Unknown')} — {event.get('message', '')}"
                            yield _add(msg)
                            break

                    except json.JSONDecodeError:
                        continue

    except Exception as e:
        yield _add(f"❌ Stream error: {str(e)}")


STATUS_EMOJI = {
    "completed": "✅",
    "failed": "❌",
    "declined": "⚠️",
    "running": "⏳"
}


def fetch_history_runs(limit: int = 10) -> list:
    """Fetch recent research runs as raw dicts."""
    try:
        resp = requests.get(f"{API_BASE}/history?limit={limit}", timeout=5)
        data = resp.json()
        return data.get("runs", [])
    except Exception:
        return []


def format_history_markdown(runs: list) -> str:
    """Summarize the activity list without duplicating the selectable cards."""
    if not runs:
        return "**A fresh start**\n\nYour research will appear here. Open any entry to revisit its report."
    noun = "run" if len(runs) == 1 else "runs"
    return f"{len(runs)} recent {noun} · Select an entry to open it."


def history_dropdown_choices(runs: list) -> list:
    """Build native, keyboard-accessible radio cards using the existing run IDs."""
    choices = []
    for run in runs:
        question = " ".join(run.get("question", "Untitled research").split())
        q = question[:87] + "…" if len(question) > 90 else question
        score = run.get("quality_score")
        score_str = f" · Quality {score:.0%}" if score is not None else ""
        status = (run.get("status") or "unknown").replace("_", " ").capitalize()
        run_id = run.get("run_id", "")
        choices.append((f"{q}\n{status}{score_str}", run_id))
    return choices


def fetch_metrics_markdown() -> str:
    """Fetch aggregate metrics and render as markdown. Real numbers only —
    shows 'no runs yet' rather than fabricating anything when empty."""
    try:
        resp = requests.get(f"{API_BASE}/metrics", timeout=5)
        data = resp.json()
    except Exception as e:
        return f"Error loading metrics: {str(e)}"

    if data.get("total_runs", 0) == 0:
        return "No runs logged yet. Your research metrics will appear after your first run."

    lines = [
        f"### Based on the last {data['window_size']} run(s)",
        "",
        f"- **Success rate:** {data['success_rate'] * 100:.1f}%  "
        f"({data['completed']} completed, {data['declined']} declined, {data['failed']} failed)",
        f"- **Avg latency:** {data['avg_latency_ms']:,} ms  "
        f"(p50: {data['p50_latency_ms']:,} ms, p95: {data['p95_latency_ms']:,} ms)",
        f"- **Avg quality score:** {data['avg_quality_score'] if data['avg_quality_score'] is not None else 'N/A'}",
        f"- **Avg retries per run:** {data['avg_retries_per_run']}",
        f"- **Total tokens used:** {data['total_tokens']:,}  "
        f"(avg {data['avg_tokens_per_run']:,} per run)",
    ]
    if data.get("cost_estimate_configured"):
        lines.append(f"- **Total estimated cost:** ${data['total_estimated_cost_usd']:.4f}")
    else:
        lines.append(
            "- **Cost estimate:** unavailable"
        )
    return "\n".join(lines)


REPORT_CSS = """
/* Mobile first. The desktop grid reserves space for the fixed activity rail. */
:root {
    --insyfy-bg: #121214;
    --insyfy-surface: #1a1a1e;
    --insyfy-raised: #222228;
    --insyfy-input: #151518;
    --insyfy-ink: #f2f2f5;
    --insyfy-muted: #b5b5c2;
    --insyfy-line: #34343d;
    --insyfy-accent: #b5adff;
    --insyfy-accent-soft: #292637;
    --insyfy-gutter: 16px;
    --insyfy-history-width: clamp(208px, 21vw, 300px);
}
body.insyfy-light {
    --insyfy-bg: #f5f5f7;
    --insyfy-surface: #ffffff;
    --insyfy-raised: #ededf2;
    --insyfy-input: #f8f8fa;
    --insyfy-ink: #202027;
    --insyfy-muted: #585866;
    --insyfy-line: #d7d7e0;
    --insyfy-accent: #6351c7;
    --insyfy-accent-soft: #eeebff;
}
html, body, .gradio-container {
    background: var(--insyfy-bg) !important;
    color: var(--insyfy-ink) !important;
}
.gradio-container, body .gradio-container.dark {
    --body-background-fill: var(--insyfy-bg);
    --body-text-color: var(--insyfy-ink);
    --body-text-color-subdued: var(--insyfy-muted);
    --background-fill-primary: var(--insyfy-surface);
    --background-fill-secondary: var(--insyfy-raised);
    --block-background-fill: var(--insyfy-surface);
    --block-border-color: var(--insyfy-line);
    --block-label-background-fill: var(--insyfy-surface);
    --block-label-text-color: var(--insyfy-muted);
    --block-title-text-color: var(--insyfy-ink);
    --input-background-fill: var(--insyfy-input);
    --input-border-color: var(--insyfy-line);
    --input-border-color-focus: var(--insyfy-accent);
    --input-text-color: var(--insyfy-ink);
    --input-placeholder-color: var(--insyfy-muted);
    --border-color-primary: var(--insyfy-line);
    --border-color-accent: var(--insyfy-accent);
    --link-text-color: var(--insyfy-accent);
    --button-secondary-background-fill: var(--insyfy-raised);
    --button-secondary-background-fill-hover: var(--insyfy-accent-soft);
    --button-secondary-border-color: var(--insyfy-line);
    --button-secondary-text-color: var(--insyfy-ink);
    --button-primary-background-fill: #aaa0fa;
    --button-primary-background-fill-hover: #bdb5ff;
    --button-primary-border-color: transparent;
    --button-primary-text-color: #171320;
    --checkbox-background-color: var(--insyfy-input);
    --checkbox-background-color-selected: var(--insyfy-accent);
    --checkbox-label-background-fill: var(--insyfy-input);
    --checkbox-label-background-fill-selected: var(--insyfy-accent-soft);
    --checkbox-label-text-color: var(--insyfy-ink);
    --checkbox-label-text-color-selected: var(--insyfy-ink);
    --shadow-drop: none;
    max-width: none !important;
    padding: 0 var(--insyfy-gutter) 28px !important;
    font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif !important;
    color-scheme: dark;
}
body.insyfy-light .gradio-container { color-scheme: light; }
.gradio-container .prose, .gradio-container .prose p,
.gradio-container .prose li { color: var(--insyfy-ink); }
.gradio-container .prose :is(h1, h2, h3, h4, strong) { color: var(--insyfy-ink); }
.gradio-container .prose a { color: var(--insyfy-accent); }
.gradio-container .form { border: 0 !important; background: transparent !important; }
.gradio-container .block { box-shadow: none !important; }
.gradio-container input, .gradio-container textarea {
    caret-color: var(--insyfy-accent);
    font-size: 14px !important;
}
.gradio-container :is(button.primary, button.secondary),
.gradio-container a.download-link {
    border-radius: 12px !important;
    min-height: 42px;
    font-weight: 500 !important;
    font-size: 13px !important;
    transition: background-color 180ms ease, border-color 180ms ease, transform 180ms ease;
}
.gradio-container :is(button, a, input, textarea):focus-visible {
    outline: 2px solid var(--insyfy-accent) !important;
    outline-offset: 3px;
}
.gradio-container button:disabled { opacity: .5; cursor: not-allowed; }
#app-header {
    align-items: center;
    flex-wrap: nowrap !important;
    min-height: 88px;
    gap: 12px;
    padding: 20px 0;
    border-bottom: 1px solid var(--insyfy-line);
    margin-bottom: 8px;
}
.brand { display: flex; align-items: center; gap: 12px; }
.brand-mark {
    display: grid; place-items: center; width: 40px; height: 40px;
    border: 1px solid var(--insyfy-line); border-radius: 12px;
    background: var(--insyfy-accent-soft); color: var(--insyfy-accent);
}
.brand h1 { margin: 0; font-size: 24px; font-weight: 600; letter-spacing: -.8px; line-height: 1.2; }
.brand p { margin: 4px 0 0; color: var(--insyfy-muted); font-size: 12px; }
#insyfy-theme-toggle { flex: 0 0 auto !important; min-width: 100px !important; width: auto; }
#workspace {
    display: grid !important;
    grid-template-columns: minmax(0, 1fr);
    gap: 20px;
    align-items: start;
    overflow: visible !important;
}
#workspace > .column { min-width: 0 !important; width: 100%; }
.dashboard-panel {
    background: var(--insyfy-surface) !important;
    border: 1px solid var(--insyfy-line) !important;
    border-radius: 12px !important;
    padding: 20px !important;
    gap: 18px !important;
    min-width: 0 !important;
}
.section-heading h2 { margin: 0; font-size: 16px; font-weight: 600; letter-spacing: -.3px; }
.section-heading p, .muted-copy, .muted-copy p {
    color: var(--insyfy-muted) !important; font-size: 12px !important; line-height: 1.7;
}
.section-heading p { margin: 6px 0 0; }
.eyebrow { color: var(--insyfy-muted); font-size: 10px; font-weight: 600; letter-spacing: 1.3px; text-transform: uppercase; margin-bottom: 8px; }
.section-divider { border-top: 1px solid var(--insyfy-line); padding-top: 18px; }
.dashboard-card { text-align: left !important; justify-content: flex-start !important; padding: 12px 14px !important; }
#report-heading { align-items: center; gap: 10px; }
.status-badge {
    display: inline-flex; align-items: center; gap: 7px; padding: 6px 10px;
    border-radius: 999px; background: var(--insyfy-raised);
    color: var(--insyfy-muted); font-size: 11px; white-space: nowrap;
}
.status-badge::before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.status-badge.active { color: var(--insyfy-accent); }
.report-empty {
    min-height: 340px; display: flex; flex-direction: column;
    align-items: center; justify-content: center; text-align: center; padding: 36px 12px;
}
.document-icon {
    width: 56px; height: 64px; border: 1px solid var(--insyfy-line);
    border-radius: 12px; background: var(--insyfy-input);
    display: flex; flex-direction: column; justify-content: center; gap: 7px; padding: 14px;
    margin-bottom: 24px; transform: rotate(-5deg);
}
.document-icon span { display: block; height: 3px; border-radius: 2px; background: var(--insyfy-muted); opacity: .65; }
.document-icon span:last-child { width: 65%; background: var(--insyfy-accent); }
.report-empty h3 { margin: 0 0 10px; font-size: 20px; font-weight: 600; letter-spacing: -.4px; }
.report-empty p { max-width: 300px; margin: 0; font-size: 13px; line-height: 1.8; color: var(--insyfy-muted); }
.report-empty .report-capabilities { margin-top: 24px; font-size: 11px; }
.report-markdown { font-size: 14px; line-height: 1.8; overflow-wrap: anywhere; padding: 4px 2px !important; }
.report-markdown h1 { font-size: 24px !important; font-weight: 600 !important; letter-spacing: -.5px; }
.report-markdown h2 { font-size: 19px !important; margin-top: 28px !important; }
.report-markdown h3 { font-size: 16px !important; }
.report-markdown pre, .report-markdown code { background: var(--insyfy-input) !important; color: var(--insyfy-ink) !important; border-radius: 6px; }
.report-markdown a { text-underline-offset: 3px; }
.report-markdown a:hover { text-decoration: underline; }
.report-markdown table { display: block; max-width: 100%; overflow-x: auto; }
.report-markdown hr { border-color: var(--insyfy-line); }
.report-actions { border-top: 1px solid var(--insyfy-line); padding-top: 18px; gap: 12px !important; }
.trace-panel { background: var(--insyfy-input) !important; border: 1px solid var(--insyfy-line) !important; border-radius: 12px !important; }
.trace-panel .message { background: var(--insyfy-raised) !important; color: var(--insyfy-ink) !important; font-size: 12px !important; }
.trace-panel .message code { color: var(--insyfy-accent) !important; background: transparent !important; }
.structured-code { border-color: var(--insyfy-line) !important; border-radius: 12px !important; }
#activity-panel { position: static; min-width: 0 !important; flex-wrap: nowrap !important; }
#activity-panel > * { flex-shrink: 0 !important; }
#activity-heading { align-items: center; gap: 8px; flex-wrap: nowrap !important; }
#refresh-history { min-width: 64px !important; min-height: 34px; padding: 4px 8px !important; font-size: 11px !important; }
#history-cards { background: transparent !important; border: 0 !important; padding: 2px !important; overflow: visible !important; }
#history-cards .wrap { flex-direction: column !important; gap: 10px !important; }
#history-cards label {
    width: 100%; min-width: 0; margin: 0 !important; padding: 14px 12px !important;
    align-items: flex-start !important; border: 1px solid var(--insyfy-line) !important;
    border-radius: 12px !important; background: var(--insyfy-input) !important;
    color: var(--insyfy-ink) !important; cursor: pointer;
    transition: transform 180ms ease, background-color 180ms ease, border-color 180ms ease;
}
#history-cards label span { white-space: pre-line; overflow-wrap: anywhere; font-size: 12px; line-height: 1.8; color: var(--insyfy-muted); }
#history-cards label span::first-line { color: var(--insyfy-ink); font-weight: 500; }
#history-cards input { margin-top: 5px; flex-shrink: 0; accent-color: var(--insyfy-accent); }
#history-cards label:has(input:checked) { border-color: var(--insyfy-accent) !important; background: var(--insyfy-accent-soft) !important; }
#history-cards label:has(input:focus-visible) { outline: 2px solid var(--insyfy-accent); outline-offset: 3px; }
#history-cards label:has(input:disabled) { cursor: wait; opacity: .6; }
.loading-skeleton { padding: 8px 0; }
.loading-skeleton p { margin: 0 0 24px; color: var(--insyfy-muted); font-size: 12px; }
.skeleton-line {
    height: 12px; border-radius: 6px; margin-bottom: 12px;
    background: linear-gradient(100deg, var(--insyfy-raised) 30%, var(--insyfy-line) 50%, var(--insyfy-raised) 70%);
    background-size: 220% 100%; animation: insyfy-shimmer 1.8s ease-in-out infinite;
}
.skeleton-line.title { width: 66%; height: 24px; margin-bottom: 28px; }
.skeleton-line.short { width: 72%; }
.skeleton-line.medium { width: 86%; }
.skeleton-paragraph { margin-bottom: 28px; }
.report-skeleton { min-height: 340px; padding-top: 32px; }
.history-skeleton .skeleton-paragraph { border: 1px solid var(--insyfy-line); border-radius: 12px; padding: 16px 12px 4px; margin-bottom: 12px; }
@keyframes insyfy-shimmer { from { background-position: 150% 0; } to { background-position: -70% 0; } }
@media (hover: hover) and (pointer: fine) {
    .dashboard-card:not(:disabled):hover,
    #history-cards label:not(:has(input:disabled)):hover {
        transform: scale(1.02); background: var(--insyfy-accent-soft) !important;
        border-color: var(--insyfy-accent) !important;
    }
}
@media (min-width: 768px) {
    :root { --insyfy-gutter: clamp(20px, 2.2vw, 40px); }
    #workspace { grid-template-columns: clamp(180px, 22vw, 296px) minmax(0, 1fr) var(--insyfy-history-width); }
    #activity-panel {
        position: fixed !important; top: 112px; right: var(--insyfy-gutter); bottom: 24px;
        width: var(--insyfy-history-width) !important; overflow-y: auto !important;
        scrollbar-width: thin; scrollbar-color: var(--insyfy-line) transparent;
        z-index: 10; align-content: start;
    }
    #research-controls { grid-column: 1; }
    #report-panel { grid-column: 2; }
}
@media (min-width: 768px) and (max-width: 1100px) {
    #workspace { gap: 12px; }
    .dashboard-panel { padding: 14px !important; }
    #activity-heading { flex-wrap: wrap !important; }
    #activity-heading > * { flex-basis: 100% !important; }
    .report-empty { padding: 28px 0; }
}
@media (max-width: 767px) {
    #activity-panel { max-height: none; }
    .report-markdown { max-height: none !important; }
    .report-empty { min-height: 280px; }
}
@media (prefers-reduced-motion: reduce) {
    .skeleton-line { animation: none; }
    .dashboard-card, #history-cards label, .gradio-container button { transition: none !important; }
    .dashboard-card:hover, #history-cards label:hover { transform: none !important; }
}
"""


EMPTY_REPORT = """
<div class="report-empty">
    <div class="document-icon" aria-hidden="true"><span></span><span></span><span></span></div>
    <h3>Make room for your next insight.</h3>
    <p>Start with a question. Get a clear research report, grounded in evidence and linked to its sources.</p>
    <p class="report-capabilities">Web research &nbsp; · &nbsp; Cited sources &nbsp; · &nbsp; Quality review</p>
</div>
"""


def skeleton_html(label: str, kind: str = "report") -> str:
    """An announced loading state with decorative, reduced-motion-safe lines."""
    paragraph = '<div class="skeleton-paragraph" aria-hidden="true">' + "".join(
        f'<div class="skeleton-line {width}"></div>' for width in ("", "medium", "short")
    ) + "</div>"
    title = '<div class="skeleton-line title" aria-hidden="true"></div>' if kind == "report" else ""
    return (
        f'<div class="loading-skeleton {kind}-skeleton" role="status" aria-live="polite" aria-busy="true">'
        f'<p>{escape(label)}</p>{title}{paragraph * (3 if kind != "metrics" else 1)}</div>'
    )


def status_badge(label: str, active: bool = False) -> str:
    return f'<span class="status-badge{" active" if active else ""}" role="status">{escape(label)}</span>'


THEME_JS = """
() => {
    let saved = 'dark';
    try { saved = localStorage.getItem('insyfy-theme') || 'dark'; } catch (e) {}
    const dark = __DARK__;
    document.body.classList.toggle('insyfy-dark', dark);
    document.body.classList.toggle('insyfy-light', !dark);
    document.documentElement.classList.toggle('dark', dark);
    document.body.classList.toggle('dark', dark);
    document.querySelector('.gradio-container')?.classList.toggle('dark', dark);
    const button = document.querySelector('#insyfy-theme-toggle');
    if (button) {
        button.setAttribute('aria-label', dark ? 'Switch to light appearance' : 'Switch to dark appearance');
        button.setAttribute('aria-pressed', String(!dark));
    }
    try { localStorage.setItem('insyfy-theme', dark ? 'dark' : 'light'); } catch (e) {}
}
"""


def create_ui():
    """A responsive research workspace; API helpers and run IDs stay unchanged."""
    with gr.Blocks(title="InSyfy — Research Workspace", fill_width=True) as demo:
        with gr.Row(elem_id="app-header"):
            gr.HTML('''<div class="brand">
                <div class="brand-mark" aria-hidden="true">
                    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6">
                        <path d="M5 17V7m7 13V4m7 13V7M2 12h20" stroke-linecap="round"/>
                    </svg>
                </div><div><h1>InSyfy</h1><p>Your research workspace</p></div></div>''', min_width=0)
            theme_toggle_btn = gr.Button("◐  Appearance", elem_id="insyfy-theme-toggle", scale=0)

        with gr.Row(elem_id="workspace"):
            with gr.Column(elem_id="research-controls", min_width=0):
                with gr.Column(elem_classes=["dashboard-panel"], min_width=0):
                    gr.HTML('<div class="section-heading"><div class="eyebrow">Start something new</div>'
                            '<h2>What are you exploring?</h2><p>Turn a question into a clearer picture.</p></div>')
                    question_input = gr.Textbox(
                        label="Research question", placeholder="What would you like to understand?",
                        lines=4, elem_id="research-question",
                    )
                    depth_selector = gr.Dropdown(
                        choices=[("Quick overview", "quick"), ("Balanced research", "standard"), ("Deep dive", "deep")],
                        value="standard", label="Research depth", interactive=True,
                    )
                    submit_btn = gr.Button("Start research  →", variant="primary", elem_id="start-research")
                    gr.HTML('<div class="eyebrow section-divider">A little inspiration</div>')
                    presets = [
                        ("Explore a market  ↗", "What are the latest trends and emerging opportunities in the AI developer tools market?"),
                        ("Compare competitors  ↗", "Compare the leading AI research assistants, their capabilities, pricing, and target customers."),
                        ("Track new research  ↗", "What are the latest advances in retrieval-augmented generation, and which approaches have the strongest evidence?"),
                    ]
                    preset_buttons = [
                        (gr.Button(label, elem_classes=["dashboard-card"]), question)
                        for label, question in presets
                    ]

                with gr.Column(elem_classes=["dashboard-panel"], min_width=0):
                    gr.HTML('<div class="section-heading"><h2>Agent activity</h2><p>Follow the research as it happens.</p></div>')
                    trace_output = gr.Chatbot(
                        value=[], height=240, show_label=False, elem_classes=["trace-panel"],
                        placeholder="Your research steps will appear here.",
                    )
                    with gr.Accordion("Workspace metrics", open=False):
                        metrics_loading = gr.HTML(skeleton_html("Loading metrics…", "metrics"))
                        metrics_output = gr.Markdown(visible=False, elem_classes=["muted-copy"])
                        refresh_metrics_btn = gr.Button("Refresh metrics", size="sm")

            with gr.Column(elem_id="report-panel", elem_classes=["dashboard-panel"], min_width=0):
                with gr.Row(elem_id="report-heading"):
                    gr.HTML('<div class="section-heading"><div class="eyebrow">From question to evidence</div>'
                            '<h2>Research report</h2></div>', min_width=0)
                    report_status = gr.HTML(status_badge("Ready"), scale=0, min_width=80)
                report_empty = gr.HTML(EMPTY_REPORT)
                report_loading = gr.HTML(skeleton_html("Preparing your research report…"), visible=False)
                report_output = gr.Markdown(
                    value="", elem_classes=["report-markdown"], max_height=650,
                )
                with gr.Column(elem_classes=["report-actions"], min_width=0):
                    download_pdf_btn = gr.DownloadButton("↓  Download PDF", interactive=False)
                    with gr.Row():
                        email_input = gr.Textbox(
                            placeholder="you@example.com", label="Email this report", scale=3, min_width=140,
                        )
                        send_email_btn = gr.Button("Send report", scale=1, min_width=100, interactive=False)
                    email_status = gr.Markdown("", elem_classes=["muted-copy"])
                    with gr.Accordion("Structured output", open=False):
                        json_output = gr.Code(language="json", show_label=False, elem_classes=["structured-code"], max_lines=25)

            with gr.Column(elem_id="activity-panel", elem_classes=["dashboard-panel"], min_width=0):
                with gr.Row(elem_id="activity-heading"):
                    gr.HTML('<div class="section-heading"><h2>Activity History</h2></div>', min_width=0)
                    refresh_history_btn = gr.Button("↻ Refresh", elem_id="refresh-history", scale=0, size="sm")
                gr.Markdown("Pick up where you left off.", elem_classes=["muted-copy"])
                history_loading = gr.HTML(skeleton_html("Loading recent activity…", "history"))
                history_output = gr.Markdown(visible=False, elem_classes=["muted-copy"])
                history_selector = gr.Radio(
                    choices=[], label="Recent research reports", show_label=False, container=False,
                    interactive=True, visible=False, elem_id="history-cards", min_width=0,
                )

        run_id_state = gr.State("")

        # Loading changes presentation only. The existing API helpers remain the
        # source of truth, and all actions continue to use the session's run ID.
        report_components = [
            report_empty, report_loading, report_output, report_status, json_output,
            submit_btn, history_selector, download_pdf_btn, send_email_btn, email_status,
        ]

        def begin_report_loading():
            return {
                report_empty: gr.update(visible=False),
                report_loading: gr.update(visible=True),
                # Keep the renderer mounted across runs; only clear its content.
                report_output: gr.update(value=""),
                report_status: status_badge("In progress", active=True),
                json_output: "", email_status: "",
                submit_btn: gr.update(interactive=False),
                history_selector: gr.update(interactive=False),
                download_pdf_btn: gr.update(value=None, interactive=False),
                send_email_btn: gr.update(interactive=False),
            }

        def finish_report_loading(markdown, structured, label="Ready"):
            has_report = bool(structured.get("run_id")) and bool(markdown.strip()) and markdown not in (
                "No report generated", "No report available", "Report not ready yet. Check status.",
            )
            return {
                report_empty: gr.update(visible=False),
                report_loading: gr.update(visible=False),
                report_output: gr.update(value=markdown),
                report_status: status_badge(label),
                json_output: to_json_code(structured) if structured else "",
                submit_btn: gr.update(interactive=True),
                history_selector: gr.update(interactive=True),
                download_pdf_btn: gr.update(value=None, interactive=has_report),
                send_email_btn: gr.update(interactive=has_report),
            }

        def on_submit(question, depth):
            yield {**begin_report_loading(), run_id_state: "", trace_output: []}
            run_id = submit_research(question, depth)
            message = f"Failed to start: {run_id}" if run_id.startswith("error") else "Research started. Connecting to live activity…"
            yield {run_id_state: run_id, trace_output: [{"role": "assistant", "content": message}]}

        def on_stream(run_id):
            yield from stream_events(run_id)

        def on_complete(run_id):
            try:
                if not run_id or run_id.startswith("error"):
                    return finish_report_loading("We couldn't start this research. Check your question and try again.", {}, "Unable to start")
                for _ in range(60):
                    status = poll_status(run_id)
                    if status in ("completed", "failed", "declined", "error"):
                        break
                    time.sleep(1)
                if status not in ("completed", "declined"):
                    message = (
                        "Research could not finish. Check Agent activity for details, then try again."
                        if status in ("failed", "error") else
                        "Research is still running. Reopen it from Activity History in a moment."
                    )
                    return finish_report_loading(message, {}, "Needs attention" if status in ("failed", "error") else "Still running")
                report_md, structured = get_report(run_id)
                return finish_report_loading(report_md, structured, "Complete" if structured else "Unavailable")
            except Exception:
                return finish_report_loading("The report couldn't be loaded. Try opening it from Activity History.", {}, "Unavailable")

        def on_refresh(selected_run_id):
            yield {
                history_loading: gr.update(visible=True),
                history_output: gr.update(visible=False),
                history_selector: gr.update(visible=False),
            }
            runs = fetch_history_runs()
            choices = history_dropdown_choices(runs)
            selected = selected_run_id if any(value == selected_run_id for _, value in choices) else None
            yield {
                history_loading: gr.update(visible=False),
                history_output: gr.update(value=format_history_markdown(runs), visible=True),
                history_selector: gr.update(choices=choices, value=selected, visible=bool(choices)),
            }

        def on_metrics():
            yield {metrics_loading: gr.update(visible=True), metrics_output: gr.update(visible=False)}
            try:
                metrics = fetch_metrics_markdown()
            except Exception:
                metrics = "Metrics couldn't be loaded. Please try refreshing."
            yield {metrics_loading: gr.update(visible=False), metrics_output: gr.update(value=metrics, visible=True)}

        def on_open_history(selected_run_id):
            if not selected_run_id:
                return
            yield begin_report_loading()
            try:
                report_md, structured = get_report(selected_run_id)
                yield {
                    **finish_report_loading(report_md, structured, "From history" if structured else "Unavailable"),
                    run_id_state: selected_run_id,
                    trace_output: [{"role": "assistant", "content": "Opened a saved research report."}],
                }
            except Exception:
                yield finish_report_loading("This report couldn't be opened. Please try again.", {}, "Unavailable")

        history_components = [history_loading, history_output, history_selector]
        metrics_components = [metrics_loading, metrics_output]
        submit_btn.click(
            fn=on_submit, inputs=[question_input, depth_selector],
            outputs=[run_id_state, trace_output, *report_components], show_progress="hidden",
        ).then(
            fn=on_stream, inputs=[run_id_state], outputs=trace_output, show_progress="hidden",
        ).then(
            fn=on_complete, inputs=[run_id_state], outputs=report_components, show_progress="hidden",
        ).then(
            fn=on_refresh, inputs=history_selector, outputs=history_components, show_progress="hidden",
        ).then(fn=on_metrics, outputs=metrics_components, show_progress="hidden")

        history_selector.input(
            fn=on_open_history, inputs=history_selector,
            outputs=[*report_components, run_id_state, trace_output], show_progress="hidden", trigger_mode="once",
        )
        refresh_history_btn.click(fn=on_refresh, inputs=history_selector, outputs=history_components, show_progress="hidden")
        refresh_metrics_btn.click(fn=on_metrics, outputs=metrics_components, show_progress="hidden")
        download_pdf_btn.click(fn=download_pdf, inputs=run_id_state, outputs=download_pdf_btn)
        send_email_btn.click(fn=send_report_email_ui, inputs=[run_id_state, email_input], outputs=email_status)
        for button, question in preset_buttons:
            button.click(fn=lambda value=question: value, outputs=question_input, queue=False, show_progress="hidden")

        # Appearance is entirely client side: no navigation or session reset.
        theme_toggle_btn.click(fn=None, js=THEME_JS.replace("__DARK__", "document.body.classList.contains('insyfy-light')"), queue=False)
        demo.load(fn=None, js=THEME_JS.replace("__DARK__", "saved !== 'light'"))
        demo.load(fn=on_refresh, inputs=history_selector, outputs=history_components, show_progress="hidden")
        demo.load(fn=on_metrics, outputs=metrics_components, show_progress="hidden")

    return demo


if __name__ == "__main__":
    demo = create_ui()
    demo.launch(css=REPORT_CSS)
