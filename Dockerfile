# Render runs FastAPI and the mounted Gradio UI as a persistent web service.
# Both use Render's $PORT, falling back to 8000 for local Docker runs.

FROM python:3.11-slim

WORKDIR /app

# System deps: minimal. xhtml2pdf/markdown2 are pure-Python (no Pango/Cairo
# needed, unlike weasyprint), so nothing extra is required for PDF export.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Documents the default; Render overrides this via $PORT at runtime.
EXPOSE 8000

# QDRANT_URL, QDRANT_API_KEY, TAVILY_API_KEY, GROQ_API_KEY, REDIS_URL, and
# any SMTP_* vars must be set as Render "Environment Variables" in the
# service's dashboard — never commit real credentials into this image or
# the repo.

CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
