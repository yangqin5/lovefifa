"""
extract_text_hybrid.py
-----------------------
ตัวแทน run_tesseract.py แบบ "hybrid": อ่าน text layer ที่ฝังอยู่ใน PDF ก่อน (แม่นยำเกือบ 100%
เพราะเป็นข้อความจริงจากไฟล์ ไม่ใช่การเดาจากภาพ) แล้วใช้ Tesseract OCR เป็นตัวสำรอง
เฉพาะหน้าที่ไม่มี text layer (เช่น หน้าที่เป็นภาพสแกนจริงๆ) เท่านั้น

ทำไมต้องทำแบบนี้:
- เอกสารหลักสูตร (มคอ.2) ส่วนใหญ่สร้างจาก Word/InDesign แล้ว export เป็น PDF
  => มี text layer ฝังอยู่แล้ว ไม่จำเป็นต้อง OCR ทั้งเล่ม
- การ OCR ทั้งเล่มทำให้ตัวอักษรไทยเพี้ยน/หาย และทำให้ regex ตัดขอบเขต
  "3.3 แผนการศึกษา" ... "3.4 คำอธิบายรายวิชา" ใน curriculum_extraction.py หา header ไม่เจอ
  (เพราะ OCR อ่าน header ผิด) แล้ว fallback ไปสแกนทั้งเอกสาร -> ได้รหัสวิชาทุกตัวในเล่ม
  ไม่ใช่แค่ในตารางแผนการศึกษา

Output: JSON schema เดียวกับ run_tesseract.py (source_path, engine, text, pages)
        เพื่อให้ curriculum_extraction.py ใช้งานต่อได้ทันทีโดยไม่ต้องแก้อะไร

การใช้งาน:
    python3 extract_text_hybrid.py <input.pdf> <output_folder> [--min-chars 20]

ค่าเริ่มต้น (ถ้าไม่ใส่ argument) จะใช้ path เดียวกับ run_tesseract.py เดิม:
    input_pdf   = data/input/sample.pdf
    output_folder = output_tesseract
"""

import argparse
import json
import os
import re

import pdfplumber
import pypdfium2 as pdfium
import pytesseract
from pytesseract import Output


class MissingTesseractLanguageError(RuntimeError):
    """Raise เมื่อ Tesseract ไม่มีไฟล์ภาษาที่ระบุไว้ใน --lang ครบ"""


