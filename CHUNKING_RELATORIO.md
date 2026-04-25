# 📄 Relatório de Implementação — Módulo de Chunking

## 🔹 1. O que foi feito

Foi implementado um módulo completo de **chunking** para o pipeline RAG existente, responsável exclusivamente por transformar textos brutos armazenados no banco Postgres em chunks otimizados para posterior indexação e recuperação.

O pipeline de chunking executa **5 etapas sequenciais**:

| Etapa | Descrição |
|-------|-----------|
| **1. Busca** | Consulta a tabela `documents` buscando registros com `texto_limpo IS NOT NULL` |
| **2. Limpeza Residual** | Remove resíduos remanescentes: hífens de quebra de linha, espaços duplicados, quebras excessivas |
| **3. Divisão Semântica** | Separa o texto em seções usando blocos de parágrafos (`\n\s*\n`) |
| **4. Chunking** | Aplica `RecursiveCharacterTextSplitter` com `chunk_size=500` e `overlap=100` |
| **5. Filtro de Qualidade** | Remove chunks com menos de 100 caracteres |

> **Nota:** O módulo utiliza o campo `texto_limpo` do Postgres, que já foi processado pela etapa de limpeza do ETL (`etl/clean_text.py`). A etapa de limpeza residual no chunking serve apenas como garantia extra contra artefatos remanescentes.

### Justificativa das escolhas

- **`chunk_size = 500`**: Tamanho ideal para documentos regulatórios da ANEEL. Chunks de 500 caracteres são grandes o suficiente para manter contexto semântico (um parágrafo completo ou trecho significativo de um artigo), mas pequenos o suficiente para garantir precisão na busca vetorial. Documentos muito grandes (>1000 chars) tendem a diluir a relevância; chunks muito pequenos (<200 chars) perdem contexto.

- **`chunk_overlap = 100`**: O overlap de 100 caracteres (~20% do chunk) garante que informações que cruzam a fronteira entre dois chunks não sejam perdidas. Isso é especialmente importante em textos jurídicos/regulatórios onde uma frase pode depender da anterior para ter sentido completo.

- **`MIN_SECAO_CHARS = 50`**: Seções com menos de 50 caracteres geralmente são cabeçalhos, números de página, ou artefatos de extração de PDF — não contêm informação semântica útil.

- **`MIN_CHUNK_CHARS = 100`**: O filtro de qualidade final elimina chunks residuais que, mesmo após o splitting, são pequenos demais para conter informação significativa para retrieval.

---

## 🔹 2. Como foi feito

### Fluxo completo dos dados

```
┌──────────────────┐
│   PostgreSQL     │
│  (texto_limpo)   │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ buscar_documentos│  ← SELECT id, titulo, texto_limpo FROM documents
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  limpar_texto    │  ← Limpeza residual: hífens, espaços duplos, \n excessivos
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│dividir_por_secoes│  ← re.split(r'\n\s*\n', texto), filtra < 50 chars
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  gerar_chunks    │  ← RecursiveCharacterTextSplitter (500/100)
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│filtrar_qualidade │  ← Remove chunks < 100 chars
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│   dict resultado │  ← {doc_id: [chunk1, chunk2, ...]}
└──────────────────┘
```

### Descrição das funções

#### `buscar_documentos(engine)`
- **Parâmetro**: `engine` — SQLAlchemy engine conectado ao Postgres
- **Retorno**: Lista de dicts `[{id, titulo, texto_limpo}, ...]`
- **Lógica**: Executa `SELECT id, titulo, texto_limpo FROM documents WHERE texto_limpo IS NOT NULL AND texto_limpo != ''`
- Utiliza `texto_limpo` (já processado pelo ETL) em vez de `texto_bruto`
- Loga a quantidade de documentos encontrados

#### `limpar_texto(texto: str) -> str`
- **Parâmetro**: texto já limpo pelo ETL (`texto_limpo`)
- **Retorno**: texto com limpeza residual aplicada
- **Lógica** (limpeza residual, complementar ao `clean_text.py`):
  1. Remove hífens de quebra de linha remanescentes: `regu-\nlação` → `regulação`
  2. Remove hífens de quebra com espaço: `sub- bacia` → `subbacia`
  3. Colapsa 3+ quebras de linha em 2 (preserva parágrafos)
  4. Remove espaços duplicados
  5. Aplica `.strip()`

#### `dividir_por_secoes(texto: str) -> list[str]`
- **Parâmetro**: texto limpo
- **Retorno**: lista de seções (strings)
- **Lógica**:
  1. Divide por `re.split(r'\n\s*\n', texto)` — separa por linhas em branco
  2. Aplica `.strip()` em cada seção
  3. Remove seções com < 50 caracteres

#### `gerar_chunks(secoes: list[str]) -> list[str]`
- **Parâmetro**: lista de seções
- **Retorno**: lista de chunks
- **Lógica**: Aplica `RecursiveCharacterTextSplitter` em cada seção individualmente, concatenando os resultados
- **Separadores**: `["\n\n", "\n", ". ", " ", ""]` — hierarquia inteligente de pontos de corte

