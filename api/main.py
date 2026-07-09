import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

from api.routes import router
import gradio as gr


def create_app() -> FastAPI:
    app = FastAPI(
        title="InSyfy",
        description="Autonomous Research & Competitive Intelligence Agent",
        version="0.3.0-step3"
    )
    
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    app.include_router(router, prefix="/api/v1")
    
    @app.on_event("startup")
    async def startup():
        print("InSyfy Step 3 starting up...")
        print(f"LLM Provider: {os.getenv('LLM_PROVIDER', 'groq')}")
        print(f"Qdrant URL: {os.getenv('QDRANT_URL', 'not set')}")
        print(f"Redis URL: {os.getenv('REDIS_URL', 'redis://localhost:6379')}")
    
    return app


app = create_app()

# Mount Gradio UI
try:
    from ui.gradio_app import create_ui, REPORT_CSS
    gradio_app = create_ui()
    app = gr.mount_gradio_app(app, gradio_app, path="/", css=REPORT_CSS)
except ImportError as e:
    print(f"Gradio UI not mounted: {e}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)