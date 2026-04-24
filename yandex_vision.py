"""Yandex Cloud Vision — OCR через REST batchAnalyze (TEXT_DETECTION).

Документация: https://yandex.cloud/en/docs/vision/vision/api-ref/Vision/batchAnalyze
"""

from __future__ import annotations

import base64
import json
import logging
from io import BytesIO
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.core.config import settings
from app.services.ai_logger import ai_timer

log = logging.getLogger(__name__)

VISION_BATCH_URL = "https://vision.api.cloud.yandex.net/vision/v1/batchAnalyze"


def yandex_vision_configured() -> bool:
    key = (settings.yandex_vision_api_key or "").strip()
    iam = (settings.yandex_vision_iam_token or "").strip()
    folder = (settings.yandex_cloud_folder_id or "").strip()
    return bool((key or iam) and folder)


def _auth_header() -> str | None:
    key = (settings.yandex_vision_api_key or "").strip()
    iam = (settings.yandex_vision_iam_token or "").strip()
    if key:
        return f"Api-Key {key}"
    if iam:
        return f"Bearer {iam}"
    return None


def _language_codes() -> list[str]:
    raw = (settings.yandex_vision_language_codes or "ru,en").replace(" ", "")
    parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
    return parts[:8] if parts else ["ru", "en"]


def _mime_for_request(mime_type: str) -> str:
    m = (mime_type or "image/jpeg").lower().split(";")[0].strip()
    if m in {"image/jpg", "image/jpeg"}:
        return "image/jpeg"
    if m == "image/png":
        return "image/png"
    return "image/jpeg"


