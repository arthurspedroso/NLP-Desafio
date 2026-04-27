import os
import sys
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional

# Add project root to sys.path to allow imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from retrieval.retriever import ANEELRetriever
from generation.generator import ANEELGenerator

app = FastAPI(title="ANEEL RAG API")

# Initialize components
retriever = ANEELRetriever()
generator = ANEELGenerator()

class QueryRequest(BaseModel):
    question: str
    top_k: Optional[int] = 5

class Source(BaseModel):
    content: str
    metadata: dict
    distance: Optional[float]

class QueryResponse(BaseModel):
    question: str
    answer: str
    sources: List[Source]

@app.get("/")
def read_root():
    return {"message": "ANEEL RAG API is running"}

@app.post("/query", response_model=QueryResponse)
async def query_rag(request: QueryRequest):
    try:
        # 1. Retrieve
        chunks = retriever.retrieve(request.question, k=request.top_k)
        
        # 2. Generate
        answer = generator.generate_answer(request.question, chunks)
        
        # 3. Format response
        sources = [
            Source(content=c['content'], metadata=c['metadata'], distance=c['distance'])
            for c in chunks
        ]
        
        return QueryResponse(
            question=request.question,
            answer=answer,
            sources=sources
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
