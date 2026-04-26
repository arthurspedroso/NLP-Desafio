import os
import logging
import sys
import time
import json
from google.api_core.exceptions import ResourceExhausted, DeadlineExceeded

import chromadb
from chromadb.utils.embedding_functions import GoogleGenerativeAiEmbeddingFunction
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
BATCH_SIZE = 50


def dividir_lista(lista, tamanho):
    for i in range(0, len(lista), tamanho):
        yield lista[i:i + tamanho]


def criar_collection(client):
    """Cria collection usando a chave paga do .env"""
    embedding_function = GoogleGenerativeAiEmbeddingFunction(
        api_key=os.getenv("GEMINI_API_KEY"),
        task_type="RETRIEVAL_DOCUMENT"
    )

    return client.get_or_create_collection(
        name="aneel_docs",
        embedding_function=embedding_function
    )


def gerar_e_salvar_embeddings():
    chroma_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "data",
        "chroma_db"
    )

    client = chromadb.PersistentClient(path=chroma_path)
    collection = criar_collection(client)

    # CACHE DOS CHUNKS
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

    for idx, (doc_id, info) in enumerate(documentos_chunks.items(), start=1):
        titulo = info["titulo"]
        chunks = info["chunks"]

        ids = [f"doc_{doc_id}_chunk_{i}" for i in range(len(chunks))]
        existentes = collection.get(ids=ids)
        ids_existentes = set(existentes["ids"])

        documents = []
        metadatas = []
        novos_ids = []

        for i, chunk in enumerate(chunks):
            chunk_id = f"doc_{doc_id}_chunk_{i}"
            if chunk_id not in ids_existentes:
                documents.append(chunk)
                metadatas.append({
                    "doc_id": doc_id,
                    "titulo": titulo
                })
                novos_ids.append(chunk_id)

        if not documents:
            logger.info(f"[{idx}/{total_docs}] Doc {doc_id} já processado. Pulando.")
            continue

        for docs_batch, meta_batch, ids_batch in zip(
            dividir_lista(documents, BATCH_SIZE),
            dividir_lista(metadatas, BATCH_SIZE),
            dividir_lista(novos_ids, BATCH_SIZE),
        ):
            tentativa = 1

            while True:
                try:
                    collection.upsert(
                        documents=docs_batch,
                        metadatas=meta_batch,
                        ids=ids_batch
                    )

                    total_chunks += len(docs_batch)
                    break

                except ResourceExhausted:
                    espera = min(10 * tentativa, 120)
                    logger.warning(f"Rate limit. Esperando {espera}s")
                    time.sleep(espera)
                    tentativa += 1

                except DeadlineExceeded:
                    espera = min(10 * tentativa, 60)
                    logger.warning(f"Timeout. Esperando {espera}s")
                    time.sleep(espera)
                    tentativa += 1

        logger.info(f"[{idx}/{total_docs}] Doc {doc_id} processado")

    logger.info(f"Finalizado! {total_chunks} chunks inseridos.")


if __name__ == "__main__":
    gerar_e_salvar_embeddings()