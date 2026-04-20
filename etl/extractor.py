import requests
import pytesseract
from pathlib import Path
from pdf2image import convert_from_path
from docling.document_converter import DocumentConverter

TMP_DIR = Path("/tmp")
converter = DocumentConverter()


def _baixar_pdf(url: str, destino: Path) -> bool:
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        destino.write_bytes(response.content)
        return True
    except Exception:
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
