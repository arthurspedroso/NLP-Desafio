"""
Módulo de Chunking Otimizado para o pipeline RAG.

Estratégia híbrida: Semântico + Tokens
=======================================
1. Busca documentos do Postgres (texto_limpo)
2. Aplica limpeza residual no texto
3. Divisão semântica inteligente:
   a) Detecta artigos jurídicos (Art. Xº)
   b) Divide por parágrafos (fallback)
   c) Agrupa seções curtas adjacentes
   d) Divide seções muito longas em sentenças
4. Chunking baseado em TOKENS (não caracteres)
5. Filtro de qualidade + deduplicação

Não implementa embeddings, banco vetorial, busca ou LLM.
"""

import logging
import re
import sys
import hashlib

from sqlalchemy import text
from langchain_text_splitters import RecursiveCharacterTextSplitter
import tiktoken

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("chunking")

# ---------------------------------------------------------------------------
# Configurações do splitter (baseado em tokens)
# ---------------------------------------------------------------------------
# Modelo de referência para contagem de tokens (compatível com a maioria dos
# modelos de embedding). Ajustar caso o modelo de embedding mude.
TIKTOKEN_MODEL = "cl100k_base"

# Tamanho ideal do chunk em tokens.
# 256 tokens ≈ 180-220 palavras ≈ 1000-1300 caracteres
# Ideal para documentos técnicos/legais: grande o bastante para contexto
# completo, pequeno o bastante para precisão no retrieval.
CHUNK_SIZE_TOKENS = 256

# Overlap em tokens (~10% do chunk_size).
# Menor que os 20% anteriores — reduz redundância sem perder coesão.
CHUNK_OVERLAP_TOKENS = 25

# Seções menores que isso (em tokens) são candidatas a merge com adjacentes.
MIN_SECAO_TOKENS = 30

# Chunks menores que isso (em tokens) são descartados.
MIN_CHUNK_TOKENS = 40

# Seções maiores que isso (em tokens) são subdivididas.
MAX_SECAO_TOKENS = 1024

# Limiar de similaridade para deduplicação (via hash dos primeiros N tokens).
DEDUP_PREFIX_TOKENS = 40

# ---------------------------------------------------------------------------
# Encoder de tokens (inicializado uma vez)
# ---------------------------------------------------------------------------
_enc = tiktoken.get_encoding(TIKTOKEN_MODEL)


def _contar_tokens(texto: str) -> int:
    """Conta tokens usando tiktoken."""
    return len(_enc.encode(texto))


# ---------------------------------------------------------------------------
# Conversão tokens → caracteres (para RecursiveCharacterTextSplitter)
# ---------------------------------------------------------------------------
# O RecursiveCharacterTextSplitter trabalha com caracteres, mas queremos
# controlar por tokens. Usamos uma estimativa conservadora:
# 1 token ≈ 4.5 caracteres em português (validado empiricamente).
_CHARS_POR_TOKEN = 4.5
CHUNK_SIZE_CHARS = int(CHUNK_SIZE_TOKENS * _CHARS_POR_TOKEN)      # ~1152
CHUNK_OVERLAP_CHARS = int(CHUNK_OVERLAP_TOKENS * _CHARS_POR_TOKEN)  # ~112


# ---------------------------------------------------------------------------
# Regex para detecção de artigos jurídicos
# ---------------------------------------------------------------------------
# Captura: "Art. 1º", "Art. 2°", "Art. 10.", "Artigo 5º", "ARTIGO 3"
_RE_ARTIGO = re.compile(
    r"(?=(?:^|\n)\s*(?:Art(?:igo)?\.?\s*\d+[º°.]?))",
    re.IGNORECASE | re.MULTILINE,
)

# Regex para detecção de tabelas Markdown
_RE_TABELA = re.compile(r"^\|.*\|$", re.MULTILINE)

