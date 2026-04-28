import os
import re
import chromadb
from sentence_transformers import SentenceTransformer


class ANEELRetriever:
    def __init__(self, collection_name="aneel_docs"):
        chroma_host = os.getenv("CHROMA_HOST", "localhost")
        chroma_port = int(os.getenv("CHROMA_PORT", "8001"))
        self.client = chromadb.HttpClient(host=chroma_host, port=chroma_port)
        self.model = SentenceTransformer("all-MiniLM-L6-v2")
        self.collection = self.client.get_collection(name=collection_name)

    def _normalize(self, text):
        return re.sub(r"\s+", " ", text.lower()).strip()

    def retrieve(self, query: str, k: int = 5):
        query_embedding = self.model.encode(query).tolist()
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=12
        )

        seen = set()
        chunks = []

        for i in range(len(results["documents"][0])):
            content = results["documents"][0][i]
            metadata = results["metadatas"][0][i]
            distance = results["distances"][0][i]

            # filtra resultados ruins
            if distance > 1.5:
                continue

            normalized = self._normalize(content)

            # remove duplicados fortes
            key = normalized[:400]

            if key in seen:
                continue

            seen.add(key)

            chunks.append({
                "content": content,
                "metadata": metadata,
                "distance": distance
            })

            if len(chunks) >= k:
                break

        return chunks