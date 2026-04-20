import json
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"


def carregar_registros() -> list[dict]:
    registros = []

    for arquivo in sorted(DATA_DIR.glob("*.json")):
        with open(arquivo, encoding="utf-8") as f:
            dados = json.load(f)

        for _, conteudo in dados.items():
            for registro in conteudo.get("registros", []):
                pdfs = registro.get("pdfs", [])
                if not pdfs:
                    continue

                pdf = pdfs[0]
                if not pdf.get("url"):
                    continue

                registros.append({
                    "titulo":   registro.get("titulo"),
                    "autor":    registro.get("autor"),
                    "assunto":  registro.get("assunto"),
                    "situacao": registro.get("situacao"),
                    "data_pub": registro.get("publicacao"),
                    "ementa":   registro.get("ementa"),
                    "url_pdf":  pdf["url"],
                    "arquivo":  pdf.get("arquivo"),
                })

    return registros


if __name__ == "__main__":
    registros = carregar_registros()
    print(f"Total de registros com PDF: {len(registros)}")
    print(registros[0])
