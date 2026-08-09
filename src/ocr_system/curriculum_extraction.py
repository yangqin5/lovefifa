import json
import os
import re

def normalize_ocr_text(text: str) -> str:
    """แก้ไข Unicode ที่ OCR แยกสระ-วรรณยุกต์ผิดรูป เช่น 'ํ'+'า' (นิคหิต+สระอา) ที่ควรเป็น 'ำ' (สระอำ ตัวเดียว)"""
    if not text:
        return text
    # นิคหิต (U+0E4D) + สระอา (U+0E32) -> สระอำ (U+0E33)
    text = text.replace("\u0e4d\u0e32", "\u0e33")
    
    # [จุดที่เพิ่ม] แก้ไขคำว่า "หรอ" ที่อยู่เดี่ยวๆ ให้กลายเป็น "หรือ"
    text = re.sub(r'(?<!\S)หรอ(?!\S)', 'หรือ', text)
    
    return text


def is_header_noise(text: str) -> bool:
    """ตรวจสอบว่าเป็นคำขยะจากหัวตารางหรือไม่"""
    text_clean = text.strip()
    
    # 1. เช็คคำโดดๆ ที่มักเป็นหัวตาราง (ถ้ามาคำเดียวโดดๆ ให้ถือเป็นขยะ)
    exact_noise_words = ["บรรยาย", "ปฏิบัติ", "ศึกษา", "ทฤษฎี", "รวม"]
    if text_clean in exact_noise_words:
        return True
        
    # 2. เช็คกลุ่มคำที่เป็นหัวตารางแน่ๆ (มีคำเหล่านี้ผสมอยู่ให้ถือเป็นขยะ)
    hard_keywords = [
        "ด้วยตนเอง", "หน่วยกิต", "รหัสวิชา", "ชื่อวิชา", 
        "ภาคการศึกษา", "ปีที่", "แผนการศึกษา", "มคอ"
    ]
    return any(kw in text_clean for kw in hard_keywords)

def is_thai_text(text: str) -> bool:
    """ตรวจสอบว่ามีตัวอักษรภาษาไทยและมีความยาวพอที่จะไม่ใช่ตัวอักษรขยะ"""
    thai_chars = re.findall(r'[\u0e00-\u0e7f]', text)
    return len(thai_chars) >= 3 # บังคับว่าต้องมีตัวอักษรไทยอย่างน้อย 3 ตัว

def is_english_text(text: str) -> bool:
    """ตรวจสอบว่ามีคำภาษาอังกฤษหรือไม่"""
    return bool(re.search(r'\b[a-zA-Z]{2,}\b', text))

def normalize_course_code(code: str) -> str:
    """แปลงเลขไทยเป็นอารบิก และลบช่องว่าง/เครื่องหมาย - ออกจากรหัสวิชา"""
    if not code:
        return code
    
    # แปลงเลขไทยเป็นอารบิก
    thai_digits = "๐๑๒๓๔๕๖๗๘๙"
    for i, td in enumerate(thai_digits):
        code = code.replace(td, str(i))
        
    code = code.replace(" ", "").replace("-", "")

    return code


def canonicalize_placeholder_code(code: str) -> str:
    """
    รหัสวิชาที่ยังไม่ระบุ (มี x ปน) มักถูก OCR อ่านผิดแตกต่างกันไปในแต่ละครั้งที่ตารางถูกพิมพ์ซ้ำ
    (เช่น 'xxxxxxxx' ตัวจริงอาจถูกอ่านเป็น '9xxxxxxx', '2xxxxxxx', '29x066xx0' ฯลฯ)
    ฟังก์ชันนี้แปลงกลับไปเป็นรูปแบบมาตรฐาน เพื่อให้ตรวจจับรายวิชาซ้ำที่มาจากแผนสหกิจ/ไม่สหกิจ
    (ซึ่งมีเนื้อหาปีเดียวกันซ้ำกันในเอกสาร) ได้อย่างถูกต้อง
    """
    code_l = code.lower()
    known_prefixes = [
        (re.compile(r"^9664x{3,5}$"), "9664xxxx"),
        (re.compile(r"^96644x{2,4}$"), "96644xxx"),
        (re.compile(r"^06036x{2,4}$"), "06036xxx"),
    ]
    for pattern, canonical in known_prefixes:
        if pattern.match(code_l):
            return canonical

    # ถ้ามี x ปนแต่ไม่ตรงกับ prefix ที่รู้จัก (ขยะจาก OCR ล้วนๆ) ให้ถือเป็นวิชาเลือกเสรีที่ไม่ระบุรหัส
    if "x" in code_l:
        return "xxxxxxxx"

    return code