def _maybe_shrink_image(image_bytes: bytes, mime_type: str) -> tuple[bytes, str]:
    """Сервис ожидает JPEG/PNG разумного размера; при перегрузе — сжимаем в JPEG."""
    max_b = max(512_000, int(settings.yandex_vision_max_image_bytes or 3_500_000))
    if len(image_bytes) <= max_b and _mime_for_request(mime_type) == "image/jpeg":
        return image_bytes, "image/jpeg"
    if len(image_bytes) <= max_b and _mime_for_request(mime_type) == "image/png":
        return image_bytes, "image/png"
    try:
        from PIL import Image

        img = Image.open(BytesIO(image_bytes)).convert("RGB")
        quality = 88
        for _ in range(4):
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            out = buf.getvalue()
            if len(out) <= max_b or quality <= 65:
                return out, "image/jpeg"
            quality -= 6
            w, h = img.size
            if w > 1600 or h > 1600:
                img = img.resize((max(1, w * 9 // 10), max(1, h * 9 // 10)), Image.Resampling.LANCZOS)
        return out, "image/jpeg"
    except Exception:
        return image_bytes, _mime_for_request(mime_type)


def _collect_text_from_annotation(td: dict[str, Any]) -> tuple[str, list[float]]:
    lines_out: list[str] = []
    confs: list[float] = []

    def walk_page(page: dict[str, Any]) -> None:
        for ent in page.get("entities") or []:
            if isinstance(ent, dict):
                t = (ent.get("text") or "").strip()
                if t:
                    lines_out.append(t)
        for block in page.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            for line in block.get("lines") or []:
                if not isinstance(line, dict):
                    continue
                lc = line.get("confidence")
                if isinstance(lc, (int, float)):
                    confs.append(float(lc))
                words = line.get("words") or []
                if words:
                    parts: list[str] = []
                    for w in words:
                        if isinstance(w, dict):
                            wt = (w.get("text") or "").strip()
                            if wt:
                                parts.append(wt)
                            wc = w.get("confidence")
                            if isinstance(wc, (int, float)):
                                confs.append(float(wc))
                    if parts:
                        lines_out.append(" ".join(parts))
                else:
                    lt = (line.get("text") or "").strip()
                    if lt:
                        lines_out.append(lt)

    for page in td.get("pages") or []:
        if isinstance(page, dict):
            walk_page(page)

    text = "\n".join(lines_out).strip()
    return text, confs


def _parse_batch_response(payload: dict[str, Any]) -> dict[str, Any]:
    results = payload.get("results") or []
    for top in results:
        if not isinstance(top, dict):
            continue
        err = top.get("error")
        if err:
            log.warning("yandex vision: analyzeSpec error: %s", err)
            continue
        inner = top.get("results") or []
        for feat in inner:
            if not isinstance(feat, dict):
                continue
            ferr = feat.get("error")
            if ferr:
                log.warning("yandex vision: feature error: %s", ferr)
                continue
            td = feat.get("textDetection") or feat.get("text_detection")
            if not isinstance(td, dict):
                continue
            text, confs = _collect_text_from_annotation(td)
            if not text:
                continue
            conf = sum(confs) / len(confs) if confs else 0.88
            conf = max(0.5, min(1.0, conf))
            return {"text": text, "confidence": conf}
    return {}


def yandex_vision_recognize_text(image_bytes: bytes, mime_type: str = "image/jpeg") -> dict[str, Any]:
    """Возвращает словарь для normalize_ocr: text, confidence. Пустой dict при пропуске или ошибке."""
    if not yandex_vision_configured() or not image_bytes:
        return {}

    auth = _auth_header()
    if not auth:
        return {}

    folder = (settings.yandex_cloud_folder_id or "").strip()
    payload_bytes, req_mime = _maybe_shrink_image(image_bytes, mime_type)
    b64 = base64.b64encode(payload_bytes).decode("ascii")
    model = (settings.yandex_vision_text_model or "page").strip()
    if model not in {"page", "line"}:
        model = "page"

    body_camel: dict[str, Any] = {
        "folderId": folder,
        "analyzeSpecs": [
            {
                "content": b64,
                "mimeType": req_mime,
                "features": [
                    {
                        "type": "TEXT_DETECTION",
                        "textDetectionConfig": {
                            "languageCodes": _language_codes(),
                            "model": model,
                        },
                    }
                ],
            }
        ],
    }
    body_snake: dict[str, Any] = {
        "folder_id": folder,
        "analyze_specs": [
            {
                "content": b64,
                "mime_type": req_mime,
                "features": [
                    {
                        "type": "TEXT_DETECTION",
                        "text_detection_config": {
                            "language_codes": _language_codes(),
                            "model": model,
                        },
                    }
                ],
            }
        ],
    }

    timeout = max(5, int(settings.yandex_vision_timeout or 20))

    def _post(body: dict[str, Any]) -> str:
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = Request(
            VISION_BATCH_URL,
            data=raw,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": auth,
            },
        )
        with urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8")

    with ai_timer("yandex_vision_ocr", provider="yandex_vision") as t:
        t.model = f"yandex:vision:{model}"
        text_body = ""
        try:
            try:
                text_body = _post(body_camel)
            except HTTPError as e1:
                if e1.code != 400:
                    raise
                try:
                    detail1 = e1.read().decode("utf-8", errors="replace")[:400]
                except Exception:
                    detail1 = str(e1)
                log.info("yandex vision: retry with snake_case body (400: %s)", detail1)
                text_body = _post(body_snake)
        except HTTPError as e:
            t.status = "error"
            try:
                detail = e.read().decode("utf-8", errors="replace")[:800]
            except Exception:
                detail = str(e)
            t.extra["http_status"] = e.code
            t.extra["detail"] = detail
            log.warning("yandex vision HTTP %s: %s", e.code, detail)
            return {}
        except URLError as e:
            t.status = "error"
            t.extra["reason"] = str(e.reason) if e.reason else str(e)
            log.warning("yandex vision network error: %s", e)
            return {}
        except Exception as e:
            t.status = "error"
            t.extra["error"] = str(e)
            log.warning("yandex vision error: %s", e)
            return {}

        try:
            parsed = json.loads(text_body) if text_body else {}
        except json.JSONDecodeError:
            t.status = "error"
            t.extra["reason"] = "invalid_json"
            return {}

        if not isinstance(parsed, dict):
            return {}

        out = _parse_batch_response(parsed)
        if not out:
            t.status = "fallback"
            t.extra["reason"] = "no_text_in_response"
        return out
