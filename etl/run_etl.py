from concurrent.futures import ThreadPoolExecutor, as_completed
from etl.loader import carregar_registros
from etl.extractor import extrair
from etl.db import criar_tabela, ja_processado, salvar_registro


def processar(registro: dict) -> dict:
    return extrair(registro)


def main():
    criar_tabela()

    todos = carregar_registros()
    pendentes = [r for r in todos if not ja_processado(r["url_pdf"])]

    total = len(todos)
    ja_feitos = total - len(pendentes)
    print(f"Total: {total} | Já processados: {ja_feitos} | Pendentes: {len(pendentes)}")

    if not pendentes:
        print("Nada a processar.")
        return

    concluidos = ja_feitos
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {executor.submit(processar, r): r for r in pendentes}
        for future in as_completed(futures):
            resultado = future.result()
            salvar_registro(resultado)
            concluidos += 1
            status = resultado["fonte"]
            print(f"[{concluidos}/{total}] {resultado.get('titulo')} — {status}")


if __name__ == "__main__":
    main()
