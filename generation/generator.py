import os
import time
from google import genai
from google.genai import errors
from dotenv import load_dotenv

load_dotenv()


class ANEELGenerator:
    def __init__(self, model_name="gemini-2.5-flash"):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY not found in environment variables.")

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

Use o contexto abaixo para responder de forma clara e objetiva.
Se a resposta não estiver no contexto, diga que não sabe.

CONTEXTO:
{context_text}

PERGUNTA:
{query}

RESPOSTA:
"""

        retries = 5
        delay = 2

        for attempt in range(retries):
            try:
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config={
                        "temperature": 0.2,
                        "max_output_tokens": 512,
                    }
                )

                return response.text.strip()

            except errors.ServerError as e:
                error_msg = str(e)

                if "503" in error_msg or "UNAVAILABLE" in error_msg:
                    print(
                        f"[Tentativa {attempt+1}/{retries}] "
                        f"Modelo sobrecarregado. Retry em {delay}s..."
                    )
                    time.sleep(delay)
                    delay *= 2
                else:
                    raise e

            except Exception as e:
                print(f"Erro inesperado: {e}")
                return f"Erro inesperado ao gerar resposta: {str(e)}"

        return "O modelo está temporariamente indisponível. Tente novamente em alguns segundos."


if __name__ == "__main__":
    generator = ANEELGenerator()

    query = "O que é a bandeira tarifária?"
    context = [
        {"content": "As bandeiras tarifárias indicam o custo real da energia elétrica..."}
    ]

    answer = generator.generate_answer(query, context)
    print(answer)