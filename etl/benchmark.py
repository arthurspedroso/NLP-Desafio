import random
import time
from collections import Counter

from etl.extractor import extrair
from etl.loader import carregar_registros


def main():
    random.seed(42)

    todos = carregar_registros()

    por_tipo = {"pdf": [], "html": [], "htm": [], "zip": [], "xlsx": []}
    for r in todos:
        tipo = r["tipo_arquivo"]
        if tipo in por_tipo:
            por_tipo[tipo].append(r)

    amostra = []
    amostra += random.sample(por_tipo["pdf"], 15)
    amostra += random.sample(por_tipo["html"] + por_tipo["htm"], min(3, len(por_tipo["html"]) + len(por_tipo["htm"])))
    amostra += random.sample(por_tipo["zip"], min(2, len(por_tipo["zip"])))

    print(f"Rodando benchmark em {len(amostra)} arquivos\n")

    fontes = Counter()
    tempos: list[tuple[str, float, str, int]] = []
    inicio = time.time()

    for i, reg in enumerate(amostra, 1):
        t0 = time.time()
        resultado = extrair(reg)
        dt = time.time() - t0

        fonte = resultado["fonte"]
        fontes[fonte] += 1
        tam = len(resultado.get("texto_bruto") or "")
        tempos.append((reg["tipo_arquivo"], dt, fonte, tam))

        print(f"[{i:2}/{len(amostra)}] {reg['tipo_arquivo']:5} {dt:5.1f}s {fonte:20} {tam:>7} chars  {reg.get('titulo', '')[:50]}")

    total = time.time() - inicio
    print(f"\nTempo total: {total:.1f}s")
    print(f"Média/arquivo: {total/len(amostra):.1f}s")
    print(f"\nDistribuição de fontes:")
    for fonte, n in sorted(fontes.items(), key=lambda x: -x[1]):
        print(f"  {fonte:20} {n}")


if __name__ == "__main__":
    main()
