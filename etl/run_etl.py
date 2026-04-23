import argparse
import logging
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed

from etl.db import criar_tabela, resetar_fallbacks, salvar_batch, urls_processadas
from etl.extractor import extrair
from etl.loader import carregar_registros

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("etl")


BATCH_SIZE = 50


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--retry-fallbacks",
        action="store_true",
        help="Marca registros com fonte=ementa_fallback ou erro como não processados e reprocessa.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=os.cpu_count() or 4,
        help="Número de processos worker (padrão: os.cpu_count()).",
    )
    args = parser.parse_args()

    criar_tabela()

    if args.retry_fallbacks:
        n = resetar_fallbacks()
        logger.info("reset de %d registros com fallback/erro", n)

    todos = carregar_registros()
    processadas = urls_processadas()
    pendentes = [r for r in todos if r["url_pdf"] not in processadas]

    total = len(todos)
    feitos = total - len(pendentes)
    logger.info("total=%d feitos=%d pendentes=%d workers=%d", total, feitos, len(pendentes), args.workers)

    if not pendentes:
        logger.info("nada a processar")
        return

    batch: list[dict] = []
    concluidos = feitos
    contagem_fonte: dict[str, int] = {}

    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(extrair, r): r for r in pendentes}
        for future in as_completed(futures):
            try:
                resultado = future.result()
            except Exception as e:
                reg = futures[future]
                logger.exception("worker falhou em %s: %s", reg.get("url_pdf"), e)
                resultado = {
                    **reg,
                    "texto_bruto": None,
                    "fonte": "erro",
                    "erro": f"worker crash: {e}",
                }

            batch.append(resultado)
            concluidos += 1
            fonte = resultado.get("fonte", "erro")
            contagem_fonte[fonte] = contagem_fonte.get(fonte, 0) + 1

            if concluidos % 20 == 0 or concluidos == total:
                resumo = " ".join(f"{k}={v}" for k, v in sorted(contagem_fonte.items()))
                logger.info("[%d/%d] %s", concluidos, total, resumo)

            if len(batch) >= BATCH_SIZE:
                salvar_batch(batch)
                batch.clear()

    if batch:
        salvar_batch(batch)

    logger.info("concluído. distribuição de fontes: %s", contagem_fonte)


if __name__ == "__main__":
    main()
