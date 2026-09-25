"""Renders a 10-second vertical product video from a Gemini script with MoviePy 2.x."""

import io
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx
import numpy as np
from moviepy import CompositeVideoClip, ImageClip, TextClip, concatenate_videoclips
from PIL import Image, ImageOps

from ai_orchestrator import AnimationStyle, Scene, VideoScriptSchema
from config import Settings
from shopify_client import Product

logger = logging.getLogger(__name__)

WIDTH, HEIGHT, FPS = 1080, 1920, 30
ZOOM_AMOUNT = 0.12
TEXT_BOX_WIDTH = 940
FONT_SIZE = 104


class VideoRenderError(RuntimeError):
    """Raised when a video cannot be rendered."""


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:60] or "product"


def _download_image(url: str, timeout: float = 20.0) -> Image.Image:
    try:
        response = httpx.get(url, timeout=timeout, follow_redirects=True)
        response.raise_for_status()
        image = Image.open(io.BytesIO(response.content))
        image = ImageOps.exif_transpose(image)
        return image.convert("RGB")
    except (httpx.HTTPError, OSError) as exc:
        raise VideoRenderError(f"Could not load image {url}: {exc}") from exc


def _cover_frame(image: Image.Image) -> np.ndarray:
    """Scale and center-crop the image so it fills the 9:16 canvas edge to edge."""
    fitted = ImageOps.fit(image, (WIDTH, HEIGHT), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
    return np.asarray(fitted)


def _zoom_function(style: AnimationStyle, duration: float):
    if style == AnimationStyle.zoom_in:
        return lambda t: 1.0 + ZOOM_AMOUNT * (t / duration)
    if style == AnimationStyle.zoom_out:
        return lambda t: 1.0 + ZOOM_AMOUNT * (1 - t / duration)
    return None


def _background_clip(frame: np.ndarray, style: AnimationStyle, duration: float) -> ImageClip:
    clip = ImageClip(frame).with_duration(duration)
    zoom = _zoom_function(style, duration)
    if zoom is not None:
        clip = clip.resized(zoom)
    return clip.with_position(("center", "center"))


def _text_clip(text: str, duration: float, font_path: str) -> TextClip:
    return (
        TextClip(
            font=font_path,
            text=text.upper(),
            font_size=FONT_SIZE,
            color="white",
            stroke_color="black",
            stroke_width=6,
            method="caption",
            size=(TEXT_BOX_WIDTH, None),
            margin=(20, 30),
            text_align="center",
            horizontal_align="center",
            vertical_align="center",
        )
        .with_duration(duration)
        .with_position(("center", "center"))
    )


class VideoEngine:
    def __init__(self, settings: Settings) -> None:
        self._output_dir = settings.output_dir
        self._font_path = settings.font_path
        if not Path(self._font_path).is_file():
            raise VideoRenderError(f"Font not found at {self._font_path}. Set FONT_PATH to a .ttf file.")

    def _scene_clip(self, scene: Scene, duration: float, frames: list[np.ndarray]) -> CompositeVideoClip:
        frame = frames[min(scene.image_index, len(frames) - 1)]
        background = _background_clip(frame, scene.animation_style, duration)
        caption = _text_clip(scene.text_overlay, duration, self._font_path)
        return CompositeVideoClip([background, caption], size=(WIDTH, HEIGHT)).with_duration(duration)

    def render(self, product: Product, script: VideoScriptSchema) -> Path:
        """Blocking render. Call from a worker thread when used inside an event loop."""
        if not product.image_urls:
            raise VideoRenderError(f"Product '{product.title}' has no images")

        frames = [_cover_frame(_download_image(url)) for url in product.image_urls[:3]]
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        output_path = self._output_dir / f"{_slugify(product.handle)}-{stamp}.mp4"

        scene_clips = [self._scene_clip(scene, duration, frames) for scene, duration in script.scenes()]
        final = concatenate_videoclips(scene_clips, method="compose")
        try:
            final.write_videofile(
                str(output_path),
                fps=FPS,
                codec="libx264",
                bitrate="8000k",
                preset="medium",
                audio=False,
                threads=4,
                ffmpeg_params=["-pix_fmt", "yuv420p", "-profile:v", "high", "-movflags", "+faststart"],
                logger=None,
            )
        except (OSError, ValueError) as exc:
            raise VideoRenderError(f"FFmpeg failed for '{product.title}': {exc}") from exc
        finally:
            final.close()
            for clip in scene_clips:
                clip.close()

        logger.info("Rendered %s (%.1fs)", output_path, final.duration)
        return output_path