# Regex para divisão por sentenças (pontuação final seguida de espaço/newline)
_RE_SENTENCA = re.compile(r"(?<=[.!?;:])\s+(?=[A-ZÀ-Ú0-9])")


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
# 3. Divisão semântica inteligente (hierárquica)
# ---------------------------------------------------------------------------
def _eh_tabela(texto: str) -> bool:
    """Verifica se o texto contém uma tabela Markdown."""
    linhas = texto.strip().split("\n")
    linhas_tabela = sum(1 for l in linhas if l.strip().startswith("|") and l.strip().endswith("|"))
    return linhas_tabela >= 2


def _dividir_por_artigos(texto: str) -> list[str]:
    """
    Tenta dividir o texto por artigos jurídicos (Art. Xº).
    Retorna lista vazia se não encontrar pelo menos 2 artigos.
    """
    partes = _RE_ARTIGO.split(texto)
    partes = [p.strip() for p in partes if p.strip()]
    if len(partes) >= 2:
        return partes
    return []


def _dividir_por_paragrafos(texto: str) -> list[str]:
    """Divide por blocos separados por linhas em branco."""
    partes = re.split(r"\n\s*\n", texto)
    return [p.strip() for p in partes if p.strip()]


def _dividir_por_sentencas(texto: str) -> list[str]:
    """Divide texto em sentenças usando pontuação."""
    partes = _RE_SENTENCA.split(texto)
    return [p.strip() for p in partes if p.strip()]


def _agrupar_secoes_curtas(secoes: list[str]) -> list[str]:
    """
    Agrupa seções adjacentes que sejam muito curtas.
    Evita gerar chunks de ruído ao mesclar parágrafos pequenos.

    Heurística: se a seção atual + próxima cabem dentro de MAX_SECAO_TOKENS,
    e a atual é menor que MIN_SECAO_TOKENS * 3 (~90 tokens), agrupa.
    """
    if not secoes:
        return []

    agrupadas = []
    buffer = secoes[0]

    for secao in secoes[1:]:
        tokens_buffer = _contar_tokens(buffer)
        tokens_secao = _contar_tokens(secao)

        # Se o buffer é curto E cabe junto com a próxima seção, agrupa
        if (tokens_buffer < MIN_SECAO_TOKENS * 3
                and tokens_buffer + tokens_secao <= MAX_SECAO_TOKENS):
            buffer = buffer + "\n\n" + secao
        else:
            agrupadas.append(buffer)
            buffer = secao

    agrupadas.append(buffer)
    return agrupadas


def _subdividir_secoes_longas(secoes: list[str]) -> list[str]:
    """
    Subdivide seções que excedem MAX_SECAO_TOKENS.
    Usa divisão por sentenças para manter coerência.
    """
    resultado = []
    for secao in secoes:
        if _contar_tokens(secao) <= MAX_SECAO_TOKENS:
            resultado.append(secao)
            continue

        # Tenta dividir por sentenças e reagrupar
        sentencas = _dividir_por_sentencas(secao)
        if len(sentencas) <= 1:
            # Não conseguiu dividir — mantém como está (o splitter cuida)
            resultado.append(secao)
            continue

        grupo_atual = sentencas[0]
        for sent in sentencas[1:]:
            tokens_grupo = _contar_tokens(grupo_atual)
            tokens_sent = _contar_tokens(sent)

            if tokens_grupo + tokens_sent <= MAX_SECAO_TOKENS:
                grupo_atual = grupo_atual + " " + sent
            else:
                resultado.append(grupo_atual)
                grupo_atual = sent

        resultado.append(grupo_atual)

    return resultado