def parse_course_descriptions(full_text: str) -> dict:
    """
    สกัดข้อมูลจากหัวข้อ "คำอธิบายรายวิชา" (หมวด 3.4) เพื่อใช้เป็น Master Database
    เนื่องจากโซนนี้ข้อความสมบูรณ์และได้ชื่อวิชา TH/EN + Prerequisite ที่ถูกต้องที่สุด
    """
    master_catalog = {}
    desc_start = re.search(r"คำอธิบายรายวิชา", full_text)
    if not desc_start:
        return master_catalog

    desc_text = full_text[desc_start.start():]
    lines = [l.strip() for l in desc_text.split("\n") if l.strip()]

    code_pattern = re.compile(r"\b([0-9\u0e50-\u0e59]{8})\b")
    
    i = 0
    while i < len(lines):
        line = lines[i]
        code_match = code_pattern.search(line)
        if code_match:
            # ถ้าบรรทัดนี้เป็นการอ้างอิงถึงวิชาบังคับก่อน (ไม่ใช่หัวข้อวิชาใหม่) ให้ข้ามไป
            # เพราะบางวิชามีรหัสของตัวเองอ่านไม่ออก (OCR หาย) ทำให้บรรทัด "วิชาบังคับก่อน : XXXXXXXX"
            # ถูกเข้าใจผิดว่าเป็นวิชาใหม่ (รหัสซ้ำกับวิชาที่ตัวเองอ้างถึงเป็น prerequisite)
            text_before_code = line[:code_match.start()]
            if re.search(r"วิชาบังคับก่อน|PREREQUISITE", text_before_code, re.IGNORECASE):
                i += 1
                continue

            raw_code = code_match.group(1)
            code = normalize_course_code(raw_code)
            
            # ดึงข้อความบริเวณรายวิชานั้น (ลบหน่วยกิตออก)
            chunk = " ".join(lines[i:min(i+8, len(lines))])
            
            # สกัดชื่อไทย
            th_match = re.search(r"([0-9]{8})\s+([\u0e00-\u0e7f\s\d]+?)(?=\s*\d\s*\()", chunk)
            name_th = th_match.group(2).strip() if th_match else None
            if name_th:
                name_th = re.sub(r"\s+", " ", name_th)

            # สกัดชื่ออังกฤษ (รองรับจุด "." ที่ OCR แทรกมาผิดๆ กลางชื่อวิชา เช่น "INFORMATION. TECHNOLOGY")
            en_match = re.search(r"([A-Z\s\-&/\.]{4,})(?=\s*วิชาบังคับก่อน|\s*PREREQUISITE)", chunk)
            name_en = en_match.group(1).strip() if en_match else None
            if name_en:
                name_en = re.sub(r"\s+", " ", name_en)

            # สกัด Prerequisite (รองรับกรณีมีวิชาบังคับก่อนมากกว่า 1 วิชา คั่นด้วย "หรือ"/"OR")
            prereq = "ไม่มี"
            prereq_match = re.search(r"PREREQUISITE[\.\s]*:\s*([A-Z0-9\s]+)", chunk, re.IGNORECASE)
            if prereq_match:
                p_text = prereq_match.group(1).strip()
                prereq_codes = re.findall(r"\d{8}", p_text)
                if p_text.upper() != "NONE" and prereq_codes:
                    prereq = " หรือ ".join(normalize_course_code(c) for c in prereq_codes)

            master_catalog[code] = {
                "name_th": name_th,
                "name_en": name_en,
                "prerequisite": prereq
            }
        i += 1
        
    return master_catalog


def clean_course_name_th(text: str) -> str | None:
    if not text:
        return None
    temp_text = re.sub(r"กลุ่มวิชาที่กําหนดโดยคณะ|กลุ่มวิชาที่กำหนดโดยคณะ|เฉพาะโครงการเข้าร่วมสหกิจ", "", text)
    thai_parts = re.findall(r"[\u0e00-\u0e7f0-9/]+", temp_text)
    clean_th = " ".join(thai_parts).strip()
    clean_th = re.sub(r"\bรวม\b", "", clean_th).strip()
    clean_th = re.sub(r"(\b\d+\b)\s+\1$", r"\1", clean_th)
    return clean_th if clean_th else None


