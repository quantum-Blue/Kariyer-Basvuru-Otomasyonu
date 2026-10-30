"""Çift Dilli (`TR` / `EN`) Başvuru Şablonu ve Bağlantı (Link) Yöneticisi (src/generator/template_manager.py).

Bu modülün amacı:
1. **Sıfır Dosya Eki (No-Attachment) ve Sıfır Spam Garantisi:**
   Soğuk e-postalarda (cold email) doğrudan PDF dosyası eklemek (`attachment`), kurumsal
   güvenlik duvarları (Mimecast, Proofpoint, Gmail Spam Filter) tarafından yüksek olasılıkla
   karantinaya alınır veya reddedilir. Bu modül dosya eki yerine adayın Türkçe/İngilizce CV,
   Portfolyo, GitHub ve LinkedIn bağlantılarını (URL) metin içerisine temiz bir blok halinde
   yerleştirir.
2. **Çift Dilli (`TR` / `EN`) Akıllı Şablon Seçimi:**
   - Türk şirketler (`language="TR"`) için Türkçe konu başlığı (`subject`), Türkçe hitap ve
     Türkçe CV bağlantısı (`cv_url_tr`) kullanılır.
   - Yabancı şirketler (`language="EN"`) için İngilizce konu başlığı, İngilizce hitap ve
     İngilizce CV bağlantısı (`cv_url_en`) kullanılır.
3. **Yanıltıcı Dosya Eki İfadelerini Yakalama (`contains_forbidden_attachment_phrase`):**
   LLM bazen alışkanlıkla *"Özgeçmişim ekte yer almaktadır"* veya *"Please find my resume attached"*
   gibi cümleler kurabilir. Oysa e-postada fiziksel bir dosya eki yoktur! Bu fonksiyon hem Türkçe
   hem İngilizce "ekte / attached" ifadelerini tespit ederek Faz 11'deki çıktı doğrulayıcıya
   (output validator) güvenlik bariyeri sağlar.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from src.config.settings import EnvConfig
from src.utils.logger import get_logger

logger = get_logger(__name__)

# E-postada fiziksel dosya eki (attachment) varmış izlenimi veren yasaklı ifadeler (Regex)
_FORBIDDEN_ATTACHMENT_REGEXES: tuple[re.Pattern[str], ...] = (
    # Türkçe "ekte / ekteki / ektedir / ilişikte" kalıpları
    re.compile(r"\b(?:ekte|ekteki|ektedir|ilişikte|ilişikteki)\b", re.IGNORECASE),
    re.compile(r"\bek\s+olarak\s+sunul", re.IGNORECASE),
    # İngilizce "attached / attachment / enclosed" kalıpları
    re.compile(r"\b(?:attached|attachment|attachments|enclosed)\b", re.IGNORECASE),
    re.compile(r"\bfind\s+my\s+(?:cv|resume|portfolio)\s+attached\b", re.IGNORECASE),
)

# Şirketin Hakkımızda metni veya RAG özeti boş geldiğinde kullanılacak varsayılan teknik vurgu
DEFAULT_PROJECT_HIGHLIGHTS_TR: str = (
    "ROS 2, 3B SLAM (Fast-LIO2, RTAB-MAP, LIO-SAM), GNSS-bağımsız otonom İHA/AUV navigasyonu, "
    "sensör füzyonu ve derin öğrenme tabanlı bilgisayarlı görü (YOLOv12, RT-DETR) projeleri"
)

DEFAULT_PROJECT_HIGHLIGHTS_EN: str = (
    "ROS 2, 3D LiDAR/Visual SLAM (Fast-LIO2, RTAB-MAP, LIO-SAM), GPS-denied autonomous UAV/AUV "
    "navigation, multi-sensor fusion, and deep learning-based computer vision (YOLOv12, RT-DETR)"
)


@dataclass(frozen=True)
class CandidateLinks:
    """Adayın e-posta imzası ve gövdesinde paylaşılacak olan çevrimiçi belge/profil bağlantıları."""

    sender_name: str = "Muhammed Enes BAL"
    cv_url_tr: str = ""
    cv_url_en: str = ""
    portfolio_url: str = ""
    github_url: str = ""
    linkedin_url: str = ""

    @classmethod
    def from_env_config(cls, env_cfg: EnvConfig) -> CandidateLinks:
        """`src/config/settings.py` içindeki `EnvConfig` nesnesinden `CandidateLinks` üretir."""
        return cls(
            sender_name=env_cfg.sender_name or "Muhammed Enes BAL",
            cv_url_tr=env_cfg.cv_url_tr,
            cv_url_en=env_cfg.cv_url_en,
            portfolio_url=env_cfg.portfolio_url,
            github_url=env_cfg.github_url,
            linkedin_url=env_cfg.linkedin_url,
        )

    def get_cv_url(self, language: str) -> str:
        """Hedef dile (`TR` veya `EN`) uygun CV bağlantısını döndürür.
        İlgili dilin CV linki boşsa diğer dilin CV linkine, o da yoksa portfolyo linkine döner.
        """
        lang = normalize_template_language(language)
        if lang == "TR":
            return self.cv_url_tr.strip() or self.cv_url_en.strip() or self.portfolio_url.strip()
        return self.cv_url_en.strip() or self.cv_url_tr.strip() or self.portfolio_url.strip()

    def primary_showcase_url(self, language: str = "TR") -> str:
        """Doğrulayıcının (validator) e-posta gövdesinde kesinlikle aradığı birincil portfolyo/CV adresi."""
        return (
            self.portfolio_url.strip()
            or self.get_cv_url(language)
            or self.github_url.strip()
            or self.linkedin_url.strip()
        )


@dataclass(frozen=True)
class RenderedEmailTemplate:
    """Üretilen veya yedek (fallback) şablondan doldurulan hazır e-posta verisi."""

    subject: str       # E-posta konu başlığı
    body: str          # E-posta düz metin (plain-text) gövdesi
    language: str      # 'TR' veya 'EN'


def normalize_template_language(language: str | None) -> str:
    """Dil kodunu standart `'TR'` veya `'EN'` değerine normalize eder.
    Tanınmayan veya boş değerlerde uluslararası standart olarak `'EN'` döndürür.
    """
    if not isinstance(language, str):
        return "EN"
    cleaned = language.strip().upper()
    if cleaned in ("TR", "TUR", "TURKISH", "TÜRKÇE", "TURKCE"):
        return "TR"
    return "EN"


def contains_forbidden_attachment_phrase(text: str) -> bool:
    """Verilen e-posta metninde fiziksel bir dosya eki varmış gibi yanıltıcı ifade
    ('ekteki dosyada', 'ektedir', 'please find attached', 'attached CV' vb.) olup olmadığını denetler.
    """
    if not isinstance(text, str) or not text.strip():
        return False

    normalized = text.replace("İ", "i").replace("I", "ı").lower().replace("i\u0307", "i")
    return any(regex.search(normalized) for regex in _FORBIDDEN_ATTACHMENT_REGEXES)


def format_links_block(links: CandidateLinks, language: str) -> str:
    """Hedef dile göre e-postanın alt kısmına eklenecek temiz CV, Portfolyo, GitHub ve LinkedIn
    bağlantı satırlarını oluşturur. Yalnızca dolu olan bağlantıları listeye dahil eder.
    """
    lang = normalize_template_language(language)
    lines: list[str] = []

    cv_link = links.get_cv_url(lang)
    portfolio_link = links.portfolio_url.strip()
    github_link = links.github_url.strip()
    linkedin_link = links.linkedin_url.strip()

    if lang == "TR":
        if cv_link:
            lines.append(f"- Özgeçmiş (CV): {cv_link}")
        if portfolio_link and portfolio_link != cv_link:
            lines.append(f"- Teknik Portfolyo: {portfolio_link}")
        if github_link:
            lines.append(f"- GitHub: {github_link}")
        if linkedin_link:
            lines.append(f"- LinkedIn: {linkedin_link}")
    else:
        if cv_link:
            lines.append(f"- Resume (CV): {cv_link}")
        if portfolio_link and portfolio_link != cv_link:
            lines.append(f"- Technical Portfolio: {portfolio_link}")
        if github_link:
            lines.append(f"- GitHub: {github_link}")
        if linkedin_link:
            lines.append(f"- LinkedIn: {linkedin_link}")

    return "\n".join(lines)


def render_fallback_email(
    company_name: str,
    language: str,
    links: CandidateLinks,
    project_highlights: str = "",
) -> RenderedEmailTemplate:
    """LLM API kullanılamadığında veya çıktı doğrulamadan geçemediğinde devreye girecek olan
    yüksek kaliteli, çift dilli (`TR` / `EN`) statik başvuru e-postasını üretir.

    Özellikler:
    - Asla dosya eki (`attachment`) ifadesi içermez; tüm belgeleri URL bağlantısı olarak sunar.
    - Şirket adı boş gelse bile (`""`) cümle akışı bozulmaz; uygun genel hitaba döner.
    - `project_highlights` boş gelse bile adayın çekirdek Robotik/SLAM/Otonom Sistemler
      yetkinliklerini profesyonelce özetler.
    """
    lang = normalize_template_language(language)
    sender = (links.sender_name or "Muhammed Enes BAL").strip()
    clean_company = (company_name or "").strip()
    links_section = format_links_block(links, lang)

    if lang == "TR":
        target_company = clean_company if clean_company else "Ar-Ge ve Mühendislik Ekibiniz"
        highlights = (
            project_highlights.strip()
            if project_highlights and project_highlights.strip()
            else DEFAULT_PROJECT_HIGHLIGHTS_TR
        )
        subject = (
            f"Robotik ve Otonom Sistemler Mühendisliği Başvurusu - {sender} ({clean_company})"
            if clean_company
            else f"Robotik ve Otonom Sistemler Mühendisliği Başvurusu - {sender}"
        )

        body_parts = [
            f"Sayın {target_company} İnsan Kaynakları ve Mühendislik Ekibi,",
            (
                f"Bilgisayar Mühendisliği mezunu bir Robotik, Otonom Sistemler ve Yapay Zekâ "
                f"mühendisi olarak {target_company} bünyesinde yürüttüğünüz yüksek teknoloji ve Ar-Ge "
                f"çalışmalarını yakından takip ediyorum."
            ),
            (
                f"Akademik ve proje geçmişimde özellikle {highlights} üzerine odaklandım. "
                f"Geliştirdiğim otonom navigasyon, 3B haritalama ve gerçek zamanlı algılama "
                f"sistemlerinden edindiğim pratik mühendislik deneyiminin ekibinizin projelerine "
                f"doğrudan katkı sağlayacağına inanıyorum."
            ),
            (
                "Güvenlik filtrelerine takılmaması adına özgeçmiş ve teknik portfolyo belgelerimi "
                "aşağıdaki bağlantılar üzerinden incelemenize sunuyorum:"
            ),
        ]
        if links_section:
            body_parts.append(links_section)

        body_parts.append(
            f"Değerlendirmeniz ve ayırdığınız vakit için teşekkür ederim.\n\n"
            f"Saygılarımla,\n{sender}\nBilgisayar Mühendisi | Robotik & Otonom Sistemler"
        )
        return RenderedEmailTemplate(
            subject=subject,
            body="\n\n".join(body_parts),
            language="TR",
        )

    # İngilizce (EN) Şablon
    target_company_en = clean_company if clean_company else "your Engineering and R&D Team"
    greeting_en = f"Dear {clean_company} Hiring and Engineering Team," if clean_company else "Dear Hiring and Engineering Team,"
    highlights_en = (
        project_highlights.strip()
        if project_highlights and project_highlights.strip()
        else DEFAULT_PROJECT_HIGHLIGHTS_EN
    )
    subject_en = (
        f"Robotics & Autonomous Systems Engineer Application - {sender} ({clean_company})"
        if clean_company
        else f"Robotics & Autonomous Systems Engineer Application - {sender}"
    )

    body_parts_en = [
        greeting_en,
        (
            f"As a Computer Engineering graduate specializing in Robotics, Autonomous Systems, "
            f"and Embedded AI, I have been closely following the robotics and autonomy "
            f"innovations developed by {target_company_en}."
        ),
        (
            f"Throughout my engineering projects—including Teknofest and TÜBİTAK research initiatives—"
            f"I have focused extensively on {highlights_en}. I am confident that my hands-on "
            f"experience in perception, state estimation, and autonomous control aligns strongly "
            f"with your engineering goals."
        ),
        (
            "You can review my resume, project details, and technical portfolio directly "
            "via the online links below:"
        ),
    ]
    if links_section:
        body_parts_en.append(links_section)

    body_parts_en.append(
        f"Thank you for your time and consideration.\n\n"
        f"Best regards,\n{sender}\nComputer Engineer | Robotics & Autonomous Systems"
    )
    return RenderedEmailTemplate(
        subject=subject_en,
        body="\n\n".join(body_parts_en),
        language="EN",
    )


class TemplateManager:
    """Aday bağlantılarını (`CandidateLinks`) tutan ve dile göre şablon/konu başlığı üreten yönetici sınıf."""

    def __init__(self, links: CandidateLinks) -> None:
        self.links = links

    def render_fallback(
        self,
        company_name: str,
        language: str = "TR",
        project_highlights: str = "",
    ) -> RenderedEmailTemplate:
        """Verilen şirket ve dil için statik yedek (fallback) başvuru e-postasını üretir."""
        return render_fallback_email(
            company_name=company_name,
            language=language,
            links=self.links,
            project_highlights=project_highlights,
        )

    def get_links_block(self, language: str = "TR") -> str:
        """Dile uygun CV / Portfolyo / GitHub / LinkedIn bağlantı bloğunu döndürür."""
        return format_links_block(self.links, language)
