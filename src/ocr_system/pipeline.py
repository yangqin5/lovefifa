import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import cv2

from .config import OCRConfig
from .curriculum_extraction import extract_curriculum_from_file
from .document_loader import load_document_pages
from .engine_factory import build_engine
from .evaluation import evaluate_from_files
from .preprocessing import preprocess_image, read_image, save_debug_image
from .schemas import Course, CurriculumResult, OCRDocumentResult, OCRLine, OCRPageResult
from .utils.io import ensure_dir, save_json, save_text


def _save_checkpoint(checkpoint_path: Path, page_results: list[OCRPageResult]) -> None:
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    data = {"pages": [asdict(p) for p in page_results]}
    tmp_path = checkpoint_path.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp_path.replace(checkpoint_path)  # atomic write กันไฟล์เสียถ้าแครชระหว่างเขียน


def _page_result_from_dict(d: dict) -> OCRPageResult:
    lines = [OCRLine(**line) for line in d["lines"]]
    return OCRPageResult(page=d["page"], text=d["text"], lines=lines, image_path=d["image_path"])


def run_ocr(config: OCRConfig) -> OCRDocumentResult:
    output_dir = ensure_dir(config.output_dir)
    page_dir = ensure_dir(config.page_image_dir)
    pages = load_document_pages(config.input_path, page_dir, dpi=config.dpi)
    engine = build_engine(config)

    stem = Path(config.input_path).stem
    checkpoint_path = output_dir / f"{stem}_checkpoint.json"

    page_results: list[OCRPageResult] = []
    completed_pages: set[int] = set()

    if config.checkpoint_enabled and config.resume and checkpoint_path.exists():
        print(f"[checkpoint] พบไฟล์ checkpoint เดิม: {checkpoint_path}")
        with open(checkpoint_path, "r", encoding="utf-8") as f:
            checkpoint_data = json.load(f)
        page_results = [_page_result_from_dict(d) for d in checkpoint_data.get("pages", [])]
        completed_pages = {p.page for p in page_results}
        print(f"[checkpoint] Resume: ทำไปแล้ว {len(completed_pages)}/{len(pages)} หน้า")

    total_pages = len(pages)
    for page_no, image_path in enumerate(pages, start=1):
        if page_no in completed_pages:
            continue

        print(f"  หน้า {page_no}/{total_pages} ...")
        image = read_image(image_path)
        if config.preprocess:
            image_for_ocr = preprocess_image(image, deskew=config.deskew)
            if config.save_debug_images:
                debug_path = output_dir / "debug" / f"page_{page_no:03d}_preprocessed.png"
                save_debug_image(image_for_ocr, debug_path)
        else:
            image_for_ocr = image

        try:
            lines = engine.recognize(image_for_ocr, page=page_no)
        except Exception as e:
            print(f"  [ERROR] หน้า {page_no} ล้มเหลว: {e}")
            if config.checkpoint_enabled:
                _save_checkpoint(checkpoint_path, page_results)
                print(f"  [checkpoint] เซฟความคืบหน้าไว้แล้ว รันคำสั่งเดิมซ้ำเพื่อ resume ต่อจากหน้า {page_no}")
            raise

        if config.min_confidence > 0:
            lines = [x for x in lines if x.confidence is None or x.confidence >= config.min_confidence]

        text = "\n".join(line.text for line in lines if line.text.strip())
        page_results.append(OCRPageResult(page=page_no, text=text, lines=lines, image_path=str(image_path)))

        if config.checkpoint_enabled and (page_no % config.checkpoint_interval == 0):
            _save_checkpoint(checkpoint_path, page_results)

    if config.checkpoint_enabled:
        _save_checkpoint(checkpoint_path, page_results)  # เซฟรอบสุดท้ายกันตกหล่น

    page_results.sort(key=lambda p: p.page)

    full_text = "\n\n".join(f"--- Page {p.page} ---\n{p.text}" for p in page_results)
    result = OCRDocumentResult(
        source_path=str(config.input_path),
        engine=engine.name,
        text=full_text,
        pages=page_results,
    )

    save_json(result.to_dict(), output_dir / f"{stem}_ocr.json")
    save_text(result.text, output_dir / f"{stem}_ocr.txt")

    if config.checkpoint_enabled and checkpoint_path.exists():
        checkpoint_path.unlink()
        print(f"[checkpoint] เสร็จสมบูรณ์ ลบไฟล์ checkpoint แล้ว")

    return result