def verify_languages(lang: str) -> None:
    """เช็คว่า Tesseract มีไฟล์ .traineddata ของทุกภาษาที่ระบุใน `lang` (เช่น "tha+eng")
    ครบหรือไม่ ก่อนเริ่ม OCR จริง

    เหตุผลที่ต้องมีฟังก์ชันนี้: ถ้าขาดภาษาใดไป (เช่น tha.traineddata ไม่ได้ติดตั้ง)
    Tesseract **จะไม่ error** แต่จะเงียบๆ ตัดภาษานั้นออกจากรายการแล้ว OCR ต่อด้วย
    ภาษาที่เหลือเท่านั้น (เช่น เหลือแค่ eng) ผลคือมันพยายามอ่านตัวอักษรไทยด้วยโมเดล
    ภาษาอังกฤษ -> ได้ตัวอักษรละตินหน้าคล้ายกันแทนทั้งหน้า (เช่น "โรงเรียน" ->
    "salSeuataun") ดูเหมือนรันผ่านสำเร็จทุกอย่างแต่ผลลัพธ์เพี้ยนทั้งเล่มโดยไม่มี error
    ใดๆ ให้เห็นเลย -- นี่คือสาเหตุที่ทำให้ "ภาษาไทยเพี้ยนมาก" เกิดขึ้นแบบเงียบๆ
    ฟังก์ชันนี้เช็คล่วงหน้าแล้ว raise ทันทีพร้อมวิธีแก้ที่ชัดเจน จะได้ไม่ต้องมานั่งไล่
    เดาสาเหตุจากผลลัพธ์ที่ออกมาทีหลัง
    """
    requested = set(lang.split("+"))
    try:
        available = set(pytesseract.get_languages(config=""))
    except Exception as exc:  # pragma: no cover - เช่น เรียก tesseract ไม่ได้เลย
        raise MissingTesseractLanguageError(
            f"เรียก Tesseract เพื่อเช็ครายชื่อภาษาไม่สำเร็จ: {exc}\n"
            "ตรวจสอบว่าติดตั้ง tesseract-ocr ไว้แล้วและอยู่ใน PATH"
        ) from exc

    missing = requested - available
    if missing:
        raise MissingTesseractLanguageError(
            f"Tesseract ขาดไฟล์ภาษา: {sorted(missing)} "
            f"(ตอนนี้มีอยู่แค่: {sorted(available)})\n\n"
            "ถ้าฝืนรันต่อ Tesseract จะไม่ error แต่จะ OCR ด้วยภาษาที่เหลือเท่านั้น "
            "(เช่น ขาด tha -> ใช้ eng ล้วน) ทำให้ข้อความของภาษาที่ขาดออกมาเพี้ยนทั้งหมด "
            "โดยไม่มี error ใดๆ ให้เห็น\n\n"
            "วิธีติดตั้งภาษาที่ขาด:\n"
            "  Ubuntu/Debian : sudo apt-get install tesseract-ocr-tha\n"
            "  macOS         : brew install tesseract-lang\n"
            "  ทั่วไป/ได้ผลแม่นกว่า: ดาวน์โหลด tha.traineddata จาก\n"
            "    https://github.com/tesseract-ocr/tessdata_best\n"
            "    แล้ววางในโฟลเดอร์ tessdata (เช็ค path ได้จาก `tesseract --list-langs`)\n\n"
            "ติดตั้งแล้วรัน `tesseract --list-langs` เพื่อยืนยันว่าเห็นภาษาที่ขาดแล้ว "
            "ก่อนรันสคริปต์นี้ใหม่"
        )


def fix_mixed_thai_arabic_digit_tokens(text: str) -> str:
    """แก้ artifact เฉพาะของ Tesseract เมื่อใช้ lang='tha+eng': บางครั้งตีความกลุ่ม
    ตัวอักษร 'x'/'X' ภาษาอังกฤษที่ติดกัน (มักเจอในรหัส placeholder เช่น 90644xxx,
    9064xxxx) เป็นเลขไทย (๐-๙) แทน เช่น 90644xxx -> 90644๒๐๓

    เจตนาใส่ฟังก์ชันนี้ไว้ที่นี่ (ไม่ใช่ใน curriculum_extraction.py): นี่คือพฤติกรรม
    ของตัว OCR engine เอง ไม่เกี่ยวกับความหมายของรหัสวิชา/โดเมนหลักสูตรเลย ควรแก้
    ให้ใกล้จุดเกิดเหตุที่สุด (ตรงนี้) เพื่อให้ text ที่ส่งออกไปสะอาดสำหรับผู้ใช้งานปลายทาง
    ทุกราย ไม่ใช่แค่ curriculum_extraction.py

    วิธีตรวจจับแบบไม่ผูกกับโดเมน (generic): ข้อความไทยจริงจะไม่ใช้เลขอารบิก (0-9)
    ปนกับเลขไทย (๐-๙) อยู่ใน "โทเค็นเดียวกันที่ไม่มีช่องว่างคั่น" เอกสารจริงเลือกใช้
    ระบบเลขแบบใดแบบหนึ่งตลอดทั้งคำ/รหัส ถ้าเจอโทเค็นที่ผสมทั้ง 2 แบบ (เช่น
    "90644๒๐๓" มีทั้ง 9,0,6,4,4 แบบอารบิก และ ๒,๐,๓ แบบไทย) ให้ถือว่าเลขไทยในนั้น
    เป็นผลจาก Tesseract อ่าน x/X ผิด แล้วแปลงกลับเป็น 'x' (ยังไม่รู้ว่าเดิมมี x กี่ตัว
    เป๊ะ ถ้า OCR หล่นตัวอักษรไปด้วย ความยาวโทเค็นหลังแก้อาจสั้นกว่าของจริง 1-2 ตัว
    ยังต้องพึ่งการเช็คความยาวที่ชั้นถัดไปอยู่ดี แต่แก้ตัวอักษรผิดชนิดให้ถูกต้องคืนได้แล้ว)

    ทดสอบกับเอกสารตัวอย่างแล้ว: ทั้งเล่มมีแค่ 3 โทเค็นที่เข้าเงื่อนไขนี้ (ทั้งหมดคือ
    รหัส placeholder ที่เพี้ยนจริง) ไม่มี false positive กับเลขไทย/เลขอารบิกที่ถูกต้อง
    อยู่แล้วที่อื่นในเอกสารเลย
    """
    thai_digits = set("๐๑๒๓๔๕๖๗๘๙")
    token_re = re.compile(r"[0-9A-Za-z\u0e50-\u0e59]+")

    def _fix_token(m: "re.Match") -> str:
        token = m.group(0)
        has_arabic_digit = any(ch.isdigit() and ch not in thai_digits for ch in token)
        has_thai_digit = any(ch in thai_digits for ch in token)
        if has_arabic_digit and has_thai_digit:
            return "".join("x" if ch in thai_digits else ch for ch in token)
        return token

    return token_re.sub(_fix_token, text)