def clean_course_name_en(text: str) -> str | None:
    if not text:
        return None
    raw_blocks = re.split(r"[\u0e00-\u0e7f]+", text)
    blacklisted_words = [
        r"\bCOURSE\b", r"\bTITLE\b", r"\bCREDIT\s*S?\b", r"\bPREREQUISITE\b",
        r"\bLECTURE\b", r"\bLAB\b", r"\bSEMESTER\b", r"\bYEAR\b", r"\bGENED\b",
        r"\bREQUIRED\b", r"\bPLAN\b", r"\bDSBA\b", r"\bIT\b"
    ]
    en_items = []
    for block in raw_blocks:
        clean_block = block
        for word_pattern in blacklisted_words:
            clean_block = re.sub(word_pattern, "", clean_block, flags=re.IGNORECASE)
        clean_block = re.sub(r"[^\w\s\-\&/]", " ", clean_block)
        clean_block = re.sub(r"^\s*\d+\s+", "", clean_block)
        clean_block = " ".join(clean_block.split()).strip()
        if clean_block and len(clean_block) > 2 and not clean_block.isdigit():
            en_items.append(clean_block)

    return " / ".join(en_items) if en_items else None


# ==========================================================================
# COOP / NO-COOP PLAN SPLITTING
# --------------------------------------------------------------------------
# หลักสูตรบางเล่ม (BIT, DSBA, IT) มี "แผนการศึกษา" 2 แผนอยู่ในเอกสารเดียวกัน:
#   3.1.4.1 แผนการศึกษาที่ไม่เข้าโครงการสหกิจศึกษา   (no_coop)
#   3.1.4.2 แผนการศึกษาสำหรับโครงการสหกิจศึกษา       (coop)
# ตัวเลขนำหน้าอาจไม่ตรงกันทุกเล่ม แต่ข้อความ "แผนการศึกษา" ... "สหกิจ" จะเหมือนกันเสมอ
# กฎการแยก: บรรทัด heading ที่มีทั้งคำว่า "แผนการศึกษา" และ "สหกิจ" ร่วมกัน
#   - มีคำว่า "ไม่" ด้วย  -> จุดเริ่มต้นของแผน no_coop
#   - ไม่มีคำว่า "ไม่"    -> จุดเริ่มต้นของแผน coop
# ==========================================================================

_PLAN_HEADER_RE = re.compile(r"^\s*\d+(?:\.\d+)*\.?\s*แผนการศึกษา", re.MULTILINE)
_DESC_HEADER_RE = re.compile(r"^\s*\d+(?:\.\d+)*\.?\s*คำอธิบายรายวิชา", re.MULTILINE)
_COOP_SPLIT_HEADER_RE = re.compile(
    r"^\s*\d+(?:\.\d+){1,4}\.?[^\n]*แผนการศึกษา[^\n]*สหกิจ[^\n]*$", re.MULTILINE
)


