from __future__ import annotations

from pathlib import Path
from typing import Optional

import fitz

from core.config import (
    MIN_IMAGE_WIDTH, MIN_IMAGE_HEIGHT, MIN_IMAGE_AREA,
    IMAGE_DIR, SAVE_IMAGE_CROPS,
)
from core.models import ImageBlock
from core.utils import image_from_pixmap, is_probably_banner


def extract_images(page: fitz.Page, page_number: int, pdf_stem: str) -> list[ImageBlock]:
    image_blocks: list[ImageBlock] = []
    page_dict = page.get_text("dict")
    img_idx = 0

    for block in page_dict.get("blocks", []):
        if block.get("type") != 1:
            continue

        bbox = block.get("bbox")
        if not bbox:
            continue

        x0, y0, x1, y1 = bbox
        width = int(x1 - x0)
        height = int(y1 - y0)
        area = width * height
        img_idx += 1
        image_id = f"{pdf_stem}_p{page_number + 1}_img{img_idx}"

        relevant = (
            width >= MIN_IMAGE_WIDTH
            and height >= MIN_IMAGE_HEIGHT
            and area >= MIN_IMAGE_AREA
            and not is_probably_banner(x0, y0, x1, y1)
        )

        image_blocks.append(
            ImageBlock(
                image_id=image_id,
                page=page_number,
                bbox=[x0, y0, x1, y1],
                width=width,
                height=height,
                relevant_for_ocr=relevant,
            )
        )

    return image_blocks


def save_image_crop(page: fitz.Page, image_block: ImageBlock) -> Optional[Path]:
    if not image_block.relevant_for_ocr:
        return None

    rect = fitz.Rect(image_block.bbox)
    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=rect, alpha=False)
    img = image_from_pixmap(pix)

    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    out_path = IMAGE_DIR / f"{image_block.image_id}.png"
    img.save(out_path)
    return out_path


def process_images(page: fitz.Page, page_number: int, pdf_stem: str, reader=None) -> list[ImageBlock]:
    """Extract images and optionally run OCR on them."""
    from extraction.ocr import run_ocr_on_image
    from core.config import OCR_MIN_TEXT_LEN

    images = extract_images(page, page_number, pdf_stem)

    for img_block in images:
        crop_path = None

        if SAVE_IMAGE_CROPS or reader is not None:
            crop_path = save_image_crop(page, img_block)

        if crop_path:
            img_block.path = str(crop_path)

        if reader is not None and crop_path:
            ocr_text = run_ocr_on_image(reader, crop_path)
            if len(ocr_text) >= OCR_MIN_TEXT_LEN:
                img_block.ocr_text = ocr_text

    return images