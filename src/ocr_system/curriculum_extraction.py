import json
import re
from pathlib import Path

# ==========================================
# 1. HELPER FUNCTIONS & PATTERNS
# ==========================================

# Pattern สำหรับตรวจจับ Code (รองรับอักขระ OCR ผิดพลาด และเลขไทย)
RAW_CODE_REGEX = re.compile(r"\b([0-9OolIxX\u0e50-\u0e59]{8})\b")
THAI_NUM_MAP = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")

def normalize_code_token(token: str) -> str:
    if len(token) != 8:
        return token
    token_translated = token.translate(THAI_NUM_MAP)
    res = []
    for char in token_translated:
        if char in ("O", "o"): res.append("0")
        elif char in ("l", "I"): res.append("1")
        elif char in ("X", "x"): res.append("x")
        else: res.append(char)
    normalized = "".join(res)
    if re.match(r"^(?:\d{8}|\d{4}x{4}|\d{5}x{3}|x{8})$", normalized, re.IGNORECASE):
        return normalized.lower() if "x" in normalized.lower() else normalized
    return token

def clean_course_name_th(text: str) -> str:
    temp_text = re.sub(r"กลุ่มวิชาที่กําหนดโดยคณะ|กลุ่มวิชาที่กำหนดโดยคณะ|เฉพาะโครงการเข้าร่วมสหกิจ", "", text)
    thai_parts = re.findall(r"[\u0e00-\u0e7f0-9/]+", temp_text)
    clean_th = " ".join(thai_parts).strip()
    clean_th = re.sub(r"\bรวม\b", "", clean_th).strip()
    clean_th = re.sub(r"^[\d\s/]+", "", clean_th).strip()
    clean_th = re.sub(r"(\b\d+\b)\s+\1$", r"\1", clean_th)
    if "วิชาเลือก" in clean_th:
        clean_th = re.sub(r"\s*หรือ\s*", " / ", clean_th)
    return clean_th if clean_th else None

