from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware

from retrieval.retriever import ANEELRetriever
from generation.generator import ANEELGenerator

app = FastAPI(title="ANEEL RAG API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

retriever = ANEELRetriever()
generator = ANEELGenerator()


class Question(BaseModel):
    question: str


@app.get("/")
def root():
    return {"status": "API funcionando"}


@app.post("/ask")
def ask(q: Question):
    try:
        # Retrieval
        chunks = retriever.retrieve(q.question)

        # Geração
        answer = generator.generate_answer(q.question, chunks)

        # Fontes
        sources = []
        for c in chunks:
            sources.append({
                "document": c["metadata"].get("document_name", "Documento desconhecido"),
                "distance": c.get("distance", None)
            })

        return {
            "question": q.question,
            "answer": answer,
            "sources": sources
        }

    except Exception as e:
        return {
            "question": q.question,
            "answer": "Erro interno ao processar a pergunta.",
            "error": str(e),
            "sources": []
        }