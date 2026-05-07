import base64
import json
import mimetypes
from pathlib import Path
from typing import Any

import requests
from pydantic import BaseModel, ConfigDict, Field, field_validator


VISION_PROMPT = """You are cataloging a private local image archive.
Look at this image and produce a searchable database record.
Do not invent details. If uncertain, say unknown.
Pay special attention to stage/theater production, audio/video equipment, projectors, lighting gear, cameras, radio equipment, antennas, network gear, documents, screenshots, logos, flyers, Berkeley/UC Berkeley/Campanile imagery, union or IATSE-related graphics.

Also monitor the classification quality. If the image is blurry, unreadable, ambiguous, mostly text, mostly equipment, or missing useful searchable details, mark needs_reprocess true and explain what a retry should focus on."""

REQUIRED_DB_FIELDS = [
    "short_caption",
    "detailed_description",
    "image_type",
    "category",
    "tags",
    "objects",
    "visible_text",
]


class ImageClassificationRecord(BaseModel):
    """Validated image metadata written to the searchable image index."""

    model_config = ConfigDict(extra="ignore")

    short_caption: str = Field(default="unknown", description="One short, factual caption for the image. Use unknown if unclear.")
    detailed_description: str = Field(default="unknown", description="A factual searchable description. Do not invent names, locations, dates, or identities.")
    image_type: str = Field(default="unknown", description="Photo, screenshot, document, logo, flyer, diagram, artwork, unknown, etc.")
    category: str = Field(default="unreviewed", description="A broad archive category useful for browsing.")
    tags: list[str] = Field(default_factory=list, description="Short searchable tags. Prefer lowercase simple phrases.")
    objects: list[str] = Field(default_factory=list, description="Visible objects, gear, equipment, landmarks, or document types.")
    visible_text: str = Field(default="", description="Any clearly readable text in the image. Use an empty string if none is readable.")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="Confidence in the classification from 0.0 to 1.0.")
    needs_reprocess: bool = Field(default=False, description="True if the image should be retried with a better prompt/model/pass.")
    retry_focus: str = Field(default="", description="Specific focus for the next retry, such as OCR, equipment ID, blurry image, document text, faces/people, logos, or model JSON formatting.")
    quality_issue: str = Field(default="", description="Short explanation of what is missing or weak in this classification.")

    @field_validator("short_caption", "detailed_description", "image_type", "category", "visible_text", "retry_focus", "quality_issue", mode="before")
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

    def to_db_dict(self) -> dict[str, str | int | float]:
        return {
            "short_caption": self.short_caption or "unknown",
            "detailed_description": self.detailed_description or "unknown",
            "image_type": self.image_type or "unknown",
            "category": self.category or "unreviewed",
            "tags": ", ".join(self.tags),
            "objects": ", ".join(self.objects),
            "visible_text": self.visible_text or "",
            "confidence": float(self.confidence),
            "needs_reprocess": 1 if self.needs_reprocess else 0,
            "retry_focus": self.retry_focus or "",
            "quality_issue": self.quality_issue or "",
        }


def _vision_disabled_result() -> dict[str, str | int | float]:
    return {
        "short_caption": "Vision disabled; thumbnail and metadata indexed only.",
        "detailed_description": "Enable vision.enabled in config.yaml and point base_url/model at a local vision model.",
        "image_type": "unknown",
        "category": "unreviewed",
        "tags": "",
        "objects": "",
        "visible_text": "",
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
    if not objects:
        retry_reasons.append("no object/equipment list")
    if confidence and confidence < 0.45:
        retry_reasons.append(f"low confidence: {confidence:.2f}")

    if retry_reasons:
        existing_focus = str(cleaned.get("retry_focus", "") or "").strip()
        if not existing_focus:
            focus_bits: list[str] = []
            if "visible_text" in missing:
                focus_bits.append("OCR/read visible text")
            if "objects" in missing or not objects:
                focus_bits.append("identify visible objects/equipment")
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
            "Return one validated image catalog record for this image. Include retry_focus if any important searchable information is missing.",
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
                            VISION_PROMPT
                            + "\n\nReturn JSON only with these fields: "
                            "short_caption, detailed_description, image_type, category, tags, objects, visible_text, confidence, needs_reprocess, retry_focus, quality_issue."
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ],
            }
        ],
        "temperature": 0.1,
        "max_tokens": 900,
    }
    headers = {"Authorization": f"Bearer {cfg.get('api_key', 'not-needed')}"}
    r = requests.post(
        cfg.get("base_url").rstrip("/") + "/chat/completions",
        json=payload,
        headers=headers,
        timeout=int(cfg.get("timeout_seconds", 180)),
    )
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"].strip()
    data = json.loads(_strip_code_fence(content))
    return review_classification_quality(data)


def classify_with_local_model(analysis_path: str, cfg: dict) -> dict[str, str | int | float]:
    if not cfg.get("enabled"):
        return _vision_disabled_result()

    structured_output = cfg.get("structured_output", "pydantic_ai")
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
