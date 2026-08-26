import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import requests

from backend.config import OLLAMA_BASE_URL, OLLAMA_MODEL


ATTENTION_CLASSIFICATIONS = {
    "positive_interest",
    "interview",
    "rejection",
    "more_information",
    "neutral",
    "automated_reply",
    "unclear",
}
LOCAL_OLLAMA_HOSTS = {"127.0.0.1", "localhost", "::1"}


class FollowUpGenerationError(Exception):
    pass


@dataclass
class FollowUpEligibility:
    eligible: bool
    reason_code: str
    reason: str
    days_remaining: int = 0


@dataclass
class FollowUpDraft:
    subject: str
    body: str


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def evaluate_follow_up(
    application: dict,
    settings: dict,
    *,
    now: datetime | None = None,
) -> FollowUpEligibility:
    if not bool(settings["follow_up_enabled"]):
        return FollowUpEligibility(False, "globally_disabled", "Follow-up özelliği kapalı.")
    if bool(application.get("follow_up_disabled")):
        return FollowUpEligibility(False, "application_disabled", "Bu başvuru için follow-up kapalı.")
    if application.get("status") != "sent":
        return FollowUpEligibility(False, "status_not_sent", "Yalnızca yanıtsız gönderilmiş başvurular uygundur.")
    if application.get("replied_at") or int(application.get("reply_count") or 0) > 0:
        return FollowUpEligibility(False, "reply_detected", "Bu başvuruya yanıt geldiği için follow-up önerilmiyor.")
    if application.get("ai_reply_classification") in ATTENTION_CLASSIFICATIONS:
        return FollowUpEligibility(False, "reply_analysis_present", "Yanıt değerlendirmesi bulunduğu için follow-up önerilmiyor.")
    if not application.get("gmail_message_id") or not application.get("gmail_thread_id"):
        return FollowUpEligibility(False, "missing_gmail_identifiers", "Doğrulanmış Gmail mesaj/thread bilgisi bulunmuyor.")
    if int(application.get("follow_up_count") or 0) >= int(settings["max_follow_ups"]):
        return FollowUpEligibility(False, "max_follow_ups_reached", "Maksimum follow-up sayısına ulaşıldı.")

    reference = _parse_timestamp(
        application.get("last_follow_up_at") or application.get("sent_at")
    )
    if reference is None:
        return FollowUpEligibility(False, "missing_sent_date", "Geçerli gönderim tarihi bulunmuyor.")
    current = now or datetime.now(timezone.utc)
    due_at = reference + timedelta(days=int(settings["follow_up_after_days"]))
    if current < due_at:
        remaining_seconds = (due_at - current).total_seconds()
        days_remaining = max(1, int((remaining_seconds + 86399) // 86400))
        return FollowUpEligibility(
            False,
            "waiting_period",
            f"Follow-up için {days_remaining} gün kaldı.",
            days_remaining,
        )
    return FollowUpEligibility(True, "eligible", "Follow-up uygun.")


def generate_follow_up(application: dict, candidate_name: str) -> FollowUpDraft:
    parsed = urlparse(OLLAMA_BASE_URL)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in LOCAL_OLLAMA_HOSTS:
        raise FollowUpGenerationError("Follow-up yalnızca yerel Ollama ile üretilebilir.")
    schema = {
        "type": "object",
        "properties": {
            "subject": {"type": "string"},
            "body": {"type": "string"},
        },
        "required": ["subject", "body"],
        "additionalProperties": False,
    }
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Kısa, nazik ve baskı kurmayan Türkçe iş başvurusu follow-up "
                    "e-postaları yaz. Yalnızca verilen bilgileri kullan."
                ),
            },
            {
                "role": "user",
                "content": f"""
Şirket: {application['company_name']}
Pozisyon: {application['position']}
Gönderim tarihi: {application['sent_at']}
Önceki follow-up sayısı: {application.get('follow_up_count', 0)}
Aday adı: {candidate_name}
Orijinal konu: {application['subject']}
Orijinal başvuru metni: {application['body']}

Yaklaşık 45-75 kelimelik kısa bir takip e-postası üret. Süreçle ilgili nazikçe
güncelleme sor. "Neden dönüş yapmadınız?", "Mailimi gördünüz mü?" veya baskı,
suçluluk ve uydurma şirket bilgisi kullanma. Konuyu aynı konuşmaya uygun biçimde
"Re:" ile başlat. JSON dışında hiçbir şey yazma.
""".strip(),
            },
        ],
        "stream": False,
        "think": False,
        "format": schema,
        "options": {"temperature": 0.2},
    }
    try:
        response = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat", json=payload, timeout=120
        )
        response.raise_for_status()
        result = json.loads(response.json()["message"]["content"])
        subject = str(result["subject"]).strip()
        body = str(result["body"]).strip()
    except (requests.RequestException, ValueError, TypeError, KeyError) as error:
        raise FollowUpGenerationError(
            "Ollama geçerli bir follow-up taslağı döndürmedi."
        ) from error
    if not subject or len(subject) > 300 or not body or len(body) > 5000:
        raise FollowUpGenerationError("Follow-up taslağı boş veya geçersiz.")
    if not subject.casefold().startswith("re:"):
        subject = f"Re: {application['subject']}"
    return FollowUpDraft(subject=subject, body=body)