#### `filtrar_qualidade(chunks: list[str]) -> list[str]`
- **Parâmetro**: lista de chunks
- **Retorno**: lista filtrada
- **Lógica**: Remove chunks com menos de 100 caracteres

#### `processar_chunking(engine) -> dict[int, list[str]]`
- **Parâmetro**: SQLAlchemy engine
- **Retorno**: dicionário `{doc_id: [chunk1, chunk2, ...]}`
- **Lógica**: Orquestra todo o pipeline — busca → limpeza → divisão → chunking → filtro
- Loga progresso e estatísticas finais

---

## 🔹 3. Arquivos criados ou alterados

| Arquivo | Ação | Descrição |
|---------|------|-----------|
| `indexing/chunking.py` | **Novo** | Módulo principal com todas as funções de chunking |
| `indexing/__init__.py` | **Novo** | Init do pacote `indexing` (substitui o `.gitkeep`) |
| `requirements.txt` | **Alterado** | Adicionada dependência `langchain-text-splitters` |
| `CHUNKING_RELATORIO.md` | **Novo** | Este relatório de documentação |

---

## 🔹 4. Decisões técnicas

### Por que usar divisão semântica antes do chunking?

Aplicar `RecursiveCharacterTextSplitter` diretamente no texto inteiro de um documento pode gerar chunks que **cruzam fronteiras semânticas** — por exemplo, o final de um artigo misturado com o início de outro. Ao dividir primeiro por parágrafos (blocos separados por linhas em branco), garantimos que:

1. **Cada seção trata de um tema coeso** — parágrafos em documentos regulatórios geralmente correspondem a um assunto
2. **O splitter opera dentro de limites semânticos** — os chunks resultantes mantêm coerência interna
3. **Seções curtas (cabeçalhos, noise) são eliminadas antes** — evita gerar chunks lixo

### Por que usar overlap?

O overlap de 100 caracteres evita três problemas críticos:

1. **Perda de contexto na fronteira**: Uma frase que começa no final do chunk N e termina no início do chunk N+1 estaria fragmentada sem overlap
2. **Referências pronominais**: "Conforme o artigo anterior..." precisa do contexto do chunk anterior para ter sentido
3. **Precisão no retrieval**: Quando o usuário faz uma pergunta que se encaixa na fronteira de dois chunks, o overlap garante que ao menos um deles contenha a informação completa

### Por que RecursiveCharacterTextSplitter?

O `RecursiveCharacterTextSplitter` do LangChain é superior a um split simples por caracteres porque:

- Usa uma **hierarquia de separadores**: tenta primeiro dividir por `\n\n`, depois `\n`, depois `. `, depois espaço, e só em último caso por caractere
- Isso garante que os cortes aconteçam em **pontos naturais** do texto (fim de parágrafo > fim de linha > fim de frase > fim de palavra)
- Respeita `chunk_size` e `chunk_overlap` automaticamente

### Problemas evitados

| Problema | Solução aplicada |
|----------|-----------------|
| Chunks quebrados no meio de palavras | `RecursiveCharacterTextSplitter` com hierarquia de separadores |
| Perda de contexto entre chunks | Overlap de 100 caracteres |
| Chunks de ruído (cabeçalhos, números de página) | Filtro mínimo de 50 chars na seção + 100 chars no chunk |
| Hífens de quebra de linha | Regex para junção: `regu-\nlação` → `regulação` |
| Mistura de seções diferentes em um chunk | Divisão semântica antes do chunking |
| Texto com formatação suja | Etapa de limpeza dedicada |

---

## 🔹 5. Possíveis melhorias futuras

### Chunking por artigos jurídicos
Os documentos da ANEEL seguem estrutura de artigos (`Art. 1º`, `Art. 2º`, etc.). Uma melhoria seria detectar esses delimitadores via regex e criar chunks por artigo, respeitando a estrutura legal:
```python
re.split(r'(?=Art\.\s*\d+)', texto)
```

### Chunking baseado em tokens
Em vez de contar caracteres, contar tokens do modelo de embedding utilizado (via `tiktoken`). Isso garantiria que cada chunk respeite o limite de tokens do modelo e evitaria truncamento silencioso durante a geração de embeddings.

### Segmentação por NLP
Usar modelos de NLP (ex: `spaCy`, `nltk.punkt`) para segmentação por sentenças, garantindo que nenhum chunk comece ou termine no meio de uma frase, independentemente do idioma.

### Metadados nos chunks
Enriquecer cada chunk com metadados (título do documento, número da seção, posição no documento) para melhorar o ranqueamento no retrieval:
```python
{"text": "...", "doc_id": 1, "titulo": "REN 482", "secao": 3, "posicao": 0.45}
```

### Chunking adaptativo
Ajustar `chunk_size` dinamicamente baseado na complexidade do texto — documentos com tabelas poderiam usar chunks maiores, enquanto textos densos poderiam usar chunks menores.

### Cache de chunks
Armazenar os chunks gerados em uma coluna ou tabela separada no Postgres para evitar reprocessamento, com hash do texto_bruto para detectar alterações.

### Detecção de tabelas
Tratar tabelas (já extraídas em Markdown pelo ETL) de forma especial — mantê-las como chunks únicos em vez de quebrá-las, preservando a estrutura tabular.
