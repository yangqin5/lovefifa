import cv2
import numpy as np
from .base import BaseOCREngine
from ocr_system.schemas import OCRLine


class EasyOCREngine(BaseOCREngine):
    name = "easyocr"

    def __init__(self, languages: list[str] = ["th", "en"], gpu: bool = False):
        import easyocr
        self.reader = easyocr.Reader(languages, gpu=gpu)

    def recognize(self, image: np.ndarray, page: int | None = None) -> list[OCRLine]:
        if len(image.shape) == 2:
            rgb = cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
        else:
            rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

        results = self.reader.readtext(rgb)  # [(box, text, confidence), ...]

        lines: list[OCRLine] = []
        for box, text, conf in results:
            text = (text or "").strip()
            if not text:
                continue
            # EasyOCR ให้ box เป็น 4 จุดอยู่แล้ว (list ของ [x, y]) ตรงกับ format ที่ Tesseract engine ใช้พอดี
            clean_box = [[int(x), int(y)] for x, y in box]
            lines.append(OCRLine(text=text, confidence=float(conf), box=clean_box, engine=self.name, page=page))
        return lines