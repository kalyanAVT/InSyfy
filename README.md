---
title: InSyfy
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
---

# InSyfy

Autonomous Research & Competitive Intelligence Agent.

InSyfy is a multi-agent research system built with LangGraph that performs autonomous web research, retrieves relevant knowledge from persistent vector memory, synthesizes evidence with citations, evaluates report quality through self-critique, and generates structured research reports.

---

# Features

* Multi-agent workflow powered by LangGraph
* Parallel web research using the Tavily Search API, with a configurable fan-out limit
* Persistent semantic memory with Qdrant Cloud, with graceful degradation if Qdrant is not configured
* Hybrid retrieval (vector search, keyword scoring, and cross-encoder re-ranking)
* Automatic citation generation, deduplicated by source and rendered as clickable links
* Evidence validation via a citation enforcement gate before a report is written
* Self-critique with retry loops for quality improvement
* Structured Markdown report generation, including per-agent token usage and the sub-queries used for research
* Real-time progress streaming over Server-Sent Events, with per-node start/complete/error events
* Report history with the ability to reopen any past report
* FastAPI REST API
* Gradio web interface with a client-side light/dark theme toggle
* Redis for state storage, live event logging, and caching
* Downloadable PDF export and email delivery of any report

---

# Workflow

```mermaid
flowchart TD
    A([User Query]) --> B[Planner Agent]
    B --> C1[Search Agent 1]
    B --> C2[Search Agent 2]
    B --> C3[Search Agent N]
    C1 --> D[Merge Results]
    C2 --> D
    C3 --> D
    D --> E[Memory Retrieval - Qdrant]
    E --> F[Hybrid Retrieval + Re-ranking]
    F --> G[Synthesizer Agent]
    G --> H{Citation Enforcement}
    H -->|insufficient evidence| I[Writer: Decline Report]
    H -->|evidence sufficient| J[Critic / Evaluator]
    J -->|quality below threshold, retries remain| B
    J -->|quality meets threshold or max retries reached| K[Writer: Generate Report]
    K --> L[(Store Report to Vector Memory)]
    I --> M([End])
    L --> N[PDF Export / Email Delivery]
    N --> M([End])
```

Sub-query fan-out (`Search Agent N`) is configurable via `MAX_PARALLEL_SEARCHES` — see Configuration below.

---

# Architecture

```mermaid
flowchart TD
    U([User]) --> UI["Gradio UI (ui/gradio_app.py)"]
    UI --> API["FastAPI (api/main.py, api/routes.py)"]
    API --> LG["LangGraph State Machine (graph/pipeline.py, graph/nodes.py)"]

    subgraph Pipeline [Pipeline Nodes]
        direction TD
        N1[Planner] --> N2[Parallel Search Agents]
        N2 --> N3[Memory Retrieval]
        N3 --> N4[Hybrid Retrieval]
        N4 --> N5[Synthesizer]
        N5 --> N6[Citation Enforcement]
        N6 --> N7[Critic]
        N7 --> N8[Writer]
    end

    LG --> Pipeline
    N3 -.-> QD[(Qdrant Cloud)]
    N8 -.-> QD
    Pipeline --> R[(Redis: state cache, live SSE event log, metrics)]
    R --> RPT[Structured Markdown Report]
    RPT --> PDF[PDF Export]
    RPT --> MAIL[Email Delivery via SMTP]
    RPT --> UI
```

---

# Agent Pipeline

## Planner

Breaks a user question into 3 to 5 focused research tasks.

Example:

```
Latest RAG systems
```

becomes

* Recent RAG architectures
* Open-source RAG frameworks
* Enterprise RAG adoption
* Research papers
* Performance benchmarks

The number of sub-queries actually sent to parallel search is capped by `MAX_PARALLEL_SEARCHES` (see Configuration below), since each one is a real API call.

---

## Search Agents

Runs multiple searches in parallel using Tavily.

Responsibilities:

* Web search
* Page scraping and content extraction
* Chunking
* Source attribution

---

## Memory Retrieval

Retrieves relevant historical research from Qdrant.

Uses:

* Semantic embeddings
* Similarity search
* A persistent knowledge base built up from prior runs

If Qdrant is not configured, this step is skipped rather than failing the run.

---

## Hybrid Retrieval

Combines

* Vector search
* Keyword overlap scoring
* Cross-encoder re-ranking

to improve retrieval quality.

---

## Synthesizer

Combines information from web search and vector memory into structured findings, each linked to the specific chunks that support it, with confidence scores and any contradictions noted.

---

## Citation Enforcement

Every claim produced by the synthesizer is checked against the retrieved chunks by semantic similarity.

If a claim's evidence falls below the configured threshold, the run is declined rather than producing an unsupported report.

---

## Critic

Evaluates report quality against the original plan.

Checks

* Coverage of the planned sub-queries
* Unresolved contradictions
* Source diversity
* Average finding confidence

