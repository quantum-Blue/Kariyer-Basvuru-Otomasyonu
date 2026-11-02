"""Google Gemini LLM İstemcisi, Çıktı Doğrulayıcı ve Fallback Mekanizması (src/generator/llm_client.py).

Bu modülün amacı:
1. Faz 10'daki `PromptBuilder` tarafından üretilen `BuiltPrompt` verisini kullanarak
   Google Gemini API (Free Tier — `gemini-2.5-flash`) üzerinden kişiselleştirilmiş
   başvuru e-postası üretmek.
2. **Sıkı Çıktı Doğrulayıcı (`validate_generated_email_body`):**
   LLM çıktısını körü körüne alıcıya göndermek tehlikelidir. Üretilen metin şu 4 güvenlik
   filtresinden geçirilir:
   - **Kelime Sınırı Kontrolü:** Metin çok kısa (`< min_word_count`) veya aşırı uzun (`> max_word_count`)
     olmamalıdır.
   - **Zorunlu Bağlantı (Link) Kontrolü:** Adayın portfolyo ve CV URL'leri metin içinde eksiksiz
     yer almalıdır.
   - **Halüsinasyonlu / Uydurma Link Yakalama (`extract_urls_from_text`):**
     LLM kendi kendine `https://example.com/cv` veya `https://muhammedenesbal.com` gibi uydurma
     bir bağlantı yazmışsa anında tespit edilip reddedilir!
   - **Yasaklı Dosya Eki (Attachment) İfadesi Kontrolü:**
     Metin içinde *"ekteki dosyada"*, *"özgeçmişim ektedir"*, *"please find attached"* gibi
     yanıltıcı ifadeler geçmemelidir.
3. **Akıllı Eksik Link Onarımı (Auto-Repair) ve Kesintisiz Fallback:**
   - Eğer LLM harika bir metin üretmiş fakat sadece sonuna portfolyo/CV linklerini eklemeyi
     unutmuşsa, istemci metni çöpe atmadan önce standart bağlantı bloğunu (`format_links_block`)
     metnin altına ekleyip tekrar doğrular (`repaired_links=True`).
   - Eğer API anahtarı boşsa, kota dolmuşsa (`HTTP 429`), ağ hatası oluşmuşsa veya LLM çıktısı
     halüsinasyon/kural ihlali içeriyorsa sistem ASLA çökmez; `used_fallback=True` ile Faz 10'un
     statik yedek şablonuna (`fallback_email`) kesintisiz geçiş yapar!
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
import json
import re
from urllib import request as urllib_request

from src.generator.prompt_builder import BuiltPrompt
from src.generator.template_manager import (
    CandidateLinks,
    contains_forbidden_attachment_phrase,
    format_links_block,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Metin içerisindeki tüm HTTP/HTTPS bağlantıları yakalayan düzenli ifade
_TEXT_URL_REGEX = re.compile(
    r"https?://[^\s<>\"'()\[\]{}]+",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class EmailValidationResult:
    """LLM tarafından üretilen e-posta gövdesinin doğrulama raporu."""

    is_valid: bool                                 # Tüm güvenlik ve kalite kurallarından geçti mi?
    word_count: int                                # Tespit edilen kelime sayısı
    missing_required_links: list[str] = field(default_factory=list)   # Metinde eksik olan zorunlu linkler
    hallucinated_links: list[str] = field(default_factory=list)       # İzin verilenler dışındaki uydurma linkler
    has_forbidden_attachment_phrase: bool = False  # 'ekte / attached' ifadesi var mı?
    errors: list[str] = field(default_factory=list)                   # İnsan tarafından okunabilir hata listesi


@dataclass(frozen=True)
class EmailGenerationResult:
    """Nihai e-posta üretim sonucu (LLM çıktısı, onarılmış çıktı veya statik Fallback)."""

    subject: str
    body: str
    language: str
    used_fallback: bool                            # Statik yedek şablon mu kullanıldı?
    repaired_links: bool = False                   # Eksik linkler otomatik onarıldı mı?
    fallback_reason: str = ""                      # Fallback kullanıldıysa nedeni


def extract_urls_from_text(text: str) -> list[str]:
    """Verilen metin içerisindeki tüm `http://` ve `https://` URL'lerini çıkarır
    ve cümle sonu noktalama işaretlerini (`.`, `,`, `;`, `)`) temizler.
    """
    if not isinstance(text, str) or not text.strip():
        return []

    found: list[str] = []
    for raw_match in _TEXT_URL_REGEX.findall(text):
        cleaned = raw_match.rstrip(".,;:!?)>\"']")
        if cleaned and cleaned not in found:
            found.append(cleaned)
    return found


def validate_generated_email_body(
    body: str,
    required_links: Sequence[str] | None = None,
    allowed_links: Sequence[str] | None = None,
    min_word_count: int = 60,
    max_word_count: int = 320,
) -> EmailValidationResult:
    """Üretilen e-posta metnini kelime sayısı, zorunlu linkler, halüsinasyonlu linkler
    ve yasaklı dosya eki ifadeleri açısından denetler.
    """
    if not isinstance(body, str) or not body.strip():
        return EmailValidationResult(
            is_valid=False,
            word_count=0,
            errors=["Üretilen e-posta metni boş."],
        )

    clean_body = body.strip()
    words = clean_body.split()
    word_count = len(words)
    errors: list[str] = []

    # 1. Kelime sayısı sınırı kontrolü
    if word_count < min_word_count:
        errors.append(
            f"Kelime sayısı çok düşük: {word_count} (minimum: {min_word_count})."
        )
    elif word_count > max_word_count:
        errors.append(
            f"Kelime sayısı çok yüksek: {word_count} (maksimum: {max_word_count})."
        )

    # 2. Yasaklı dosya eki ('ekteki dosyada', 'attached' vb.) kontrolü
    has_attachment_word = contains_forbidden_attachment_phrase(clean_body)
    if has_attachment_word:
        errors.append(
            "Metin yasaklı dosya eki (ekte / attached) ifadesi içeriyor."
        )

    # 3. Zorunlu bağlantıların (Portfolyo / CV URL) varlık kontrolü
    req_list = [u.strip() for u in (required_links or []) if u and u.strip()]
    missing_links: list[str] = []
    for req_url in req_list:
        if req_url not in clean_body:
            missing_links.append(req_url)

    if missing_links:
        errors.append(
            f"Zorunlu aday bağlantıları metinde eksik: {missing_links}"
        )

    # 4. Halüsinasyonlu / Uydurma URL kontrolü
    extracted_urls = extract_urls_from_text(clean_body)
    allowed_set = {
        u.strip().rstrip("/")
        for u in (allowed_links if allowed_links is not None else req_list)
        if u and u.strip()
    }
    # Required linkler her zaman allowed kabul edilir
    for r_url in req_list:
        allowed_set.add(r_url.rstrip("/"))

    hallucinated: list[str] = []
    if allowed_set:
        for found_url in extracted_urls:
            normalized_found = found_url.rstrip("/")
            if normalized_found not in allowed_set:
                hallucinated.append(found_url)

    if hallucinated:
        errors.append(
            f"LLM halüsinasyon/uydurma bağlantı üretti: {hallucinated}"
        )

    return EmailValidationResult(
        is_valid=(len(errors) == 0),
        word_count=word_count,
        missing_required_links=missing_links,
        hallucinated_links=hallucinated,
        has_forbidden_attachment_phrase=has_attachment_word,
        errors=errors,
    )


def _default_gemini_rest_backend(
    system_instruction: str,
    user_prompt: str,
    model_name: str,
    temperature: float,
    api_key: str,
    timeout_sec: float = 25.0,
) -> str:
    """Harici ağır SDK bağımlılığı gerektirmeden Google Gemini REST API (`generateContent`)
    üzerinden doğrudan üretim yapan varsayılan istemci fonksiyonu.
    """
    endpoint = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model_name}:generateContent?key={api_key}"
    )
    payload = {
        "system_instruction": {
            "parts": [{"text": system_instruction}],
        },
        "contents": [
            {
                "role": "user",
                "parts": [{"text": user_prompt}],
            }
        ],
        "generationConfig": {
            "temperature": temperature,
        },
    }
    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib_request.Request(
        endpoint,
        data=data_bytes,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib_request.urlopen(req, timeout=timeout_sec) as resp:
        raw_resp = resp.read().decode("utf-8", errors="replace")
        parsed = json.loads(raw_resp)

    candidates = parsed.get("candidates") or []
    if not candidates:
        raise RuntimeError("Gemini API yanıtında 'candidates' alanı boş döndü.")

    parts = candidates[0].get("content", {}).get("parts") or []
    text_chunks = [str(p.get("text", "")) for p in parts if isinstance(p, dict)]
    return "\n".join(text_chunks).strip()


class GeminiLLMClient:
    """Google Gemini API üzerinden kişiselleştirilmiş başvuru e-postası üreten,
    çıktıyı doğrulayan, eksik linkleri onaran ve hata durumunda statik şablona (fallback)
    kesintisiz geçen dayanıklı istemci sınıfı.
    """

    def __init__(
        self,
        api_key: str = "",
        model_name: str = "gemini-2.5-flash",
        temperature: float = 0.4,
        min_word_count: int = 60,
        max_word_count: int = 320,
        generation_backend: Callable[[str, str, str, float], str] | None = None,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.model_name = (model_name or "gemini-2.5-flash").strip()
        self.temperature = float(temperature)
        self.min_word_count = min_word_count
        self.max_word_count = max_word_count
        self._backend = generation_backend

    def _collect_allowed_links(
        self,
        built_prompt: BuiltPrompt,
        links: CandidateLinks | None,
    ) -> list[str]:
        """Metin içerisinde bulunmasına izin verilen tüm meşru aday bağlantılarını toplar."""
        allowed: list[str] = list(built_prompt.required_links)
        if links is not None:
            for candidate_url in (
                links.cv_url_tr,
                links.cv_url_en,
                links.portfolio_url,
                links.github_url,
                links.linkedin_url,
            ):
                if candidate_url and candidate_url.strip() and candidate_url.strip() not in allowed:
                    allowed.append(candidate_url.strip())
        return allowed

    def generate_email(
        self,
        built_prompt: BuiltPrompt,
        links: CandidateLinks | None = None,
    ) -> EmailGenerationResult:
        """Verilen `BuiltPrompt` için LLM e-postası üretir, doğrular ve gerekirse fallback'e döner.

        Adım Adım Çalışma Mantığı:
        1. Eğer `api_key` boşsa ve özel bir test `generation_backend` verilmemişse,
           ağ çağrısı yapmadan doğrudan `built_prompt.fallback_email` döndürür.
        2. LLM backend'ini çağırır. Ağ veya API kotası hatası (`429`, `ConnectionError`)
           oluşursa hatayı loglar ve `fallback_email` döndürür.
        3. LLM çıktısını `validate_generated_email_body` ile denetler. Tamamen geçerliyse döndürür.
        4. **Akıllı Onarım (Auto-Repair):** Eğer tek sorun LLM'in zorunlu linkleri metne
           eklemeyi unutmasıysa (halüsinasyonlu link yoksa ve yasaklı 'attached/ekte' ifadesi yoksa),
           adayın link bloğunu metnin sonuna ekleyip tekrar doğrular. Geçerse `repaired_links=True`
           ile döndürür.
        5. Onarım da doğrulamadan geçemezse (örn. uydurma link veya kelime sınırı aşımı varsa)
           güvenli `fallback_email` şablonuna döner.
        """
        # 1. API anahtarı kontrolü
        if not self.api_key:
            reason = "GEMINI_API_KEY tanımlanmamış -> Statik yedek (fallback) şablon kullanıldı."
            logger.info(reason)
            return EmailGenerationResult(
                subject=built_prompt.fallback_email.subject,
                body=built_prompt.fallback_email.body,
                language=built_prompt.language,
                used_fallback=True,
                repaired_links=False,
                fallback_reason=reason,
            )

        # 2. LLM Üretim Çağrısı
        try:
            if self._backend is not None:
                raw_output = self._backend(
                    built_prompt.system_instruction,
                    built_prompt.user_prompt,
                    self.model_name,
                    self.temperature,
                )
            else:
                raw_output = _default_gemini_rest_backend(
                    system_instruction=built_prompt.system_instruction,
                    user_prompt=built_prompt.user_prompt,
                    model_name=self.model_name,
                    temperature=self.temperature,
                    api_key=self.api_key,
                )
        except Exception as exc:
            reason = f"Gemini LLM API hatası ({exc}) -> Statik yedek (fallback) şablona geçildi."
            logger.warning(reason)
            return EmailGenerationResult(
                subject=built_prompt.fallback_email.subject,
                body=built_prompt.fallback_email.body,
                language=built_prompt.language,
                used_fallback=True,
                repaired_links=False,
                fallback_reason=reason,
            )

        clean_output = (raw_output or "").strip()
        allowed_links = self._collect_allowed_links(built_prompt, links)

        # 3. İlk Doğrulama Kontrolü
        validation = validate_generated_email_body(
            body=clean_output,
            required_links=built_prompt.required_links,
            allowed_links=allowed_links,
            min_word_count=self.min_word_count,
            max_word_count=self.max_word_count,
        )

        if validation.is_valid:
            logger.info(
                "LLM e-postası başarıyla üretildi ve doğrulandı (%s, %d kelime).",
                built_prompt.company_name,
                validation.word_count,
            )
            return EmailGenerationResult(
                subject=built_prompt.subject,
                body=clean_output,
                language=built_prompt.language,
                used_fallback=False,
                repaired_links=False,
            )

        # 4. AKILLI EKSİK BAĞLANTI ONARIMI (Auto-Repair):
        # Eğer metinde uydurma (halüsinasyon) link YOKSA ve yasaklı 'attached/ekte' kelimesi YOKSA,
        # fakat yalnızca zorunlu portfolyo/CV linkleri unutulmuşsa, linkleri ekleyip tekrar dene!
        if (
            validation.missing_required_links
            and not validation.hallucinated_links
            and not validation.has_forbidden_attachment_phrase
            and clean_output
        ):
            links_to_append = (
                format_links_block(links, built_prompt.language)
                if links is not None
                else "\n".join(f"- {u}" for u in validation.missing_required_links)
            )
            repaired_body = f"{clean_output}\n\n{links_to_append}".strip()
            repaired_validation = validate_generated_email_body(
                body=repaired_body,
                required_links=built_prompt.required_links,
                allowed_links=allowed_links,
                min_word_count=self.min_word_count,
                max_word_count=self.max_word_count,
            )
            if repaired_validation.is_valid:
                logger.info(
                    "LLM çıktısında eksik olan aday bağlantıları otomatik onarıldı (%s).",
                    built_prompt.company_name,
                )
                return EmailGenerationResult(
                    subject=built_prompt.subject,
                    body=repaired_body,
                    language=built_prompt.language,
                    used_fallback=False,
                    repaired_links=True,
                )

        # 5. Doğrulama başarısız -> Güvenli Statik Fallback Şablonuna Geçiş
        reason = (
            f"LLM çıktısı doğrulamadan geçemedi ({'; '.join(validation.errors)}) -> "
            f"Statik yedek (fallback) şablon kullanıldı."
        )
        logger.warning(reason)
        return EmailGenerationResult(
            subject=built_prompt.fallback_email.subject,
            body=built_prompt.fallback_email.body,
            language=built_prompt.language,
            used_fallback=True,
            repaired_links=False,
            fallback_reason=reason,
        )
