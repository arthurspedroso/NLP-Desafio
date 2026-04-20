# ANEEL RAG API — Contexto do Projeto

## Objetivo
Sistema de RAG (Retrieval-Augmented Generation) que responde perguntas sobre documentos do setor elétrico brasileiro (ANEEL). O desempenho será avaliado com base em um benchmark de perguntas reais anotadas por um especialista.

## Contexto do Desafio
- Grupo de estudos sobre agentes de IA (NLP, LLMs, RAG, Chunking, Parsing, Retrieval)
- Equipe de 3 pessoas, todos iniciantes em NLP
- Prazo: 7 dias
- Foco principal: pipeline de recuperação de informação robusta, não agentes
- Prioridade: **entregar rápido com código funcional**

## Dados de Entrada
- **3 arquivos JSON** na pasta `data/`, cada um cobrindo um ano diferente
- Cada JSON é organizado por **data** (ex: "2021-12-31")
- Cada data contém um array `registros[]`
- Estimativa: ~20.000 registros no total

### Estrutura de cada registro:
```json
{
  "numeracaoItem": "1.",
  "titulo": "DSP - DESPACHO 3284/2016",
  "autor": "ANEEL",
  "material": "Legislação",
  "esfera": "Esfera:Outros",
  "situacao": "Situação:NÃO CONSTA REVOGAÇÃO EXPRESSA",
  "assinatura": "Assinatura:15/12/2016",
  "publicacao": "Publicação:30/12/2016",
  "assunto": "Assunto:Acatamento",
  "ementa": "Texto descritivo do despacho ou null",
  "pdfs": [
    {
      "tipo": "Texto Integral:",
      "url": "http://www2.aneel.gov.br/cedoc/dsp20163284.pdf",
      "arquivo": "dsp20163284.pdf",
      "baixado": true
    }
  ]
}
```

### Campos importantes:
- **ementa** → resumo do documento (pode ser null)
- **titulo** → identifica o documento (DSP, PRT, REN, etc.)
- **autor** → superintendência responsável
- **assunto** → categoria do documento
- **situacao** → se foi revogado ou não
- **pdfs[].url** → URL do PDF para download e extração

---

## Visão Geral do Pipeline RAG

O padrão RAG tem dois grandes pipelines:

```
PIPELINE DE INDEXAÇÃO (roda uma vez):
  Documentos → Extração → Chunking → Embedding → Vector Store

PIPELINE DE CONSULTA (roda a cada pergunta):
  Pergunta → Embedding → Retrieval → Prompt → LLM → Resposta
```

---

## FASE 1 — ETL (Extração de Texto)

> Roda uma única vez em uma VM no GCP. Não precisa de API key.
> Output: tabela `documents` com texto bruto extraído de cada PDF.

### Estratégia de extração
```
PDF baixado para /tmp/
      ↓
Docling tenta extrair
  ├── Texto extraído com sucesso? → salva no banco
  └── Falhou ou texto vazio?
        ↓
      Tesseract OCR
        ├── Texto extraído? → salva no banco
        └── Falhou também? → loga o erro, salva registro sem texto
```

### Paralelismo com Threads
- Usa `ThreadPoolExecutor` com **12 workers**
- Cada worker: download → extração → salva no banco → deleta PDF
- Workers independentes: falha em um não afeta os outros
- Progresso salvo a cada registro (pode retomar se a VM cair)
- Disco nunca acumula: PDF deletado logo após extração

### VM recomendada no GCP
| Item | Especificação |
|---|---|
| Máquina | `e2-standard-8` (8 vCPUs, 32GB RAM) |
| Disco | 50GB SSD |
| SO | Ubuntu 22.04 LTS |
| Tempo estimado | 6 a 12 horas |

### Instalação na VM (Ubuntu)
```bash
sudo apt update
sudo apt install -y tesseract-ocr tesseract-ocr-por poppler-utils
pip install docling pytesseract requests psycopg2-binary sqlalchemy python-dotenv Pillow pdf2image
```