def clean_course_name_en(text: str) -> str:
    if not text: return None
    raw_blocks = re.split(r"[\u0e00-\u0e7f]+", text)
    blacklisted_words = [
        r"\bCOURSE\b", r"\bTITLE\b", r"\bCREDIT\s*S?\b", r"\bPREREQUISITE\b",
        r"\bLECTURE\b", r"\bLAB\b", r"\bSEMESTER\b", r"\bYEAR\b", r"\bGENED\b",
        r"\bREQUIRED\b", r"\bPLAN\b", r"\bDSBA\b"
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

    if not en_items: return None
    final_items = []
    for idx, item in enumerate(en_items):
        if idx > 0:
            item = re.sub(r"^ELECTIVE\s+IN\s+", "", item, flags=re.IGNORECASE).strip()
        final_items.append(item.strip())
    
    result = " / ".join(final_items)
    corrections = {
        r"\bNFORMATION\b": "INFORMATION", r"\bANAGEMENT\b": "MANAGEMENT",
        r"\bNTRODUCTION\b": "INTRODUCTION", r"\bROGRAMMING\b": "PROGRAMMING",
        r"\bLGEBRA\b": "ALGEBRA", r"\bPTIMIZATION\b": "OPTIMIZATION",
    }
    for pattern, replacement in corrections.items():
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    return result.upper() if result else None

# ==========================================
# 2. SECTION SCOPING, PREREQUISITE & EXTRACTION
# ==========================================

def merge_split_semester_headers(lines: list[str]) -> list[str]:
    merged: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if re.search(r"ภาคการศึกษาที่\s*$", line) and i + 1 < len(lines):
            next_line = lines[i + 1].strip()
            if re.fullmatch(r"[12]", next_line):
                merged.append(f"{line} {next_line}")
                i += 2
                continue
        merged.append(line)
        i += 1
    return merged

def apply_section_scoping(lines: list[str]) -> list[str]:
    """กำหนดขอบเขตตามกฎ: เริ่มที่ ปีที่..ภาคการศึกษา.. และหยุดที่ รวมตลอดหลักสูตร"""
    start_idx = 0
    end_idx = len(lines)

    start_patterns = [
        r"ปีที่\s*\d+\s*ภาคการศึกษาที่",
        r"ชั้นปีที่\s*1",
        r"แผนการศึกษา"
    ]
    end_patterns = [
        r"รวมตลอดหลักสูตร" # หยุดเก็บรหัสวิชาเมื่อเจอคำนี้
    ]

    for idx, line in enumerate(lines):
        if any(re.search(p, line, re.IGNORECASE) for p in start_patterns):
            start_idx = idx
            break

    for idx in range(start_idx, len(lines)):
        if any(re.search(p, lines[idx], re.IGNORECASE) for p in end_patterns):
            end_idx = idx
            break

    return lines[start_idx:end_idx]

def extract_prerequisites_from_desc(lines: list[str]) -> dict:
    """สกัด Prerequisite จากส่วน 'คำอธิบายรายวิชาเฉพาะ' โดยตรง"""
    prereqs = {}
    in_desc = False
    current_code = None
    
    for line in lines:
        if "คำอธิบายรายวิชาเฉพาะ" in line or "คำอธิบายรายวิชา" in line:
            in_desc = True
            
        if in_desc:
            tokens = [normalize_code_token(t) for t in line.split()]
            codes = [t for t in tokens if RAW_CODE_REGEX.match(t)]
            
            # ถ้าเจอรายวิชาใหม่ (ความยาวบรรทัดมักจะไม่ยาวมากในส่วนหัว)
            if codes and len(line) < 120 and not re.search(r"(บังคับก่อน|ผ่านมาก่อน|Prerequisite)", line, re.IGNORECASE):
                current_code = codes[0]
                
            # ดึงวิชาบังคับก่อน
            if current_code and re.search(r"(บังคับก่อน|ผ่านมาก่อน|เงื่อนไขรายวิชา|Prerequisite)", line, re.IGNORECASE):
                if codes:
                    # คัดเอารหัสที่ไม่ใช่ตัวเองและไม่ใช่รหัส xxxx
                    prereq_candidates = [c for c in codes if c != current_code and "x" not in c.lower()]
                    if prereq_candidates:
                        prereqs[current_code] = prereq_candidates[0]
                elif "ไม่มี" in line or "None" in line:
                    prereqs[current_code] = "ไม่มี"
                    
    return prereqs

def extract_curriculum_from_file(ocr_path: str, program: str | None = None, plan: str | None = None) -> dict:
    with open(ocr_path, "r", encoding="utf-8") as f:
        ocr_data = json.load(f)

    if program is None:
        stem = Path(ocr_path).stem.replace("_ocr", "")
        program = stem.split("_")[0].upper() if stem else "UNKNOWN"

    full_text = ocr_data.get("text", "") if isinstance(ocr_data, dict) else str(ocr_data)
    
    # กฎ: ถ้ามีคำว่า "ไม่" คือ no_coop
    if plan is None or plan == "unspecified":
        if "ไม่" in full_text:
            plan = "no_coop"
        else:
            plan = "coop"

    raw_lines = [line.strip() for line in full_text.split("\n") if line.strip()]
    raw_lines = merge_split_semester_headers(raw_lines)
    
    # สกัด Prerequisite รอไว้ล่วงหน้าจาก Text เต็ม
    prereqs_map = extract_prerequisites_from_desc(raw_lines)

    # Scoping เฉพาะตาราง
    scoped_lines = apply_section_scoping(raw_lines)

    courses_dict = {}
    current_year = 1
    current_semester = 1

    credit_full_pattern = re.compile(r"(\d\s*[\( ]\s*\d+\s*-\s*\d+\s*-\s*\d+\s*[\) ]?(?:\s*หรือ\s*\d\s*[\( ]\s*\d+\s*-\s*\d+\s*-\s*\d+\s*[\) ]?)?)")
    credit_simple_pattern = re.compile(r"\b(\d)\b")

    i = 0
    while i < len(scoped_lines):
        line_str = scoped_lines[i]

        if len(line_str) <= 60:
            if re.search(r"หมวดวิชาเลือก|วิชาเลือกเสรี|ELECTIVE", line_str, re.IGNORECASE):
                current_year = 0
                current_semester = 0
            else:
                if re.search(r"ปีที่\s*1|1st\s*Year", line_str, re.IGNORECASE): current_year = 1
                elif re.search(r"ปีที่\s*2|2nd\s*Year", line_str, re.IGNORECASE): current_year = 2
                elif re.search(r"ปีที่\s*3|3rd\s*Year", line_str, re.IGNORECASE): current_year = 3
                elif re.search(r"ปีที่\s*4|4th\s*Year", line_str, re.IGNORECASE): current_year = 4

                if re.search(r"ภาคการศึกษาที่\s*1|Semester\s*1", line_str, re.IGNORECASE): current_semester = 1
                elif re.search(r"ภาคการศึกษาที่\s*2|Semester\s*2", line_str, re.IGNORECASE): current_semester = 2

        # กฎ: ถ้าข้างหน้าเป็นตัวเลข ข้างหลังเป็นตัวเลข เราตรวจจับรหัสวิชาตรงนี้เป็นแกนหลัก
        tokens = line_str.split()
        found_code = None
        for tok in tokens:
            norm_tok = normalize_code_token(tok)
            if RAW_CODE_REGEX.match(norm_tok):
                found_code = norm_tok
                break

        if found_code:
            code = found_code
            candidate_lines = [line_str]
            combined_chunk = line_str

            for j in range(i + 1, min(i + 10, len(scoped_lines))):
                next_line = scoped_lines[j]
                
                # ตรวจสอบว่าเป็นกรณี "หรือ" หรือไม่ (ตามกฎ: ให้เก็บไว้บรรทัดเดียวกัน)
                is_or_condition = False
                if re.search(r"หรือ\s*$", combined_chunk.strip()) or re.search(r"^\s*หรือ", next_line.strip()):
                    is_or_condition = True

                has_new_code = any(RAW_CODE_REGEX.match(normalize_code_token(t)) for t in next_line.split())
                
                # ถ้ารหัสวิชาตามด้วย หรือ ให้เก็บไว้รวมกัน ไม่เบรก chunk
                if has_new_code and not is_or_condition:
                    break
                
                if not is_or_condition and re.search(r"^\s*(รวมตลอดหลักสูตร|รวม|ภาคการศึกษา|ปีที่|หมวดวิชา|กลุ่ม|FREE ELECTIVE)", next_line, re.IGNORECASE):
                    break

                if not is_or_condition and len(next_line) > 80 and not credit_full_pattern.search(next_line):
                    break

                candidate_lines.append(next_line)
                combined_chunk = " ".join(candidate_lines)

            note = None
            if "กลุ่มวิชาที่กําหนดโดยคณะ" in combined_chunk or "กลุ่มวิชาที่กำหนดโดยคณะ" in combined_chunk: note = "กลุ่มวิชาที่กำหนดโดยคณะ"
            elif "เฉพาะโครงการเข้าร่วมสหกิจ" in combined_chunk: note = "เฉพาะโครงการเข้าร่วมสหกิจ"

            credit_match = credit_full_pattern.search(combined_chunk)
            if credit_match:
                raw_credits = credit_match.group(1)
                credits = raw_credits.replace(" ", "")
                if "หรือ" in raw_credits: credits = credits.replace("หรือ", " หรือ ")
                clean_chunk = combined_chunk.replace(credit_match.group(0), "")
            else:
                credit_simple = credit_simple_pattern.search(combined_chunk)
                credits = credit_simple.group(1) if credit_simple else "3"
                clean_chunk = combined_chunk

            # ดึงวิชาบังคับก่อนจาก Dictionary ที่สแกนได้จากคำอธิบายรายวิชาเฉพาะ
            prerequisite = prereqs_map.get(code, "ไม่มี")

            # กวาดรหัสวิชาออกเพื่อคลีนชื่อวิชา
            all_tokens = [normalize_code_token(t) for t in combined_chunk.split()]
            for c in [t for t in all_tokens if RAW_CODE_REGEX.match(t)]:
                clean_chunk = clean_chunk.replace(c, " ")

            name_th = clean_course_name_th(clean_chunk)
            name_en = clean_course_name_en(clean_chunk)

            category = "หมวดวิชาเฉพาะ"
            if code.startswith("9664") or (name_th and "ศึกษาทั่วไป" in name_th): category = "หมวดวิชาศึกษาทั่วไป"
            elif (name_th and "เลือกเสรี" in name_th) or code.lower() == "xxxxxxxx": category = "หมวดวิชาเลือกเสรี"

            course_type = "เลือก" if ("x" in code.lower() or (name_th and "เลือก" in name_th) or current_year == 0) else "บังคับ"
            flexible_year_semester = "3/1, 3/2, 4/1" if (current_year == 0 and current_semester == 0) else None

            is_placeholder_code = any(code.endswith(ext) for ext in ["xxx", "xxxx", "xxxxxxxx"])
            unique_key = f"{code}_{name_th}" if is_placeholder_code else code

            if unique_key in courses_dict:
                if current_year > 0:
                    courses_dict[unique_key]["year"] = current_year
                    courses_dict[unique_key]["semester"] = current_semester
                    courses_dict[unique_key]["flexible_year_semester"] = None
                    courses_dict[unique_key]["type"] = "เลือก" if ("x" in code.lower() or (name_th and "เลือก" in name_th)) else "บังคับ"
            else:
                courses_dict[unique_key] = {
                    "code": code,
                    "name_th": name_th,
                    "name_en": name_en,
                    "credits": credits,
                    "year": current_year,
                    "semester": current_semester,
                    "category": category,
                    "type": course_type,
                    "prerequisite": prerequisite,
                    "flexible_year_semester": flexible_year_semester,
                    "note": note,
                }

        i += 1

    return {
        "source": "OCR curriculum extraction",
        "description": f"Extracted academic plan from OCR for {program} ({plan})",
        "program": program,
        "plan": plan,
        "courses": list(courses_dict.values()),
    }