"""CLI entry point: OCR (Hybrid) -> Curriculum Extraction -> Evaluation.

รองรับการรันหลายหลักสูตรในครั้งเดียว (AIT, BIT, DSBA, IT) โดย:
- หลักสูตรที่มี ground truth แผนเดียว (AIT) จะสกัด+ประเมินผลแบบเดิม
- หลักสูตรที่มี ground truth 2 แผน คือ coop/no_coop (BIT, DSBA, IT) จะใช้ PDF ไฟล์เดียว
  แล้วแยกเนื้อหาโซน "แผนการศึกษา" ออกเป็น 2 เวอร์ชันตาม heading ที่มีคำว่า
  "แผนการศึกษา" + "สหกิจ" (ดู curriculum_extraction.split_ocr_text_by_coop) ก่อนสกัด
  และประเมินผลแยกกันเทียบกับ ground truth ของแต่ละแผน
"""

import argparse
import json
import shutil
from pathlib import Path

try:
    from .config import OCRConfig
    from .pipeline import run_ocr
    from .run_tesseract import extract_hybrid
    from .curriculum_extraction import extract_curriculum_from_file, extract_curriculum_variants
    from .evaluation import evaluate_from_files
except (ImportError, ValueError):
    from config import OCRConfig
    from pipeline import run_ocr
    from run_tesseract import extract_hybrid
    from curriculum_extraction import extract_curriculum_from_file, extract_curriculum_variants
    from evaluation import evaluate_from_files

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent.parent if BASE_DIR.name == "ocr_system" else BASE_DIR

# Path กำหนดตำแหน่งไฟล์
INPUT_DIR = PROJECT_ROOT / "data" / "input"
GT_DIR = PROJECT_ROOT / "data" / "ground_truth"
OUTPUT_TESSERACT_DIR = PROJECT_ROOT / "output_tesseract"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"

# ==========================================================================
# ตารางจับคู่หลักสูตร -> (ไฟล์ input PDF, ground truth ของแต่ละแผน)
#
# หลักสูตรที่มี "gt" มากกว่า 1 key (coop/no_coop) จะถูกมองว่าเป็นหลักสูตรที่มี
# แผนการศึกษา 2 แผนอยู่ในเอกสาร PDF เดียวกัน -> ระบบจะแยกเนื้อหาให้เองตอน extraction
# หลักสูตรที่มี key เดียวคือ "single" (เช่น AIT) จะสกัด+ประเมินแบบแผนเดียวตามเดิม
# ==========================================================================
PROGRAMS = {
    "AIT": {
        "input": "AIT_curriculum_book.pdf",
        "gt": {"single": "AIT_academic_plan.json"},
    },
    "BIT": {
        "input": "BIT_curriculum_book.pdf",
        "gt": {
            "coop": "BIT_academic_plan_coop.json",
            "no_coop": "BIT_academic_plan_no_coop.json",
        },
    },
    "DSBA": {
        "input": "DSBA_curriculum_book.pdf",
        "gt": {
            "coop": "DSBA_academic_plan_coop.json",
            "no_coop": "DSBA_academic_plan_no_coop.json",
        },
    },
    "IT": {
        "input": "IT_curriculum_book.pdf",
        "gt": {
            "coop": "IT_academic_plan_coop.json",
            "no_coop": "IT_academic_plan_no_coop.json",
        },
    },
}


def run_ocr_step(program: str, input_path: Path, program_out_dir: Path) -> Path:
    """ขั้นตอนที่ 1: อ่านข้อความจาก PDF แบบ hybrid (text layer ก่อน, OCR เป็นตัวสำรอง) ต่อหลักสูตร

    คืนค่า path ของไฟล์ OCR JSON หลัก (ชื่อ <PROGRAM>_ocr_result.json) ที่ใช้ในขั้นตอนถัดไป
    """
    print(f"[1/3] [{program}] Running hybrid text extraction on: {input_path.name}")

    if not input_path.exists():
        raise FileNotFoundError(f"Input document not found at: {input_path}")

    program_out_dir.mkdir(parents=True, exist_ok=True)
    extract_hybrid(str(input_path), str(program_out_dir))

    # extract_hybrid เขียนไฟล์ชื่อตายตัว ocr_result.json / ocr_result.txt ในโฟลเดอร์ที่ส่งไป
    # -> เติม prefix ชื่อหลักสูตรให้ทั้งสองไฟล์
    raw_json = program_out_dir / "ocr_result.json"
    raw_txt = program_out_dir / "ocr_result.txt"

    final_json = program_out_dir / f"{program}_ocr_result.json"
    final_txt = program_out_dir / f"{program}_ocr_result.txt"
    raw_json.rename(final_json)
    raw_txt.rename(final_txt)

    # สำเนาซ้ำในชื่อรูปแบบ "<PROGRAM>_curriculum_book_ocr.json/.txt" (ตาม stem ของไฟล์ input)
    # เผื่อ tooling อื่นที่ยังอ้างอิงชื่อไฟล์รูปแบบ "{stem}_ocr.json" (เช่น pipeline.run_ocr เดิม
    # และ fallback glob "*_ocr.json" ใน run_extraction_step ด้านล่าง)
    stem_json = program_out_dir / f"{input_path.stem}_ocr.json"
    stem_txt = program_out_dir / f"{input_path.stem}_ocr.txt"
    shutil.copyfile(final_json, stem_json)
    shutil.copyfile(final_txt, stem_txt)

    print(f"    -> OCR output written to: {program_out_dir}")
    return final_json


