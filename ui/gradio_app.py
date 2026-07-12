import gradio as gr
import requests
import json
import time


API_BASE = "http://localhost:8000/api/v1"


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
    """Render history runs as a read-only markdown summary."""
    if not runs:
        return "No history yet"
    lines = ["## Recent Research\n"]
    for run in runs:
        emoji = STATUS_EMOJI.get(run.get("status", ""), "❓")
        q = run.get("question", "")[:60]
        score = run.get("quality_score")
        score_str = f" (Q: {score:.2f})" if score else ""
        lines.append(f"{emoji} {q}...{score_str}")
    return "\n".join(lines)


def history_dropdown_choices(runs: list) -> list:
    """Build (label, run_id) choices so a past report can be selected and opened."""
    choices = []
    for run in runs:
        emoji = STATUS_EMOJI.get(run.get("status", ""), "❓")
        q = run.get("question", "")[:70]
        score = run.get("quality_score")
        score_str = f" · Q:{score:.2f}" if score else ""
        run_id = run.get("run_id", "")
        choices.append((f"{emoji} {q}{score_str}  ({run_id})", run_id))
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
        return "### No runs logged yet\n\nRun a research query to start collecting real metrics."

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
            "- **Cost estimate:** not configured — set `COST_PER_1K_TOKENS` in `.env` to enable"
        )
    return "\n".join(lines)


REPORT_CSS = """
:root {
    --insyfy-ink: #172b29;
    --insyfy-ink-soft: #4d6260;
    --insyfy-teal: #2a7672;
    --insyfy-teal-deep: #1c534f;
    --insyfy-line: #c7d6d3;
    --insyfy-paper: #eef4f3;
}

/* Report markdown panel */
.report-markdown {
    background: var(--insyfy-paper) !important;
    border: 1px solid var(--insyfy-line) !important;
    border-radius: 8px !important;
    padding: 24px 28px !important;
}
.report-markdown h1 {
    font-size: 26px !important;
    color: var(--insyfy-ink) !important;
    border-bottom: 2px solid var(--insyfy-teal) !important;
    padding-bottom: 10px !important;
    margin-top: 0 !important;
}
.report-markdown h2 {
    font-size: 19px !important;
    color: var(--insyfy-teal-deep) !important;
    margin-top: 28px !important;
    padding-left: 10px !important;
    border-left: 3px solid var(--insyfy-teal) !important;
}
.report-markdown h3 {
    font-size: 16px !important;
    color: var(--insyfy-ink) !important;
    margin-top: 20px !important;
}
.report-markdown em:first-of-type {
    color: var(--insyfy-ink-soft) !important;
    display: block !important;
    font-size: 13px !important;
    margin-bottom: 10px !important;
}
.report-markdown code {
    background: #ffffff !important;
    border: 1px solid var(--insyfy-line) !important;
    border-radius: 4px !important;
    padding: 1px 4px !important;
    color: var(--insyfy-teal-deep) !important;
    letter-spacing: 1px !important;
}
.report-markdown ul li, .report-markdown ol li {
    margin-bottom: 6px !important;
}
.report-markdown a {
    color: var(--insyfy-teal-deep) !important;
    text-decoration: none !important;
    border-bottom: 1px solid var(--insyfy-line) !important;
}
.report-markdown a:hover {
    border-bottom-color: var(--insyfy-teal-deep) !important;
}
.report-markdown hr {
    border: none !important;
    border-top: 1px solid var(--insyfy-line) !important;
    margin: 22px 0 !important;
}

/* Live agent trace — chat bubble styling */
.trace-panel {
    background: #0f1e1c !important;
    border-radius: 8px !important;
    border: 1px solid var(--insyfy-line) !important;
}
.trace-panel .message {
    background: #16302c !important;
    color: #d7ece8 !important;
    border: 1px solid #234641 !important;
    font-family: 'IBM Plex Mono', 'Courier New', monospace !important;
    font-size: 12.5px !important;
}
.trace-panel .message code {
    background: #0b1817 !important;
    color: #7cc9c3 !important;
}

/* Structured output code pane */
.structured-code {
    border-radius: 8px !important;
    border: 1px solid var(--insyfy-line) !important;
}

/* Canvas layout panes */
.canvas-pane {
    background: var(--insyfy-paper) !important;
    border: 1px solid var(--insyfy-line) !important;
    border-radius: 10px !important;
    padding: 18px 20px !important;
}

/* History panel cards */
.history-panel {
    background: var(--insyfy-paper) !important;
    border: 1px solid var(--insyfy-line) !important;
    border-radius: 8px !important;
    padding: 16px 20px !important;
}

/* Custom theme toggle button (client-side only, no page reload) */
#insyfy-theme-toggle {
    font-size: 20px !important;
    border-radius: 50% !important;
    width: 44px !important;
    height: 44px !important;
    min-width: 44px !important;
    padding: 0 !important;
}

/* Best-effort: hide Gradio's built-in dark/light toggle. That one works by
   navigating to ?__theme=dark/light, which forces a full page reload and
   is what was wiping out all run state on theme switch. If it's still
   visible after this, right-click it -> Inspect and send me the actual
   selector so this can be tightened. */
button[aria-label*="theme" i],
button[title*="theme" i],
.theme-toggle,
.dark-toggle-button {
    display: none !important;
}

/* ---- Dark theme (toggled via body.insyfy-dark, not Gradio's system) ---- */
body.insyfy-dark {
    --insyfy-ink: #e7f1ef;
    --insyfy-ink-soft: #9fb8b4;
    --insyfy-teal: #4fa39e;
    --insyfy-teal-deep: #7cc9c3;
    --insyfy-line: #2c3f3d;
    --insyfy-paper: #142523;
}
body.insyfy-dark,
body.insyfy-dark .gradio-container {
    background: #0b1615 !important;
}
body.insyfy-dark .report-markdown code {
    background: #0f1e1c !important;
}
body.insyfy-dark .trace-panel .message {
    background: #0b1817 !important;
}
body.insyfy-dark .canvas-pane {
    background: #142523 !important;
}
"""


