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
        "Use a subtle clean studio portrait style with soft natural lighting and minimal changes. "
        "Do not over-beautify or over-correct the face."
    ),
    "dubai": (
        "Apply a subtle luxury Dubai aesthetic with elegant lighting, premium styling, "
        "and upscale atmosphere, but keep the real face natural and recognizable."
    ),
}


class OpenAIImageGenerator:
    def __init__(self) -> None:
        self.client = OpenAI(api_key=settings.openai_api_key)

    def _build_prompt(self, style_code: str) -> str:
        style_prompt = STYLE_PROMPTS[style_code]
        return (
            "Edit the uploaded portrait photo and keep the same real person recognizable. "
            "Preserve the person's natural face, expression, eye shape, nose shape, lips, jawline, "
            "hair texture, and overall likeness. "
            "Do not replace the face with a different person. "
            "Change only styling, lighting, atmosphere, and premium visual presentation. "
            "Keep the result realistic, refined, and natural-looking. "
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
            "Keep the exact same person. Preserve facial identity, face shape, eyes, nose, lips, "
            "skin tone, hair, and overall likeness. Do not turn the subject into another person. "
            "Apply only a premium portrait transformation with realistic lighting and refined styling. "
            f"{style_prompt}"
        )

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

                if not output:
                    raise RuntimeError("Replicate не вернул output")

                first = output[0] if isinstance(output, list) else output

                if hasattr(first, "read"):
                    image_bytes = first.read()
                else:
                    raise RuntimeError("Replicate вернул неожиданный формат результата")

                if not image_bytes:
                    raise RuntimeError("Replicate вернул пустой результат")

                return image_bytes

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
