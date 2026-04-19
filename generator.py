from __future__ import annotations

import asyncio
import base64
import os
import time
from dataclasses import dataclass

import replicate
from openai import OpenAI

from config import settings


@dataclass(frozen=True)
class GeneratedVariant:
    image_bytes: bytes
    style_code: str
    variant_index: int


STYLE_PROMPTS: dict[str, str] = {
    "classic": (
        "Create a distinctly new premium studio portrait based on the uploaded photo. "
        "Keep the same real person highly recognizable and preserve identity very accurately. "
        "Preserve face shape, facial proportions, eye shape, eyebrows, nose, lips, jawline, "
        "skin tone, hairline, hair texture, beard details if present, and natural expression. "
        "Do not change the person into someone else. "
        "Do not simply return the original image with tiny edits. "
        "Do not keep the original outdoor, casual, or real-life background. "
        "Replace the background with a clean deep black professional studio backdrop. "
        "Use elegant controlled studio lighting, premium portrait framing, realistic skin texture, "
        "sharp facial detail, rich contrast, and a high-end editorial studio look. "
        "The result must look like a newly shot professional studio portrait with a black backdrop, "
        "not a small retouch of the original photo. "
        "Avoid blur, washed-out detail, soft mushy texture, compression artifacts, plastic skin, "
        "excessive smoothing, beauty filter look, or over-beautified facial changes."
    ),
    "dubai": (
        "Create a distinctly new premium luxury portrait based on the uploaded photo. "
        "Keep the same real person highly recognizable and preserve identity very accurately. "
        "Preserve face shape, facial proportions, eye shape, eyebrows, nose, lips, jawline, "
        "skin tone, hairline, hair texture, beard details if present, and natural expression. "
        "Do not change the person into someone else. "
        "Do not simply return the original image with tiny edits. "
        "Transform the image into a refined luxury portrait with premium lighting, elegant styling, "
        "expensive atmosphere, polished composition, and rich cinematic tones. "
        "The result must feel like a newly created high-end portrait, not a minimal retouch of the original photo. "
        "Avoid blur, washed-out detail, soft mushy texture, compression artifacts, plastic skin, "
        "excessive smoothing, beauty filter look, or artificial facial changes."
    ),
}


class OpenAIImageGenerator:
    def __init__(self) -> None:
        self.client = OpenAI(api_key=settings.openai_api_key)

    def _build_prompt(self, style_code: str) -> str:
        style_prompt = STYLE_PROMPTS[style_code]
        return (
            "Identity preservation is the top priority. "
            "Keep the same real person clearly recognizable and preserve identity accurately. "
            "Preserve facial geometry, facial proportions, skin tone, hairline, hair texture, "
            "and natural expression. "
            "Do not turn the subject into another person. "
            "Do not simply return the original image with tiny edits. "
            "Create a distinctly new premium portrait with stronger professional styling, cleaner composition, "
            "and a more expensive visual presentation. "
            "The image must look sharp, high-quality, detailed, and professional. "
            "Avoid blur, compression artifacts, low-detail rendering, soft mushy texture, plastic skin, "
            "or over-retouched beauty filter look. "
            f"{style_prompt}"
        )

    def _edit_image_once(self, image_path: str, style_code: str) -> bytes:
        prompt = self._build_prompt(style_code)

        with open(image_path, "rb") as image_file:
            response = self.client.images.edit(
                model=settings.openai_image_model,
                image=image_file,
                prompt=prompt,
                size=settings.openai_image_size,
                n=1,
            )

        if not getattr(response, "data", None):
            raise RuntimeError("OpenAI не вернул data")

        item = response.data[0]
        b64_json = getattr(item, "b64_json", None)
        if not b64_json:
            raise RuntimeError("OpenAI не вернул b64_json")

        return base64.b64decode(b64_json)

    async def generate_variants(
        self,
        image_path: str,
        style_code: str,
        variants_count: int = 1,
    ) -> list[GeneratedVariant]:
        results: list[GeneratedVariant] = []

        for idx in range(variants_count):
            image_bytes = await asyncio.to_thread(
                self._edit_image_once,
                image_path,
                style_code,
            )
            results.append(
                GeneratedVariant(
                    image_bytes=image_bytes,
                    style_code=style_code,
                    variant_index=idx + 1,
                )
            )

        return results


class ReplicateImageGenerator:
    def __init__(self) -> None:
        os.environ["REPLICATE_API_TOKEN"] = settings.replicate_api_token

    def _build_prompt(self, style_code: str) -> str:
        style_prompt = STYLE_PROMPTS[style_code]
        return (
            "Identity preservation is the top priority. "
            "Keep the same real person clearly recognizable and preserve identity accurately. "
            "Preserve facial geometry, facial proportions, skin tone, hairline, hair texture, "
            "and natural expression. "
            "Do not turn the subject into another person. "
            "Do not simply return the original image with tiny edits. "
            "Create a distinctly new premium portrait with stronger professional styling, cleaner composition, "
            "and a more expensive visual presentation. "
            "The image must look sharp, high-quality, detailed, and professional. "
            "Avoid blur, compression artifacts, low-detail rendering, soft mushy texture, plastic skin, "
            "or over-retouched beauty filter look. "
            f"{style_prompt}"
        )

    def _extract_bytes_from_output(self, output) -> bytes:
        if not output:
            raise RuntimeError("Replicate не вернул output")

        first = output[0] if isinstance(output, list) else output

        if hasattr(first, "read"):
            image_bytes = first.read()
            if not image_bytes:
                raise RuntimeError("Replicate вернул пустой результат")
            return image_bytes

        raise RuntimeError("Replicate вернул неожиданный формат результата")

    def _run_once(self, image_path: str, style_code: str) -> bytes:
        prompt = self._build_prompt(style_code)
        last_error: Exception | None = None

        for attempt in range(3):
            try:
                with open(image_path, "rb") as image_file:
                    output = replicate.run(
                        settings.replicate_model,
                        input={
                            "input_image": image_file,
                            "prompt": prompt,
                        },
                    )

                return self._extract_bytes_from_output(output)

            except Exception as e:
                last_error = e
                err = str(e).lower()

                if "429" in err or "throttled" in err or "rate limit" in err:
                    time.sleep(3 + attempt * 2)
                    continue

                raise

        raise RuntimeError(f"Replicate throttled after retries: {last_error}")

    async def generate_variants(
        self,
        image_path: str,
        style_code: str,
        variants_count: int = 1,
    ) -> list[GeneratedVariant]:
        results: list[GeneratedVariant] = []

        for idx in range(variants_count):
            image_bytes = await asyncio.to_thread(
                self._run_once,
                image_path,
                style_code,
            )
            results.append(
                GeneratedVariant(
                    image_bytes=image_bytes,
                    style_code=style_code,
                    variant_index=idx + 1,
                )
            )

        return results


def build_generator():
    if settings.image_provider == "replicate":
        return ReplicateImageGenerator()
    return OpenAIImageGenerator()


generator = build_generator()
