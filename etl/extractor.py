import os
import threading
import requests
import pytesseract
from curl_cffi import requests as cf_requests
from pathlib import Path
from pdf2image import convert_from_path
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions

TMP_DIR = Path("/tmp")
FLARESOLVERR_URL = os.getenv("FLARESOLVERR_URL", "http://localhost:8191/v1")

pytesseract.pytesseract.tesseract_cmd = "/usr/bin/tesseract"

_pipeline_options = PdfPipelineOptions()
_pipeline_options.do_ocr = False

converter = DocumentConverter(
    format_options={
        InputFormat.PDF: PdfFormatOption(pipeline_options=_pipeline_options)
    }
)

_cf_lock = threading.Lock()
_cf_cookies: dict = {}
_cf_user_agent: str = ""


def _renovar_cookies():
    global _cf_cookies, _cf_user_agent
    import uuid
    session_id = f"aneel_{uuid.uuid4().hex[:8]}"

    requests.post(FLARESOLVERR_URL, json={"cmd": "sessions.create", "session": session_id}, timeout=10)

    requests.post(FLARESOLVERR_URL, json={
        "cmd": "request.get",
        "url": "https://www2.aneel.gov.br/",
        "session": session_id,
        "maxTimeout": 60000,
    }, timeout=90)

    r = requests.post(FLARESOLVERR_URL, json={
        "cmd": "request.get",
        "url": "https://www2.aneel.gov.br/cedoc/dsp20163284.pdf",
        "session": session_id,
        "maxTimeout": 60000,
    }, timeout=90)
    r.raise_for_status()

    sol = r.json()["solution"]
    _cf_cookies = {c["name"]: c["value"] for c in sol["cookies"]}
    _cf_user_agent = sol["userAgent"]

    requests.post(FLARESOLVERR_URL, json={"cmd": "sessions.destroy", "session": session_id}, timeout=10)


def _obter_sessao() -> tuple[dict, str]:
    if not _cf_cookies:
        with _cf_lock:
            if not _cf_cookies:
                _renovar_cookies()
    return _cf_cookies, _cf_user_agent


def _baixar_pdf(url: str, destino: Path) -> bool:
    cookies, user_agent = _obter_sessao()
    for tentativa in range(2):
        try:
            r = cf_requests.get(
                url.replace("http://", "https://"),
                cookies=cookies,
                headers={"User-Agent": user_agent, "Referer": "https://www2.aneel.gov.br/"},
                impersonate="chrome120",
                timeout=30,
            )
            if r.status_code == 403 and tentativa == 0:
                with _cf_lock:
                    _renovar_cookies()
                cookies, user_agent = _cf_cookies, _cf_user_agent
                continue
            if r.status_code == 200 and r.content[:4] == b"%PDF":
                destino.write_bytes(r.content)
                return True
        except Exception:
            pass
    return False


def _extrair_docling(caminho: Path) -> str:
    result = converter.convert(str(caminho))
    return result.document.export_to_markdown()


def _extrair_tesseract(caminho: Path) -> str:
    imagens = convert_from_path(str(caminho))
    return "\n".join(
        pytesseract.image_to_string(img, lang="por") for img in imagens
    )


def extrair(registro: dict) -> dict:
    url = registro["url_pdf"]
    arquivo = registro.get("arquivo") or url.split("/")[-1]
    caminho = TMP_DIR / arquivo

    resultado = {**registro, "texto_bruto": None, "fonte": "erro", "erro": None}

    if not _baixar_pdf(url, caminho):
        resultado["erro"] = f"Falha no download: {url}"
        return resultado

    try:
        try:
            texto = _extrair_docling(caminho)
            if texto.strip():
                resultado["texto_bruto"] = texto
                resultado["fonte"] = "docling"
                return resultado
        except Exception:
            pass

        try:
            texto = _extrair_tesseract(caminho)
            if texto.strip():
                resultado["texto_bruto"] = texto
                resultado["fonte"] = "tesseract"
                return resultado
        except Exception as e:
            resultado["erro"] = str(e)

        return resultado

    finally:
        if caminho.exists():
            caminho.unlink()
