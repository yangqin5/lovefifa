import json
import os
from collections import defaultdict

# ==========================================
# 1. DISTANCE & ERROR METRIC FUNCTIONS
# ==========================================

def levenshtein_distance(s1, s2):
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    return previous_row[-1]

def calculate_exact_match(ref, hyp):
    return 1.0 if str(ref if ref is not None else "").strip() == str(hyp if hyp is not None else "").strip() else 0.0

def calculate_cer(ref, hyp):
    ref_str = str(ref).strip() if ref is not None else ""
    hyp_str = str(hyp).strip() if hyp is not None else ""
    if len(ref_str) == 0:
        return 0.0 if len(hyp_str) == 0 else 1.0
    return levenshtein_distance(ref_str, hyp_str) / len(ref_str)

def calculate_wer(ref, hyp):
    ref_words = str(ref).strip().split() if ref is not None else []
    hyp_words = str(hyp).strip().split() if hyp is not None else []
    if len(ref_words) == 0:
        return 0.0 if len(hyp_words) == 0 else 1.0
    return levenshtein_distance(ref_words, hyp_words) / len(ref_words)

def format_percent(val):
    return f"{val * 100:.2f}%"

def format_exact_match(val):
    return {
        "percentage": f"{val * 100:.2f}%",
        "is_perfect_match": val == 1.0
    }

# ==========================================
# 2. MATCH-BY-CODE EVALUATION
# ==========================================

def evaluate_from_files(ground_truth_json, prediction_json, output_json_path=None):
    with open(ground_truth_json, "r", encoding="utf-8") as f:
        gt_data = json.load(f)["courses"]
    
    with open(prediction_json, "r", encoding="utf-8") as f:
        pred_data = json.load(f)["courses"]

    # กรองแถวหมายเหตุที่ไม่ใช่รายวิชาออก
    gt_data = [c for c in gt_data if c.get("code") and not str(c["code"]).startswith("หมายเหตุ")]
    pred_data = [c for c in pred_data if c.get("code") and not str(c["code"]).startswith("หมายเหตุ")]

    # สร้าง Candidate Pool จาก Prediction เพื่อจับคู่โดย Code
    pred_pool = defaultdict(list)
    for p in pred_data:
        pred_pool[p.get("code")].append(p)

    target_fields = ["code", "name_th", "name_en", "credits", "year", "semester", "category", "type", "prerequisite", "note"]
    
    metrics = {
        "field_level": defaultdict(lambda: {"em": [], "cer": [], "wer": []}),
        "page_level": defaultdict(lambda: {"em": [], "cer": [], "wer": []}),
        "overall_average": {"em": [], "cer": [], "wer": []}
    }

    matched_gt_count = 0

    for gt_course in gt_data:
        gt_code = gt_course.get("code")
        page_key = f"Year {gt_course.get('year')} / Sem {gt_course.get('semester')}"
        
        matched_pred = None
        candidates = pred_pool.get(gt_code, [])

        if candidates:
            # กรณีเจอ Code ตรงกัน
            if len(candidates) == 1:
                matched_pred = candidates.pop(0)
            else:
                # กรณีมีรหัสซ้ำ (เช่น รหัสวิชาเลือก xxx) ให้หา Candidate ที่ชื่อไทยใกล้เคียงที่สุด
                best_idx = 0
                best_cer = 999.0
                gt_name = str(gt_course.get("name_th", ""))
                for idx, cand in enumerate(candidates):
                    cand_name = str(cand.get("name_th", ""))
                    cer_val = calculate_cer(gt_name, cand_name)
                    if cer_val < best_cer:
                        best_cer = cer_val
                        best_idx = idx
                matched_pred = candidates.pop(best_idx)

        if matched_pred:
            matched_gt_count += 1
            for field in target_fields:
                gt_val = gt_course.get(field)
                pred_val = matched_pred.get(field)

                em = calculate_exact_match(gt_val, pred_val)
                cer = calculate_cer(gt_val, pred_val)
                wer = calculate_wer(gt_val, pred_val)

                metrics["field_level"][field]["em"].append(em)
                metrics["field_level"][field]["cer"].append(cer)
                metrics["field_level"][field]["wer"].append(wer)

                metrics["page_level"][page_key]["em"].append(em)
                metrics["page_level"][page_key]["cer"].append(cer)
                metrics["page_level"][page_key]["wer"].append(wer)

                metrics["overall_average"]["em"].append(em)
                metrics["overall_average"]["cer"].append(cer)
                metrics["overall_average"]["wer"].append(wer)
        else:
            # กรณีหาไม่พบใน Prediction (Missed Record / False Negative)
            for field in target_fields:
                metrics["field_level"][field]["em"].append(0.0)
                metrics["field_level"][field]["cer"].append(1.0)
                metrics["field_level"][field]["wer"].append(1.0)

                metrics["page_level"][page_key]["em"].append(0.0)
                metrics["page_level"][page_key]["cer"].append(1.0)
                metrics["page_level"][page_key]["wer"].append(1.0)

                metrics["overall_average"]["em"].append(0.0)
                metrics["overall_average"]["cer"].append(1.0)
                metrics["overall_average"]["wer"].append(1.0)

    # คำนวณ Record-level Precision, Recall, F1
    total_gt = len(gt_data)
    total_pred = len(pred_data)
    recall = matched_gt_count / total_gt if total_gt > 0 else 0.0
    precision = matched_gt_count / total_pred if total_pred > 0 else 0.0
    f1_score = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

    def summarize(metric_dict):
        result = {}
        for k, v in metric_dict.items():
            em_avg = sum(v["em"]) / len(v["em"]) if len(v["em"]) > 0 else 0
            cer_avg = sum(v["cer"]) / len(v["cer"]) if len(v["cer"]) > 0 else 0
            wer_avg = sum(v["wer"]) / len(v["wer"]) if len(v["wer"]) > 0 else 0
            
            result[k] = {
                "Exact_Match": format_exact_match(em_avg),
                "CER": format_percent(cer_avg),
                "WER": format_percent(wer_avg),
            }
        return result

    avg_em = sum(metrics["overall_average"]["em"]) / len(metrics["overall_average"]["em"]) if metrics["overall_average"]["em"] else 0
    avg_cer = sum(metrics["overall_average"]["cer"]) / len(metrics["overall_average"]["cer"]) if metrics["overall_average"]["cer"] else 0
    avg_wer = sum(metrics["overall_average"]["wer"]) / len(metrics["overall_average"]["wer"]) if metrics["overall_average"]["wer"] else 0

    final_evaluation_result = {
        "record_level_matching": {
            "total_ground_truths": total_gt,
            "total_predictions": total_pred,
            "matched_records": matched_gt_count,
            "precision": format_percent(precision),
            "recall": format_percent(recall),
            "f1_score": format_percent(f1_score)
        },
        "Overall_Average": {
            "Exact_Match": format_exact_match(avg_em),
            "CER": format_percent(avg_cer),
            "WER": format_percent(avg_wer)
        },
        "Field_Level": summarize(metrics["field_level"]),
        "Page_Level": summarize(metrics["page_level"])
    }

    if output_json_path:
        output_dir = os.path.dirname(output_json_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        with open(output_json_path, "w", encoding="utf-8") as f:
            json.dump(final_evaluation_result, f, indent=4, ensure_ascii=False)
            
    return final_evaluation_result