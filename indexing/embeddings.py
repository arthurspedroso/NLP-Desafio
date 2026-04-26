import os
import logging
import sys
import time
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

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

CACHE_CHUNKS = "chunks_cache.json"
BATCH_SIZE = 200
MAX_WORKERS = 8


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


def processar_documento(collection, idx, total_docs, doc_id, info):
    titulo = info["titulo"]
    chunks = info["chunks"]

    documents = []
    metadatas = []
    novos_ids = []

    for i, chunk in enumerate(chunks):
        chunk_id = f"doc_{doc_id}_chunk_{i}"
        documents.append(chunk)
        metadatas.append({
            "doc_id": doc_id,
            "titulo": titulo
        })
        novos_ids.append(chunk_id)

    total_chunks_local = 0

    for docs_batch, meta_batch, ids_batch in zip(
        dividir_lista(documents, BATCH_SIZE),
        dividir_lista(metadatas, BATCH_SIZE),
        dividir_lista(novos_ids, BATCH_SIZE),
    ):
        try:
            collection.upsert(
                documents=docs_batch,
                metadatas=meta_batch,
                ids=ids_batch
            )
            total_chunks_local += len(docs_batch)

        except Exception as e:
            logger.warning(f"Erro no doc {doc_id}: {e}")

    logger.info(f"[{idx}/{total_docs}] Doc {doc_id} processado")
    return total_chunks_local


def gerar_e_salvar_embeddings():
    logger.info("Iniciando embeddings...")

    chroma_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "data",
        "chroma_db"
    )

    client = chromadb.PersistentClient(path=chroma_path)
    collection = criar_collection(client)

    if os.path.exists(CACHE_CHUNKS):
        logger.info("Carregando chunks do cache...")
        with open(CACHE_CHUNKS, "r", encoding="utf-8") as f:
            documentos_chunks = json.load(f)
    else:
        logger.info("Gerando chunks...")
        documentos_chunks = processar_chunking(engine)

        with open(CACHE_CHUNKS, "w", encoding="utf-8") as f:
            json.dump(documentos_chunks, f, ensure_ascii=False)

    total_docs = len(documentos_chunks)
    total_chunks = 0

    logger.info(f"Total de documentos: {total_docs}")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = []

        for idx, (doc_id, info) in enumerate(documentos_chunks.items(), start=1):
            futures.append(
                executor.submit(
                    processar_documento,
                    collection,
                    idx,
                    total_docs,
                    doc_id,
                    info
                )
            )

        for future in as_completed(futures):
            try:
                total_chunks += future.result()
            except Exception as e:
                logger.error(f"Erro em thread: {e}")

    logger.info(f"Finalizado! {total_chunks} chunks inseridos.")


if __name__ == "__main__":
    gerar_e_salvar_embeddings()