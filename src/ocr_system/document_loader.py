from pathlib import Path
from pdf2image import convert_from_path
import cv2
from PIL import Image
from .utils.io import ensure_dir

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}


def is_image(path: str | Path) -> bool:
    return Path(path).suffix.lower() in IMAGE_EXTENSIONS


def is_pdf(path: str | Path) -> bool:
    return Path(path).suffix.lower() == ".pdf"


def pdf_to_images(pdf_path: str | Path, output_dir: str | Path, dpi: int = 300,
                   force_reconvert: bool = False) -> list[Path]:
    output_dir = ensure_dir(output_dir)
    stem = Path(pdf_path).stem

    # เช็คว่ามีภาพแปลงไว้แล้วหรือยัง ถ้ามีและไม่บังคับแปลงใหม่ ใช้ของเดิมเลย
    if not force_reconvert:
        existing = sorted(output_dir.glob(f"{stem}_page_*.jpg"))
        if existing:
            print(f"    [skip-convert] พบภาพ {len(existing)} หน้าอยู่แล้วใน {output_dir} ใช้ของเดิม")
            return existing

    pages = convert_from_path(str(pdf_path), dpi=dpi)
    image_paths: list[Path] = []
    for idx, page in enumerate(pages, start=1):
        out = output_dir / f"{stem}_page_{idx:03d}.jpg"
        page.save(out, "JPEG")
        image_paths.append(out)
    return image_paths

def load_document_pages(input_path: str | Path, output_dir: str | Path, dpi: int = 300,
                         force_reconvert: bool = False) -> list[Path]:
    input_path = Path(input_path)
    if is_pdf(input_path):
        return pdf_to_images(input_path, output_dir, dpi=dpi, force_reconvert=force_reconvert)
    if is_image(input_path):
        return [input_path]
    raise ValueError(f"Unsupported file type: {input_path.suffix}")