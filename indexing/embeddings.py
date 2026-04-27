import os
import logging
import sys
import json
import time

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
from dotenv import load_dotenv

from etl.db import engine
from indexing.chunking import processar_chunking

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("embeddings")

load_dotenv()

BATCH_SIZE = 200


def dividir_lista(lista, tamanho):
    for i in range(0, len(lista), tamanho):
        yield lista[i:i + tamanho]


def criar_collection(client):
    embedding_function = SentenceTransformerEmbeddingFunction(
        model_name="all-MiniLM-L6-v2"
    )
    return client.get_or_create_collection(
        name="aneel_docs",
        embedding_function=embedding_function
    )


def _ids_ja_indexados(collection) -> set:
    total = collection.count()
    if total == 0:
        return set()
    result = collection.get(include=[])
    ids = result.get("ids", [])
    doc_ids = set()
    for id_str in ids:
        partes = id_str.split("_chunk_")
        if len(partes) == 2:
            doc_ids.add(partes[0].replace("doc_", ""))
    logger.info("%d chunks já indexados (%d doc_ids únicos)", total, len(doc_ids))
    return doc_ids


def gerar_e_salvar_embeddings():
    t_inicio = time.time()
    logger.info("Iniciando embeddings...")

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(base_dir, "data")
    cache_path = os.path.join(data_dir, "chunks_cache.json")

    os.makedirs(data_dir, exist_ok=True)

    chroma_host = os.getenv("CHROMA_HOST", "chromadb")
    chroma_port = int(os.getenv("CHROMA_PORT", "8000"))
    client = chromadb.HttpClient(host=chroma_host, port=chroma_port)

    try:
        client.heartbeat()
        logger.info("ChromaDB conectado em %s:%s", chroma_host, chroma_port)
    except Exception as e:
        logger.error("ChromaDB inacessível em %s:%s — %s", chroma_host, chroma_port, e)
        sys.exit(1)

    collection = criar_collection(client)
    ids_indexados = _ids_ja_indexados(collection)

    if os.path.exists(cache_path):
        logger.info("Carregando chunks do cache...")
        with open(cache_path, "r", encoding="utf-8") as f:
            documentos_chunks = json.load(f)
    else:
        logger.info("Gerando chunks...")
        documentos_chunks = processar_chunking(engine)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(documentos_chunks, f, ensure_ascii=False)
        logger.info("Cache salvo em %s", cache_path)

    total_docs = len(documentos_chunks)
    total_chunks = 0
    total_erros = 0
    pulados = 0

    logger.info("Total de documentos a indexar: %d", total_docs)

    for idx, (doc_id, info) in enumerate(documentos_chunks.items(), start=1):
        if str(doc_id) in ids_indexados:
            pulados += 1
            continue

        titulo = info["titulo"]
        autor = info.get("autor", "")
        assunto = info.get("assunto", "")
        situacao = info.get("situacao", "")
        data_pub = info.get("data_pub", "")
        chunks = info["chunks"]

        documents, metadatas, ids = [], [], []
        for i, chunk in enumerate(chunks):
            documents.append(chunk)
            metadatas.append({
                "doc_id": str(doc_id),
                "titulo": titulo,
                "autor": autor,
                "assunto": assunto,
                "situacao": situacao,
                "data_pub": data_pub,
            })
            ids.append(f"doc_{doc_id}_chunk_{i}")

        for docs_b, meta_b, ids_b in zip(
            dividir_lista(documents, BATCH_SIZE),
            dividir_lista(metadatas, BATCH_SIZE),
            dividir_lista(ids, BATCH_SIZE),
        ):
            try:
                collection.upsert(documents=docs_b, metadatas=meta_b, ids=ids_b)
                total_chunks += len(docs_b)
            except Exception as e:
                logger.warning("Erro no doc %s: %s", doc_id, e, exc_info=True)
                total_erros += 1

        if idx % 100 == 0:
            logger.info("[%d/%d] %d chunks inseridos, %d erros", idx, total_docs, total_chunks, total_erros)

    elapsed = time.time() - t_inicio
    logger.info(
        "Finalizado em %.1fs — %d chunks inseridos, %d docs pulados (já indexados), %d erros.",
        elapsed, total_chunks, pulados, total_erros,
    )


if __name__ == "__main__":
    gerar_e_salvar_embeddings()
