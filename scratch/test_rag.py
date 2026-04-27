import os
import sys

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from retrieval.retriever import ANEELRetriever
from generation.generator import ANEELGenerator

def main():
    print("--- Inicializando RAG ---")
    retriever = ANEELRetriever()
    generator = ANEELGenerator()
    
    question = "Quais são as principais regras para microgeração distribuída?"
    print(f"\nPergunta: {question}")
    
    print("\n[1/2] Recuperando chunks relevantes...")
    chunks = retriever.retrieve(question, k=3)
    for i, c in enumerate(chunks):
        print(f"  Chunk {i+1} (Doc: {c['metadata'].get('titulo', 'S/T')}): {c['content'][:100]}...")
    
    print("\n[2/2] Gerando resposta com Gemini...")
    answer = generator.generate_answer(question, chunks)
    
    print("\n--- RESPOSTA FINAL ---")
    print(answer)

if __name__ == "__main__":
    main()