def split_ocr_text_by_coop(full_text: str) -> dict:
    """แยกโซน 'แผนการศึกษา' ของ full_text ออกเป็น 2 เวอร์ชัน (coop / no_coop)

    คืนค่า:
        {
            "coop": <full_text ฉบับที่แทนที่โซนแผนการศึกษาด้วยเฉพาะส่วน coop>,
            "no_coop": <full_text ฉบับที่แทนที่โซนแผนการศึกษาด้วยเฉพาะส่วน no_coop>,
            "split_found": bool  # True ถ้าหา heading แยกแผนได้จริง
        }

    ส่วนก่อน/หลังโซนแผนการศึกษา (เช่นหัวข้อ "คำอธิบายรายวิชา" ที่ใช้สร้าง master
    catalog) จะยังคงเดิมทุกตัวอักษรในทั้งสองเวอร์ชัน เพื่อให้ extract_curriculum_from_file
    ทำงานได้ตามปกติกับทั้งสองเวอร์ชันโดยไม่ต้องแก้ logic การสกัดข้อมูลเดิมเลย
    """
    plan_start = _PLAN_HEADER_RE.search(full_text)
    if not plan_start:
        # หาโซนแผนการศึกษาไม่เจอเลย -> ไม่มีอะไรให้แยก
        return {"coop": full_text, "no_coop": full_text, "split_found": False}

    plan_end = _DESC_HEADER_RE.search(full_text)
    end_pos = plan_end.start() if plan_end else len(full_text)

    prefix = full_text[: plan_start.start()]
    plan_text = full_text[plan_start.start() : end_pos]
    suffix = full_text[end_pos:]

    matches = list(_COOP_SPLIT_HEADER_RE.finditer(plan_text))
    if not matches:
        # เจอโซนแผนการศึกษา แต่หา heading ที่แยก coop/no_coop ไม่เจอ (เช่น เล่มที่มีแผนเดียว)
        return {"coop": full_text, "no_coop": full_text, "split_found": False}

    sections = {"coop": [], "no_coop": []}
    for idx, m in enumerate(matches):
        label = "no_coop" if "ไม่" in m.group(0) else "coop"
        start = m.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(plan_text)
        sections[label].append(plan_text[start:end])

    result = {"split_found": True}
    for label in ("coop", "no_coop"):
        # ถ้าไม่เจอส่วนของ label นี้เลย (ผิดปกติ) ให้ fallback ไปใช้ plan_text ทั้งก้อนแทน
        # เพื่อไม่ให้ courses หายไปเงียบๆ โดยไม่มีสัญญาณเตือน
        variant_plan_text = "\n".join(sections[label]) if sections[label] else plan_text
        result[label] = prefix + variant_plan_text + suffix

    return result


def extract_curriculum_variants(ocr_path: str, program: str, split_plans: bool = True) -> dict:
    """เรียก extract_curriculum_from_file ซ้ำสำหรับแต่ละแผน (coop / no_coop) โดยไม่แก้ logic
    การสกัดข้อมูลเดิมเลย -- ใช้วิธีสร้างไฟล์ OCR JSON ชั่วคราวที่มี "text" เฉพาะโซนของ
    แผนนั้นๆ (ส่วนอื่นของเอกสารคงเดิม) แล้วเรียกฟังก์ชันเดิมกับไฟล์ชั่วคราวนั้น

    คืนค่า:
        {"program": program, "plans": {"coop": {...}, "no_coop": {...}}, "split_found": bool}

    หาก split_plans=False จะคืนค่ารูปแบบเดิมของ extract_curriculum_from_file ตรงๆ
    (ใช้กับหลักสูตรที่มีแผนการศึกษาแผนเดียว เช่น AIT)
    """
    if not split_plans:
        return extract_curriculum_from_file(ocr_path=ocr_path, program=program)

    with open(ocr_path, "r", encoding="utf-8") as f:
        ocr_data = json.load(f)
    full_text = ocr_data.get("text", "") if isinstance(ocr_data, dict) else str(ocr_data)
    source_path = ocr_data.get("source_path", ocr_path) if isinstance(ocr_data, dict) else ocr_path
    engine = ocr_data.get("engine", "") if isinstance(ocr_data, dict) else ""

    variants = split_ocr_text_by_coop(full_text)

    plans = {}
    for label in ("coop", "no_coop"):
        tmp_path = f"{ocr_path}.__{label}__.tmp.json"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(
                    {"source_path": source_path, "engine": engine, "text": variants[label]},
                    f,
                    ensure_ascii=False,
                )
            plans[label] = extract_curriculum_from_file(ocr_path=tmp_path, program=program, plan=label)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    return {"program": program, "plans": plans, "split_found": variants["split_found"]}