def run_extraction(ocr_json_path: str | Path, output_dir: str | Path) -> tuple[dict, Path]:
    """สกัดข้อมูลรายวิชาจากไฟล์ OCR JSON แล้ว Validate ผ่าน Schemas"""
    ocr_json_path = Path(ocr_json_path)
    output_dir = ensure_dir(output_dir)
    stem = ocr_json_path.stem.replace("_ocr", "")

    print(f"[extraction] กำลังสกัดข้อมูลรายวิชาจาก: {ocr_json_path.name}")
    raw_extraction = extract_curriculum_from_file(str(ocr_json_path))

    # Validate และแปลงผลลัพธ์ผ่าน Dataclass Schemas
    courses = [Course(**c) for c in raw_extraction.get("courses", [])]
    curriculum = CurriculumResult(
        source=raw_extraction.get("source", "OCR curriculum extraction"),
        description=raw_extraction.get("description", "Extracted academic plan"),
        program=raw_extraction.get("program", "DSBA"),
        plan=raw_extraction.get("plan", "no_coop"),
        courses=courses,
    )

    pred_dict = curriculum.to_dict()
    pred_path = output_dir / f"{stem}_prediction.json"
    save_json(pred_dict, pred_path)

    print(f"[extraction] บันทึกผลลัพธ์ Extraction เรียบร้อย: {pred_path.name}")
    return pred_dict, pred_path


def run_evaluation(
    ground_truth_path: str | Path, prediction_path: str | Path, output_dir: str | Path
) -> tuple[dict, Path]:
    """ประเมินผล Accuracy โดยจับคู่ด้วย Code Key Pool และคำนวณ Metrics"""
    output_dir = ensure_dir(output_dir)
    stem = Path(prediction_path).stem.replace("_prediction", "")

    eval_out_path = output_dir / f"{stem}_evaluation.json"
    print(f"[evaluation] กำลังประเมินผลเทียบกับ Ground Truth: {Path(ground_truth_path).name}")

    eval_result = evaluate_from_files(
        ground_truth_json=str(ground_truth_path),
        prediction_json=str(prediction_path),
        output_json_path=str(eval_out_path),
    )

    print(f"[evaluation] บันทึกรายงานผลการประเมินเรียบร้อย: {eval_out_path.name}")
    return eval_result, eval_out_path


def run_pipeline(
    config: OCRConfig, ground_truth_path: str | Path | None = None
) -> dict[str, Any]:
    """รันกระบวนการแบบ End-to-End: OCR -> Extraction -> Evaluation (Optional)"""
    output_dir = ensure_dir(config.output_dir)
    stem = Path(config.input_path).stem

    # Step 1: OCR
    ocr_result = run_ocr(config)
    ocr_json_path = output_dir / f"{stem}_ocr.json"

    # Step 2: Extraction
    pred_dict, pred_path = run_extraction(ocr_json_path, output_dir)

    pipeline_result = {
        "ocr_result": ocr_result,
        "prediction_path": str(pred_path),
        "prediction": pred_dict,
        "evaluation_path": None,
        "evaluation": None,
    }

    # Step 3: Evaluation (ถ้าระบุ Ground Truth Path)
    if ground_truth_path:
        eval_dict, eval_path = run_evaluation(
            ground_truth_path=ground_truth_path,
            prediction_path=pred_path,
            output_dir=output_dir,
        )
        pipeline_result["evaluation_path"] = str(eval_path)
        pipeline_result["evaluation"] = eval_dict

    return pipeline_result