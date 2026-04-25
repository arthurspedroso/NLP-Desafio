"""
Módulo de Chunking para o pipeline RAG.

Responsável por:
  1. Buscar documentos do Postgres (texto_limpo)
  2. Aplicar limpeza residual no texto
  3. Dividir em seções semânticas (parágrafos)
  4. Aplicar chunking com controle de tamanho e overlap
  5. Filtrar chunks de baixa qualidade

Não implementa embeddings, banco vetorial, busca ou LLM.
"""

import logging
import re
import sys

from sqlalchemy import text
from langchain_text_splitters import RecursiveCharacterTextSplitter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("chunking")

# ---------------------------------------------------------------------------
# Configurações do splitter
# ---------------------------------------------------------------------------
CHUNK_SIZE = 500
CHUNK_OVERLAP = 100
MIN_SECAO_CHARS = 50
MIN_CHUNK_CHARS = 100


# ---------------------------------------------------------------------------
# 1. Buscar documentos
# ---------------------------------------------------------------------------
def buscar_documentos(engine):
    """
    Busca documentos com texto_limpo preenchido no banco de dados.

    Utiliza o campo texto_limpo, que já foi processado pela etapa de
    limpeza do ETL (clean_text.py).

    Retorna uma lista de dicts com as chaves: id, titulo, texto_limpo.
    """
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT id, titulo, texto_limpo
            FROM documents
            WHERE texto_limpo IS NOT NULL AND texto_limpo != ''
        """))
        documentos = [
            {"id": row[0], "titulo": row[1], "texto_limpo": row[2]}
            for row in result
        ]
    logger.info("Documentos encontrados com texto_limpo: %d", len(documentos))
    return documentos


# ---------------------------------------------------------------------------
# 2. Limpeza de texto
# ---------------------------------------------------------------------------
def limpar_texto(texto: str) -> str:
    """
    Aplica limpeza residual no texto já processado pelo ETL.

    O texto_limpo já passou pelo clean_text.py, mas esta função
    garante a remoção de quaisquer resíduos restantes:
      - Remoção de hífens de quebra de linha remanescentes
      - Colapso de múltiplas quebras de linha (3+ → 2)
      - Remoção de espaços duplicados
      - strip() final
    """
    if not texto:
        return ""

    # Remove hífens de quebra de linha remanescentes: "regu-\nlação" → "regulação"
    texto = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", texto)

    # Trata o padrão com espaço após o hífen: "sub- bacia" → "subbacia"
    texto = re.sub(r"(\w)-\s+(\w)", r"\1\2", texto)

    # Colapsa 3+ quebras de linha em 2 (preserva separação de parágrafos)
    texto = re.sub(r"\n{3,}", "\n\n", texto)

    # Remove espaços duplicados
    texto = re.sub(r" {2,}", " ", texto)

    return texto.strip()


# ---------------------------------------------------------------------------
# 3. Divisão semântica (por parágrafos)
# ---------------------------------------------------------------------------
def dividir_por_secoes(texto: str) -> list[str]:
    """
    Divide o texto em seções semânticas usando blocos de parágrafos.

    Utiliza regex para dividir por linhas em branco (\\n\\s*\\n).
    Remove seções com menos de MIN_SECAO_CHARS caracteres.
    """
    if not texto:
        return []

    secoes_brutas = re.split(r"\n\s*\n", texto)

    secoes = []
    for secao in secoes_brutas:
        secao_limpa = secao.strip()
        if len(secao_limpa) >= MIN_SECAO_CHARS:
            secoes.append(secao_limpa)

    logger.debug(
        "Divisão semântica: %d seções brutas → %d seções válidas (>= %d chars)",
        len(secoes_brutas),
        len(secoes),
        MIN_SECAO_CHARS,
    )
    return secoes


# ---------------------------------------------------------------------------
# 4. Chunking por tamanho
# ---------------------------------------------------------------------------
_splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE,
    chunk_overlap=CHUNK_OVERLAP,
    length_function=len,
    separators=["\n\n", "\n", ". ", " ", ""],
)


def gerar_chunks(secoes: list[str]) -> list[str]:
    """
    Aplica o RecursiveCharacterTextSplitter em cada seção e retorna
    a lista final de chunks.
    """
    chunks = []
    for secao in secoes:
        pedacos = _splitter.split_text(secao)
        chunks.extend(pedacos)
    return chunks


# ---------------------------------------------------------------------------
# 5. Filtro de qualidade
# ---------------------------------------------------------------------------
def filtrar_qualidade(chunks: list[str]) -> list[str]:
    """
    Remove chunks com menos de MIN_CHUNK_CHARS caracteres.
    """
    antes = len(chunks)
    chunks_filtrados = [c for c in chunks if len(c) >= MIN_CHUNK_CHARS]
    removidos = antes - len(chunks_filtrados)
    if removidos:
        logger.debug(
            "Filtro de qualidade: %d chunks removidos (< %d chars)",
            removidos,
            MIN_CHUNK_CHARS,
        )
    return chunks_filtrados


# ---------------------------------------------------------------------------
# 6. Pipeline final
# ---------------------------------------------------------------------------
def processar_chunking(engine) -> dict[int, dict]:
    """
    Pipeline completo de chunking:
      1. Busca documentos no Postgres
      2. Limpa o texto_bruto
      3. Divide em seções semânticas
      4. Gera chunks com RecursiveCharacterTextSplitter
      5. Filtra chunks de baixa qualidade

    Retorna:
        dict mapeando doc_id → dict com titulo e chunks
        Exemplo: {1: {"titulo": "...", "chunks": ["chunk1...", "chunk2..."]}}
    """
    documentos = buscar_documentos(engine)

    if not documentos:
        logger.warning("Nenhum documento encontrado para chunking.")
        return {}

    resultado: dict[int, dict] = {}
    total_chunks = 0

    for doc in documentos:
        doc_id = doc["id"]
        titulo = doc.get("titulo", "sem título")
        texto = doc["texto_limpo"]

        # Etapa 2: Limpeza residual
        texto_limpo = limpar_texto(texto)

        if not texto_limpo:
            logger.warning("Doc %d (%s): texto vazio após limpeza, pulando.", doc_id, titulo)
            continue

        # Etapa 3: Divisão semântica
        secoes = dividir_por_secoes(texto_limpo)

        if not secoes:
            logger.warning("Doc %d (%s): nenhuma seção válida após divisão, pulando.", doc_id, titulo)
            continue

        # Etapa 4: Chunking
        chunks = gerar_chunks(secoes)

        # Etapa 5: Filtro de qualidade
        chunks = filtrar_qualidade(chunks)

        if chunks:
            resultado[doc_id] = {"titulo": titulo, "chunks": chunks}
            total_chunks += len(chunks)
            logger.info(
                "Doc %d (%s): %d seções → %d chunks",
                doc_id,
                titulo[:60],
                len(secoes),
                len(chunks),
            )
        else:
            logger.warning("Doc %d (%s): 0 chunks após filtragem.", doc_id, titulo)

    logger.info(
        "Pipeline concluído: %d documentos processados, %d chunks gerados.",
        len(resultado),
        total_chunks,
    )
    return resultado


# ---------------------------------------------------------------------------
# Execução direta (para testes)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    from etl.db import engine

    resultado = processar_chunking(engine)

    print(f"\n{'='*60}")
    print(f"RESUMO DO CHUNKING")
    print(f"{'='*60}")
    print(f"Documentos processados: {len(resultado)}")
    print(f"Total de chunks: {sum(len(v['chunks']) for v in resultado.values())}")
    print()

    # Mostra amostra dos primeiros 3 documentos
    for doc_id, info in list(resultado.items())[:3]:
        chunks = info["chunks"]
        print(f"--- Doc {doc_id}: {len(chunks)} chunks ---")
        for i, chunk in enumerate(chunks[:2]):
            preview = chunk[:120].replace("\n", " ")
            print(f"  Chunk {i+1} ({len(chunk)} chars): {preview}...")
        if len(chunks) > 2:
            print(f"  ... e mais {len(chunks) - 2} chunks")
        print()







# PARA SALVAR EM UM JSON:
# import json
# with open("chunks.json", "w", encoding="utf-8") as f:
#     json.dump(resultado, f, ensure_ascii=False, indent=2)