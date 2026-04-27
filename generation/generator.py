import os
from google import genai
from dotenv import load_dotenv

load_dotenv()


class ANEELGenerator:
    def __init__(self, model_name="gemini-2.5-flash"):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY not found in environment variables.")

        # NOVA API
        self.client = genai.Client(api_key=api_key)
        self.model_name = model_name

    def generate_answer(self, query: str, context_chunks: list):
        """
        Generates an answer based on the query and provided context chunks.
        """

        context_text = "\n\n".join(
            [f"--- Chunk ---\n{c['content']}" for c in context_chunks]
        )

        prompt = f"""
Você é um assistente especializado na legislação da ANEEL (Agência Nacional de Energia Elétrica).

Use o contexto abaixo para responder.
Se não estiver no contexto, diga que não sabe.

CONTEXTO:
{context_text}

PERGUNTA:
{query}

RESPOSTA:
"""

        response = self.client.models.generate_content(
            model=self.model_name,
            contents=prompt
        )

        return response.text


if __name__ == "__main__":
    generator = ANEELGenerator()

    query = "O que é a bandeira tarifária?"
    context = [
        {"content": "As bandeiras tarifárias indicam o custo real da energia elétrica..."}
    ]

    answer = generator.generate_answer(query, context)
    print(answer)