def extract_curriculum_from_file(ocr_path: str, program: str = "IT (International Program)", plan: str | None = None) -> dict:
    with open(ocr_path, "r", encoding="utf-8") as f:
        ocr_data = json.load(f)

    full_text = ocr_data.get("text", "") if isinstance(ocr_data, dict) else str(ocr_data)
    full_text = normalize_ocr_text(full_text)

    # 1. Master Catalog (ถ้ามี)
    master_catalog = parse_course_descriptions(full_text) if 'parse_course_descriptions' in globals() else {}

    # 2. ตัดขอบเขตเฉพาะส่วน "แผนการศึกษา"
    plan_start = re.search(r"^\s*\d+(?:\.\d+)*\.?\s*แผนการศึกษา", full_text, re.MULTILINE)
    plan_end = re.search(r"^\s*\d+(?:\.\d+)*\.?\s*คำอธิบายรายวิชา", full_text, re.MULTILINE)

    if plan_start:
        end_pos = plan_end.start() if plan_end else len(full_text)
        plan_text = full_text[plan_start.start():end_pos]
    else:
        plan_text = full_text

    lines = [line.strip() for line in plan_text.split("\n") if line.strip()]

    courses_dict = {}
    current_year = 1
    current_semester = 1
    current_note = None

    code_pattern = re.compile(r"\b([0-9xX\u0e50-\u0e59]{8,10})\b")
    credit_full_pattern = re.compile(r"(\d\s*[\( ]\s*[\dxX]+\s*-\s*[\dxX]+\s*-\s*[\dxX]+\s*[\) ]?)")

    # [จุดที่เพิ่ม 1] ตัวแปรสำหรับจดจำวิชาล่าสุด เพื่อใช้เช็คว่าวิชาต่อไปเป็นวิชาทางเลือกหรือไม่
    last_course_key = None
    last_course_line_index = -1

    is_flexible_elective_zone = False
    i = 0
    while i < len(lines):
        line_str = lines[i]

        #Tracking ปี / เทอม
        if re.search(r"หมวดวิชาเลือก|กลุ่มวิชา|วิชาเลือกเฉพาะ", line_str) and not re.search(r"\d{8}", line_str):
            is_flexible_elective_zone = True
        # State Reset: หากวนกลับมาเจอโครงสร้างปี ให้ยกเลิกโซนวิชาเลือก
        elif re.search(r"ปีที่\s*[1-4]|1st|2nd|3rd|4th\s*Year", line_str, re.IGNORECASE):
            is_flexible_elective_zone = False 

        # 2. อัปเดต State ปัจจุบัน
        if not is_flexible_elective_zone:
            if re.search(r"ปีที่\s*1|1st\s*Year", line_str, re.IGNORECASE): current_year = 1
            elif re.search(r"ปีที่\s*2|2nd\s*Year", line_str, re.IGNORECASE): current_year = 2
            elif re.search(r"ปีที่\s*3|3rd\s*Year", line_str, re.IGNORECASE): current_year = 3
            elif re.search(r"ปีที่\s*4|4th\s*Year", line_str, re.IGNORECASE): current_year = 4

            if re.search(r"ภาคการศึกษาที่\s*1|Semester\s*1", line_str, re.IGNORECASE): current_semester = 1
            elif re.search(r"ภาคการศึกษาที่\s*2|Semester\s*2", line_str, re.IGNORECASE): current_semester = 2
            elif re.search(r"ภาคฤดูร้อน|Summer", line_str, re.IGNORECASE): current_semester = 3
        

        # Tracking Note
        if "กลุ่มวิชาที่กําหนดโดยคณะ" in line_str or "กลุ่มวิชาที่กำหนดโดยคณะ" in line_str:
            current_note = "กลุ่มวิชาที่กำหนดโดยคณะ"
        elif "เฉพาะโครงการเข้าร่วมสหกิจ" in line_str:
            current_note = "เฉพาะโครงการเข้าร่วมสหกิจ"
        elif re.search(r"ปีที่|ภาคการศึกษาที่|หมวดวิชา", line_str):
            current_note = None

        code_match = code_pattern.search(line_str)
        if code_match:
            raw_code = code_match.group(1)
            code = normalize_course_code(raw_code)
            if "x" in code.lower():
                code = canonicalize_placeholder_code(code)

            # 3. Transformation & Overrides: บังคับจัดการวิชาตามประเภทขั้นสุดท้าย
            assign_year = current_year
            assign_semester = current_semester
            
            # หากอยู่ในโซนวิชาเลือก (Year 0 / Sem 0)
            if is_flexible_elective_zone:
                # ข้อยกเว้น: ถ้าเป็นวิชา Placeholder (เช่น 06036xxx หรือ xxxxxxxx) ให้ยึดปี/เทอมตามแผน
                if "x" in code.lower():
                    pass # ใช้ assign_year/semester ตาม State ปกติ
                else:
                    assign_year = 0
                    assign_semester = 0
            
            # ข้อยกเว้นพิเศษ: สหกิจศึกษา ให้โยนเข้า Year 0 เสมอตาม Ground Truth
            if "สหกิจ" in line_str:
                assign_year = 0
                assign_semester = 0

            # =========================================================================
            # [จุดที่แก้ไข] - ดึงหน่วยกิต, ชื่อไทย (ถอยหลัง) และชื่ออังกฤษ (เดินหน้า)
            # =========================================================================
            
            # --- 1. สกัดหน่วยกิต (กวาดหาทั้งหมดในรัศมีรอบๆ รหัสวิชา) ---
            credits_list = []
            for k in range(max(0, i - 3), min(len(lines), i + 4)):
                c_match = credit_full_pattern.search(lines[k])
                if c_match:
                    c_str = c_match.group(1).replace(" ", "")
                    if c_str not in credits_list:
                        credits_list.append(c_str)
            # หากเจอมากกว่า 1 แบบ ให้เอามาเชื่อมกัน
            credits = " \nหรือ \n".join(credits_list) if credits_list else "3(2-2-5)"

            # --- 2. สกัด name_th (ถอยหลังสะสมบรรทัดที่ใช่เข้าด้วยกัน) ---
            th_lines = []
            for k in range(i, max(-1, i - 5), -1):
                line_k = lines[k].strip()
                
                # หากถอยไปชนรหัสวิชาของข้ออื่น ให้หยุดทันที
                if k < i and code_pattern.search(line_k):
                    break  
                
                line_k_clean = code_pattern.sub("", line_k)
                line_k_clean = credit_full_pattern.sub("", line_k_clean).strip()
                
                # กวาดขยะ OCR เฉพาะกิจ (we 0, ตัว 'ง' โดดๆ, หรือลูกน้ำ)
                line_k_clean = re.sub(r'\bwe\s*\d+\b|(?<!\S)[ง,](?!\S)', ' ', line_k_clean, flags=re.IGNORECASE)
                line_k_clean = re.sub(r'[\s\-_:=]+', ' ', line_k_clean).strip()
                
                if is_thai_text(line_k_clean) and not is_header_noise(line_k_clean):
                    th_lines.insert(0, line_k_clean) # แทรกไว้ข้างหน้าเพื่อให้ลำดับถูกต้อง

            name_th = " ".join(th_lines)

            # --- 3. สกัด name_en (เดินหน้าสะสมบรรทัดที่ใช่เข้าด้วยกัน) ---
            en_lines = []
            for k in range(i, min(len(lines), i + 4)):
                line_k = lines[k].strip()
                
                # หากเดินหน้าไปชนรหัสวิชาถัดไป ให้หยุดทันที
                if k > i and code_pattern.search(line_k):
                    break  
                
                line_k_clean = code_pattern.sub("", line_k)
                line_k_clean = credit_full_pattern.sub("", line_k_clean).strip()
                
                # ซ่อมแซมจุดที่แทรกมาในคำภาษาอังกฤษ (เช่น ELECTIVE.COURSE)
                line_k_clean = line_k_clean.replace(".", " ")
                line_k_clean = re.sub(r'(?<!\S)[ง,](?!\S)', ' ', line_k_clean)
                
                if is_english_text(line_k_clean) and not is_header_noise(line_k_clean):
                    en_lines.append(line_k_clean)

            name_en = " ".join(en_lines)
            name_en = re.sub(r'\s+', ' ', name_en).strip()

            # แมปทับด้วย Master Catalog
            prerequisite = "ไม่มี"
            if code in master_catalog:
                master_info = master_catalog[code]
                
                # 🛡️ เช็คก่อนทับ: ต้องมีค่า และต้องมีตัวอักษรไทยจริงๆ เท่านั้นถึงจะยอมให้เขียนทับ
                if master_info.get("name_th") and is_thai_text(master_info["name_th"]): 
                    name_th = master_info["name_th"]
                
                # 🛡️ เช็คก่อนทับ: ต้องมีค่า และต้องมีตัวอักษรอังกฤษจริงๆ เท่านั้นถึงจะยอมให้เขียนทับ
                if master_info.get("name_en") and is_english_text(master_info["name_en"]): 
                    name_en = master_info["name_en"]
                    
                prerequisite = master_info.get("prerequisite", "ไม่มี")

            # Clean Up ชื่อวิชาขั้นสุดท้าย
            if name_th:
                name_th = re.sub(r'[\*\-\•\_\=\+\|]', '', name_th)
                name_th = re.sub(r'\s+', ' ', name_th).strip()
                cleaned_th = clean_course_name_th(name_th)
                name_th = cleaned_th if cleaned_th else name_th
            
            if name_en:
                # บังคับลบอักษรภาษาไทยออกจากชื่อภาษาอังกฤษแบบเด็ดขาด
                name_en = re.sub(r'[\u0e00-\u0e7f]', '', name_en) 
                name_en = re.sub(r'[\*\-\•\_\=\+\|]', '', name_en)
                name_en = re.sub(r'\s+', ' ', name_en).strip()
                cleaned_en = clean_course_name_en(name_en)
                name_en = cleaned_en if cleaned_en else name_en

            # จัดหมวดหมู่รายวิชา
            category = "หมวดวิชาเฉพาะ"
            if code.startswith("9664") or "ศึกษาทั่วไป" in name_th:
                category = "หมวดวิชาศึกษาทั่วไป"
            elif "เลือกเสรี" in name_th or "x" in code.lower():
                category = "หมวดวิชาเลือกเสรี"

            # 🔴 แก้ปัญหาที่ 2: ปรับปรุงการระบุประเภทวิชา (Course Type) ให้ครอบคลุม
            if "เลือก" in category or "เลือก" in name_th or "x" in code.lower() or "ELECTIVE" in name_en.upper():
                course_type = "เลือก"
            else:
                course_type = "บังคับ"
                
            # จัดการ Flexible Year Semester สำหรับรายวิชาที่เป็น Year 0 / Sem 0
            flexible_ys = "4/2" if assign_year == 0 and assign_semester == 0 and "สหกิจ" not in name_th else None

            # การสร้าง Unique Key เพื่อกันวิชาซ้ำ
            if "x" in code.lower():
                slot_match = re.search(r"(\d+)\s*$", name_th) if name_th else None
                slot_suffix = slot_match.group(1) if slot_match else ""
                unique_key = f"{code}_{assign_year}_{assign_semester}_{slot_suffix}"
            else:
                unique_key = code

            is_alternative = False
            if last_course_key and last_course_line_index != -1 and (i - last_course_line_index) <= 4:
                # ดึงข้อความตั้งแต่บรรทัดหลังวิชาที่แล้ว จนถึงบรรทัดวิชาปัจจุบัน
                between_text = " ".join(lines[last_course_line_index + 1 : i + 1])
                # เช็คว่ามีคำว่า หรือ, OR (แบบแยกคำ) คั่นกลางหรือไม่
                if re.search(r'(?<!\S)(หรือ|OR)(?!\S)', between_text, re.IGNORECASE):
                    is_alternative = True

            if is_alternative and last_course_key in courses_dict:
                # ใช้วิธีนำไปผูกติดกับวิชาเดิม แทนที่จะสร้างใหม่
                prev_course = courses_dict[last_course_key]
                prev_course["code"] += f"\nหรือ\n{code}"
                
                if name_th:
                    prev_course["name_th"] = f"{prev_course['name_th']}\nหรือ\n{name_th}" if prev_course.get("name_th") else name_th
                if name_en:
                    prev_course["name_en"] = f"{prev_course['name_en']}\nOR\n{name_en}" if prev_course.get("name_en") else name_en
                
                # บังคับอัปเดตประเภทเป็น "เลือก" และบันทึก Note
                prev_course["type"] = "เลือก"
                if current_note:
                    prev_course["note"] = current_note or prev_course.get("note")

                # ขยับบรรทัดล่าสุดมาที่วิชานี้ และข้ามการสร้าง Dictionary ใหม่
                last_course_line_index = i
                i += 1
                continue

            if unique_key not in courses_dict:
                courses_dict[unique_key] = {
                    "code": code,
                    "name_th": name_th,
                    "name_en": name_en,
                    "credits": credits,
                    "year": assign_year,
                    "semester": assign_semester,
                    "category": category,
                    "type": course_type,
                    "prerequisite": prerequisite,
                    "flexible_year_semester": flexible_ys,
                    "note": current_note
                }
            last_course_key = unique_key
            last_course_line_index = i

        i += 1
        
    return {
        "program": program,
        "plan": plan,
        "courses": list(courses_dict.values())
    }