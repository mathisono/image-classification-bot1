import json
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import ExifTags, Image, IptcImagePlugin

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except Exception:
    pass


EXIF_NAME_TO_ID = {name: tag_id for tag_id, name in ExifTags.TAGS.items()}
GPS_INFO_ID = EXIF_NAME_TO_ID.get("GPSInfo", 34853)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        for encoding in ("utf-8", "utf-16le", "latin-1"):
            try:
                return value.decode(encoding).rstrip("\x00").strip()
            except UnicodeDecodeError:
                continue
        return value[:64].hex()
    if isinstance(value, (list, tuple)):
        return ", ".join(part for part in (_text(item) for item in value) if part)
    return str(value).strip()


def _number(value: Any) -> float | int | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return int(result) if result.is_integer() else result


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        decoded = _text(value)
        return decoded[:1000]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    numeric = _number(value)
    return numeric if numeric is not None else str(value)


def _normalize_exif_datetime(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    for pattern in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, pattern).isoformat()
        except ValueError:
            continue
    return text[:64]


def _gps_decimal(values: Any, reference: Any) -> float | None:
    if not isinstance(values, (list, tuple)) or len(values) < 3:
        return None
    parts = [_number(value) for value in values[:3]]
    if any(value is None for value in parts):
        return None
    degrees, minutes, seconds = (float(value) for value in parts)
    result = degrees + minutes / 60.0 + seconds / 3600.0
    if _text(reference).upper() in {"S", "W"}:
        result *= -1.0
    return round(result, 8)


def _read_xmp(info: dict[str, Any]) -> dict[str, Any]:
    raw = info.get("xmp") or info.get("XML:com.adobe.xmp")
    if not raw:
        return {}
    text = _text(raw)
    if not text:
        return {}
    result: dict[str, Any] = {"raw_preview": text[:4000]}
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return result

    wanted = {
        "title": "title",
        "description": "description",
        "creator": "creator",
        "rights": "copyright",
        "subject": "keywords",
        "CreateDate": "created_at",
        "DateCreated": "created_at",
    }
    for element in root.iter():
        local_name = element.tag.rsplit("}", 1)[-1]
        field_name = wanted.get(local_name)
        if not field_name:
            continue
        values = [part.strip() for part in element.itertext() if part.strip()]
        if values and field_name not in result:
            result[field_name] = ", ".join(dict.fromkeys(values))[:2000]
    return result


def _read_iptc(image: Image.Image) -> dict[str, Any]:
    try:
        raw = IptcImagePlugin.getiptcinfo(image) or {}
    except Exception:
        return {}
    mapping = {
        (2, 5): "title",
        (2, 25): "keywords",
        (2, 80): "author",
        (2, 116): "copyright",
        (2, 120): "description",
    }
    result: dict[str, Any] = {}
    for key, field_name in mapping.items():
        value = raw.get(key)
        if value:
            result[field_name] = _text(value)[:4000]
    return result


def _metadata_prompt_context(metadata: dict[str, Any]) -> str:
    fields = [
        ("capture date", metadata.get("captured_at")),
        ("camera make", metadata.get("camera_make")),
        ("camera model", metadata.get("camera_model")),
        ("lens", metadata.get("lens_model")),
        ("title", metadata.get("image_title")),
        ("description", metadata.get("image_description")),
        ("author", metadata.get("image_author")),
        ("keywords", metadata.get("metadata_keywords")),
        ("software", metadata.get("software")),
        ("GPS latitude", metadata.get("gps_latitude")),
        ("GPS longitude", metadata.get("gps_longitude")),
    ]
    lines = [f"- {label}: {value}" for label, value in fields if value not in (None, "")]
    if not lines:
        return ""
    return (
        "Original-file metadata is provided only as auxiliary context. It may be missing, stale, or wrong. "
        "Describe only what is visibly present and never treat metadata as visual proof.\n" + "\n".join(lines)
    )[:2400]


