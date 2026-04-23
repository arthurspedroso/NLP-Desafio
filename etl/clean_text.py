import logging
import re
import sys

from sqlalchemy import text

from etl.db import engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
logger = logging.getLogger("clean_text")

RUIDO_UI = [
    "Imprimir", "Voltar", "Página inicial", "Fechar",
    "Topo da página", "Ir para o topo", "Voltar ao topo",
]

BATCH_SIZE = 500


def limpar(texto: str) -> str:
    for ruido in RUIDO_UI:
        texto = texto.replace(ruido, "")
    # hífens de quebra de linha: "sub- bacia" → "sub-bacia"
    texto = re.sub(r"(\w)-\s+(\w)", r"\1-\2", texto)
    # colapsa whitespace excessivo
    texto = re.sub(r" {2,}", " ", texto)
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    return texto.strip()


def main():
    with engine.connect() as conn:
        total = conn.execute(
            text("SELECT COUNT(*) FROM documents WHERE texto_bruto IS NOT NULL AND texto_bruto != ''")
        ).scalar()

    logger.info("total com texto_bruto: %d", total)

    offset = 0
    atualizados = 0

    while offset < total:
        with engine.begin() as conn:
            rows = conn.execute(text("""
                SELECT id, texto_bruto FROM documents
                WHERE texto_bruto IS NOT NULL AND texto_bruto != ''
                ORDER BY id
                LIMIT :lim OFFSET :off
            """), {"lim": BATCH_SIZE, "off": offset}).fetchall()

            if not rows:
                break

            params = [{"id": row[0], "limpo": limpar(row[1])} for row in rows]
            conn.execute(text(
                "UPDATE documents SET texto_limpo = :limpo WHERE id = :id"
            ), params)
            atualizados += len(rows)

        offset += BATCH_SIZE
        logger.info("processados %d/%d", atualizados, total)

    logger.info("concluído. %d registros com texto_limpo preenchido.", atualizados)


if __name__ == "__main__":
    main()
