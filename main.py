"""FastAPI entry point: manual trigger, daily scheduler and run status."""

import asyncio
import json
import logging
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, status

from ai_orchestrator import AIOrchestrator, AIOrchestratorError
from config import Settings, get_settings
from shopify_client import ShopifyClient, ShopifyError
from video_engine import VideoEngine, VideoRenderError

logging.basicConfig(
    level=logging.INFO,
    format='{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
)
logger = logging.getLogger("video_pipeline")

_run_lock = asyncio.Lock()
_state: dict[str, Any] = {"running": False, "last_run": None, "next_scheduled_run": None}


def _append_manifest(settings: Settings, record: dict[str, Any]) -> None:
    manifest = settings.output_dir / "manifest.jsonl"
    with manifest.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


async def run_pipeline() -> dict[str, Any]:
    if _run_lock.locked():
        logger.warning("Pipeline already running, skipping this trigger")
        return {"skipped": True}

    async with _run_lock:
        settings = get_settings()
        started = datetime.now(timezone.utc)
        _state["running"] = True
        summary: dict[str, Any] = {"started_at": started.isoformat(), "videos": [], "errors": []}
        try:
            products = await ShopifyClient(settings).fetch_top_products()
            orchestrator = AIOrchestrator(settings)
            engine = VideoEngine(settings)
            for product in products:
                try:
                    script = await orchestrator.generate_script(product)
                    path = await asyncio.to_thread(engine.render, product, script)
                    record = {
                        "product_id": product.id,
                        "title": product.title,
                        "url": product.url,
                        "video_path": str(path),
                        "script": script.model_dump(mode="json"),
                        "status": "ready_for_publishing",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    }
                    _append_manifest(settings, record)
                    summary["videos"].append(record)
                except (AIOrchestratorError, VideoRenderError) as exc:
                    logger.error("Product '%s' failed: %s", product.title, exc)
                    summary["errors"].append({"product": product.title, "error": str(exc)})
        except (ShopifyError, VideoRenderError) as exc:
            logger.error("Pipeline aborted: %s", exc)
            summary["errors"].append({"product": None, "error": str(exc)})
        finally:
            _state["running"] = False
            summary["finished_at"] = datetime.now(timezone.utc).isoformat()
            summary["duration_seconds"] = round((datetime.now(timezone.utc) - started).total_seconds(), 1)
            _state["last_run"] = summary
        logger.info("Pipeline finished: %d videos, %d errors", len(summary["videos"]), len(summary["errors"]))
        return summary


def _next_run_time(hour_utc: int) -> datetime:
    now = datetime.now(timezone.utc)
    target = now.replace(hour=hour_utc, minute=0, second=0, microsecond=0)
    return target if target > now else target + timedelta(days=1)


async def _daily_scheduler(hour_utc: int) -> None:
    while True:
        next_run = _next_run_time(hour_utc)
        _state["next_scheduled_run"] = next_run.isoformat()
        await asyncio.sleep((next_run - datetime.now(timezone.utc)).total_seconds())
        try:
            await run_pipeline()
        except Exception:  # keep the scheduler alive no matter what a run does
            logger.exception("Scheduled run crashed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    task = None
    if settings.enable_scheduler:
        task = asyncio.create_task(_daily_scheduler(settings.daily_run_hour_utc))
        logger.info("Daily scheduler enabled at %02d:00 UTC", settings.daily_run_hour_utc)
    yield
    if task:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="Shopify to Video AI Engine", version="1.0.0", lifespan=lifespan)


@app.post("/api/generate-daily-videos", status_code=status.HTTP_202_ACCEPTED)
async def generate_daily_videos(background_tasks: BackgroundTasks) -> dict[str, str]:
    if _run_lock.locked():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A run is already in progress")
    background_tasks.add_task(run_pipeline)
    return {"status": "accepted", "detail": "Video generation started. Check /api/status for progress."}


@app.get("/api/status")
async def pipeline_status() -> dict[str, Any]:
    settings = get_settings()
    return {
        "running": _state["running"],
        "next_scheduled_run": _state["next_scheduled_run"],
        "gemini_model": settings.gemini_model,
        "output_dir": str(settings.output_dir.resolve()),
        "last_run": _state["last_run"],
    }


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
