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
        "Create a new premium portrait based on the uploaded photo. "
        "Keep the same real person highly recognizable, but do not simply return the original image. "
        "Improve lighting, composition, skin tone balance, styling, clothing presentation, and background mood. "
        "Make it look like a professionally shot premium portrait, not a basic retouch. "
        "Use elegant studio-style lighting, clean premium atmosphere, and refined portrait composition."
    ),
    "dubai": (
        "Create a new premium luxury portrait based on the uploaded photo. "
        "Keep the same real person highly recognizable, but do not simply return the original image. "
        "Transform the scene into a soft luxury portrait with premium lighting, elegant styling, "
        "expensive atmosphere, refined beauty, and polished composition. "
        "The result must look like a newly created premium portrait, not a small edit of the original photo."
    ),
}


class OpenAIImageGenerator:
    def __init__(self) -> None:
        self.client = OpenAI(api_key=settings.openai_api_key)

    def _build_prompt(self, style_code: str) -> str:
        style_prompt = STYLE_PROMPTS[style_code]
        return (
            "Keep the same real person clearly recognizable and preserve identity. "
            "Preserve facial identity, face shape, eyes, nose, lips, skin tone, and hair texture. "
            "Do not turn the subject into another person. "
            "However, do not simply return the original image. "
            "Create a distinctly new premium portrait with upgraded composition, cleaner light, "
            "better framing, premium styling, and a more expensive visual atmosphere. "
            "The result must feel like a new professionally created portrait, not a minimal retouch. "
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
            "Keep the same real person clearly recognizable and preserve identity. "
            "Preserve facial identity, face shape, eyes, nose, lips, skin tone, and hair texture. "
            "Do not turn the subject into another person. "
            "However, do not simply return the original image. "
            "Create a distinctly new premium portrait with upgraded composition, cleaner light, "
            "better framing, premium styling, and a more expensive visual atmosphere. "
            "The result must feel like a new professionally created portrait, not a minimal retouch. "
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
