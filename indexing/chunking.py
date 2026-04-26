import logging
import re
import sys
import hashlib
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor, as_completed

from sqlalchemy import text
from langchain_text_splitters import RecursiveCharacterTextSplitter
import tiktoken

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("chunking")

TIKTOKEN_MODEL = "cl100k_base"
CHUNK_SIZE_TOKENS = 256
CHUNK_OVERLAP_TOKENS = 25
MIN_SECAO_TOKENS = 30
MIN_CHUNK_TOKENS = 40
MAX_SECAO_TOKENS = 1024

_enc = tiktoken.get_encoding(TIKTOKEN_MODEL)

@lru_cache(maxsize=50000)
def _contar_tokens(texto: str) -> int:
    return len(_enc.encode(texto))

_CHARS_POR_TOKEN = 4.5
CHUNK_SIZE_CHARS = int(CHUNK_SIZE_TOKENS * _CHARS_POR_TOKEN)
CHUNK_OVERLAP_CHARS = int(CHUNK_OVERLAP_TOKENS * _CHARS_POR_TOKEN)

_RE_ARTIGO = re.compile(
    r"(?=(?:^|\n)\s*(?:Art(?:igo)?\.?\s*\d+[º°.]?))",
    re.IGNORECASE | re.MULTILINE,
)
_RE_SENTENCA = re.compile(r"(?<=[.!?;:])\s+(?=[A-ZÀ-Ú0-9])")

def buscar_documentos(engine):
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT id, titulo, texto_limpo
            FROM documents
            WHERE texto_limpo IS NOT NULL AND texto_limpo != ''
        """))
        return [
            {"id": row[0], "titulo": row[1], "texto_limpo": row[2]}
            for row in result
        ]

def limpar_texto(texto: str) -> str:
    if not texto:
        return ""
    texto = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", texto)
    texto = re.sub(r"(\w)-\s+(\w)", r"\1\2", texto)
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    texto = re.sub(r" {2,}", " ", texto)
    return texto.strip()

def _eh_tabela(texto: str) -> bool:
    linhas = texto.strip().split("\n")
    linhas_tabela = sum(1 for l in linhas if l.strip().startswith("|") and l.strip().endswith("|"))
    return linhas_tabela >= 2

def _dividir_por_artigos(texto: str):
    partes = _RE_ARTIGO.split(texto)
    partes = [p.strip() for p in partes if p.strip()]
    return partes if len(partes) >= 2 else []

def _dividir_por_paragrafos(texto: str):
    return [p.strip() for p in re.split(r"\n\s*\n", texto) if p.strip()]

def _dividir_por_sentencas(texto: str):
    return [p.strip() for p in _RE_SENTENCA.split(texto) if p.strip()]

def dividir_por_secoes(texto: str):
    secoes = _dividir_por_artigos(texto) or _dividir_por_paragrafos(texto)
    return [s for s in secoes if _contar_tokens(s) >= MIN_SECAO_TOKENS]

_splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE_CHARS,
    chunk_overlap=CHUNK_OVERLAP_CHARS,
    length_function=len,
    separators=["\n\n", "\n", ". ", "; ", ", ", " ", ""],
)

def gerar_chunks(secoes):
    chunks = []
    for secao in secoes:
        if _contar_tokens(secao) <= CHUNK_SIZE_TOKENS or _eh_tabela(secao):
            chunks.append(secao)
        else:
            chunks.extend(_splitter.split_text(secao))
    return chunks

def _hash_prefixo(texto: str) -> str:
    return hashlib.md5(texto[:300].encode("utf-8")).hexdigest()

def filtrar_qualidade(chunks):
    chunks_filtrados = [c for c in chunks if _contar_tokens(c) >= MIN_CHUNK_TOKENS]
    vistos = set()
    chunks_dedup = []
    for chunk in chunks_filtrados:
        h = _hash_prefixo(chunk)
        if h not in vistos:
            vistos.add(h)
            chunks_dedup.append(chunk)
    return chunks_dedup

def processar_doc(doc):
    doc_id = doc["id"]
    titulo = doc["titulo"]
    texto_limpo = limpar_texto(doc["texto_limpo"])
    secoes = dividir_por_secoes(texto_limpo)
    chunks = filtrar_qualidade(gerar_chunks(secoes))
    return doc_id, {"titulo": titulo, "chunks": chunks}

def processar_chunking(engine):
    documentos = buscar_documentos(engine)
    resultado = {}

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(processar_doc, doc) for doc in documentos]

        for i, future in enumerate(as_completed(futures), start=1):
            doc_id, info = future.result()
            if info["chunks"]:
                resultado[doc_id] = info

            if i % 100 == 0:
                logger.info(f"{i}/{len(documentos)} documentos processados")

    logger.info(f"Chunking concluído: {len(resultado)} docs")
    return resultado