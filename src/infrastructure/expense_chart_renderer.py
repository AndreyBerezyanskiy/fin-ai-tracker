from __future__ import annotations

import math
import zlib
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from application.services.expense_charts import ExpenseChartData
from domain.currencies import currency_prefix

WIDTH = 1080
HEIGHT = 1080
SCALE = 2

BACKGROUND_TOP = (13, 26, 42)
BACKGROUND_BOTTOM = (8, 17, 29)
CARD = (24, 40, 59)
CARD_BORDER = (42, 65, 90)
WHITE = (244, 247, 252)
MUTED = (151, 167, 194)
GREEN = (65, 224, 164)
RED = (255, 116, 116)

PALETTE = (
    (78, 225, 169),
    (255, 111, 111),
    (255, 203, 82),
    (88, 172, 255),
    (181, 112, 255),
    (255, 143, 91),
)

REGULAR_FONT_PATHS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial.ttf",
)
BOLD_FONT_PATHS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
)


def _font_path(candidates: tuple[str, ...]) -> str:
    for candidate in candidates:
        if Path(candidate).is_file():
            return candidate
    raise RuntimeError("A Unicode TrueType font is required to render expense charts")


def _money(value: object, currency: str) -> str:
    number = f"{value:,.2f}".replace(",", " ").replace(".", ",")
    return f"{currency_prefix(currency)}{number}"


def _truncate(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    width: int,
) -> str:
    if draw.textlength(text, font=font) <= width:
        return text
    result = text
    while result and draw.textlength(f"{result}…", font=font) > width:
        result = result[:-1]
    return f"{result.rstrip()}…"


