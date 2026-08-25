import pytest

from app.vision import ImageClassificationRecord, _parse_json_object, review_classification_quality


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ('{"caption": "radio tower"}', {"caption": "radio tower"}),
        ('```json\n{"caption": "radio tower"}\n```', {"caption": "radio tower"}),
        ('Here is the result:\n{"caption": "radio tower"}\nDone.', {"caption": "radio tower"}),
    ],
)
def test_parse_json_object_accepts_common_model_wrappers(content, expected):
    assert _parse_json_object(content) == expected


def test_parse_json_object_rejects_non_json_response():
    with pytest.raises(ValueError):
        _parse_json_object("I could not classify this image")


@pytest.mark.parametrize(("value", "expected"), [("High", 0.9), ("medium", 0.6), ("25%", 0.25)])
def test_classification_normalizes_confidence_labels(value, expected):
    assert ImageClassificationRecord(confidence=value).confidence == expected


def test_quality_accepts_images_without_text_or_discrete_objects():
    result = review_classification_quality(
        {
            "short_caption": "Clouds over a mountain ridge",
            "detailed_description": "A landscape photograph showing clouds above a distant mountain ridge.",
            "scene_type": "landscape",
            "orientation": "landscape",
            "image_type": "photo",
            "category": "nature",
            "tags": ["clouds", "mountains", "landscape"],
            "objects": [],
            "visible_text": "",
            "confidence": "high",
        }
    )
    assert result["needs_reprocess"] == 0
    assert result["scene_type"] == "landscape"
    assert result["orientation"] == "landscape"
    assert result["people_detected"] == 0
    assert result["people_count"] == 0
    assert result["face_detected"] == 0
    assert result["face_count"] == 0
