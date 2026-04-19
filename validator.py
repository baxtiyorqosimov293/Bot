from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageStat


@dataclass
class ValidationResult:
    ok: bool
    message: str = ""


class PhotoValidator:
    MIN_WIDTH = 500
    MIN_HEIGHT = 500
    MIN_BRIGHTNESS = 35

    def validate(self, image_path: str) -> ValidationResult:
        path = Path(image_path)
        if not path.exists():
            return ValidationResult(False, "Фото не найдено.")

        try:
            with Image.open(path) as img:
                width, height = img.size

                if width < self.MIN_WIDTH or height < self.MIN_HEIGHT:
                    return ValidationResult(
                        False,
                        "Фото слишком маленькое. Отправь более чёткое фото, где лицо видно крупно.",
                    )

                grayscale = img.convert("L")
                stat = ImageStat.Stat(grayscale)
                brightness = stat.mean[0]

                if brightness < self.MIN_BRIGHTNESS:
                    return ValidationResult(
                        False,
                        "Фото слишком тёмное. Отправь фото с более хорошим освещением.",
                    )

        except Exception:
            return ValidationResult(
                False,
                "Не удалось прочитать фото. Попробуй отправить другое изображение.",
            )

        return ValidationResult(True, "")
