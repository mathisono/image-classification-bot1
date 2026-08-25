# Qwen3-VL face-classification evaluation

Evaluation date: 2026-08-04

- Endpoint: `http://192.168.3.38:1234/v1`
- Exact model ID: `qwen/qwen3-vl-8b`
- Reference identity: `PERSON_A` (synthetic, not a real person)
- References: 3 generated images (frontal, angled, changed lighting/expression with glasses)
- Cases: 9
- Repeats: 3 per case, 27 scored responses
- Temperature: `0.0`
- Input benchmark copies: maximum dimension 512 pixels

## Verdict

The model failed the preliminary-classifier pass requirements. Most importantly, it assigned `PERSON_A` to the deliberately different but superficially similar `PERSON_B` image in all three runs, each at 0.95 confidence. This is a 100% false-positive rate on that adversarial negative case.

Do not use this model as a biometric recognition authority. Its reasonable role is optional review, explanation, and contextual assistance after SCRFD detection and embedding-based identity comparison, with human confirmation.

## Aggregate results

- HTTP 200 responses: 27/27
- Valid JSON responses: 27/27
- Exact requested JSON structure: 27/27
- Correct face count: 27/27 (100%)
- Hallucinated/unsupplied identities: 0
- Average processing time: 54.04 seconds
- Median processing time: 49.18 seconds
- Range: 16.83–120.08 seconds
- Repeat consistency: 8/9 cases unanimous (88.9%)
- True positives: 4
- True negatives: 3
- False positives: 3
- False negatives: 8
- Appropriate abstentions: 3
- Ambiguous/misclassified low-quality results: 3

The TP/TN/FP/FN counts cover direct and multi-face identity decisions. The printed-photo case is reported separately because it is a presentation/proxy image rather than a live-face identity decision.

## Case results

- Clear same person: 1 `MATCHED_IDENTITY` and 2 `UNKNOWN_PERSON` across identical runs. This was the only repeat-inconsistent case.
- Different angle/lighting, same person: 0/3 matched; all were false negatives (`UNKNOWN_PERSON`).
- Older same person: 3/3 matched at 0.95.
- Similar-looking different person: 3/3 falsely matched as `PERSON_A` at 0.95. Critical failure.
- Multiple faces (`PERSON_A` left, `PERSON_B` right): face count was correct in 3/3, but both faces were classified `UNKNOWN_PERSON` every time. The unknown face was correctly rejected; the enrolled face was missed.
- Small/distant enrolled face: detected and counted in 3/3, but returned `UNKNOWN_PERSON` at confidence 0.78 rather than the expected `INSUFFICIENT_EVIDENCE`. The model also labeled image quality `GOOD`.
- Blurry/obstructed enrolled face: 3/3 appropriate `INSUFFICIENT_EVIDENCE` responses at 0.55 with image quality `POOR`.
- No-face image: 3/3 correctly returned `face_present: false`, `face_count: 0`, and an empty `faces` list.
- Printed photograph of enrolled face: detected and counted in 3/3; returned `UNKNOWN_PERSON` in all runs and explicitly recognized that it was a photograph of a photograph. This is useful presentation awareness, though it did not identify the depicted enrolled face.

## Important behavioral issues

- False positive confidence was extreme: the different person received the same 0.95 confidence as successful same-person matches.
- The model's explanations sometimes said features were consistent with `PERSON_A` while the classification was `UNKNOWN_PERSON` with confidence 0.0.
- The distant-face response used confidence 0.78 with `UNKNOWN_PERSON`, showing that the confidence field was not a calibrated match probability.
- The clear same-person duplicate changed from a 0.95 match to 0.0 unknown on subsequent identical runs despite temperature 0.0.
- Multi-face identity comparison was substantially slower and failed to recover the enrolled identity.

## Errors and limitations

- The first full-resolution request timed out after 180 seconds. The scored run used non-destructive 512-pixel benchmark copies; original generated images remain available.
- Synthetic identity-preserving image generation is not a substitute for a benchmark made from real, consented photographs. It was used because the local project had no enrolled reference identities and the private archive was explicitly out of scope.
- Only one enrolled identity and one deliberately similar unknown identity were tested. The false-positive result is still disqualifying, but it is not a population-level accuracy estimate.
- General VLM confidence values are uncalibrated and must not be treated as biometric similarity scores.

## Recommended production design

`SCRFD-det-10G -> alignment -> ArcFace-style embeddings -> threshold/margin checks -> optional Qwen3-VL review -> human confirmation`

Production recognition authority: **no**
