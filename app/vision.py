import base64
import json
import mimetypes
from pathlib import Path
from typing import Any

import requests
from pydantic import BaseModel, ConfigDict, Field, field_validator


VISION_PROMPT = """Look at the image and answer simple questions.

Use only what you can see. Do not guess names, dates, places, brands, identities, or relationships.

Questions:
1. What is the image mainly showing?
2. What kind of image is it: photo, screenshot, document, artwork, diagram, logo, or unknown?
3. Is it portrait, landscape, square, or unknown?
4. What broad category would help someone find it later?
5. What visible objects or equipment are present?
6. What readable text is visible, if any?
7. Are any people or faces visible? If yes, how many?
8. Is the image clear enough to describe confidently?

Return JSON only with concise answers."""

REQUIRED_DB_FIELDS = [
    "short_caption",
    "detailed_description",
    "scene_type",
    "orientation",
    "image_type",
    "category",
    "tags",
]


class ImageClassificationRecord(BaseModel):
    """Validated image metadata written to the searchable image index."""

    model_config = ConfigDict(extra="ignore")

    short_caption: str = Field(default="unknown", description="One short, factual caption for the image. Use unknown if unclear.")
    detailed_description: str = Field(default="unknown", description="A factual searchable description. Do not invent names, locations, dates, or identities.")
    scene_type: str = Field(default="unknown", description="Portrait, landscape, document, screenshot, object, people, group photo, equipment, or unknown.")
    orientation: str = Field(default="unknown", description="Portrait, landscape, square, or unknown.")
    image_type: str = Field(default="unknown", description="Photo, screenshot, document, logo, flyer, diagram, artwork, unknown, etc.")
    category: str = Field(default="unreviewed", description="A broad archive category useful for browsing.")
    tags: list[str] = Field(default_factory=list, description="Short searchable tags. Prefer lowercase simple phrases.")
    objects: list[str] = Field(default_factory=list, description="Visible objects, gear, equipment, landmarks, or document types.")
    visible_text: str = Field(default="", description="Any clearly readable text in the image. Use an empty string if none is readable.")
    people_detected: bool = Field(default=False, description="True if any person is visible.")
    people_count: int = Field(default=0, ge=0, description="Count of visible people.")
    face_detected: bool = Field(default=False, description="True if any human face is visible.")
    face_count: int = Field(default=0, ge=0, description="Count of visible human faces.")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="Confidence in the classification from 0.0 to 1.0.")
    needs_reprocess: bool = Field(default=False, description="True if the image should be retried with a better prompt/model/pass.")
    retry_focus: str = Field(default="", description="Specific focus for the next retry, such as OCR, equipment ID, blurry image, document text, faces/people, logos, or model JSON formatting.")
    quality_issue: str = Field(default="", description="Short explanation of what is missing or weak in this classification.")

    @field_validator("short_caption", "detailed_description", "scene_type", "orientation", "image_type", "category", "visible_text", "retry_focus", "quality_issue", mode="before")
    @classmethod
    def _stringify_text_fields(cls, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, list):
            return ", ".join(str(item).strip() for item in value if str(item).strip())
        return str(value).strip()

    @field_validator("tags", "objects", mode="before")
    @classmethod
    def _normalize_list_fields(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            parts = [p.strip() for p in value.replace(";", ",").split(",")]
            return [p for p in parts if p]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return [str(value).strip()] if str(value).strip() else []

    @field_validator("people_detected", "face_detected", mode="before")
    @classmethod
    def _coerce_bool(cls, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y", "on"}
        return bool(value)

    @field_validator("people_count", "face_count", mode="before")
    @classmethod
    def _coerce_int(cls, value: Any) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_confidence(cls, value: Any) -> float:
        if isinstance(value, str):
            label = value.strip().lower()
            labels = {"high": 0.9, "medium": 0.6, "moderate": 0.6, "low": 0.3}
            if label in labels:
                return labels[label]
            if label.endswith("%"):
                try:
                    return float(label[:-1]) / 100
                except ValueError:
                    return 0.0
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def to_db_dict(self) -> dict[str, str | int | float]:
        return {
            "short_caption": self.short_caption or "unknown",
            "detailed_description": self.detailed_description or "unknown",
            "scene_type": self.scene_type or "unknown",
            "orientation": self.orientation or "unknown",
            "image_type": self.image_type or "unknown",
            "category": self.category or "unreviewed",
            "tags": ", ".join(self.tags),
            "objects": ", ".join(self.objects),
            "visible_text": self.visible_text or "",
            "people_detected": 1 if self.people_detected else 0,
            "people_count": max(0, int(self.people_count or 0)),
            "face_detected": 1 if self.face_detected else 0,
            "face_count": max(0, int(self.face_count or 0)),
            "confidence": float(self.confidence),
            "needs_reprocess": 1 if self.needs_reprocess else 0,
            "retry_focus": self.retry_focus or "",
            "quality_issue": self.quality_issue or "",
        }


def _vision_disabled_result() -> dict[str, str | int | float]:
    return {
        "short_caption": "Vision disabled; thumbnail and metadata indexed only.",
        "detailed_description": "Enable vision.enabled in config.yaml and point base_url/model at a local vision model.",
        "scene_type": "unknown",
        "orientation": "unknown",
        "image_type": "unknown",
        "category": "unreviewed",
        "tags": "",
        "objects": "",
        "visible_text": "",
        "people_detected": 0,
        "people_count": 0,
        "face_detected": 0,
        "face_count": 0,
        "confidence": 0.0,
        "needs_reprocess": 0,
        "retry_focus": "Enable a local vision model and reprocess.",
        "quality_issue": "Vision classification is disabled.",
    }


def _strip_code_fence(content: str) -> str:
    content = content.strip()
    if content.startswith("```"):
        content = content.strip("`")
        if content.lower().startswith("json"):
            content = content[4:].strip()
    return content


def _parse_json_object(content: str) -> dict[str, Any]:
    """Parse a JSON object even when a local model wraps it in prose or fences."""
    cleaned = _strip_code_fence(content)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError as original_error:
        decoder = json.JSONDecoder()
        for index, char in enumerate(cleaned):
            if char != "{":
                continue
            try:
                value, _ = decoder.raw_decode(cleaned[index:])
                break
            except json.JSONDecodeError:
                continue
        else:
            raise original_error
    if not isinstance(value, dict):
        raise ValueError("vision model response must contain a JSON object")
    return value


def review_classification_quality(result: dict[str, Any]) -> dict[str, str | int | float]:
    """Detect weak/missing classification records and set retry guidance."""
    cleaned = ImageClassificationRecord.model_validate(result).to_db_dict()

    missing: list[str] = []
    for field_name in REQUIRED_DB_FIELDS:
        value = str(cleaned.get(field_name, "") or "").strip().lower()
        if not value or value in {"unknown", "none", "n/a", "null", "unreviewed"}:
            missing.append(field_name)

    retry_reasons: list[str] = []
    if missing:
        retry_reasons.append("missing fields: " + ", ".join(missing))

    caption = str(cleaned.get("short_caption", "") or "").strip()
    description = str(cleaned.get("detailed_description", "") or "").strip()
    tags = str(cleaned.get("tags", "") or "").strip()
    objects = str(cleaned.get("objects", "") or "").strip()
    confidence = float(cleaned.get("confidence", 0.0) or 0.0)

    if len(caption) < 8:
        retry_reasons.append("caption too short")
    if len(description) < 30:
        retry_reasons.append("description too thin")
    if not tags:
        retry_reasons.append("no searchable tags")
    if confidence and confidence < 0.45:
        retry_reasons.append(f"low confidence: {confidence:.2f}")

    for field_name in ("scene_type", "orientation"):
        value = str(cleaned.get(field_name, "") or "").strip().lower()
        if not value or value in {"unknown", "none", "n/a", "null"}:
            retry_reasons.append(f"missing {field_name}")

    if retry_reasons:
        existing_focus = str(cleaned.get("retry_focus", "") or "").strip()
        if not existing_focus:
            focus_bits: list[str] = []
            if "scene_type" in missing:
                focus_bits.append("identify scene type")
            if "orientation" in missing:
                focus_bits.append("classify orientation")
            if "tags" in missing or not tags:
                focus_bits.append("generate searchable tags")
            if len(description) < 30:
                focus_bits.append("write a fuller factual description")
            existing_focus = "; ".join(focus_bits) or "retry with a stronger vision model/prompt"

        cleaned["needs_reprocess"] = 1
        cleaned["quality_issue"] = "; ".join(retry_reasons)
        cleaned["retry_focus"] = existing_focus

    return cleaned


def _classify_with_pydantic_ai(analysis_path: str, cfg: dict) -> dict[str, str | int | float]:
    try:
        from pydantic_ai import Agent, BinaryContent, PromptedOutput
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider
    except ImportError as exc:
        raise RuntimeError("pydantic-ai is not installed. Run: pip install -r requirements.txt") from exc

    image_path = Path(analysis_path)
    mime = mimetypes.guess_type(str(image_path))[0] or "image/jpeg"
    base_url = cfg.get("base_url", "http://127.0.0.1:1234/v1").rstrip("/")

    model = OpenAIChatModel(
        cfg.get("model"),
        provider=OpenAIProvider(base_url=base_url, api_key=cfg.get("api_key", "not-needed")),
    )
    agent = Agent(
        model,
        output_type=PromptedOutput(
            ImageClassificationRecord,
            name="ImageClassificationRecord",
            description="Validated image metadata plus retry guidance for the local image librarian database.",
        ),
        instructions=VISION_PROMPT,
    )
    result = agent.run_sync(
        [
            "Return one validated archive record for this image. Keep values concise and factual.",
            BinaryContent(data=image_path.read_bytes(), media_type=mime),
        ]
    )
    record = result.output
    if not isinstance(record, ImageClassificationRecord):
        record = ImageClassificationRecord.model_validate(record)
    return review_classification_quality(record.model_dump())


def _classify_with_legacy_json_request(analysis_path: str, cfg: dict) -> dict[str, str | int | float]:
    with open(analysis_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")

    mime = mimetypes.guess_type(analysis_path)[0] or "image/jpeg"
    payload = {
        "model": cfg.get("model"),
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            VISION_PROMPT + "\n\nReturn JSON only with these fields: "
                            "short_caption, detailed_description, scene_type, orientation, image_type, category, tags, objects, visible_text, people_detected, people_count, face_detected, face_count, confidence, needs_reprocess, retry_focus, quality_issue."
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ],
            }
        ],
        "temperature": 0.0,
        "max_tokens": int(cfg.get("max_tokens", 2000)),
        "reasoning_effort": "none",
        "thinking": {"type": "disabled"},
    }
    headers = {"Authorization": f"Bearer {cfg.get('api_key', 'not-needed')}"}
    r = requests.post(
        cfg.get("base_url").rstrip("/") + "/chat/completions",
        json=payload,
        headers=headers,
        timeout=int(cfg.get("timeout_seconds", 180)),
    )
    r.raise_for_status()
    response = r.json()
    choice = response["choices"][0]
    content = str(choice["message"].get("content") or "").strip()
    if not content:
        raise ValueError(f"vision model returned empty content (finish_reason={choice.get('finish_reason', 'unknown')})")
    data = _parse_json_object(content)
    return review_classification_quality(data)


