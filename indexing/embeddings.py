import os
import logging
import sys
import time
from google.api_core.exceptions import ResourceExhausted

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


def gerar_e_salvar_embeddings():
    gemini_api_key = os.getenv("GEMINI_API_KEY")
    if not gemini_api_key or gemini_api_key == "sua_chave_aqui":
        logger.error("GEMINI_API_KEY não configurada no arquivo .env")
        return

    # Usar PersistentClient para persistir no disco
    chroma_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "data",
        "chroma_db"
    )
    logger.info(f"Inicializando ChromaDB em {chroma_path}...")
    client = chromadb.PersistentClient(path=chroma_path)

    logger.info("Configurando função de embedding do Gemini...")
    embedding_function = GoogleGenerativeAiEmbeddingFunction(
        api_key=gemini_api_key,
        task_type="RETRIEVAL_DOCUMENT"
    )

    collection = client.get_or_create_collection(
        name="aneel_docs",
        embedding_function=embedding_function
    )

    logger.info("Processando chunks a partir do banco PostgreSQL...")
    documentos_chunks = processar_chunking(engine)

    if not documentos_chunks:
        logger.warning("Nenhum documento com chunks retornado.")
        return

    total_chunks = 0

    for doc_id, info in documentos_chunks.items():
        titulo = info.get("titulo", "Sem título")
        chunks = info.get("chunks", [])

        if not chunks:
            continue

        documents = []
        metadatas = []
        ids = []

        for i, chunk in enumerate(chunks):
            documents.append(chunk)
            metadatas.append({
                "doc_id": doc_id,
                "titulo": titulo
            })
            ids.append(f"doc_{doc_id}_chunk_{i}")

        # verificar quais já existem
        existentes = collection.get(ids=ids)
        ids_existentes = set(existentes["ids"])

        # filtrar apenas novos
        novos_documents = []
        novos_metadatas = []
        novos_ids = []

        for i in range(len(ids)):
            if ids[i] not in ids_existentes:
                novos_documents.append(documents[i])
                novos_metadatas.append(metadatas[i])
                novos_ids.append(ids[i])

        if not novos_ids:
            logger.info(f"Documento {doc_id} já indexado. Pulando...")
            continue

        logger.info(
            f"Adicionando {len(novos_documents)} chunks do documento {doc_id} ('{titulo[:40]}...') no ChromaDB..."
        )

        while True:
            try:
                collection.upsert(
                    documents=novos_documents,
                    metadatas=novos_metadatas,
                    ids=novos_ids
                )

                total_chunks += len(novos_documents)

                # delay preventivo
                time.sleep(1)
                break

            except ResourceExhausted:
                logger.warning(
                    "Limite da API atingido. Esperando 40 segundos para continuar..."
                )
                time.sleep(40)

    logger.info(f"Sucesso! {total_chunks} chunks inseridos/atualizados no ChromaDB.")


def testar_busca(query_text: str):
    """
    Testa o retriever com uma query simples.
    """
    gemini_api_key = os.getenv("GEMINI_API_KEY")
    if not gemini_api_key or gemini_api_key == "sua_chave_aqui":
        return

    chroma_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "data",
        "chroma_db"
    )
    client = chromadb.PersistentClient(path=chroma_path)

    embedding_function = GoogleGenerativeAiEmbeddingFunction(
        api_key=gemini_api_key,
        task_type="RETRIEVAL_QUERY"
    )

    try:
        collection = client.get_collection(
            name="aneel_docs",
            embedding_function=embedding_function
        )
    except Exception as e:
        logger.error(f"Erro ao carregar coleção: {e}")
        return

    logger.info(f"Realizando busca de teste para: '{query_text}'")
    results = collection.query(
        query_texts=[query_text],
        n_results=2
    )

    logger.info("Resultados da busca:")
    if not results['ids'] or not results['ids'][0]:
        logger.info("Nenhum resultado encontrado.")
        return

    for i in range(len(results['ids'][0])):
        logger.info(f"--- Resultado {i+1} ---")
        logger.info(f"ID: {results['ids'][0][i]}")
        dist = (
            results['distances'][0][i]
            if 'distances' in results and results['distances']
            else 'N/A'
        )
        logger.info(f"Distância: {dist}")
        logger.info(f"Metadados: {results['metadatas'][0][i]}")
        doc = results['documents'][0][i]
        logger.info(f"Documento: {doc[:200]}...\n")


if __name__ == "__main__":
    gerar_e_salvar_embeddings()
    print("-" * 50)
    testar_busca("quais os critérios para a compensação de energia?")