def run_extraction_step(program: str, ocr_json_path: Path, program_out_dir: Path, split_plans: bool) -> dict:
    """ขั้นตอนที่ 2: สกัดข้อมูลหลักสูตร

    - split_plans=False -> ผลลัพธ์รูปแบบเดิม {"program", "plan", "courses": [...]}
    - split_plans=True  -> ผลลัพธ์ {"program", "plans": {"coop": {...}, "no_coop": {...}}, "split_found": bool}
      (ดู curriculum_extraction.extract_curriculum_variants)
    """
    print(f"[2/3] [{program}] Extracting curriculum from OCR output...")

    if not ocr_json_path.exists():
        json_files = list(program_out_dir.glob("*_ocr.json"))
        if json_files:
            ocr_json_path = json_files[0]
        else:
            raise FileNotFoundError(f"Could not find any OCR JSON output in {program_out_dir}")

    if split_plans:
        result = extract_curriculum_variants(ocr_path=str(ocr_json_path), program=program, split_plans=True)
        if not result.get("split_found"):
            print(
                f"    [Warning] [{program}] ไม่พบ heading 'แผนการศึกษา' + 'สหกิจ' ที่ใช้แยกแผน "
                "coop/no_coop ในเนื้อหา OCR -> ใช้ข้อมูลชุดเดียวกันสำหรับทั้งสองแผนไปก่อน "
                "กรุณาตรวจสอบ OCR text และผลลัพธ์การประเมินด้วยความระมัดระวัง"
            )
    else:
        result = extract_curriculum_from_file(ocr_path=str(ocr_json_path), program=program)

    final_output_json = program_out_dir / f"{program}_extracted_result.json"
    with open(final_output_json, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=4)

    print(f"    -> Extracted curriculum written to: {final_output_json}")
    return result


def run_evaluation_step(program: str, gt_map: dict, extracted_json_path: Path) -> dict:
    """ขั้นตอนที่ 3: ประเมินผลเทียบกับ Ground Truth ของแต่ละแผน

    - gt_map มี key เดียวคือ "single" -> เขียนผลลัพธ์เป็น <PROGRAM>_evaluation_summary.json
    - gt_map มี key "coop"/"no_coop" -> เขียนผลลัพธ์แยกเป็น
      <PROGRAM>_coop_evaluation_summary.json และ <PROGRAM>_no_coop_evaluation_summary.json
    """
    all_results = {}

    for plan_key, gt_filename in gt_map.items():
        gt_path = GT_DIR / gt_filename
        print(f"[3/3] [{program}/{plan_key}] Evaluating extraction against: {gt_path.name}")

        if not gt_path.exists():
            print(f"    [Warning] Ground truth file not found at {gt_path}. Skipping evaluation for '{plan_key}'.")
            continue

        if plan_key == "single":
            out_name = f"{program}_evaluation_summary.json"
            plan_arg = None
        else:
            out_name = f"{program}_{plan_key}_evaluation_summary.json"
            plan_arg = plan_key

        output_path = OUTPUTS_DIR / out_name

        eval_result = evaluate_from_files(
            ground_truth_json=gt_path,
            prediction_json=extracted_json_path,
            output_json_path=str(output_path),
            plan_key=plan_arg,
        )

        print(f"\n================ EVALUATION RESULT [{program}/{plan_key}] ================")
        print(json.dumps(eval_result, ensure_ascii=False, indent=2))
        print("=========================================================================\n")

        all_results[plan_key] = eval_result

    return all_results


def process_program(program: str, cfg: dict, skip_ocr: bool, skip_extraction: bool) -> None:
    program_out_dir = OUTPUT_TESSERACT_DIR / program
    input_path = INPUT_DIR / cfg["input"]
    gt_map = cfg["gt"]
    split_plans = "single" not in gt_map  # มี coop/no_coop 2 แผนถ้าไม่ใช่ ground truth แผนเดียว

    ocr_json_path = program_out_dir / f"{program}_ocr_result.json"

    # 1. OCR Step
    if skip_ocr:
        print(f"[{program}] Skipping OCR step, using existing output in: {program_out_dir}")
    else:
        ocr_json_path = run_ocr_step(program, input_path, program_out_dir)

    # 2. Extraction Step
    extracted_json_path = program_out_dir / f"{program}_extracted_result.json"
    if skip_extraction:
        if not extracted_json_path.exists():
            raise FileNotFoundError(
                f"--skip-extraction was set but {extracted_json_path} does not exist."
            )
        print(f"[{program}] Skipping Extraction step, reusing: {extracted_json_path}")
    else:
        run_extraction_step(program, ocr_json_path, program_out_dir, split_plans)

    # 3. Evaluation Step
    run_evaluation_step(program, gt_map, extracted_json_path)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run OCR, Extract Curriculum, and Evaluate against Ground Truth for one or all programs."
    )
    parser.add_argument(
        "--program",
        type=str,
        choices=list(PROGRAMS.keys()) + ["all"],
        default="all",
        help="หลักสูตรที่ต้องการรัน (AIT/BIT/DSBA/IT) หรือ 'all' เพื่อรันทุกหลักสูตร (ค่าเริ่มต้น)",
    )
    parser.add_argument(
        "--skip-ocr",
        action="store_true",
        help="Skip OCR and use existing OCR output JSON",
    )
    parser.add_argument(
        "--skip-extraction",
        action="store_true",
        help="Skip extraction and use existing extracted_result.json",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    programs_to_run = list(PROGRAMS.keys()) if args.program == "all" else [args.program]

    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

    for program in programs_to_run:
        cfg = PROGRAMS[program]
        print(f"\n########## เริ่มประมวลผลหลักสูตร: {program} ##########")
        process_program(program, cfg, args.skip_ocr, args.skip_extraction)

    print("\nPipeline completed successfully.")


if __name__ == "__main__":
    main()