def create_ui():
    """Create and return the Gradio Blocks UI."""

    with gr.Blocks(title="InSyfy — Autonomous Research Agent") as demo:
        with gr.Row():
            with gr.Column(scale=10):
                gr.Markdown("""
                # 🔬 InSyfy
                ### Autonomous Research & Competitive Intelligence Agent

                Enter a research question below. InSyfy will:
                1. Plan sub-queries
                2. Search the web in parallel
                3. Retrieve from memory
                4. Synthesize findings with citations
                5. Self-critique and retry if needed
                6. Deliver a structured report
                """)
            with gr.Column(scale=1, min_width=60):
                theme_toggle_btn = gr.Button("🌓", elem_id="insyfy-theme-toggle")

        with gr.Row():
            with gr.Column(scale=3):
                # Input panel
                question_input = gr.Textbox(
                    label="Research Question",
                    placeholder="What are the latest advances in...",
                    lines=3
                )

                with gr.Row():
                    depth_selector = gr.Dropdown(
                        choices=["quick", "standard", "deep"],
                        value="standard",
                        label="Research Depth"
                    )
                submit_btn = gr.Button("Start Research", variant="primary")

                # Live trace panel — narrates each agent step as it happens
                gr.Markdown("### Agent Trace")
                trace_output = gr.Chatbot(
                    label="",
                    height=380,
                    elem_classes=["trace-panel"],
                    show_label=False
                )

                # Metrics panel — real, computed aggregate stats
                gr.Markdown("---")
                gr.Markdown("### Metrics")
                metrics_output = gr.Markdown(elem_classes=["history-panel"])
                refresh_metrics_btn = gr.Button("Refresh Metrics")

            with gr.Column(scale=5):
                # Open a past report — sits directly above the canvas it
                # controls, instead of being a disconnected sidebar control.
                with gr.Row():
                    history_selector = gr.Dropdown(
                        choices=[],
                        label="Open a past report",
                        interactive=True,
                        scale=4
                    )
                    open_history_btn = gr.Button("Open Selected Report", scale=1)

                # The canvas: the report document itself, growing/updating
                # in place. max_height turns it into its own scrollable
                # window instead of growing the whole page.
                gr.Markdown("### Report")
                report_output = gr.Markdown(
                    elem_classes=["report-markdown", "canvas-pane"],
                    max_height=650
                )

                with gr.Row():
                    download_pdf_btn = gr.DownloadButton("Download PDF")
                    email_input = gr.Textbox(
                        placeholder="you@example.com",
                        label="Email report to",
                        scale=2
                    )
                    send_email_btn = gr.Button("Send", scale=1)
                email_status = gr.Markdown("")

                # Collapsible — this was the long, always-open JSON blob
                with gr.Accordion("Structured Output", open=False):
                    json_output = gr.Code(
                        language="json",
                        label="",
                        show_label=False,
                        elem_classes=["structured-code"],
                        max_lines=25
                    )

            with gr.Column(scale=2):
                # History panel — browsing only; opening a report happens
                # via the dropdown above the canvas now.
                gr.Markdown("### History")
                history_output = gr.Markdown(elem_classes=["history-panel"])
                refresh_history_btn = gr.Button("Refresh")


        # State
        run_id_state = gr.State("")

        # Event handlers
        def on_submit(question, depth):
            run_id = submit_research(question, depth)
            if run_id.startswith("error"):
                return run_id, [{"role": "assistant", "content": f"Failed to start: {run_id}"}], "", ""
            return (
                run_id,
                [{"role": "assistant", "content": f"Started run `{run_id}` — waiting for events..."}],
                "",
                ""
            )

        def on_stream(run_id):
            for update in stream_events(run_id):
                yield update

        def on_complete(run_id):
            # Wait for pipeline to finish
            for _ in range(60):  # Max 60 seconds wait
                status = poll_status(run_id)
                if status in ("completed", "failed", "declined", "error"):
                    break
                time.sleep(1)

            report_md, structured_json = get_report(run_id)
            return report_md, to_json_code(structured_json)

        def on_refresh():
            runs = fetch_history_runs()
            return format_history_markdown(runs), gr.Dropdown(choices=history_dropdown_choices(runs))

        def on_open_history(selected_run_id):
            if not selected_run_id:
                empty_msg = [{"role": "assistant", "content": "Select a report from the dropdown first."}]
                return gr.update(), gr.update(), gr.update(), empty_msg
            report_md, structured_json = get_report(selected_run_id)
            trace_msg = [{"role": "assistant", "content": f"Loaded from history — run `{selected_run_id}`"}]
            return report_md, to_json_code(structured_json), selected_run_id, trace_msg

        # Wire up: submit → get run_id → start streaming → get report
        submit_btn.click(
            fn=on_submit,
            inputs=[question_input, depth_selector],
            outputs=[run_id_state, trace_output, report_output, json_output]
        ).then(
            fn=on_stream,
            inputs=[run_id_state],
            outputs=[trace_output]
        ).then(
            fn=on_complete,
            inputs=[run_id_state],
            outputs=[report_output, json_output]
        ).then(
            fn=on_refresh,
            outputs=[history_output, history_selector]
        ).then(
            fn=fetch_metrics_markdown,
            outputs=[metrics_output]
        )

        refresh_history_btn.click(
            fn=on_refresh,
            outputs=[history_output, history_selector]
        )

        refresh_metrics_btn.click(
            fn=fetch_metrics_markdown,
            outputs=[metrics_output]
        )

        open_history_btn.click(
            fn=on_open_history,
            inputs=[history_selector],
            outputs=[report_output, json_output, run_id_state, trace_output]
        )

        download_pdf_btn.click(
            fn=download_pdf,
            inputs=[run_id_state],
            outputs=[download_pdf_btn]
        )

        send_email_btn.click(
            fn=send_report_email_ui,
            inputs=[run_id_state, email_input],
            outputs=[email_status]
        )

        # Theme toggle: pure client-side class toggle, no Python round-trip,
        # no page navigation — this is what actually fixes the reload bug.
        theme_toggle_btn.click(
            fn=None,
            inputs=None,
            outputs=None,
            js="""
            () => {
                document.body.classList.toggle('insyfy-dark');
                try {
                    localStorage.setItem(
                        'insyfy-theme',
                        document.body.classList.contains('insyfy-dark') ? 'dark' : 'light'
                    );
                } catch (e) {}
            }
            """
        )

        # Restore saved theme preference on page load, still client-side only
        demo.load(
            fn=None,
            inputs=None,
            outputs=None,
            js="""
            () => {
                try {
                    if (localStorage.getItem('insyfy-theme') === 'dark') {
                        document.body.classList.add('insyfy-dark');
                    }
                } catch (e) {}
            }
            """
        )

        # Load history and metrics on startup
        demo.load(fn=on_refresh, outputs=[history_output, history_selector])
        demo.load(fn=fetch_metrics_markdown, outputs=[metrics_output])

    return demo


if __name__ == "__main__":
    demo = create_ui()
    demo.launch(css=REPORT_CSS)