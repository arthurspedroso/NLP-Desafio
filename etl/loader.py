import json
from pathlib import Path
from urllib.parse import urlparse

DATA_DIR = Path(__file__).parent.parent / "data"

TIPOS_SUPORTADOS = {"pdf", "html", "htm"}
TIPOS_SEM_CONTEUDO = {"zip", "rar", "xlsx", "xlsm", "xls", "doc", "docx"}


def _detectar_tipo(url: str) -> str:
    path = urlparse(url).path.lower()
    nome = path.rsplit("/", 1)[-1]
    if "." not in nome:
        return "desconhecido"
    ext = nome.rsplit(".", 1)[-1].strip(") ").split("?")[0][:10]
    return ext


def carregar_registros() -> list[dict]:
    registros = []

    for arquivo in sorted(DATA_DIR.glob("*.json")):
        with open(arquivo, encoding="utf-8") as f:
            dados = json.load(f)

        for _, conteudo in dados.items():
            for registro in conteudo.get("registros", []):
                ementa = registro.get("ementa")
                if isinstance(ementa, str):
                    ementa = ementa.strip() or None

                for pdf in registro.get("pdfs", []):
                    url = pdf.get("url")
                    if not url:
                        continue

                    tipo = _detectar_tipo(url)

                    if tipo not in TIPOS_SUPORTADOS and tipo not in TIPOS_SEM_CONTEUDO:
                        continue

                    if tipo in TIPOS_SEM_CONTEUDO and not ementa:
                        continue

                    registros.append({
                        "titulo":       registro.get("titulo"),
                        "autor":        registro.get("autor"),
                        "assunto":      registro.get("assunto"),
                        "situacao":     registro.get("situacao"),
                        "data_pub":     registro.get("publicacao"),
                        "ementa":       ementa,
                        "url_pdf":      url,
                        "arquivo":      pdf.get("arquivo"),
                        "tipo_arquivo": tipo,
                    })

    return registros


if __name__ == "__main__":
    from collections import Counter
    registros = carregar_registros()
    print(f"Total processável: {len(registros)}")
    tipos = Counter(r["tipo_arquivo"] for r in registros)
    for tipo, n in sorted(tipos.items(), key=lambda x: -x[1]):
        print(f"  {tipo:15} {n}")
    com_ementa = sum(1 for r in registros if r["ementa"])
    print(f"Com ementa: {com_ementa} ({100*com_ementa/len(registros):.1f}%)")