### Estrutura da Fase 1
```
etl/
├── loader.py        ← Lê os 3 JSONs, retorna lista de registros
├── extractor.py     ← Baixa PDF → Docling → Tesseract (fallback) → deleta PDF
├── db.py            ← Cria tabela documents, salva registros, verifica progresso
└── run_etl.py       ← Entry point com ThreadPoolExecutor
```

#### `etl/loader.py`
- Lê os 3 arquivos JSON de `data/`
- Itera por cada data e cada registro
- Retorna lista de todos os registros com metadados e URL do PDF

#### `etl/extractor.py`
- Baixa o PDF para `/tmp/{arquivo}.pdf`
- Tenta extração com Docling:
  ```python
  from docling.document_converter import DocumentConverter
  converter = DocumentConverter()
  result = converter.convert(caminho_pdf)
  texto = result.document.export_to_markdown()
  ```
- Se texto vazio → tenta Tesseract:
  ```python
  from pdf2image import convert_from_path
  import pytesseract
  imagens = convert_from_path(caminho_pdf)
  texto = "\n".join(pytesseract.image_to_string(img, lang="por") for img in imagens)
  ```
- Deleta o arquivo temporário após extração
- Retorna `{ metadados, texto, fonte: "docling"|"tesseract"|"erro" }`

#### `etl/db.py`
- Cria a tabela `documents`:
  ```sql
  CREATE TABLE documents (
    id          SERIAL PRIMARY KEY,
    titulo      TEXT,
    autor       TEXT,
    assunto     TEXT,
    situacao    TEXT,
    data_pub    TEXT,
    url_pdf     TEXT UNIQUE,
    texto_bruto TEXT,      -- texto extraído do PDF
    fonte       TEXT,      -- 'docling', 'tesseract' ou 'erro'
    processado  BOOLEAN DEFAULT FALSE,
    erro        TEXT
  );
  ```
- `salvar_registro(dados)` — insere no banco
- `ja_processado(url)` — checa se já foi feito (para retomar)

#### `etl/run_etl.py`
```python
from concurrent.futures import ThreadPoolExecutor, as_completed

with ThreadPoolExecutor(max_workers=12) as executor:
    futures = {executor.submit(processar_registro, r): r for r in pendentes}
    for future in as_completed(futures):
        resultado = future.result()
        salvar_registro(resultado)
        print(f"[{progresso}/{total}] {resultado['titulo']} ✓")
```

---

## FASE 2 — Indexação RAG (Chunking + Embedding)

> Roda após a Fase 1. Precisa de GEMINI_API_KEY.
> Lê o texto bruto do banco, quebra em chunks e gera embeddings.

### Por que chunking é necessário?
PDFs longos têm muitas páginas. Fazer embedding do documento inteiro perde precisão.
Chunking divide o texto em pedaços menores e sobrepostos, cada um com seu próprio embedding,
permitindo recuperar exatamente o trecho relevante para a pergunta.

### Estratégia de chunking
- Tamanho: **500 tokens** por chunk
- Overlap: **50 tokens** (sobreposição entre chunks para não perder contexto)
- Cada chunk herda os metadados do documento pai (titulo, autor, data, etc.)

```
Texto do documento (ex: 3 páginas)
      ↓ chunker.py
chunk 1: tokens 0-500   (com metadados)
chunk 2: tokens 450-950  (com metadados, overlap de 50)
chunk 3: tokens 900-1400 (com metadados, overlap de 50)
      ↓ embedder.py → Gemini text-embedding-004
vetor por chunk (768 dimensões)
      ↓ vectorstore.py
tabela chunks no PostgreSQL + pgvector
```

### Estrutura da Fase 2
```
indexing/
├── chunker.py       ← Quebra texto em chunks com overlap
└── embedder.py      ← Gera embeddings dos chunks via Gemini (lotes de 10)
```

