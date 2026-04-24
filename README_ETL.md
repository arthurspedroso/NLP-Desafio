# ANEEL RAG — ETL

Pipeline de extração de texto dos documentos do setor elétrico brasileiro (ANEEL).
Baixa os PDFs, extrai o texto e salva em um banco PostgreSQL pronto para a fase de indexação RAG.

---

## Pré-requisitos

- [Docker](https://docs.docker.com/get-docker/) e Docker Compose instalados
- Arquivos JSON dos dados da ANEEL na pasta `data/`

---

## Opção 1 — Restaurar dump pronto (recomendado)

Se você recebeu o arquivo `aneel_rag_limpo.dump`, não precisa rodar o ETL.
São 27.008 documentos já extraídos e com texto limpo.

**1. Sobe o banco:**
```bash
docker compose up -d postgres
```

**2. Restaura o dump** (substitua pelo caminho onde o arquivo está):
```bash
docker exec -i api-rag-postgres-1 pg_restore -U postgres -d aneel_rag < /caminho/para/aneel_rag_limpo.dump
```

**3. Verifica se funcionou:**
```bash
docker exec api-rag-postgres-1 psql -U postgres -d aneel_rag -c "SELECT COUNT(*) FROM documents;"
```
Deve retornar `27008`.

---

## Opção 2 — Rodar o ETL do zero

Use essa opção se quiser extrair os documentos você mesmo.
O processo completo leva entre 6 e 12 horas dependendo da máquina.

### Configuração

Crie um arquivo `.env` na raiz do projeto:
```
DATABASE_HOST=localhost
DATABASE_PORT=5433
DATABASE_NAME=aneel_rag
DATABASE_USER=postgres
DATABASE_PASSWORD=postgres
```

### Passo a passo

**1. Sobe o banco:**
```bash
docker compose up -d postgres
```

**2. Constrói e inicia o ETL:**
```bash
docker compose --profile etl up --build etl
```

O container vai processar todos os documentos dos JSONs em paralelo e salvar no banco.
O progresso é exibido no terminal e pode ser retomado se interrompido.

**3. Após o ETL terminar, popula o texto limpo:**
```bash
docker compose --profile etl run etl python -m etl.clean_text
```

**4. Verifica o resultado:**
```bash
docker exec api-rag-postgres-1 psql -U postgres -d aneel_rag -c "
SELECT fonte, COUNT(*) FROM documents GROUP BY fonte ORDER BY COUNT(*) DESC;
"
```

### Reprocessar falhas

Se quiser retentar documentos que falharam no download:
```bash
docker compose --profile etl run etl python -m etl.run_etl --retry-fallbacks
```

---

## O que cada arquivo faz

```
etl/
├── loader.py       Lê os JSONs de data/ e retorna a lista de documentos com metadados e URL
├── extractor.py    Baixa o PDF → tenta PyMuPDF → tenta Tesseract OCR → fallback para ementa
├── db.py           Cria a tabela documents, salva em batch, controla o que já foi processado
├── run_etl.py      Entry point: orquestra tudo em paralelo com ProcessPoolExecutor
├── clean_text.py   Lê texto_bruto, remove ruído de UI e normaliza espaços, salva em texto_limpo
└── benchmark.py    Testa a extração em uma amostra pequena para validar o pipeline
```

### Estratégia de extração por tipo de arquivo

| Tipo | Estratégia |
|---|---|
| PDF digital | PyMuPDF extrai texto e tabelas diretamente |
| PDF escaneado | Tesseract OCR (fallback quando PyMuPDF não encontra texto suficiente) |
| HTML | BeautifulSoup remove scripts e extrai texto |
| ZIP / XLSX / outros | Usa a ementa do JSON como texto |

### Fontes no banco após o ETL

| fonte | Significado |
|---|---|
| `pymupdf` | Texto extraído digitalmente |
| `pymupdf_tabelas` | Texto + tabelas em markdown |
| `tesseract` | Texto via OCR |
| `html` | Texto extraído do HTML |
| `ementa_fallback` | PDF falhou, usou o resumo do JSON |
| `ementa_direta` | Tipo não suportado, usou o resumo do JSON |
| `erro` | Falha total (URL quebrada no servidor da ANEEL) |

---

## Gerar um novo dump

Após o ETL e o clean_text, para exportar o banco:
```bash
docker exec api-rag-postgres-1 pg_dump -U postgres -Fc aneel_rag > /caminho/para/pasta/aneel_rag_limpo.dump
```
