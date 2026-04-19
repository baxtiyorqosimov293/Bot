from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass

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

    def _edit_image_once(self, image_path: str, style_code: str) -> bytes:
        style_prompt = STYLE_PROMPTS[style_code]

        prompt = (
            "Edit the uploaded portrait photo and keep the same real person recognizable. "
            "Preserve the person's natural face, expression, and overall likeness. "
            "Do not replace the face with a different person. "
            "Keep the result realistic, soft, and natural-looking. "
            "You may improve lighting, background, styling, and composition, "
            "but the final image must still clearly look like the original person. "
            f"{style_prompt}"
        )

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
        variants_count: int = 3,
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


generator = OpenAIImageGenerator()