def ocr_single_page(pil_image, lang="tha+eng"):
    """OCR สำรอง สำหรับหน้าที่ไม่มี text layer (เช่น หน้าสแกนจริง)

    เดิม (รอบแรก): join ทุก "word" ด้วย " " เดียวกันหมด -> พังกับภาษาไทย เพราะ
    Tesseract ตัดข้อความไทยเป็น word ทีละตัวอักษร (ไม่มีเว้นวรรคในภาษาไทยให้อ้างอิง)
    ได้ "ค ว า ม ห ม า ย" ทั้งที่อ่านตัวอักษรถูกทุกตัว

    เดิม (รอบสอง): ลองคำนวณ space เองจากช่องว่างแนวนอนระหว่าง word-box เทียบกับ
    ความสูงตัวอักษร (gap_ratio) -- ดูเหมือนได้ผลกับภาษาไทย แต่พังกับคำภาษาอังกฤษ
    บางคำ เช่น "SPORTS"+"AND" ช่องว่างจริงระหว่างคำ (~12px) ดันมาใกล้เคียงกับ
    ช่องว่างระหว่างตัวอักษรไทยในคำเดียวกัน (~5-15px) พอดี ไม่มี threshold ตัวเลขเดียว
    ที่แยกสองกรณีนี้ได้แน่นอน 100% (เกณฑ์ยิ่งเข้มก็ยิ่งพังกับอังกฤษ ยิ่งหลวมก็ยิ่งพังกับไทย)

    แก้จริงคือ: เลิกคำนวณ space เองจาก pixel gap แล้วปล่อยให้ Tesseract ตัดสินใจเรื่อง
    การเว้นวรรค/ตัดบรรทัดเอง ผ่าน image_to_string() ซึ่งใช้ text-layout analysis ภายใน
    ของ Tesseract เอง (แม่นกว่าการเทียบ bounding box มือเราเอง เพราะมันดูทั้งบรรทัด
    ไม่ใช่แค่คู่ word ที่ติดกัน) -- ส่วน image_to_data() ยังใช้แค่สำหรับดึงตำแหน่ง/ความเชื่อมั่น
    ของแต่ละ word ไปเก็บใน "words" (เพื่อ debug/QA ทีหลัง) ไม่ได้ใช้ต่อข้อความจาก box แล้ว"""
    text = pytesseract.image_to_string(pil_image, lang=lang).strip()

    data = pytesseract.image_to_data(pil_image, lang=lang, output_type=Output.DICT)
    words = []
    for i in range(len(data["text"])):
        word_text = data["text"][i].strip()
        conf = float(data["conf"][i])
        if word_text and conf >= 0:
            x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
            words.append(
                {
                    "text": word_text,
                    "confidence": round(conf / 100.0, 2),
                    "box": [[int(x), int(y)], [int(x + w), int(y)], [int(x + w), int(y + h)], [int(x), int(y + h)]],
                }
            )

    return text, words


