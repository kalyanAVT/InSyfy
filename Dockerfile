# HF Spaces (Docker SDK). The native "Gradio SDK" Space type expects a
# bare gr.Blocks app; InSyfy mounts Gradio inside FastAPI via
# gr.mount_gradio_app, so it needs the Docker SDK instead — this Dockerfile
# is what that Space type runs.

FROM python:3.11-slim

WORKDIR /app

# System deps: minimal. xhtml2pdf/markdown2 are pure-Python (no Pango/Cairo
# needed, unlike weasyprint), so nothing extra is required for PDF export.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

COPY . .

# HF Spaces (Docker SDK) routes traffic to port 7860 by default.
# See app_port in the README.md Spaces frontmatter — keep both in sync.
EXPOSE 7860

# QDRANT_URL, QDRANT_API_KEY, TAVILY_API_KEY, GROQ_API_KEY, REDIS_URL, and
# any SMTP_* vars must be set as HF Spaces "Secrets" in the Space settings
# UI — never commit real credentials into this image or the repo.

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "7860"]