"""Dinamik RAG + Şirket Misyonu Prompt İnşa Modülü (src/generator/prompt_builder.py).

Bu modülün amacı:
1. Kazınan şirket web sitesi metnini (`company_context` — Hakkımızda / Kariyer / Vizyon),
   ChromaDB RAG vektör veritabanından çekilen en alakalı 3–4 aday projesini (`rag_results`)
   ve çift dilli (`TR` / `EN`) bağlantı şablonlarını birleştirerek Google Gemini LLM için
   yapılandırılmış bir `BuiltPrompt` (Sistem Talimatı + Kullanıcı Promptu + Hazır Yedek Şablon)
   oluşturmak.
2. **Boş Şirket Metni Koruması (Faz 10 Kritik Kabul Kriteri):**
   Gerçek dünyada bazı şirketlerin web sitesinde neredeyse hiç metin olmayabilir (sadece görsel
   veya tek bir iletişim sayfası bulunabilir). Şirketin "Hakkımızda" metni boş (`""`) gelse bile
   prompt ASLA bozulmaz; hedef dile göre genel ama profesyonel bir **Robotik, Otonom Sistemler,
   ROS 2, SLAM ve Yapay Zekâ Ar-Ge** vurgusuna otomatik geçiş yapar.
3. **Token Taşması (Context Overflow) Koruması:**
   Kazınan şirket sayfası on binlerce karakterlik gürültülü metin içeriyorsa, bunu
   `max_company_context_chars` sınırında güvenli şekilde kırparak LLM gecikmesini ve kota aşımını önler.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from src.generator.template_manager import (
    DEFAULT_PROJECT_HIGHLIGHTS_EN,
    DEFAULT_PROJECT_HIGHLIGHTS_TR,
    CandidateLinks,
    RenderedEmailTemplate,
    format_links_block,
    normalize_template_language,
    render_fallback_email,
)
from src.rag.vector_store import SearchResult
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Şirketin Hakkımızda / Kariyer metni boş geldiğinde kullanılacak varsayılan bağlam (TR ve EN)
DEFAULT_COMPANY_MISSION_FALLBACK_TR: str = (
    "Şirket hakkında detaylı web metni bulunamadı. Şirketin Türkiye'de robotik, otonom sistemler, "
    "ROS 2, SLAM, bilgisayarlı görü, gömülü sistemler ve yapay zekâ Ar-Ge alanında faaliyet "
    "gösteren bir teknoloji/mühendislik firması olduğu varsayılarak profesyonel bir robotik ve "
    "otonom sistemler mühendisliği vurgusu yapılmalıdır."
)

DEFAULT_COMPANY_MISSION_FALLBACK_EN: str = (
    "Detailed company description was not available on the webpage. Emphasize a strong, "
    "professional focus on robotics, autonomous systems, ROS 2, 3D SLAM, computer vision, "
    "sensor fusion, and embedded AI R&D engineering."
)


class VectorStoreQueryProtocol(Protocol):
    """`VectorStoreManager` ile gevşek bağlı (duck-typing) protokol arayüzü."""

    def query(
        self,
        query_text: str,
        top_k: int = 4,
        language: str | None = None,
    ) -> list[SearchResult]: ...


@dataclass(frozen=True)
class BuiltPrompt:
    """LLM istemcisine (Faz 11) gönderilecek olan tam yapılandırılmış prompt ve güvenlik verileri."""

    company_name: str
    language: str                                    # 'TR' veya 'EN'
    subject: str                                     # Önerilen/varsayılan e-posta konu başlığı
    system_instruction: str                          # LLM rol ve katı kısıtlama talimatları
    user_prompt: str                                 # Şirket misyonu + RAG parçaları + zorunlu linkler
    required_links: list[str] = field(default_factory=list)  # Çıktıda yer alması gereken URL'ler
    company_context_used: str = ""                   # Kırpılmış veya varsayılana dönmüş şirket metni
    rag_context_used: str = ""                       # Prompt içine gömülen RAG proje özetleri
    fallback_email: RenderedEmailTemplate = field(   # LLM hata verirse anında kullanılacak statik şablon
        default_factory=lambda: RenderedEmailTemplate(subject="", body="", language="EN")
    )


def _truncate_text_safely(text: str, max_chars: int) -> str:
    """Metni kelime ortasından bölmeden `max_chars` sınırında güvenli biçimde kırpar."""
    cleaned = " ".join(text.split())
    if len(cleaned) <= max_chars:
        return cleaned

    sliced = cleaned[:max_chars]
    last_space = sliced.rfind(" ")
    if last_space > max_chars // 2:
        sliced = sliced[:last_space]
    return sliced.rstrip() + "..."


def _format_rag_chunks_for_prompt(
    rag_results: Sequence[SearchResult] | None,
    language: str,
) -> str:
    """ChromaDB'den dönen `SearchResult` parçalarını numaralı maddeler halinde birleştirir.
    Eğer RAG sonucu boşsa adayın varsayılan teknik proje özetini döndürür.
    """
    if rag_results:
        lines: list[str] = []
        for idx, item in enumerate(rag_results, start=1):
            chunk_text = " ".join(item.text.split()).strip()
            if chunk_text:
                lines.append(f"{idx}. {chunk_text}")
        if lines:
            return "\n".join(lines)

    if language == "TR":
        return f"1. {DEFAULT_PROJECT_HIGHLIGHTS_TR}"
    return f"1. {DEFAULT_PROJECT_HIGHLIGHTS_EN}"


def build_application_prompt(
    company_name: str,
    company_context: str,
    language: str,
    links: CandidateLinks,
    rag_results: Sequence[SearchResult] | None = None,
    min_word_count: int = 60,
    max_word_count: int = 320,
    max_company_context_chars: int = 2000,
) -> BuiltPrompt:
    """Şirket misyonunu, RAG yetkinliklerini ve çift dilli link kurallarını birleştirerek
    Gemini LLM için tam bir `BuiltPrompt` inşa eder.

    Args:
        company_name: Hedef şirketin adı.
        company_context: Kazınan şirket 'Hakkımızda / Kariyer' metni (boş olabilir).
        language: Hedef dil (`'TR'` veya `'EN'`).
        links: Adayın CV, Portfolyo, GitHub ve LinkedIn URL bağlantıları.
        rag_results: ChromaDB'den çekilen en alakalı CV/Portfolyo parçaları.
        min_word_count: Üretilecek e-posta gövdesinin minimum kelime sınırı.
        max_word_count: Üretilecek e-posta gövdesinin maksimum kelime sınırı.
        max_company_context_chars: Şirket metni için maksimum karakter sınırı.

    Returns:
        LLM çağrısına ve olası fallback senaryosuna hazır `BuiltPrompt` nesnesi.
    """
    lang = normalize_template_language(language)
    clean_company = (company_name or "").strip() or (
        "Hedef Teknoloji Şirketi" if lang == "TR" else "Target Robotics Company"
    )

    # 1. Şirket metnini kontrol et; boşsa genel Robotik/Otonom Sistemler vurgusuna dön!
    raw_context = (company_context or "").strip()
    if raw_context:
        effective_company_context = _truncate_text_safely(raw_context, max_company_context_chars)
    else:
        logger.debug(
            "Şirket (%s) için Hakkımızda metni boş geldi; genel robotik/otonom sistemler vurgusu kullanılıyor.",
            clean_company,
        )
        effective_company_context = (
            DEFAULT_COMPANY_MISSION_FALLBACK_TR
            if lang == "TR"
            else DEFAULT_COMPANY_MISSION_FALLBACK_EN
        )

    # 2. RAG parçalarını biçimlendir ve yedek (fallback) e-postayı şimdiden hazırla
    rag_context_str = _format_rag_chunks_for_prompt(rag_results, lang)
    fallback_template = render_fallback_email(
        company_name=clean_company,
        language=lang,
        links=links,
        project_highlights=rag_context_str if rag_results else "",
    )

    # 3. Zorunlu bağlantı listesini ve metin bloğunu oluştur
    links_block = format_links_block(links, lang)
    required_urls: list[str] = []
    primary_url = links.primary_showcase_url(lang)
    if primary_url:
        required_urls.append(primary_url)
    cv_url = links.get_cv_url(lang)
    if cv_url and cv_url not in required_urls:
        required_urls.append(cv_url)

    # 4. Hedef dile göre Sistem Talimatı (System Instruction) ve Kullanıcı Promptunu oluştur
    if lang == "TR":
        system_instruction = (
            f"Sen Bilgisayar Mühendisi (Robotik, Otonom Sistemler ve Yapay Zekâ uzmanı) "
            f"{links.sender_name} adına profesyonel iş ve Ar-Ge başvurusu yazan kıdemli bir "
            f"kariyer asistanısın.\n"
            f"KESİN KURALLAR:\n"
            f"1. E-postayı tamamen TÜRKÇE, akıcı, samimi ve mühendislik odaklı bir dille yaz.\n"
            f"2. Kelime sayısı {min_word_count} ile {max_word_count} kelime arasında olmalıdır.\n"
            f"3. ASLA fiziksel dosya eki (attachment) varmış gibi 'ekteki dosyada', 'özgeçmişim ektedir', "
            f"'ilişikte sunulmuştur' gibi ifadeler KULLANMA!\n"
            f"4. Adayın aşağıda verilen CV ve Portfolyo URL bağlantılarını HİÇ DEĞİŞTİRMEDEN "
            f"(kısaltmadan ve uydurma link eklemeden) e-posta gövdesine yerleştir.\n"
            f"5. Yalnızca doğrudan gönderilebilecek e-posta gövdesini (body) döndür."
        )

        user_prompt = (
            f"HEDEF ŞİRKET ADI: {clean_company}\n\n"
            f"ŞİRKET HAKKINDA / FAALİYET ALANI:\n{effective_company_context}\n\n"
            f"ADAYIN EN ALAKALI PROJE VE YETKİNLİKLERİ (RAG VERİ TABANI):\n{rag_context_str}\n\n"
            f"E-POSTAYA EKLENMESİ ZORUNLU BAĞLANTILAR (URL):\n{links_block}\n\n"
            f"Lütfen {clean_company} ekibine hitaben, şirketin vizyonu ile adayın ROS 2, SLAM, "
            f"otonom sistemler ve yapay zekâ projelerini eşleştiren, {min_word_count}-{max_word_count} "
            f"kelime aralığında profesyonel başvuru e-postası gövdesini oluştur."
        )
    else:
        system_instruction = (
            f"You are an expert technical career assistant writing a tailored engineering job "
            f"application email on behalf of {links.sender_name} (Computer Engineer specializing "
            f"in Robotics, Autonomous Systems, ROS 2, SLAM, and Embedded AI).\n"
            f"STRICT RULES:\n"
            f"1. Write the email entirely in professional, concise ENGLISH.\n"
            f"2. Keep the total length strictly between {min_word_count} and {max_word_count} words.\n"
            f"3. NEVER mention file attachments (do NOT use phrases like 'attached CV', "
            f"'please find attached', or 'enclosed resume').\n"
            f"4. Include the exact Resume/Portfolio URLs provided below without modifying or "
            f"hallucinating any links.\n"
            f"5. Output ONLY the ready-to-send email body text."
        )

        user_prompt = (
            f"TARGET COMPANY NAME: {clean_company}\n\n"
            f"COMPANY MISSION / CONTEXT:\n{effective_company_context}\n\n"
            f"CANDIDATE'S MOST RELEVANT PROJECTS & SKILLS (FROM RAG):\n{rag_context_str}\n\n"
            f"MANDATORY CANDIDATE LINKS TO INCLUDE:\n{links_block}\n\n"
            f"Write a compelling {min_word_count}-{max_word_count} word engineering application "
            f"email body addressed to the {clean_company} team that connects their work with the "
            f"candidate's robotics, ROS 2, SLAM, and autonomous systems projects."
        )

    return BuiltPrompt(
        company_name=clean_company,
        language=lang,
        subject=fallback_template.subject,
        system_instruction=system_instruction,
        user_prompt=user_prompt,
        required_links=required_urls,
        company_context_used=effective_company_context,
        rag_context_used=rag_context_str,
        fallback_email=fallback_template,
    )


class PromptBuilder:
    """RAG vektör veritabanı (`VectorStoreManager`) ve aday bağlantılarıyla (`CandidateLinks`)
    entegre çalışan nesne yönelimli prompt inşa sınıfı.
    """

    def __init__(
        self,
        links: CandidateLinks,
        vector_store: VectorStoreQueryProtocol | None = None,
        top_k: int = 4,
        min_word_count: int = 60,
        max_word_count: int = 320,
        max_company_context_chars: int = 2000,
    ) -> None:
        self.links = links
        self.vector_store = vector_store
        self.top_k = top_k
        self.min_word_count = min_word_count
        self.max_word_count = max_word_count
        self.max_company_context_chars = max_company_context_chars

    def build_for_company(
        self,
        company_name: str,
        company_context: str = "",
        language: str = "TR",
        rag_results: Sequence[SearchResult] | None = None,
    ) -> BuiltPrompt:
        """Şirket için gerekirse RAG veritabanını otomatik sorgulayarak `BuiltPrompt` üretir."""
        lang = normalize_template_language(language)
        resolved_rag = list(rag_results) if rag_results is not None else []

        # Eğer dışarıdan hazır RAG parçaları verilmemişse ve bir vector_store bağlıysa otomatik sorgula
        if not resolved_rag and self.vector_store is not None:
            query_seed = (
                company_context.strip()[:500]
                if company_context and company_context.strip()
                else "ROS 2 SLAM autonomous UAV AUV robotics computer vision"
            )
            try:
                resolved_rag = self.vector_store.query(
                    query_text=query_seed,
                    top_k=self.top_k,
                )
            except Exception as exc:
                logger.warning("RAG sorgusu sırasında hata oluştu, varsayılan yetkinlik özeti kullanılacak: %s", exc)
                resolved_rag = []

        return build_application_prompt(
            company_name=company_name,
            company_context=company_context,
            language=lang,
            links=self.links,
            rag_results=resolved_rag,
            min_word_count=self.min_word_count,
            max_word_count=self.max_word_count,
            max_company_context_chars=self.max_company_context_chars,
        )