def extract_page_text_precise(page, gap_threshold: float = 1.6) -> str:
    """ดึงข้อความจากหน้า PDF โดยเรียงตัวอักษรเองจากตำแหน่งจริง (page.chars) แทนการใช้
    page.extract_text() ตรงๆ เพราะเจอปัญหาเฉพาะเอกสารไทยบางเล่ม 2 แบบ:

    1) สระ/วรรณยุกต์ลอย (เช่น ่ ้ ์ ็ ั ซึ่งกว้าง=0 ในบางฟอนต์) ถูกจัดลำดับผิดที่
       เช่น "เส้น" กลายเป็น "เสน้" เพราะ pdfplumber จัดเรียงตาม x0 ดิบ แต่ตัวอักษร
       ที่กว้าง=0 บางครั้งมี x0 คลาดเคลื่อนจากลำดับจริงในสตรีม PDF ไปเล็กน้อย (เช่น 0.1pt)
       ทำให้ sort สลับที่กัน -> แก้โดยปัดเศษ x0 ก่อนเรียง แล้วถ้าใกล้เคียงกันมาก
       ให้ยึดลำดับเดิมในสตรีม PDF (ซึ่งผู้สร้างเอกสารเรียงถูกไว้แล้ว) แทน

    2) มี "ช่องว่างหลอน" (literal space character) ฝังอยู่กลางคำ ระหว่างตัวอักษรกับ
       สระ/วรรณยุกต์ (เช่น จากการจัด layout แบบ justify ในต้นฉบับ Word) ทำให้ได้ "เส้ น"
       มีช่องว่างแทรกกลางคำ -> แก้โดยไม่สนใจตำแหน่ง space character ที่มีอยู่ในสตรีมเลย
       (ทิ้งไปทั้งหมด) แล้วคำนวณ "ช่องว่างจริง" เองจากระยะห่างระหว่างตัวอักษรที่มองเห็นได้
       (gap = ตัวอักษรถัดไป.x0 - ตัวอักษรก อนหน้า.x1) ถ้าห่างเกิน gap_threshold ถึงจะ
       ใส่ space ให้ในผลลัพธ์ ไม่ใช้ space token ดิบจาก PDF เป็นตัวตัดสินอีกต่อไป
    """
    # กรอง literal space ออก และกรอง PUA character (U+E000–U+F8FF) ออกด้วย
    # PUA พบในเอกสารที่ใช้ฟอนต์ไทยรุ่นเก่า (เช่น THSarabunPSK) ที่ไม่มีตาราง GSUB/GPOS
    # ฟอนต์เหล่านี้ฝัง ToUnicode CMap ที่ map วรรณยุกต์/สระบางตัว (เช่น ไม้เอก ไม้โท การันต์)
    # ไปเป็น "presentation form" ในช่วง PUA แทน Unicode มาตรฐาน (เช่น U+0E48) โดยตรง
    # -- เป็นเรื่องปกติของฟอนต์กลุ่มนี้ ไม่ใช่ pdfplumber หรือโค้ดนี้อ่านผิด --
    # ตัวอักษรกลุ่มนี้มักกว้าง = 0 (x0 == x1) และไม่ควรถูกนำไปคำนวณ gap ระหว่างคำ
    # หรือต่อเข้า buf เพราะจะกลายเป็นตัวอักษรที่มองไม่เห็นแต่ฝังอยู่ในผลลัพธ์จริง
    # (ไม่มีทาง match กับ Ground Truth ได้เลยไม่ว่ากรณีใด) -- ตัดทิ้งตั้งแต่ขั้นตอนนี้ดีที่สุด
    def _is_pua(ch: str) -> bool:
        return bool(ch) and 0xE000 <= ord(ch[0]) <= 0xF8FF

    chars = [c for c in page.chars if c["text"] != " " and not _is_pua(c["text"])]
    if not chars:
        return ""

    lines: dict = {}
    for idx, c in enumerate(chars):
        key = round(c["top"], 1)
        lines.setdefault(key, []).append((idx, c))

    out_lines = []
    for top in sorted(lines.keys()):
        line_chars = sorted(lines[top], key=lambda pair: (round(pair[1]["x0"]), pair[0]))
        buf = []
        prev = None
        for idx, c in line_chars:
            if prev is not None:
                gap = c["x0"] - prev["x1"]
                if gap > gap_threshold:
                    buf.append(" ")
            buf.append(c["text"])
            # เก็บ x1 ที่กว าง (ชัดเจน) ที่สุดไว้เทียบระยะ ป องกันกรณีตัวถัดไปกว าง=0
            # (สระ/วรรณยุกต ) ทำให ค า x1 ย อนกลับไปน อยกว าตัวจริงก อนหน า
            prev = c if c["x1"] > (prev["x1"] if prev else -1) else prev
        out_lines.append("".join(buf))
    return "\n".join(out_lines)


