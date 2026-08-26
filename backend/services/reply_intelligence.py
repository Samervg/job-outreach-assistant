import json
from dataclasses import dataclass
from urllib.parse import urlparse

import requests

from backend.config import OLLAMA_BASE_URL, OLLAMA_MODEL


CLASSIFICATIONS = {
    "positive_interest",
    "interview",
    "rejection",
    "more_information",
    "neutral",
    "automated_reply",
    "unclear",
}
SUGGESTED_STATUSES = {
    "positive_interest": "replied",
    "interview": "interview",
    "rejection": "rejected",
    "more_information": "replied",
    "neutral": "replied",
    "automated_reply": "replied",
    "unclear": "replied",
}
LOCAL_OLLAMA_HOSTS = {"127.0.0.1", "localhost", "::1"}


class ReplyAnalysisError(Exception):
    pass


@dataclass
class ReplyAnalysis:
    classification: str
    suggested_status: str
    confidence: float
    reason: str


def _require_local_ollama() -> None:
    parsed = urlparse(OLLAMA_BASE_URL)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in LOCAL_OLLAMA_HOSTS:
        raise ReplyAnalysisError(
            "Yanıt analizi yalnızca yerel Ollama adresiyle kullanılabilir."
        )


def analyze_reply(reply_body: str) -> ReplyAnalysis:
    body = reply_body.strip()
    if not body:
        raise ReplyAnalysisError("Analiz edilecek yanıt metni boş.")
    _require_local_ollama()

    schema = {
        "type": "object",
        "properties": {
            "classification": {"type": "string", "enum": sorted(CLASSIFICATIONS)},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "reason": {"type": "string"},
        },
        "required": ["classification", "confidence", "reason"],
        "additionalProperties": False,
    }
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Bir iş başvurusu yanıtını ihtiyatlı biçimde sınıflandır. "
                    "Yalnızca verilen yanıt metnini kullan. Belirsizse unclear seç. "
                    "Reason tek kısa Türkçe cümle olsun."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Kategoriler: positive_interest, interview, rejection, "
                    "more_information, neutral, automated_reply, unclear.\n\n"
                    "Yanıt metni:\n" + body
                ),
            },
        ],
        "stream": False,
        "think": False,
        "format": schema,
        "options": {"temperature": 0},
    }
    try:
        response = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat", json=payload, timeout=120
        )
        response.raise_for_status()
        generated = json.loads(response.json()["message"]["content"])
        classification = str(generated["classification"]).strip()
        confidence = float(generated["confidence"])
        reason = " ".join(str(generated["reason"]).split())
    except (requests.RequestException, ValueError, TypeError, KeyError) as error:
        raise ReplyAnalysisError(
            "Ollama geçerli bir yanıt değerlendirmesi döndürmedi."
        ) from error

    if classification not in CLASSIFICATIONS or not 0 <= confidence <= 1:
        raise ReplyAnalysisError("Ollama yanıt değerlendirmesi geçersiz.")
    if not reason or len(reason) > 300:
        raise ReplyAnalysisError("Ollama kısa ve geçerli bir değerlendirme nedeni döndürmedi.")
    return ReplyAnalysis(
        classification=classification,
        suggested_status=SUGGESTED_STATUSES[classification],
        confidence=confidence,
        reason=reason,
    )
