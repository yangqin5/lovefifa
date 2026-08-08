from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


EngineName = Literal["paddle", "tesseract", "trocr", "ensemble", "easyocr"]


@dataclass
class OCRConfig:
    input_path: Path
    output_dir: Path = Path("outputs")
    engine: EngineName = "easyocr"
    languages: str = "tha+eng"
    paddle_lang: str = "th"
    easyocr_langs: list[str] = field(default_factory=lambda: ["th", "en"])
    trocr_model_name: str = "microsoft/trocr-base-printed"
    dpi: int = 300
    preprocess: bool = True
    deskew: bool = True
    save_debug_images: bool = False
    min_confidence: float = 0.0
    device: str = "cpu"
    page_image_dir: Path = field(default_factory=lambda: Path("outputs/pages"))
    # --- checkpoint / resume ---
    checkpoint_enabled: bool = True
    checkpoint_interval: int = 1   # เซฟทุกกี่หน้า (1 = เซฟทุกหน้า)
    resume: bool = True 