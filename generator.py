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
        "Create a clearly new premium studio portrait based on the uploaded photo. "
        "Keep the same real person highly recognizable and preserve identity very accurately. "
        "Preserve face shape, facial proportions, eye shape, eyebrows, nose, lips, jawline, "
        "skin tone, hairline, hair texture, beard details if present, and natural expression. "
        "Do not change the person into someone else. "
        "Do not simply return the original image with tiny edits. "
        "Replace the original background with a premium dark charcoal or deep gray studio backdrop, "
        "not a pure black void. "
        "Use soft flattering professional studio lighting, realistic skin texture, sharper portrait framing, "
        "subtle premium contrast, and elegant editorial portrait styling. "
        "Add soft separation around dark hair so hair remains clearly visible against the background. "
        "Make the result look like a newly shot professional studio portrait with stronger premium transformation, "
        "not just a light retouch. "
        "Avoid harsh shadows, ugly facial stiffness, plastic skin, excessive smoothing, beauty filter look, "
        "blur, low detail, compression artifacts, washed-out texture, or identity drift."
    ),
    "dubai": (
        "Create a clearly new premium luxury portrait based on the uploaded photo. "
        "Keep the same real person highly recognizable and preserve identity very accurately. "
        "Preserve face shape, facial proportions, eye shape, eyebrows, nose, lips, jawline, "
        "skin tone, hairline, hair texture, beard details if present, and natural expression. "
        "Do not change the person into someone else. "
        "Do not simply return the original image with tiny edits. "
        "Transform the image into a refined luxury portrait with upgraded lighting, polished composition, "
        "premium styling, elegant atmosphere, and richer tones. "
        "Make it feel like a newly created expensive portrait, not a minimal edit of the original photo. "
        "Avoid blur, washed-out detail, plastic skin, excessive smoothing, beauty filter look, "
        "harsh unattractive face rendering, or identity drift."
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
            "natural expression, and unique identity details. "
            "Do not turn the subject into another person. "
            "Do not simply return the original image with tiny edits. "
            "Create a noticeably new premium portrait with better composition, stronger professional lighting, "
            "cleaner framing, and more premium visual presentation. "
            "The result must look sharp, high-quality, detailed, natural, and visually flattering. "
            "Keep the face realistic, balanced, and attractive without artificial beauty-filter look. "
            "Avoid plastic skin, over-retouching, harsh shadows, blur, compression artifacts, "
            "low-detail rendering, washed texture, or stiff facial rendering. "
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
            "natural expression, and unique identity details. "
            "Do not turn the subject into another person. "
            "Do not simply return the original image with tiny edits. "
            "Create a noticeably new premium portrait with better composition, stronger professional lighting, "
            "cleaner framing, and more premium visual presentation. "
            "The result must look sharp, high-quality, detailed, natural, and visually flattering. "
            "Keep the face realistic, balanced, and attractive without artificial beauty-filter look. "
            "Avoid plastic skin, over-retouching, harsh shadows, blur, compression artifacts, "
            "low-detail rendering, washed texture, or stiff facial rendering. "
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
