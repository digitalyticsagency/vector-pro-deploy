"""Turns Shopify product data into a structured 3-scene video script via Gemini."""

import asyncio
import logging
from enum import Enum

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel, Field, ValidationError

from config import Settings
from shopify_client import Product

logger = logging.getLogger(__name__)

SCENE_DURATIONS: tuple[float, float, float] = (3.0, 4.0, 3.0)


class AnimationStyle(str, Enum):
    zoom_in = "zoom_in"
    zoom_out = "zoom_out"
    static = "static"


class Scene(BaseModel):
    text_overlay: str = Field(description="Ultra-punchy marketing copy, maximum 5 words.")
    image_index: int = Field(ge=0, le=2, description="Which product image to show: 0, 1 or 2.")
    animation_style: AnimationStyle


class VideoScriptSchema(BaseModel):
    hook: Scene = Field(description="Scene 1, 3.0 seconds. Stop the scroll.")
    core_value: Scene = Field(description="Scene 2, 4.0 seconds. Main benefit or feature.")
    call_to_action: Scene = Field(description="Scene 3, 3.0 seconds. CTA, mention the price if known.")

    def scenes(self) -> list[tuple[Scene, float]]:
        return list(zip((self.hook, self.core_value, self.call_to_action), SCENE_DURATIONS))


class AIOrchestratorError(RuntimeError):
    """Raised when Gemini fails to return a usable script."""


SYSTEM_INSTRUCTION = (
    "You are a senior short-form video copywriter for e-commerce. You write scripts for "
    "10-second vertical product videos (TikTok, Reels, Shorts). Each text overlay is at most "
    "5 words, no hashtags, no emojis, no quotation marks. Use only facts present in the product data."
)


def _build_prompt(product: Product) -> str:
    description = product.description[:1500] or "No description provided."
    return (
        f"Product title: {product.title}\n"
        f"Price: {product.price_label or 'not available'}\n"
        f"Description: {description}\n"
        f"Number of images available: {len(product.image_urls)} "
        f"(valid image_index values: 0 to {len(product.image_urls) - 1})\n\n"
        "Write the 3-scene script: hook (3s), core_value (4s), call_to_action (3s). "
        "Prefer a different image for each scene when more than one is available."
    )


def _sanitize(script: VideoScriptSchema, image_count: int) -> VideoScriptSchema:
    for scene, _ in script.scenes():
        scene.image_index = min(max(scene.image_index, 0), max(image_count - 1, 0))
        words = scene.text_overlay.replace('"', "").split()
        scene.text_overlay = " ".join(words[:5]) or "Shop now"
    return script


class AIOrchestrator:
    def __init__(self, settings: Settings) -> None:
        self._client = genai.Client(api_key=settings.gemini_api_key)
        self._model = settings.gemini_model
        self._config = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            response_schema=VideoScriptSchema,
            temperature=0.9,
        )

    async def generate_script(self, product: Product, retries: int = 2) -> VideoScriptSchema:
        prompt = _build_prompt(product)
        last_error: Exception | None = None
        for attempt in range(1, retries + 2):
            try:
                response = await self._client.aio.models.generate_content(
                    model=self._model, contents=prompt, config=self._config
                )
                script = response.parsed
                if not isinstance(script, VideoScriptSchema):
                    script = VideoScriptSchema.model_validate_json(response.text or "")
                logger.info("Gemini script ready for '%s' (attempt %d)", product.title, attempt)
                return _sanitize(script, len(product.image_urls))
            except (genai_errors.APIError, ValidationError, ValueError) as exc:
                last_error = exc
                logger.warning("Gemini attempt %d failed for '%s': %s", attempt, product.title, exc)
                await asyncio.sleep(2 ** attempt)
        raise AIOrchestratorError(f"Gemini failed for '{product.title}': {last_error}")