Automatically retries the plan (up to a configured limit) if the quality score falls short of the threshold.

---

## Writer

Produces the final Markdown report, including the executive summary, findings, evidence gaps, sources, methodology, sub-queries used, and per-agent token usage, and stores a summary into persistent vector memory for future retrieval.

---

# Technology Stack

| Component        | Technology             |
| ----------------- | ----------------------- |
| Agent framework    | LangGraph               |
| LLM                | Groq (OpenAI-compatible fallback) |
| Search engine      | Tavily Search            |
| Vector database    | Qdrant Cloud             |
| State store        | Redis                    |
| Backend            | FastAPI                  |
| Frontend           | Gradio                   |
| Embeddings         | sentence-transformers    |
| Re-ranking         | Cross-encoder             |
| Validation         | Pydantic                  |
| Async runtime      | asyncio                   |

---

# Project Structure

```text
InSyfy/
|
+-- agents/
|   +-- __init__.py
|   +-- planner.py
|   +-- searcher.py
|   +-- memory_rag.py
|   +-- synthesizer.py
|   +-- critic.py
|   +-- writer.py
|   +-- token_utils.py
|
+-- graph/
|   +-- __init__.py
|   +-- state.py
|   +-- nodes.py
|   +-- pipeline.py
|
+-- retrieval/
|   +-- __init__.py
|   +-- chunking.py
|   +-- citation_enforcement.py
|   +-- qdrant_store.py
|
+-- api/
|   +-- __init__.py
|   +-- main.py
|   +-- routes.py
|   +-- schemas.py
|   +-- stream.py
|   +-- pdf_export.py
|   +-- email_sender.py
|
+-- db/
|   +-- redis_client.py
|
+-- ui/
|   +-- __init__.py
|   +-- gradio_app.py
|
+-- prompts/
|   +-- __init__.py
|   +-- loader.py
|   +-- v1/
|       +-- planner.yaml
|       +-- searcher.yaml
|       +-- synthesizer.yaml
|       +-- critic.yaml
|
+-- .env.example
+-- requirements.txt
+-- README.md
+-- Dockerfile
+-- .dockerignore
```

Note: `prompts/v1/*.yaml` are loaded at runtime by `prompts/loader.py` and are the actual source of truth for the planner, synthesizer, and critic prompts — editing them changes agent behavior on the next process restart (results are cached in-process after first load). `searcher.yaml` remains informational only, since `SearchAgent` calls the Tavily API directly and has no LLM prompt of its own.

---

# Quick Start

## 1. Clone Repository

```bash
git clone https://github.com/kalyanAVT/InSyfy.git

cd InSyfy
```

---

## 2. Create Virtual Environment

### Windows

```bash
python -m venv .venv

.venv\Scripts\activate
```

### Linux / macOS

```bash
python3 -m venv .venv

source .venv/bin/activate
```

---

## 3. Install Dependencies

```bash
pip install -r requirements.txt
```

---

# Configuration

Copy `.env.example` to `.env` in the project root and fill in your own values.

```env
# Qdrant Cloud (free tier available)
QDRANT_URL=paste_your_qdrant_endpoint_url
QDRANT_API_KEY=paste_your_qdrant_api_key

# Search
TAVILY_API_KEY=your_tavily_api_key

# LLM (Groq is the default provider; OpenAI is a fallback)
GROQ_API_KEY=your_groq_api_key
# OPENAI_API_KEY=your_openai_api_key

LLM_PROVIDER=groq
LLM_MODEL=llama-3.3-70b-versatile

# Redis
REDIS_URL=redis://localhost:6379

# Quality thresholds
CITATION_THRESHOLD=0.50
QUALITY_THRESHOLD=0.75
MAX_RETRIES=2

# How many sub-queries run in parallel search. The planner targets 3-5
# sub-queries per plan; each one is a real Tavily API call plus scraping,
# so raise this with the added cost in mind.
MAX_PARALLEL_SEARCHES=5
```

If `QDRANT_URL` or `QDRANT_API_KEY` is left unset, the application still starts; persistent memory and memory-retrieval features are simply disabled for that run, and `/health` reports Qdrant as disconnected.

---

# Redis Setup

## Option 1: Redis Cloud

Use the free Redis Cloud service.

```env
REDIS_URL=redis://username:password@your-host:port
```

---

## Option 2: Docker

```bash
docker run -d -p 6379:6379 --name redis redis:7-alpine
```

---

## Option 3: Windows

Install Redis for Windows, or use Redis Cloud instead.

---

# Running the Application

Start the server.

```bash
uvicorn api.main:app --reload --port 8000
```

Open your browser at

```
http://localhost:8000
```

The FastAPI backend and Gradio interface are both served from this address.

---

# API Endpoints