def page_has_pua_glyphs(page) -> bool:
    """เช็คว่าหน้านี้มีตัวอักษร PUA (U+E000–U+F8FF) ปนอยู่ใน page.chars ดิบหรือไม่
    ต้องเช็คจาก page.chars โดยตรง (ก่อนผ่าน extract_page_text_precise ซึ่งกรอง PUA
    ออกไปแล้ว) ไม่งั้น looks_garbled() จะไม่มีวันเห็น PUA เลยเพราะถูกกรองทิ้งไปก่อนหน้านั้น
    -- ถ้าไม่เช็คจุดนี้ หน้าที่มีปัญหาฟอนต์จะเงียบๆ สูญเสียวรรณยุกต์ไปโดยไม่ fallback ไป OCR"""
    for c in page.chars:
        t = c.get("text") or ""
        if t and 0xE000 <= ord(t[0]) <= 0xF8FF:
            return True
    return False


def looks_garbled(text: str, min_len_to_check: int = 40, min_thai_ratio: float = 0.05) -> bool:
    """ตรวจจับกรณี PDF มี font encoding เพี้ยน (พบได้ในเอกสารไทยที่ font ไม่มี
    ToUnicode CMap ที่ถูกต้อง) - text layer จะดึงตัวอักษรผิดออกมาทั้งหน้าเป็นภาษาอังกฤษ/
    สัญลักษณ์ปนกันแบบอ่านไม่ได้ (เช่น "LsalSeuairaua CHARM SCHOOL") แม้ข้อความจะยาว
    พอผ่านเกณฑ์ min_chars ก็ตาม ต่างจากกรณี "ไม่มี text layer" (ข้อความสั้น/ว่าง)
    ซึ่งเช็คแยกอยู่แล้วด้วย min_chars

    วิธีเช็ค: เอกสารหลักสูตรไทยควรมีสัดส่วนตัวอักษรไทย (Unicode \\u0e00-\\u0e7f) อยู่
    พอสมควรในทุกหน้าที่มีเนื้อหา ถ้าความยาวข้อความเยอะแต่มีตัวอักษรไทยน้อยผิดปกติ
    ให้ถือว่า encoding เพี้ยน (heuristic นี้อาจพลาดกับหน้าที่เป็นภาษาอังกฤษล้วนจริงๆ
    เช่น หน้าปกภาษาอังกฤษ/หน้าอ้างอิงต่างประเทศ - ยอมรับความเสี่ยงนี้เพราะหน้าแบบนั้น
    OCR ก็จะได้ผลลัพธ์ไม่ต่างกันมาก)
    """
    stripped = text.strip()
    if len(stripped) < min_len_to_check:
        return False

    # ตรวจจับ PUA character (U+E000–U+F8FF) ที่หลุดรอดมาจาก extract_page_text_precise
    # (เผื่อกรณีเรียก looks_garbled ตรงๆ กับ text ที่ยังไม่ผ่านการกรอง PUA มาก่อน)
    # เจอแม้แต่ตัวเดียวถือว่า "เพี้ยน" ทันที เพราะเป็นสัญญาณชัดเจนว่าฟอนต์นี้มีปัญหา
    # ToUnicode CMap ของวรรณยุกต์/สระบางตัว ต่อให้สัดส่วนตัวอักษรไทยโดยรวมยังดูปกติ
    # (เพราะตัวอักษรส่วนใหญ่ในหน้ายังแปลถูก มีแค่บางจุดที่เป็น PUA) ก็ต้องบังคับ OCR fallback
    # เพื่อให้ได้วรรณยุกต์/สระที่ถูกต้องจากภาพจริงแทน
    pua_chars = sum(1 for ch in stripped if 0xE000 <= ord(ch) <= 0xF8FF)
    if pua_chars > 0:
        return True

    thai_chars = sum(1 for ch in stripped if "\u0e00" <= ch <= "\u0e7f")
    letter_chars = sum(1 for ch in stripped if ch.isalpha())
    if letter_chars == 0:
        return False
    thai_ratio = thai_chars / letter_chars
    return thai_ratio < min_thai_ratio


