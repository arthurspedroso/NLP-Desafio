import logging
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FuturesTimeout
from pathlib import Path

import fitz
import numpy as np
import pytesseract
from bs4 import BeautifulSoup
from curl_cffi import requests as cf_requests
from pdf2image import convert_from_path

fitz.TOOLS.mupdf_display_errors(False)

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
            blocos = page.get_text("blocks", sort=True)

            try:
                tabelas = page.find_tables()
                table_rects = [fitz.Rect(tab.bbox) for tab in tabelas]
            except Exception:
                tabelas = []
                table_rects = []

            textos_limpos = []
            for b in blocos:
                if b[6] != 0:
                    continue
                bloco_rect = fitz.Rect(b[:4])
                if any(bloco_rect.intersects(tr) for tr in table_rects):
                    continue
                texto_bloco = b[4].replace("\n", " ").strip()
                texto_bloco = " ".join(texto_bloco.split())
                if texto_bloco:
                    textos_limpos.append(texto_bloco)

            texto_pagina = "\n\n".join(textos_limpos)
            partes.append(texto_pagina)

            for tab in tabelas:
                try:
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
    nome = caminho.name
    try:
        imagens = convert_from_path(str(caminho), dpi=150)
    except Exception as e:
        logger.warning("pdf2image falhou em %s: %s", nome, e)
        return None

    n_pags = len(imagens)
    partes = []
    inicio = time.time()
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        for i, img in enumerate(imagens, 1):
            if time.time() - inicio > 600:
                logger.warning("tesseract timeout total em %s (pág %d/%d)", nome, i, n_pags)
                break

            arr = np.array(img.convert("L"))
            if (arr > 240).mean() > 0.99:
                continue

            fut = pool.submit(pytesseract.image_to_string, img, lang="por", config="--oem 1 --psm 6")
            try:
                t = fut.result(timeout=60)
                partes.append(t)
            except _FuturesTimeout:
                logger.warning("tesseract timeout página %d/%d de %s", i, n_pags, nome)
            except Exception as e:
                logger.warning("tesseract falhou página %d/%d de %s: %s", i, n_pags, nome, e)

            if i % 10 == 0:
                logger.info("tesseract %s: %d/%d páginas (%.0fs)", nome, i, n_pags, time.time() - inicio)
    finally:
        pool.shutdown(wait=False)

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

    t_inicio = time.time()
    try:
        t0 = time.time()
        ok = _baixar(url, caminho)
        logger.info("download %s: %.1fs", url, time.time() - t0)
        if not ok:
            return _aplicar_fallback_ementa(resultado, registro, f"falha no download: {url}")

        if tipo == "pdf":
            t0 = time.time()
            pymu = _extrair_pdf_pymupdf(caminho)
            dt_pymu = time.time() - t0
            if pymu:
                texto, fonte = pymu
                logger.info("pymupdf %s: %.1fs, %d chars", url, dt_pymu, len(texto))
                resultado["texto_bruto"] = texto
                resultado["fonte"] = fonte
                logger.info("total %s: %.1fs (%s)", url, time.time() - t_inicio, fonte)
                return resultado

            logger.info("pymupdf sem texto %s: %.1fs", url, dt_pymu)
            t0 = time.time()
            tess = _extrair_pdf_tesseract(caminho)
            dt_tess = time.time() - t0
            if tess:
                logger.info("tesseract %s: %.1fs, %d chars", url, dt_tess, len(tess))
                resultado["texto_bruto"] = tess
                resultado["fonte"] = "tesseract"
                logger.info("total %s: %.1fs (tesseract)", url, time.time() - t_inicio)
                return resultado

            logger.info("tesseract sem texto %s: %.1fs", url, dt_tess)
            return _aplicar_fallback_ementa(resultado, registro, "pymupdf e tesseract falharam")

        if tipo in ("html", "htm"):
            texto = _extrair_html(caminho)
            if texto:
                resultado["texto_bruto"] = texto
                resultado["fonte"] = "html"
                logger.info("total %s: %.1fs (html)", url, time.time() - t_inicio)
                return resultado
            return _aplicar_fallback_ementa(resultado, registro, "parse html falhou")

        return _aplicar_fallback_ementa(resultado, registro, f"tipo inesperado: {tipo}")

    finally:
        if caminho.exists():
            try:
                caminho.unlink()
            except Exception:
                pass
