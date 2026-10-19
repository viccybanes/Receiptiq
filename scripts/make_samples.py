"""Generate labeled synthetic receipts; cursive typography is not real handwriting."""

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def font(cursive=False):
    candidates = [Path("C:/Windows/Fonts/segoesc.ttf" if cursive else "C:/Windows/Fonts/consola.ttf"),
                  Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), 32)
    return ImageFont.load_default(size=32)


def create_samples(destination, count=60):
    destination.mkdir(parents=True, exist_ok=True)
    manifest = []
    printed, cursive = font(), font(True)
    for index in range(count):
        category = ("printed", "synthetic-cursive", "mixed")[index % 3]
        number = f"NS-{index + 1:03}"
        text = ["NORTHSIDE MARKET", "123 Market Street", "Receipt No: " + number, "2026-10-19 PHP", "",
                "Coffee 2 x 50.00 100.00", "Bread 1 x 25.00 25.00", "", "Subtotal 125.00",
                "Tax 0.00", "Total 125.00", "", "Thank you"]
        image = Image.new("RGB", (900, 1150), "#fffefb")
        draw = ImageDraw.Draw(image)
        for line_number, line in enumerate(text):
            use_cursive = category == "synthetic-cursive" or category == "mixed" and line_number in (5, 6)
            draw.text((55, 45 + line_number * 72), line, fill="#20251e", font=cursive if use_cursive else printed)
        path = destination / f"{index + 1:03}-{category}.png"
        image.save(path)
        if index == 0:
            image.save(destination / "sample.pdf", "PDF", resolution=120)
        manifest.append({"file": path.name, "category": category,
                         "expected": {"supplier": "NORTHSIDE MARKET", "receipt_date": "2026-10-19",
                                      "receipt_number": number, "currency": "PHP", "total": "125.00"},
                         "expected_item_count": 2})
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=60)
    parser.add_argument("--out", type=Path, default=Path("artifacts/samples"))
    args = parser.parse_args()
    create_samples(args.out, args.count)
    print(f"Created {args.count} labeled synthetic receipts in {args.out}")
