import base64
import json
import mimetypes
import time
from datetime import datetime, timezone
from pathlib import Path

import requests


ENDPOINT = "http://192.168.3.38:1234/v1"
PREFERRED_MODEL = "qwen/qwen3-vl-8b"
ROOT = Path(__file__).resolve().parent
IMAGES = ROOT / "images_512"
RESULTS = ROOT / "results"
RUNS_PER_CASE = 3

PROMPT = r'''You are evaluating a face-classification system using only the labeled reference photographs supplied in this request.

Your task is to inspect the test image, detect visible human faces, and compare each face against the supplied reference groups.

Do not use outside knowledge. Do not recognize celebrities. Do not infer names from appearance. A name may be returned only when the test face is sufficiently consistent with the labeled reference photographs.

Comparison guidance:

- Compare overall facial geometry and stable visible features.
- Account cautiously for differences in pose, expression, lighting, image quality, glasses, facial hair, and age.
- A superficial resemblance is not enough.
- When image quality is poor or important facial details are unavailable, return INSUFFICIENT_EVIDENCE.
- When the face is usable but does not match any enrolled reference group, return UNKNOWN_PERSON.
- For an image containing no usable face, return NO_FACE.
- Classify every visible face independently.
- Do not infer any sensitive personal traits.

Return valid JSON only. Do not include Markdown or commentary outside the JSON.

Return exactly this structure:

{
 "face_detection": {
 "face_present": true,
 "face_count": 1,
 "image_quality": "GOOD",
 "quality_issues": []
 },
 "faces": [
 {
 "face_index": 0,
 "location": "center, left, right, upper-left, or another brief location",
 "classification": "MATCHED_IDENTITY, UNKNOWN_PERSON, INSUFFICIENT_EVIDENCE, or NO_FACE",
 "matched_identity": "Exact supplied label or empty string",
 "confidence": 0.0,
 "match_margin": "CLEAR, NARROW, or NOT_APPLICABLE",
 "supporting_observations": [
 "Brief visible comparison points"
 ],
 "conflicting_observations": [
 "Visible differences or reasons for uncertainty"
 ],
 "needs_specialized_face_model": true
 }
 ],
 "overall_result": "PASS, REVIEW_REQUIRED, or UNUSABLE",
 "model_limitations": [
 "Important limitations affecting this result"
 ]
}

Use these confidence guidelines:

- 0.90 to 1.00: exceptionally clear agreement across several good reference images.
- 0.75 to 0.89: strong apparent match, but still requires biometric-model confirmation.
- 0.55 to 0.74: possible resemblance; classify as INSUFFICIENT_EVIDENCE.
- Below 0.55: UNKNOWN_PERSON or INSUFFICIENT_EVIDENCE.

For this evaluation, do not return MATCHED_IDENTITY below 0.75.

Set needs_specialized_face_model to true for every face because this general vision-language model is not the final biometric authority.

Analyze the supplied test image now and return JSON only.'''

REFERENCES = [
    IMAGES / "person_a_ref_frontal.jpg",
    IMAGES / "person_a_ref_angle.jpg",
    IMAGES / "person_a_ref_lighting_glasses.jpg",
]

CASES = [
    {"id": "clear_same", "path": "test_clear_person_a.jpg", "expected_count": 1, "expected": ["PERSON_A"]},
    {"id": "angle_lighting_same", "path": "test_angle_person_a.jpg", "expected_count": 1, "expected": ["PERSON_A"]},
    {"id": "older_same", "path": "test_older_person_a.jpg", "expected_count": 1, "expected": ["PERSON_A"]},
    {"id": "similar_different", "path": "test_similar_person_b.jpg", "expected_count": 1, "expected": ["UNKNOWN_PERSON"]},
    {"id": "multiple_faces", "path": "test_multiple_a_and_b.jpg", "expected_count": 2, "expected": ["PERSON_A", "UNKNOWN_PERSON"]},
    {"id": "small_distant", "path": "test_small_distant_person_a.jpg", "expected_count": 1, "expected": ["INSUFFICIENT_EVIDENCE"]},
    {"id": "blurry_obstructed", "path": "test_blurry_obstructed_person_a.jpg", "expected_count": 1, "expected": ["INSUFFICIENT_EVIDENCE"]},
    {"id": "no_face", "path": "test_no_face.jpg", "expected_count": 0, "expected": ["NO_FACE"]},
    {"id": "printed_photo", "path": "test_printed_photo_person_a.jpg", "expected_count": 1, "expected": ["PERSON_A"]},
]