def dividir_por_secoes(texto: str) -> list[str]:
    """
    Divisão semântica hierárquica:
      1. Tenta dividir por artigos jurídicos (Art. Xº)
      2. Fallback: divide por parágrafos
      3. Agrupa seções curtas adjacentes
      4. Subdivide seções que excedem MAX_SECAO_TOKENS
      5. Filtra seções abaixo do mínimo

    Mantém tabelas Markdown como seções intactas.
    """
    if not texto:
        return []

    # Etapa 1: tenta divisão por artigos
    secoes = _dividir_por_artigos(texto)

    # Etapa 2: fallback por parágrafos
    if not secoes:
        secoes = _dividir_por_paragrafos(texto)

    if not secoes:
        return []

    # Etapa 3: agrupa seções curtas adjacentes
    secoes = _agrupar_secoes_curtas(secoes)

    # Etapa 4: subdivide seções muito longas (exceto tabelas)
    secoes_finais = []
    for secao in secoes:
        if _eh_tabela(secao):
            # Tabelas são mantidas intactas
            secoes_finais.append(secao)
        else:
            # Subdivide se necessário
            subdivididas = _subdividir_secoes_longas([secao])
            secoes_finais.extend(subdivididas)

    # Etapa 5: filtra seções muito curtas
    secoes_filtradas = [
        s for s in secoes_finais
        if _contar_tokens(s) >= MIN_SECAO_TOKENS
    ]

    logger.debug(
        "Divisão semântica: %d seções brutas → %d após merge/split → %d após filtro (>= %d tokens)",
        len(secoes),
        len(secoes_finais),
        len(secoes_filtradas),
        MIN_SECAO_TOKENS,
    )
    return secoes_filtradas


# ---------------------------------------------------------------------------
# 4. Chunking por tamanho (baseado em tokens via proxy de caracteres)
# ---------------------------------------------------------------------------
_splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE_CHARS,
    chunk_overlap=CHUNK_OVERLAP_CHARS,
    length_function=len,
    separators=["\n\n", "\n", ". ", "; ", ", ", " ", ""],
)


def gerar_chunks(secoes: list[str]) -> list[str]:
    """
    Aplica o RecursiveCharacterTextSplitter em cada seção e retorna
    a lista final de chunks.

    Seções que já cabem dentro do limite de tokens são mantidas intactas
    (sem split desnecessário). Tabelas são sempre mantidas inteiras.
    """
    chunks = []
    for secao in secoes:
        tokens_secao = _contar_tokens(secao)

        # Se a seção já cabe no chunk, não precisa dividir
        if tokens_secao <= CHUNK_SIZE_TOKENS:
            chunks.append(secao)
            continue

        # Tabelas: mantém intactas mesmo que ultrapassem o limite
        if _eh_tabela(secao):
            chunks.append(secao)
            continue

        # Aplica o splitter
        pedacos = _splitter.split_text(secao)
        chunks.extend(pedacos)

    return chunks


# ---------------------------------------------------------------------------
# 5. Filtro de qualidade + deduplicação
# ---------------------------------------------------------------------------
def _hash_prefixo(texto: str) -> str:
    """Gera hash dos primeiros DEDUP_PREFIX_TOKENS tokens para deduplicação."""
    tokens = _enc.encode(texto)[:DEDUP_PREFIX_TOKENS]
    prefixo = _enc.decode(tokens)
    return hashlib.md5(prefixo.encode("utf-8")).hexdigest()


def filtrar_qualidade(chunks: list[str]) -> list[str]:
    """
    Remove chunks de baixa qualidade e deduplica:
      1. Remove chunks com menos de MIN_CHUNK_TOKENS tokens
      2. Remove chunks quase idênticos (mesmo hash de prefixo)
    """
    antes = len(chunks)

    # Filtro por tamanho mínimo
    chunks_filtrados = [
        c for c in chunks if _contar_tokens(c) >= MIN_CHUNK_TOKENS
    ]
    removidos_tamanho = antes - len(chunks_filtrados)

    # Deduplicação por hash de prefixo
    vistos = set()
    chunks_dedup = []
    for chunk in chunks_filtrados:
        h = _hash_prefixo(chunk)
        if h not in vistos:
            vistos.add(h)
            chunks_dedup.append(chunk)

    removidos_dedup = len(chunks_filtrados) - len(chunks_dedup)

    if removidos_tamanho or removidos_dedup:
        logger.debug(
            "Filtro: %d removidos por tamanho (< %d tokens), %d removidos por duplicação",
            removidos_tamanho,
            MIN_CHUNK_TOKENS,
            removidos_dedup,
        )

    return chunks_dedup


