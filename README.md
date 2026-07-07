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

---

# Workflow

```text
                User Query
                     |
                     v
                Planner Agent
                     |
     +---------------+---------------+
     v               v               v
 Search Agent 1  Search Agent 2  Search Agent N   (fan-out is configurable)
     |               |               |
     +---------------+---------------+
                     |
                     v
          Memory Retrieval (Qdrant)
                     |
                     v
     Hybrid Retrieval + Re-ranking
                     |
                     v
           Synthesizer Agent
                     |
                     v
       Citation Enforcement Layer
        (declines if evidence is
         insufficient for a claim)
                     |
                     v
            Critic / Evaluator
         (retries the plan if the
          quality score is too low)
                     |
                     v
             Writer / Reporter
                     |
                     v
        Store Report into Vector Memory
```

---

# Architecture

```text
User
 |
 v
Gradio UI  (ui/gradio_app.py)
 |
 v
FastAPI  (api/main.py, api/routes.py)
 |
 v
LangGraph State Machine  (graph/pipeline.py, graph/nodes.py)
 |
 +-- Planner
 +-- Parallel Search Agents
 +-- Memory Retrieval (Qdrant)
 +-- Hybrid Retrieval
 +-- Synthesizer
 +-- Citation Enforcement
 +-- Critic
 +-- Writer
 |
 v
Redis  (state cache, live SSE event log)
 |
 v
Structured Markdown Report
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
|
+-- db/
|   +-- redis_client.py
|
+-- ui/
|   +-- __init__.py
|   +-- gradio_app.py
|
+-- prompts/
|   +-- v1/
|       +-- planner.yaml
|       +-- searcher.yaml
|       +-- synthesizer.yaml
|
+-- .env.example
+-- requirements.txt
+-- README.md
```

Note: `prompts/v1/*.yaml` are currently reference documentation only. The agents define their prompts inline in code; the YAML files are not yet loaded at runtime. This is a known gap, see Known Limitations below.

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
| GET    | /api/v1/history             | List previous research runs       |
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

# Development

Freeze dependencies:

```bash
pip freeze > requirements.txt
```

There is no automated test suite yet. This is tracked under Roadmap below.

---

# Known Limitations

These are known, currently unresolved issues, listed here for transparency rather than left silent:

* CORS is configured permissively. `api/main.py` sets `allow_origins=["*"]` together with `allow_credentials=True`. Browsers reject credentialed requests against a wildcard origin, and a wildcard origin should not be used in production regardless. Needs an explicit allow-list before any real deployment.
* Prompt YAML files are not wired up. `prompts/v1/*.yaml` describe the intended prompts, but `planner.py`, `synthesizer.py`, and `critic.py` currently define their prompts inline in code. Editing the YAML files has no effect until this is connected.
* Citation enforcement encodes claims one at a time. `retrieval/citation_enforcement.py` calls the embedder in a loop per claim rather than batching. Correct, but slower than necessary on runs with many findings.
* Token usage depends on provider SDK support. Per-agent token counts are read from the LLM response's `usage_metadata`, with a `response_metadata` fallback. If the installed `langchain-groq` or `langchain-openai` version does not populate either field, token counts will report as zero rather than fail. Verify with a live run.

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

## Planned

* Wire up prompt YAML files as the actual source of truth
* Tighten CORS configuration for production
* Automated test suite and CI/CD pipeline
* Evaluation framework
* Weights and Biases logging
* Multi-document research
* Scheduled monitoring
* Report export
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