#### `indexing/chunker.py`
- Lê documentos da tabela `documents` (texto_bruto não nulo)
- Quebra cada texto em chunks de 500 tokens com overlap de 50
- Adiciona metadados do documento pai em cada chunk:
  ```python
  {
    "document_id": 123,
    "titulo": "DSP - DESPACHO 3386/2016",
    "autor": "SCG/ANEEL",
    "data_pub": "30/12/2016",
    "texto_chunk": "...trecho do documento..."
  }
  ```
- Retorna lista de chunks prontos para embedding

#### `indexing/embedder.py`
- Recebe os chunks do chunker
- Chama Gemini `text-embedding-004` em lotes de 10
- Aguarda 1s entre lotes (rate limit)
- Salva cada chunk + vetor na tabela `chunks`

### Tabela `chunks` no banco:
```sql
CREATE TABLE chunks (
  id          SERIAL PRIMARY KEY,
  document_id INTEGER REFERENCES documents(id),
  titulo      TEXT,
  autor       TEXT,
  data_pub    TEXT,
  texto_chunk TEXT,
  embedding   vector(768)   -- pgvector
);
```

---

## FASE 3 — Consulta RAG (Retrieval + Generation)

> Pipeline de consulta. Roda a cada pergunta recebida pela API.

### Fluxo de consulta
```
POST /questions { "pergunta": "..." }
      ↓
retriever.py → gera embedding da pergunta
      ↓
vectorstore.py → busca os 5 chunks mais similares (cosine distance)
      ↓
prompt.py → monta contexto com os chunks + metadados
      ↓
llm.py → envia para Gemini Flash
      ↓
{ "resposta": "...", "fontes": ["DSP 3386/2016", ...] }
```

### Estrutura da Fase 3
```
retrieval/
├── vectorstore.py   ← Salva e busca chunks por similaridade no pgvector
└── retriever.py     ← Gera embedding da pergunta e busca chunks relevantes

generation/
├── prompt.py        ← Monta o prompt com chunks recuperados
└── llm.py           ← Chama Gemini Flash e retorna resposta
```

#### `retrieval/vectorstore.py`
```sql
-- Busca por cosine distance (operador <=> do pgvector)
SELECT titulo, autor, data_pub, texto_chunk,
       embedding <=> :vetor AS distance
FROM chunks
ORDER BY embedding <=> :vetor
LIMIT 5;
```

#### `retrieval/retriever.py`
- Recebe a pergunta em texto
- Gera embedding da pergunta via Gemini
- Chama vectorstore e retorna os 5 chunks mais relevantes

#### `generation/prompt.py`
Monta o prompt no formato:
```
Você é um especialista no setor elétrico brasileiro.
Com base APENAS nos trechos abaixo, responda a pergunta.
Se a resposta não estiver nos trechos, diga que não sabe.

[TITULO] DSP - DESPACHO 3386/2016
[AUTOR] SCG/ANEEL
[DATA] 30/12/2016
[TRECHO] ...texto do chunk...

---
[próximo chunk...]

Pergunta: {pergunta}

Responda de forma clara e cite o título do documento.
```

#### `generation/llm.py`
- Envia o prompt para `gemini-1.5-flash`
- Retorna a resposta em texto

---

## Estrutura Completa do Projeto