def classify_with_local_model(analysis_path: str, cfg: dict) -> dict[str, str | int | float]:
    if not cfg.get("enabled"):
        return _vision_disabled_result()

    # Legacy JSON is the safest default for OpenAI-compatible local model servers.
    structured_output = cfg.get("structured_output", "legacy_json")
    fallback_to_legacy = bool(cfg.get("fallback_to_legacy_json", True))

    if structured_output == "legacy_json":
        return _classify_with_legacy_json_request(analysis_path, cfg)

    try:
        return _classify_with_pydantic_ai(analysis_path, cfg)
    except Exception as pydantic_ai_error:
        if not fallback_to_legacy:
            raise
        try:
            result = _classify_with_legacy_json_request(analysis_path, cfg)
            result["quality_issue"] = (str(result.get("quality_issue", "") or "") + f"; pydantic-ai fallback used: {pydantic_ai_error}").strip("; ")
            return result
        except Exception as legacy_error:
            failure = ImageClassificationRecord(
                short_caption="Classification failed",
                detailed_description="The model call or structured output validation failed.",
                image_type="unknown",
                category="failed",
                tags=["classification failed"],
                objects=[],
                confidence=0.0,
                needs_reprocess=True,
                retry_focus="retry model call; verify local model supports image input and JSON/structured output",
                quality_issue=f"Pydantic AI error: {pydantic_ai_error}; legacy fallback error: {legacy_error}",
            ).to_db_dict()
            return failure
