"""Create a synthetic Korean receipt for repeatable OCR checks; no personal data."""

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font", type=Path, default=Path("C:/Windows/Fonts/malgun.ttf"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/synthetic-receipt.jpg"))
    args = parser.parse_args()
    font = ImageFont.truetype(str(args.font), 32)
    image = Image.new("RGB", (1000, 800), "white")
    draw = ImageDraw.Draw(image)
    rows = [
        (45, ["이마트 테스트점"]),
        (115, ["2026-10-21"]),
        (200, ["상품명", "단가", "수량", "금액"]),
        (285, ["서울우유 1L", "2,980", "1", "2,980"]),
        (365, ["풀무원 두부", "1,500", "2", "3,000"]),
        (445, ["물티슈 100매", "1,000", "1", "1,000"]),
        (575, ["합계", "", "", "6,980"]),
    ]
    for y, cells in rows:
        for x, text in zip((45, 550, 740, 820), cells):
            draw.text((x, y), text, font=font, fill="black")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    image.save(args.output, "JPEG", quality=95)
    print(f"Created {args.output}")


if __name__ == "__main__":
    main()
