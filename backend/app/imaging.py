"""Bounded image decoding and preprocessing; originals are never modified."""

import warnings
from pathlib import Path

import cv2
import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageEnhance, ImageOps, UnidentifiedImageError

MAX_PIXELS = 32_000_000
MAX_PAGES = 30
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


def inspect_file(path: Path):
    with path.open("rb") as stream:
        signature = stream.read(5)
    if signature == b"%PDF-":
        document = None
        try:
            document = pdfium.PdfDocument(path)
            if not 1 <= len(document) <= MAX_PAGES:
                raise ValueError(f"PDF must contain 1-{MAX_PAGES} pages.")
            for page in document:
                try:
                    width, height = page.get_size()
                finally:
                    page.close()
                if width <= 0 or height <= 0 or width * height * 4 > MAX_PIXELS:
                    raise ValueError("PDF page is too large to render safely.")
            return "application/pdf"
        except pdfium.PdfiumError as exc:
            raise ValueError("PDF is unreadable or password protected.") from exc
        finally:
            if document is not None:
                document.close()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                if image.format not in ("JPEG", "PNG"):
                    raise ValueError("Choose JPG, PNG, or PDF files.")
                if image.width * image.height > MAX_PIXELS:
                    raise ValueError("Image exceeds the 32 megapixel limit.")
                kind = Image.MIME[image.format]
                image.verify()
            return kind
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("Image is unreadable or exceeds the 32 megapixel limit.") from exc


def pages(path, mime):
    if mime == "application/pdf":
        document = pdfium.PdfDocument(path)
        try:
            for index in range(len(document)):
                page = document[index]
                bitmap = page.render(scale=2)
                image = bitmap.to_pil().convert("RGB")
                bitmap.close()
                page.close()
                yield image
        finally:
            document.close()
    else:
        with Image.open(path) as source:
            yield ImageOps.exif_transpose(source).convert("RGB")


def preprocess(image, rotation):
    image = image.rotate(-rotation, expand=True)
    image.thumbnail((2200, 3000))
    original = image.copy()
    gray = np.array(ImageOps.grayscale(image))
    binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    coords = cv2.findNonZero(binary)
    angle = 0.0
    if coords is not None and len(coords) > 20:
        measured = float(cv2.minAreaRect(coords)[-1])
        if measured > 45:
            measured -= 90
        if abs(measured) <= 8:
            angle = measured
    matrix = cv2.getRotationMatrix2D((gray.shape[1] / 2, gray.shape[0] / 2), angle, 1)
    corrected = cv2.warpAffine(gray, matrix, (gray.shape[1], gray.shape[0]), borderValue=255)
    enhanced = ImageEnhance.Contrast(Image.fromarray(corrected)).enhance(1.15).convert("RGB")
    return original, enhanced, {"rotation": rotation, "deskew_degrees": angle, "matrix": matrix.tolist()}