# ---------------------------------------------------------------------------
# 6. Pipeline final
# ---------------------------------------------------------------------------
def processar_chunking(engine) -> dict[int, list[str]]:
    """
    Pipeline completo de chunking otimizado:
      1. Busca documentos no Postgres
      2. Limpa o texto_bruto
      3. Divide em seções semânticas (artigos → parágrafos → merge/split)
      4. Gera chunks com RecursiveCharacterTextSplitter (calibrado por tokens)
      5. Filtra chunks de baixa qualidade + deduplica

    Retorna:
        dict mapeando doc_id → lista de chunks
        Exemplo: {1: ["chunk1...", "chunk2..."], 2: [...]}
    """
    documentos = buscar_documentos(engine)

    if not documentos:
        logger.warning("Nenhum documento encontrado para chunking.")
        return {}

    resultado: dict[int, list[str]] = {}
    total_chunks = 0
    total_secoes = 0

    for doc in documentos:
        doc_id = doc["id"]
        titulo = doc.get("titulo", "sem título")
        texto = doc["texto_limpo"]

        # Etapa 2: Limpeza residual
        texto_limpo = limpar_texto(texto)

        if not texto_limpo:
            logger.warning("Doc %d (%s): texto vazio após limpeza, pulando.", doc_id, titulo)
            continue

        # Etapa 3: Divisão semântica inteligente
        secoes = dividir_por_secoes(texto_limpo)

        if not secoes:
            logger.warning("Doc %d (%s): nenhuma seção válida após divisão, pulando.", doc_id, titulo)
            continue

        total_secoes += len(secoes)

        # Etapa 4: Chunking
        chunks = gerar_chunks(secoes)

        # Etapa 5: Filtro de qualidade + deduplicação
        chunks = filtrar_qualidade(chunks)

        if chunks:
            resultado[doc_id] = chunks
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
        "Pipeline concluído: %d documentos → %d seções → %d chunks.",
        len(resultado),
        total_secoes,
        total_chunks,
    )
    return resultado


# ---------------------------------------------------------------------------
# Execução direta (para testes e comparação)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    from etl.db import engine

    resultado = processar_chunking(engine)

    # Estatísticas detalhadas
    todos_chunks = [c for chunks in resultado.values() for c in chunks]
    tokens_por_chunk = [_contar_tokens(c) for c in todos_chunks]

    print(f"\n{'='*60}")
    print(f"RESUMO DO CHUNKING OTIMIZADO")
    print(f"{'='*60}")
    print(f"Documentos processados: {len(resultado)}")
    print(f"Total de chunks:        {len(todos_chunks)}")

    if tokens_por_chunk:
        print(f"\nDistribuição de tokens por chunk:")
        print(f"  Mínimo:  {min(tokens_por_chunk)} tokens")
        print(f"  Máximo:  {max(tokens_por_chunk)} tokens")
        print(f"  Média:   {sum(tokens_por_chunk) / len(tokens_por_chunk):.0f} tokens")
        print(f"  Mediana: {sorted(tokens_por_chunk)[len(tokens_por_chunk)//2]} tokens")

    print()

    # Mostra amostra dos primeiros 3 documentos
    for doc_id, chunks in list(resultado.items())[:3]:
        print(f"--- Doc {doc_id}: {len(chunks)} chunks ---")
        for i, chunk in enumerate(chunks[:2]):
            tokens = _contar_tokens(chunk)
            preview = chunk[:120].replace("\n", " ")
            print(f"  Chunk {i+1} ({tokens} tokens, {len(chunk)} chars): {preview}...")
        if len(chunks) > 2:
            print(f"  ... e mais {len(chunks) - 2} chunks")
        print()



# PARA SALVAR EM UM JSON:
# import json
# with open("chunks.json", "w", encoding="utf-8") as f:
#     json.dump(resultado, f, ensure_ascii=False, indent=2)