class PillowExpenseChartRenderer:
    def __init__(self) -> None:
        self.regular_font_path = _font_path(REGULAR_FONT_PATHS)
        self.bold_font_path = _font_path(BOLD_FONT_PATHS)

    @staticmethod
    def _point(x: float, y: float) -> tuple[int, int]:
        return round(x * SCALE), round(y * SCALE)

    @staticmethod
    def _box(
        left: float, top: float, right: float, bottom: float
    ) -> tuple[int, int, int, int]:
        return tuple(round(value * SCALE) for value in (left, top, right, bottom))  # type: ignore[return-value]

    def _font(self, size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(
            self.bold_font_path if bold else self.regular_font_path,
            size * SCALE,
        )

    def _fit_font(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        *,
        maximum: int,
        minimum: int,
        width: int,
        bold: bool = True,
    ) -> ImageFont.FreeTypeFont:
        for size in range(maximum, minimum - 1, -2):
            font = self._font(size, bold=bold)
            if draw.textlength(text, font=font) <= width * SCALE:
                return font
        return self._font(minimum, bold=bold)

    @staticmethod
    def _category_colors(data: ExpenseChartData) -> tuple[tuple[int, int, int], ...]:
        used: set[int] = set()
        colors: list[tuple[int, int, int]] = []
        for category in data.categories:
            index = zlib.crc32(category.code.encode("utf-8")) % len(PALETTE)
            while index in used and len(used) < len(PALETTE):
                index = (index + 1) % len(PALETTE)
            used.add(index)
            colors.append(PALETTE[index])
        return tuple(colors)

    def _background(self) -> Image.Image:
        image = Image.new("RGB", self._point(WIDTH, HEIGHT), BACKGROUND_TOP)
        draw = ImageDraw.Draw(image)
        for y in range(HEIGHT * SCALE):
            progress = y / (HEIGHT * SCALE - 1)
            color = tuple(
                round(top + (bottom - top) * progress)
                for top, bottom in zip(BACKGROUND_TOP, BACKGROUND_BOTTOM, strict=True)
            )
            draw.line((0, y, WIDTH * SCALE, y), fill=color)
        return image

    def _rounded_card(
        self,
        draw: ImageDraw.ImageDraw,
        box: tuple[int, int, int, int],
        *,
        radius: int = 28,
    ) -> None:
        draw.rounded_rectangle(
            box,
            radius=radius * SCALE,
            fill=CARD,
            outline=CARD_BORDER,
            width=1 * SCALE,
        )

    def _draw_donut(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        data: ExpenseChartData,
        colors: tuple[tuple[int, int, int], ...],
    ) -> None:
        box = self._box(75, 230, 605, 760)
        glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow)
        start = -90.0
        gap = 2.2
        for category, color in zip(data.categories, colors, strict=True):
            sweep = float(category.percentage) * 3.6
            glow_draw.arc(
                box,
                start=start + gap / 2,
                end=start + sweep - gap / 2,
                fill=(*color, 120),
                width=94 * SCALE,
            )
            start += sweep
        image.alpha_composite(glow.filter(ImageFilter.GaussianBlur(18 * SCALE)))

        start = -90.0
        for category, color in zip(data.categories, colors, strict=True):
            sweep = float(category.percentage) * 3.6
            segment_start = start + gap / 2
            segment_end = start + sweep - gap / 2
            draw.arc(
                box,
                start=segment_start,
                end=segment_end,
                fill=color,
                width=88 * SCALE,
            )
            if category.percentage >= 8:
                angle = math.radians(start + sweep / 2)
                x = 340 + math.cos(angle) * 218
                y = 495 + math.sin(angle) * 218
                label = f"{category.percentage:.0f}%"
                font = self._font(25, bold=True)
                text_box = draw.textbbox((0, 0), label, font=font)
                draw.text(
                    self._point(x - (text_box[2] - text_box[0]) / SCALE / 2, y - 15),
                    label,
                    font=font,
                    fill=(8, 22, 31),
                )
            start += sweep

        center_total = _money(data.total, data.currency)
        total_font = self._fit_font(
            draw, center_total, maximum=55, minimum=34, width=330, bold=True
        )
        total_width = draw.textlength(center_total, font=total_font)
        draw.text(
            (340 * SCALE - total_width / 2, 456 * SCALE),
            center_total,
            font=total_font,
            fill=WHITE,
        )
        subtitle = "за період"
        subtitle_font = self._font(27)
        subtitle_width = draw.textlength(subtitle, font=subtitle_font)
        draw.text(
            (340 * SCALE - subtitle_width / 2, 525 * SCALE),
            subtitle,
            font=subtitle_font,
            fill=MUTED,
        )

    def _draw_legend(
        self,
        draw: ImageDraw.ImageDraw,
        data: ExpenseChartData,
        colors: tuple[tuple[int, int, int], ...],
    ) -> None:
        count = len(data.categories)
        card_height = min(94, (570 - max(0, count - 1) * 12) // max(count, 1))
        gap = 12
        top = 220
        name_font = self._font(25, bold=True)
        value_font = self._font(23, bold=True)
        for category, color in zip(data.categories, colors, strict=True):
            bottom = top + card_height
            self._rounded_card(draw, self._box(650, top, 1010, bottom), radius=22)
            center_y = (top + bottom) / 2
            draw.ellipse(self._box(669, center_y - 23, 715, center_y + 23), fill=color)
            name = _truncate(draw, category.name, name_font, 260 * SCALE)
            draw.text(self._point(733, top + 14), name, font=name_font, fill=WHITE)
            value = f"{_money(category.amount, data.currency)} · {category.percentage:.0f}%"
            draw.text(self._point(733, top + 50), value, font=value_font, fill=color)
            top = bottom + gap

    def _draw_insight(self, draw: ImageDraw.ImageDraw, data: ExpenseChartData) -> None:
        self._rounded_card(draw, self._box(60, 850, 1020, 1015), radius=32)
        if data.previous_total <= 0:
            icon = "diamond"
            color = (181, 112, 255)
            headline = "Перший орієнтир для порівняння"
            detail = "Наступний період уже покаже вашу динаміку"
        elif data.difference < 0:
            icon = "down"
            color = GREEN
            headline = f"На {_money(abs(data.difference), data.currency)} менше"
            detail = "Гарний темп — так тримати!"
        elif data.difference > 0:
            icon = "up"
            color = RED
            headline = f"На {_money(data.difference, data.currency)} більше"
            detail = "Варто придивитися до найбільших категорій"
        else:
            icon = "flat"
            color = (88, 172, 255)
            headline = "Витрати залишилися на тому самому рівні"
            detail = "Стабільність теж допомагає планувати"

        draw.ellipse(
            self._box(90, 890, 190, 990),
            fill=(18, 59, 58),
            outline=color,
            width=2 * SCALE,
        )
        line_width = 7 * SCALE
        if icon == "diamond":
            draw.polygon(
                (
                    self._point(140, 914),
                    self._point(166, 940),
                    self._point(140, 966),
                    self._point(114, 940),
                ),
                outline=color,
                width=line_width,
            )
        else:
            start, end = {
                "down": ((116, 916), (164, 964)),
                "up": ((116, 964), (164, 916)),
                "flat": ((114, 940), (166, 940)),
            }[icon]
            draw.line((*self._point(*start), *self._point(*end)), fill=color, width=line_width)
            if icon == "down":
                arrow_points = (self._point(164, 964), self._point(164, 940), self._point(140, 964))
            elif icon == "up":
                arrow_points = (self._point(164, 916), self._point(140, 916), self._point(164, 940))
            else:
                arrow_points = (self._point(166, 940), self._point(147, 925), self._point(147, 955))
            draw.line(arrow_points, fill=color, width=line_width, joint="curve")

        headline_font = self._fit_font(
            draw, headline, maximum=38, minimum=27, width=760, bold=True
        )
        draw.text(self._point(225, 890), headline, font=headline_font, fill=WHITE)
        draw.text(
            self._point(225, 950),
            detail,
            font=self._font(25),
            fill=MUTED,
        )

    def render(self, data: ExpenseChartData) -> bytes:
        base = self._background().convert("RGBA")
        draw = ImageDraw.Draw(base)
        self._rounded_card(draw, self._box(35, 35, 1045, 1045), radius=42)

        title = f"Витрати • {data.period_label}"
        title_font = self._fit_font(draw, title, maximum=61, minimum=38, width=930, bold=True)
        draw.text(self._point(75, 85), title, font=title_font, fill=WHITE)
        draw.text(
            self._point(78, 165),
            "Структура витрат за категоріями",
            font=self._font(25),
            fill=MUTED,
        )

        colors = self._category_colors(data)
        self._draw_donut(base, draw, data, colors)
        self._draw_legend(draw, data, colors)
        self._draw_insight(draw, data)

        output = BytesIO()
        base.convert("RGB").resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS).save(
            output,
            format="PNG",
            optimize=True,
        )
        return output.getvalue()


__all__ = ["PillowExpenseChartRenderer"]
