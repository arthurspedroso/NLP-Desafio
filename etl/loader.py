import json
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"


def carregar_registros() -> list[dict]:
    vistos: dict[str, dict] = {}

    for arquivo in sorted(DATA_DIR.glob("*.json")):
        with open(arquivo, encoding="utf-8") as f:
            dados = json.load(f)

        for _, conteudo in dados.items():
            for registro in conteudo.get("registros", []):
                for pdf in registro.get("pdfs", []):
                    url = pdf.get("url")
                    if not url or url in vistos:
                        continue

                    vistos[url] = {
                        "titulo":   registro.get("titulo"),
                        "autor":    registro.get("autor"),
                        "assunto":  registro.get("assunto"),
                        "situacao": registro.get("situacao"),
                        "data_pub": registro.get("publicacao"),
                        "ementa":   registro.get("ementa"),
                        "url_pdf":  url,
                        "arquivo":  pdf.get("arquivo"),
                    }

    return list(vistos.values())

if __name__ == "__main__":
    registros = carregar_registros()
    print(f"Total de registros com PDF: {len(registros)}")
    print(registros[0])
