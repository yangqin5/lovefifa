from dataclasses import dataclass, asdict, field
from typing import Any


@dataclass
class OCRLine:
    text: str
    confidence: float | None = None
    box: Any | None = None
    engine: str | None = None
    page: int | None = None


@dataclass
class OCRPageResult:
    page: int
    text: str
    lines: list[OCRLine]
    image_path: str


@dataclass
class OCRDocumentResult:
    source_path: str
    engine: str
    text: str
    pages: list[OCRPageResult]

    def to_dict(self) -> dict:
        return asdict(self)

@dataclass
class Course:
    code: str
    name_th: str | None = None
    name_en: str | None = None
    credits: str = "3"
    year: int = 0  # 1-4 หรือ 0 สำหรับวิชาเลือก/ไม่ระบุ
    semester: int = 0  # 1-2 หรือ 0 สำหรับวิชาเลือก/ไม่ระบุ
    category: str = "หมวดวิชาเฉพาะ"
    type: str = "บังคับ"  # "บังคับ" หรือ "เลือก"
    prerequisite: str = "ไม่มี"
    flexible_year_semester: str | None = None  # เช่น "3/1, 3/2, 4/1"
    note: str | None = None  # เช่น "กลุ่มวิชาที่กำหนดโดยคณะ"


@dataclass
class CurriculumResult:
    source: str
    description: str
    program: str
    plan: str
    courses: list[Course] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)