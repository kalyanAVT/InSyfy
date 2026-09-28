"""Exercise the dashboard's Gradio event contract without live services."""
from unittest.mock import Mock

import gradio as gr
import pytest
from gradio.state_holder import SessionState

from ui import gradio_app as ui


@pytest.fixture
def dashboard(monkeypatch):
    monkeypatch.setenv("GRADIO_ANALYTICS_ENABLED", "False")
    demo = ui.create_ui()
    return demo, SessionState(demo)


async def invoke(dashboard, name, inputs):
    """Collect real Gradio responses, including intermediate generator frames."""
    demo, state = dashboard
    index = next(index for index, event in demo.fns.items() if event.name == name)
    event = demo.fns[index]
    frames = []
    iterator = None
    while True:
        result = await demo.process_api(
            index, inputs, state=state, iterator=iterator, session_hash="dashboard-test",
            simple_format=True,  # Return full frames rather than wire-format diffs.
        )
        frames.append(dict(zip(event.outputs, result["data"])))
        if not result.get("is_generating"):
            break
        iterator = result["iterator"]
    return frames


def component(dashboard, *, elem_id=None, kind=None):
    return next(
        block for block in dashboard[0].blocks.values()
        if (elem_id is None or block.elem_id == elem_id)
        and (kind is None or isinstance(block, kind))
    )


async def load_history(dashboard, monkeypatch):
    monkeypatch.setattr(ui, "fetch_history_runs", lambda: [
        {"run_id": "saved-run", "question": "Saved research", "status": "completed", "quality_score": .9},
    ])
    await invoke(dashboard, "on_refresh", [None])


@pytest.mark.asyncio
async def test_submission_shows_loading_before_request_and_recovers_on_failure(dashboard, monkeypatch):
    observed = []
    monkeypatch.setattr(ui, "submit_research", lambda question, depth: observed.append((question, depth)) or "error: unavailable")
    start = component(dashboard, elem_id="start-research")
    frames = await invoke(dashboard, "on_submit", ["What is changing in research tools?", "deep"])
    assert frames[0][start]["interactive"] is False
    assert observed == [("What is changing in research tools?", "deep")]
    run_state = component(dashboard, kind=gr.State)
    assert dashboard[1][run_state._id] == "error: unavailable"

    finished = await invoke(dashboard, "on_complete", [None])
    assert finished[-1][start]["interactive"] is True
    assert finished[-1][component(dashboard, kind=gr.DownloadButton)]["interactive"] is False
    assert "couldn't start" in str(finished[-1])


@pytest.mark.asyncio
async def test_open_history_keeps_run_id_for_exports_and_clears_previous_pdf(dashboard, monkeypatch, tmp_path):
    await load_history(dashboard, monkeypatch)
    report = {"run_id": "saved-run", "question": "Saved question", "report_markdown": "# Saved report", "quality_score": .9}
    response = Mock(status_code=200, content=b"%PDF-1.4\n%%EOF")
    response.json.return_value = report
    get = Mock(return_value=response)
    monkeypatch.setattr(ui.requests, "get", get)
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))

    frames = await invoke(dashboard, "on_open_history", ["saved-run"])
    run_state = component(dashboard, kind=gr.State)
    assert dashboard[1][run_state._id] == "saved-run"
    download = component(dashboard, kind=gr.DownloadButton)
    assert frames[0][download]["interactive"] is False
    assert frames[0][download]["value"] is None
    loaded = next(frame for frame in frames if frame.get(download, {}).get("interactive") is True)
    assert loaded[download]["value"] is None

    await invoke(dashboard, "download_pdf", [None])
    assert get.call_args.args[0] == f"{ui.API_BASE}/report/saved-run/pdf"

    response.json.return_value = {"message": "Sent."}
    post = Mock(return_value=response)
    monkeypatch.setattr(ui.requests, "post", post)
    await invoke(dashboard, "send_report_email_ui", [None, "research@example.com"])
    assert post.call_args.args[0] == f"{ui.API_BASE}/report/saved-run/email"
    assert post.call_args.kwargs["json"] == {"to_email": "research@example.com"}


@pytest.mark.asyncio
async def test_completed_research_hides_skeleton_and_enables_export(dashboard, monkeypatch):
    run_state = component(dashboard, kind=gr.State)
    dashboard[1][run_state._id] = "new-run"
    monkeypatch.setattr(ui, "poll_status", lambda run_id: "completed")
    monkeypatch.setattr(ui, "get_report", lambda run_id: ("# Findings\n\nSupported evidence.", {"run_id": run_id}))
    frames = await invoke(dashboard, "on_complete", [None])
    assert frames[-1][component(dashboard, kind=gr.DownloadButton)]["interactive"] is True
    assert frames[-1][component(dashboard, elem_id="start-research")]["interactive"] is True
    assert "Supported evidence." in str(frames[-1])
    loading = [block for block in dashboard[0].blocks.values() if isinstance(block, gr.HTML) and "Preparing your research" in str(block.value)]
    assert frames[-1][loading[0]]["visible"] is False


@pytest.mark.asyncio
async def test_refresh_retains_selection_and_zero_quality_score(dashboard, monkeypatch):
    monkeypatch.setattr(ui, "fetch_history_runs", lambda: [
        {"run_id": "saved-run", "question": "Saved research", "status": "declined", "quality_score": 0},
    ])
    await invoke(dashboard, "on_refresh", [None])
    frames = await invoke(dashboard, "on_refresh", ["saved-run"])
    cards = component(dashboard, elem_id="history-cards")
    update = next(frame[cards] for frame in frames if "choices" in frame[cards])
    assert update["value"] == "saved-run"
    assert "Quality 0%" in update["choices"][0][0]
    assert update["visible"] is True


@pytest.mark.asyncio
async def test_report_exception_restores_controls(dashboard, monkeypatch):
    await load_history(dashboard, monkeypatch)
    def fail(_):
        raise RuntimeError("fixture network failure")

    monkeypatch.setattr(ui, "get_report", fail)
    frames = await invoke(dashboard, "on_open_history", ["saved-run"])
    start = component(dashboard, elem_id="start-research")
    assert any(frame.get(start, {}).get("interactive") is True for frame in frames)
    assert any("couldn't be opened" in str(frame) for frame in frames)