| Method | Endpoint                 | Description                    |
| ------ | ------------------------- | -------------------------------- |
| POST   | /api/v1/research           | Start a research run              |
| GET    | /api/v1/stream/{run_id}    | Stream live progress events (SSE) |
| GET    | /api/v1/status/{run_id}    | Check research status             |
| GET    | /api/v1/report/{run_id}    | Retrieve the final report         |
| GET    | /api/v1/report/{run_id}/pdf | Download the report as a PDF     |
| POST   | /api/v1/report/{run_id}/email | Email the report as a PDF attachment |
| GET    | /api/v1/history             | List previous research runs       |
| GET    | /api/v1/metrics             | Aggregate stats across logged runs |
| DELETE | /api/v1/report/{run_id}    | Delete a report                    |
| GET    | /api/v1/health               | Check Redis and Qdrant connectivity |

---

# Example Request

```http
POST /api/v1/research
```

```json
{
  "question": "Latest advances in Retrieval-Augmented Generation"
}
```

Using curl:

```bash
curl -X POST http://localhost:8000/api/v1/research \
-H "Content-Type: application/json" \
-d "{\"question\":\"Latest advances in Retrieval-Augmented Generation\"}"
```

---

# Deploying to Hugging Face Spaces

InSyfy runs as a Docker Space on Hugging Face — not the native Gradio SDK Space type, since the Gradio UI here is mounted inside a FastAPI app rather than being a standalone `gr.Blocks` app. The `Dockerfile` and the frontmatter at the top of this README handle that.

1. Create a new Space at huggingface.co/new-space, choosing **Docker** as the Space SDK.
2. Push this repository to the Space's git remote (Spaces work like any git repo).
3. In the Space's **Settings > Repository secrets**, set the following. Do not commit these to `.env` in the repo:

| Secret | Required | Notes |
| ------ | -------- | ----- |
| `QDRANT_URL` | Optional | Memory features are disabled gracefully if unset |
| `QDRANT_API_KEY` | Optional | Same as above |
| `TAVILY_API_KEY` | Required | Web search will not function without it |
| `GROQ_API_KEY` | Required | Or set `LLM_PROVIDER=openai` and `OPENAI_API_KEY` instead |
| `REDIS_URL` | Required | Use a managed Redis (e.g. Redis Cloud free tier) — Spaces containers don't persist a local Redis between restarts |
| `SMTP_HOST`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_ADDRESS` | Optional | Only needed for the "email report" feature |
| `ALLOWED_ORIGINS` | Optional | Defaults to `*` (public access) |

4. The Space builds and starts automatically. It listens on port 7860 internally, matching `app_port` in the frontmatter above and `EXPOSE 7860` in the Dockerfile — if you change one, change both.
5. First requests that use embeddings or re-ranking will download model weights (`sentence-transformers`, cross-encoder) from the Hugging Face Hub on first use. This is fast on Spaces since it's on the same network as the Hub, but expect a slower first research run after a fresh deploy or restart.

# Development

Freeze dependencies:

```bash
pip freeze > requirements.txt
```

There is no automated test suite yet. This is tracked under Roadmap below.

---

# Known Limitations

These are known, currently unresolved issues, listed here for transparency rather than left silent:

* Citation enforcement encodes claims one at a time. `retrieval/citation_enforcement.py` calls the embedder in a loop per claim rather than batching. Correct, but slower than necessary on runs with many findings.
* Token usage depends on provider SDK support. Per-agent token counts are read from the LLM response's `usage_metadata`, with a `response_metadata` fallback. If the installed `langchain-groq` or `langchain-openai` version does not populate either field, token counts will report as zero rather than fail. Verify with a live run.
* PDF export uses `xhtml2pdf`, which supports a subset of CSS 2.1 (no flexbox/grid, no CSS variables). The PDF's styling is a simplified, literal-color version of the in-app report theme rather than a pixel-identical copy.

---

# Roadmap

## Completed

* Step 1: Foundation (linear pipeline)
* Step 2: Full pipeline
  * Parallel search with a configurable fan-out limit
  * Hybrid retrieval
  * Citation enforcement gate
  * Critic retry loop
* Step 3
  * Gradio UI
  * Real-time per-node SSE streaming
  * Redis state management
  * Report history with reopening past reports
  * Light/dark theme toggle
  * Per-agent token usage tracking
* Step 4
  * Prompt YAML files wired up as the actual runtime source of truth
  * CORS configuration corrected for public access
  * PDF export and email delivery of reports
  * Deployed to Hugging Face Spaces (Docker SDK)

## Planned

* Automated test suite and CI/CD pipeline
* Evaluation framework
* Weights and Biases logging
* Multi-document research
* Scheduled monitoring
* Team collaboration
* Enterprise deployment

---

# Contributing

Contributions are welcome.

1. Fork the repository.
2. Create a feature branch.

```bash
git checkout -b feature/my-feature
```

3. Commit your changes.

```bash
git commit -m "Add new feature"
```

4. Push the branch.

```bash
git push origin feature/my-feature
```

5. Open a Pull Request.

---

# License

This project is licensed under the MIT License.