def extract_hybrid(input_pdf: str, output_folder: str, min_chars: int = 20, lang: str = "tha+eng") -> dict:
    # เช็คก่อนเลยว่า Tesseract มีไฟล์ภาษาที่ขอใช้ครบหรือไม่ -- ถ้าไม่เช็คตรงนี้แล้วปล่อยรันต่อ
    # จะได้ผลลัพธ์เพี้ยนทั้งเล่มแบบไม่มี error ให้เห็น (ดู docstring ของ verify_languages)
    verify_languages(lang)

    os.makedirs(output_folder, exist_ok=True)

    json_data = {
        "source_path": input_pdf,
        "engine": "hybrid:pdf-text-layer+tesseract-fallback",
        "text": "",
        "pages": [],
    }

    all_pages_text = []
    fallback_pages = []  # หน้าที่ต้องพึ่ง OCR (ไว้ตรวจสอบ/QA ทีหลัง)

    # เปิดทั้งสองตัวพร้อมกัน: pdfplumber สำหรับ text layer, pdfium ไว้ render ภาพ (เฉพาะตอน fallback)
    with pdfplumber.open(input_pdf) as pdf:
        pdfium_doc = None  # เปิดแบบ lazy เฉพาะตอนจำเป็น จะได้ไม่เสียเวลาถ้าไม่มีหน้าไหนต้อง fallback เลย

        for page_index, page in enumerate(pdf.pages):
            print(f"กำลังประมวลผลหน้า {page_index + 1}/{len(pdf.pages)}...")
            text_layer = extract_page_text_precise(page).strip()
            words = []

            # เช็ค PUA จาก page.chars ดิบ (ก่อนโดนกรองออกใน extract_page_text_precise)
            # ควบคู่กับ looks_garbled ปกติ -- ถ้าเจอ PUA แปลว่าฟอนต์หน้านี้มีปัญหา
            # ToUnicode CMap ของวรรณยุกต์/สระ ต้องบังคับ fallback ไป OCR เสมอ
            has_pua = page_has_pua_glyphs(page)

            if len(text_layer) >= min_chars and not looks_garbled(text_layer) and not has_pua:
                # ใช้ text layer ตรงๆ - แม่นยำที่สุด ไม่ต้อง OCR
                page_text = text_layer
                source = "text_layer"
            else:
                # เข้าเงื่อนไข fallback ได้ 2 กรณี: (1) text layer สั้น/ว่าง (ไม่มี text layer จริง)
                # หรือ (2) ยาวพอแต่ตรวจแล้วเป็นตัวอักษรเพี้ยน (font encoding ผิด) -> OCR แทน
                if pdfium_doc is None:
                    pdfium_doc = pdfium.PdfDocument(input_pdf)
                bitmap = pdfium_doc[page_index].render(scale=3.0)
                pil_image = bitmap.to_pil()
                page_text, words = ocr_single_page(pil_image, lang=lang)
                # แก้ artifact เฉพาะของ Tesseract (x/X ปนกันถูกอ่านเป็นเลขไทย) ทันทีที่นี่
                # ทำเฉพาะ branch นี้ (tesseract_fallback) เท่านั้น เพราะ text_layer เป็น
                # ตัวอักษรจริงจาก PDF โดยตรง ไม่ได้ผ่านโมเดลภาษาของ Tesseract จึงไม่มี
                # artifact แบบนี้เกิดขึ้นได้ตั้งแต่แรก ไม่ต้องเสียเวลาสแกนซ้ำ
                page_text = fix_mixed_thai_arabic_digit_tokens(page_text)
                source = "tesseract_fallback"
                fallback_pages.append(page_index + 1)
                # เช็คซ้ำอีกครั้งหลัง OCR: ถ้าผล OCR เองก็ยังดูเพี้ยน (สัดส่วนภาษาไทยต่ำผิดปกติ
                # ทั้งที่ควรมีเนื้อหาไทยเยอะ) ให้เตือนทันที อาจเกิดจากคุณภาพภาพต่ำ/DPI ไม่พอ
                # หรือปัญหาภาษาอื่นที่ verify_languages เช็คไม่ครอบคลุม -- ไม่ปล่อยให้เงียบผ่านไป
                if looks_garbled(page_text):
                    print(
                        f"    [คำเตือน] หน้า {page_index + 1}: ผล Tesseract OCR ยังดูเพี้ยน "
                        "(สัดส่วนตัวอักษรไทยต่ำผิดปกติ) ควรตรวจสอบคุณภาพภาพ/ความละเอียด "
                        "หรือ traineddata ของภาษาที่ใช้"
                    )

            formatted_page_text = f"--- Page {page_index + 1} ---\n{page_text}"
            all_pages_text.append(formatted_page_text)
            json_data["pages"].append(
                {"page": page_index + 1, "text": page_text, "words": words, "source": source}
            )

    json_data["text"] = "\n\n".join(all_pages_text)
    json_data["fallback_pages"] = fallback_pages

    output_json_path = os.path.join(output_folder, "ocr_result.json")
    output_txt_path = os.path.join(output_folder, "ocr_result.txt")

    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump(json_data, f, ensure_ascii=False, indent=4)
    with open(output_txt_path, "w", encoding="utf-8") as f:
        f.write(json_data["text"])

    print(f"\nเสร็จสิ้น: {len(json_data['pages'])} หน้า")
    print(f"หน้าที่ต้อง fallback ไป OCR ({len(fallback_pages)} หน้า): {fallback_pages}")
    print(f"บันทึกผลลัพธ์ที่: {output_json_path}")

    return json_data


def main():
    parser = argparse.ArgumentParser(description="Hybrid PDF text extraction: text layer first, OCR fallback per-page.")
    parser.add_argument("input_pdf", nargs="?", default="data/input/sample.pdf")
    parser.add_argument("output_folder", nargs="?", default="output_tesseract")
    parser.add_argument("--min-chars", type=int, default=20, help="ถ้า text layer ของหน้านั้นสั้นกว่านี้ ถือว่าเป็นหน้าสแกน -> fallback OCR")
    parser.add_argument("--lang", default="tha+eng")
    args = parser.parse_args()

    extract_hybrid(args.input_pdf, args.output_folder, min_chars=args.min_chars, lang=args.lang)


if __name__ == "__main__":
    main()