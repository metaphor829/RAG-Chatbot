from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from starlette.concurrency import iterate_in_threadpool
from pathlib import Path
from dotenv import load_dotenv
import logging
import os

from .engine import RagEngine

load_dotenv()
logging.basicConfig(level=logging.INFO)

APP_DIR = Path(__file__).resolve().parent
STORAGE_DIR = APP_DIR / "data" / "storage"
DATA_DIR = APP_DIR / "data" / "documents"
STORAGE_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="RAG-Chatbot", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

engine = RagEngine(storage_dir=STORAGE_DIR)


class ChatRequest(BaseModel):
    query: str


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}

@app.get("/status")
async def status() -> dict:
    return engine.status()

@app.post("/reindex")
async def reindex() -> dict:
    await engine.index_directory(DATA_DIR)
    return engine.status()

@app.on_event("startup")
async def startup() -> None:
    force_rebuild = os.getenv("REBUILD_INDEX", "").lower() in {"1", "true", "yes"}
    if force_rebuild or not engine.has_persisted_index():
        await engine.index_directory(DATA_DIR)
        return
    if not engine.load_persisted_index():
        await engine.index_directory(DATA_DIR)


@app.post("/chat")
async def chat(req: ChatRequest):
    query_engine = engine.get_query_engine()
    if query_engine is None:
        raise HTTPException(
            status_code=400,
            detail="No indexed documents found. Place PDFs in app/data/documents.",
        )

    response = query_engine.query(req.query)

    async def event_stream():
        async for chunk in iterate_in_threadpool(response.response_gen):
            yield f"data: {chunk}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