def extract_original_metadata(path: str, max_pixels: int) -> dict[str, Any]:
    """Read embedded metadata from the original file without modifying or fully rewriting it.

    Missing embedded metadata is normal and returns NO_METADATA. Extraction errors are
    recorded but should not block derivative creation or vision classification.
    """
    result: dict[str, Any] = {
        "metadata_status": "NO_METADATA",
        "metadata_source": "",
        "metadata_extracted_at": datetime.now().astimezone().isoformat(),
        "metadata_error": "",
        "metadata_json": "{}",
        "metadata_search_text": "",
        "captured_at": "",
        "camera_make": "",
        "camera_model": "",
        "lens_model": "",
        "orientation": None,
        "gps_latitude": None,
        "gps_longitude": None,
        "gps_altitude": None,
        "image_title": "",
        "image_description": "",
        "image_author": "",
        "copyright": "",
        "software": "",
        "metadata_keywords": "",
        "exposure_time": None,
        "f_number": None,
        "iso_speed": None,
        "focal_length_mm": None,
        "metadata_prompt_context": "",
    }

    try:
        Image.MAX_IMAGE_PIXELS = max_pixels
        with Image.open(Path(path)) as image:
            payload: dict[str, Any] = {
                "container": {
                    "format": image.format,
                    "mode": image.mode,
                    "width": image.width,
                    "height": image.height,
                }
            }
            sources: list[str] = []
            exif_raw: dict[str, Any] = {}
            exif = image.getexif()
            if exif:
                sources.append("EXIF")
                for tag_id, value in exif.items():
                    tag_name = ExifTags.TAGS.get(tag_id, str(tag_id))
                    if tag_name == "GPSInfo":
                        continue
                    exif_raw[tag_name] = _json_value(value)
                payload["exif"] = exif_raw

                def exif_value(name: str):
                    tag_id = EXIF_NAME_TO_ID.get(name)
                    return exif.get(tag_id) if tag_id is not None else None

                result.update(
                    captured_at=_normalize_exif_datetime(
                        exif_value("DateTimeOriginal") or exif_value("DateTimeDigitized") or exif_value("DateTime")
                    ),
                    camera_make=_text(exif_value("Make"))[:200],
                    camera_model=_text(exif_value("Model"))[:200],
                    lens_model=_text(exif_value("LensModel"))[:200],
                    orientation=_number(exif_value("Orientation")),
                    image_description=_text(exif_value("ImageDescription"))[:4000],
                    image_author=_text(exif_value("Artist"))[:500],
                    copyright=_text(exif_value("Copyright"))[:500],
                    software=_text(exif_value("Software"))[:500],
                    exposure_time=_number(exif_value("ExposureTime")),
                    f_number=_number(exif_value("FNumber")),
                    iso_speed=_number(exif_value("PhotographicSensitivity") or exif_value("ISOSpeedRatings")),
                    focal_length_mm=_number(exif_value("FocalLength")),
                )

                gps = {}
                try:
                    gps = dict(exif.get_ifd(GPS_INFO_ID))
                except Exception:
                    value = exif.get(GPS_INFO_ID)
                    if isinstance(value, dict):
                        gps = value
                if gps:
                    gps_named = {ExifTags.GPSTAGS.get(key, str(key)): value for key, value in gps.items()}
                    payload["gps"] = {key: _json_value(value) for key, value in gps_named.items()}
                    result["gps_latitude"] = _gps_decimal(gps_named.get("GPSLatitude"), gps_named.get("GPSLatitudeRef"))
                    result["gps_longitude"] = _gps_decimal(gps_named.get("GPSLongitude"), gps_named.get("GPSLongitudeRef"))
                    altitude = _number(gps_named.get("GPSAltitude"))
                    if altitude is not None and _number(gps_named.get("GPSAltitudeRef")) == 1:
                        altitude = -float(altitude)
                    result["gps_altitude"] = altitude

            iptc = _read_iptc(image)
            if iptc:
                sources.append("IPTC")
                payload["iptc"] = iptc
            xmp = _read_xmp(image.info)
            if xmp:
                sources.append("XMP")
                payload["xmp"] = xmp

            result["image_title"] = _text(
                iptc.get("title") or xmp.get("title") or exif_raw.get("XPTitle")
            )[:1000]
            result["image_description"] = _text(
                result["image_description"] or iptc.get("description") or xmp.get("description")
            )[:4000]
            result["image_author"] = _text(
                result["image_author"] or iptc.get("author") or xmp.get("creator") or exif_raw.get("XPAuthor")
            )[:500]
            result["copyright"] = _text(
                result["copyright"] or iptc.get("copyright") or xmp.get("copyright")
            )[:500]
            result["metadata_keywords"] = _text(
                iptc.get("keywords") or xmp.get("keywords") or exif_raw.get("XPKeywords")
            )[:4000]
            if not result["captured_at"]:
                result["captured_at"] = _text(xmp.get("created_at"))[:64]

            result["metadata_source"] = ",".join(sources)
            result["metadata_status"] = "EXTRACTED" if sources else "NO_METADATA"
            result["metadata_json"] = json.dumps(payload, ensure_ascii=False, sort_keys=True)[:65535]
            search_parts = [
                result.get("image_title"), result.get("image_description"), result.get("image_author"),
                result.get("metadata_keywords"), result.get("camera_make"), result.get("camera_model"),
                result.get("lens_model"), result.get("software"), result.get("captured_at"),
            ]
            result["metadata_search_text"] = " | ".join(
                str(value).strip() for value in search_parts if value not in (None, "")
            )[:12000]
            result["metadata_prompt_context"] = _metadata_prompt_context(result)
    except Exception as exc:
        result["metadata_status"] = "ERROR"
        result["metadata_error"] = f"{type(exc).__name__}: {exc}"[:2000]
        result["metadata_json"] = json.dumps({"error": result["metadata_error"]})

    return result