def image_part(path: Path, label: str) -> list[dict]:
    mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return [
        {"type": "text", "text": label},
        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
    ]


def structurally_valid(value) -> bool:
    if not isinstance(value, dict) or set(value) != {"face_detection", "faces", "overall_result", "model_limitations"}:
        return False
    detection = value.get("face_detection")
    if not isinstance(detection, dict) or set(detection) != {"face_present", "face_count", "image_quality", "quality_issues"}:
        return False
    if not isinstance(value.get("faces"), list):
        return False
    required = {"face_index", "location", "classification", "matched_identity", "confidence", "match_margin", "supporting_observations", "conflicting_observations", "needs_specialized_face_model"}
    return all(isinstance(face, dict) and set(face) == required for face in value["faces"])


def main():
    for path in REFERENCES + [IMAGES / case["path"] for case in CASES]:
        if not path.is_file():
            raise SystemExit(f"Missing image: {path}")

    models_response = requests.get(ENDPOINT + "/models", timeout=10)
    models_response.raise_for_status()
    model_ids = [item.get("id") for item in models_response.json().get("data", []) if isinstance(item, dict) and item.get("id")]
    if PREFERRED_MODEL not in model_ids:
        raise SystemExit(f"Exact preferred model is unavailable. Available models: {model_ids}")
    model_id = PREFERRED_MODEL

    RESULTS.mkdir(exist_ok=True)
    records = []
    for case in CASES:
        test_path = IMAGES / case["path"]
        for run in range(1, RUNS_PER_CASE + 1):
            content = [{"type": "text", "text": PROMPT}]
            for index, path in enumerate(REFERENCES, start=1):
                content.extend(image_part(path, f"Confirmed reference image {index} for enrolled identity label PERSON_A:"))
            content.extend(image_part(test_path, "TEST IMAGE TO CLASSIFY:"))
            payload = {
                "model": model_id,
                "messages": [{"role": "user", "content": content}],
                "temperature": 0.0,
                "max_tokens": 1200,
            }
            started = time.perf_counter()
            record = {"case": case["id"], "run": run, "test_image": str(test_path), "expected_count": case["expected_count"], "expected": case["expected"]}
            try:
                response = requests.post(ENDPOINT + "/chat/completions", headers={"Authorization": "Bearer not-needed"}, json=payload, timeout=180)
                record["processing_time_seconds"] = round(time.perf_counter() - started, 3)
                record["http_status"] = response.status_code
                response.raise_for_status()
                body = response.json()
                raw = body["choices"][0]["message"]["content"]
                record["raw_content"] = raw
                try:
                    parsed = json.loads(raw)
                    record["json_valid"] = True
                    record["structure_valid"] = structurally_valid(parsed)
                    record["result"] = parsed
                except json.JSONDecodeError as exc:
                    record["json_valid"] = False
                    record["structure_valid"] = False
                    record["error"] = f"JSON decode: {exc}"
            except Exception as exc:
                record["processing_time_seconds"] = round(time.perf_counter() - started, 3)
                record["json_valid"] = False
                record["structure_valid"] = False
                record["error"] = f"{type(exc).__name__}: {exc}"
            records.append(record)
            (RESULTS / f"{case['id']}-run-{run}.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
            print(json.dumps({"case": case["id"], "run": run, "seconds": record["processing_time_seconds"], "http": record.get("http_status"), "json": record["json_valid"], "error": record.get("error")}), flush=True)

    bundle = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "endpoint": ENDPOINT,
        "model": model_id,
        "reference_identity": "PERSON_A",
        "reference_images": [str(path) for path in REFERENCES],
        "runs_per_case": RUNS_PER_CASE,
        "records": records,
    }
    (ROOT / "evaluation_results.json").write_text(json.dumps(bundle, indent=2), encoding="utf-8")
    print(f"Saved {ROOT / 'evaluation_results.json'}", flush=True)


if __name__ == "__main__":
    main()
