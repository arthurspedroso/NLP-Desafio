import logging
import tempfile
import time
from pathlib import Path

import fitz
import pytesseract
from bs4 import BeautifulSoup
from curl_cffi import requests as cf_requests
from pdf2image import convert_from_path

logger = logging.getLogger(__name__)

pytesseract.pytesseract.tesseract_cmd = "/usr/bin/tesseract"

TMP_DIR = Path("/tmp")
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Referer": "https://www2.aneel.gov.br/",
    "Accept": "application/pdf,text/html,*/*",
}


def _baixar(url: str, destino: Path) -> bool:
    url = url.replace("http://", "https://")
    for tentativa in range(3):
        try:
            r = cf_requests.get(
                url,
                headers=HEADERS,
                impersonate="chrome120",
                timeout=60,
            )
            if r.status_code == 200 and r.content:
                destino.write_bytes(r.content)
                return True
            logger.warning("status %s em %s (tentativa %d)", r.status_code, url, tentativa + 1)
        except Exception as e:
            logger.warning("erro baixando %s: %s (tentativa %d)", url, e, tentativa + 1)
        time.sleep(2 ** tentativa)
    return False


def _texto_parece_ok(texto: str, num_paginas: int) -> bool:
    if not texto or not texto.strip():
        return False
    chars_por_pagina = len(texto) / max(num_paginas, 1)
    alfa = sum(c.isalpha() for c in texto)
    return chars_por_pagina > 100 and alfa / max(len(texto), 1) > 0.4


def _extrair_pdf_pymupdf(caminho: Path) -> tuple[str, str] | None:
    try:
        doc = fitz.open(str(caminho))
    except Exception as e:
        logger.warning("pymupdf falhou abrindo %s: %s", caminho, e)
        return None

    partes = []
    tem_tabela = False

    try:
        for page in doc:
            texto_pagina = page.get_text("text") or ""
            partes.append(texto_pagina)

            try:
                tabelas = page.find_tables()
                for tab in tabelas:
                    md = tab.to_markdown()
                    if md.strip():
                        partes.append("\n" + md + "\n")
                        tem_tabela = True
            except Exception:
                pass

        texto = "\n".join(partes)
        num_paginas = len(doc)
    finally:
        doc.close()

    if not _texto_parece_ok(texto, num_paginas):
        return None

    fonte = "pymupdf_tabelas" if tem_tabela else "pymupdf"
    return texto, fonte


def _extrair_pdf_tesseract(caminho: Path) -> str | None:
    try:
        imagens = convert_from_path(str(caminho), dpi=200)
    except Exception as e:
        logger.warning("pdf2image falhou em %s: %s", caminho, e)
        return None

    partes = []
    for img in imagens:
        try:
            t = pytesseract.image_to_string(img, lang="por", config="--oem 1 --psm 6")
            partes.append(t)
        except Exception as e:
            logger.warning("tesseract falhou numa página de %s: %s", caminho, e)

    texto = "\n".join(partes)
    return texto if texto.strip() else None


def _extrair_html(caminho: Path) -> str | None:
    try:
        raw = caminho.read_bytes()
    except Exception:
        return None

    for encoding in ("utf-8", "latin-1", "iso-8859-1"):
        try:
            conteudo = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        return None

    sopa = BeautifulSoup(conteudo, "html.parser")
    for tag in sopa(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    texto = sopa.get_text(separator="\n", strip=True)
    return texto if texto.strip() else None


def _resultado_base(registro: dict) -> dict:
    return {
        "titulo":       registro.get("titulo"),
        "autor":        registro.get("autor"),
        "assunto":      registro.get("assunto"),
        "situacao":     registro.get("situacao"),
        "data_pub":     registro.get("data_pub"),
        "url_pdf":      registro.get("url_pdf"),
        "tipo_arquivo": registro.get("tipo_arquivo"),
        "texto_bruto":  None,
        "fonte":        "erro",
        "erro":         None,
    }


def _aplicar_fallback_ementa(resultado: dict, registro: dict, motivo: str) -> dict:
    ementa = registro.get("ementa")
    if ementa:
        resultado["texto_bruto"] = ementa
        resultado["fonte"] = "ementa_fallback"
        resultado["erro"] = motivo
    else:
        resultado["fonte"] = "erro"
        resultado["erro"] = motivo
    return resultado


def extrair(registro: dict) -> dict:
    resultado = _resultado_base(registro)
    tipo = registro.get("tipo_arquivo", "desconhecido")

    if tipo not in ("pdf", "html", "htm"):
        ementa = registro.get("ementa")
        if ementa:
            resultado["texto_bruto"] = ementa
            resultado["fonte"] = "ementa_direta"
            return resultado
        resultado["erro"] = f"tipo não suportado sem ementa: {tipo}"
        return resultado

    url = registro["url_pdf"]
    sufixo = f".{tipo}"

    with tempfile.NamedTemporaryFile(
        dir=TMP_DIR, suffix=sufixo, delete=False
    ) as tmp:
        caminho = Path(tmp.name)

    try:
        if not _baixar(url, caminho):
            return _aplicar_fallback_ementa(resultado, registro, f"falha no download: {url}")

        if tipo == "pdf":
            pymu = _extrair_pdf_pymupdf(caminho)
            if pymu:
                texto, fonte = pymu
                resultado["texto_bruto"] = texto
                resultado["fonte"] = fonte
                return resultado

            tess = _extrair_pdf_tesseract(caminho)
            if tess:
                resultado["texto_bruto"] = tess
                resultado["fonte"] = "tesseract"
                return resultado

            return _aplicar_fallback_ementa(resultado, registro, "pymupdf e tesseract falharam")

        if tipo in ("html", "htm"):
            texto = _extrair_html(caminho)
            if texto:
                resultado["texto_bruto"] = texto
                resultado["fonte"] = "html"
                return resultado
            return _aplicar_fallback_ementa(resultado, registro, "parse html falhou")

        return _aplicar_fallback_ementa(resultado, registro, f"tipo inesperado: {tipo}")

    finally:
        if caminho.exists():
            try:
                caminho.unlink()
            except Exception:
                pass