```
aneel-rag-api/
│
├── data/                       ← Os 3 JSONs da ANEEL
│   ├── ano1.json
│   ├── ano2.json
│   └── ano3.json
│
├── etl/                        ← FASE 1: Extração de texto (roda na VM GCP)
│   ├── loader.py
│   ├── extractor.py
│   ├── db.py
│   └── run_etl.py
│
├── indexing/                   ← FASE 2: Chunking + Embedding (pipeline de indexação)
│   ├── chunker.py
│   └── embedder.py
│
├── retrieval/                  ← FASE 3: Busca por similaridade (pipeline de consulta)
│   ├── vectorstore.py
│   └── retriever.py
│
├── generation/                 ← FASE 3: Geração de resposta (pipeline de consulta)
│   ├── prompt.py
│   └── llm.py
│
├── api/                        ← Endpoints FastAPI
│   ├── question_router.py      ← POST /questions
│   └── index_router.py         ← POST /index (dispara Fase 2)
│
├── database/
│   └── db.py                   ← Configuração SQLAlchemy + pgvector
│
├── main.py                     ← Entry point FastAPI
├── requirements.txt            ← Fase 2 e 3
├── requirements-etl.txt        ← Fase 1 (VM GCP)
├── docker-compose.yml
└── .env
```

---

## Stack Tecnológico

| Componente | Tecnologia |
|---|---|
| Runtime | Python 3.11+ |
| API | FastAPI |
| ORM | SQLAlchemy |
| Banco | PostgreSQL + pgvector |
| Extração PDF | Docling + Tesseract (fallback) |
| Embeddings | Gemini `text-embedding-004` |
| LLM | Gemini Flash `gemini-1.5-flash` |
| Paralelismo ETL | ThreadPoolExecutor |
| Infraestrutura | Docker + VM GCP (Fase 1) |

---

## Docker Compose
```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB: aneel_rag
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data

volumes:
  pgdata:
```

## Variáveis de Ambiente (.env)
```
DATABASE_HOST=localhost
DATABASE_PORT=5432
DATABASE_NAME=aneel_rag
DATABASE_USER=postgres
DATABASE_PASSWORD=postgres
GEMINI_API_KEY=sua_chave_aqui   # só necessário nas Fases 2 e 3
```

---

## Divisão de Tarefas da Equipe

### Pessoa 1 — ETL (Fase 1) — 1.5 a 2 dias
Arquivos: `etl/`
- Lê os JSONs e extrai URLs dos PDFs
- Download → Docling → Tesseract (fallback) → deleta PDF
- Paralelismo com ThreadPoolExecutor (12 workers)
- Sobe e roda na VM do GCP

### Pessoa 2 — Indexação + Banco (Fase 2) — 1.5 a 2 dias
Arquivos: `indexing/`, `retrieval/vectorstore.py`, `database/db.py`
- Configura PostgreSQL + pgvector
- Implementa chunking com overlap
- Gera embeddings via Gemini e salva na tabela `chunks`
- Implementa busca por cosine distance

### Pessoa 3 — Consulta + API (Fase 3) — 1 a 1.5 dias
Arquivos: `retrieval/retriever.py`, `generation/`, `api/`, `main.py`
- Implementa retriever (embedding da pergunta + busca)
- Monta e calibra o prompt
- Expõe endpoints com FastAPI

---

## Notas Técnicas

### pgvector — dimensão:
```sql
embedding vector(768)   -- text-embedding-004 do Gemini
```

### Rate limit Gemini (free tier):
- Embeddings: lotes de 10, aguardar 1s entre lotes
- LLM: 1 chamada por pergunta

### Retomada da Fase 1 em caso de falha:
```python
if ja_processado(registro["pdfs"][0]["url"]):
    continue  # pula e vai para o próximo
```

### Como rodar Fase 1 na VM GCP:
```bash
git clone <repo> && cd aneel-rag-api
sudo apt install -y tesseract-ocr tesseract-ocr-por poppler-utils
pip install -r requirements-etl.txt
python etl/run_etl.py
```

### Como rodar Fases 2 e 3 localmente:
```bash
docker-compose up -d
pip install -r requirements.txt
curl -X POST http://localhost:8000/index       # gera chunks + embeddings
curl -X POST http://localhost:8000/questions \
  -H "Content-Type: application/json" \
  -d '{"pergunta": "Qual PCH foi registrada no rio do Peixe?"}'
```