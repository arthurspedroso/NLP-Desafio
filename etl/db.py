import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = (
    f"postgresql://{os.getenv('DATABASE_USER', 'postgres')}"
    f":{os.getenv('DATABASE_PASSWORD', 'postgres')}"
    f"@{os.getenv('DATABASE_HOST', 'localhost')}"
    f":{os.getenv('DATABASE_PORT', '5433')}"
    f"/{os.getenv('DATABASE_NAME', 'aneel_rag')}"
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)


def criar_tabela():
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS documents (
                id           SERIAL PRIMARY KEY,
                titulo       TEXT,
                autor        TEXT,
                assunto      TEXT,
                situacao     TEXT,
                data_pub     TEXT,
                url_pdf      TEXT UNIQUE,
                tipo_arquivo TEXT,
                texto_bruto  TEXT,
                texto_limpo  TEXT,
                fonte        TEXT,
                processado   BOOLEAN DEFAULT FALSE,
                erro         TEXT
            )
        """))
        conn.execute(text(
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS texto_limpo TEXT"
        ))


def urls_processadas() -> set[str]:
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT url_pdf FROM documents
            WHERE texto_bruto IS NOT NULL
              AND texto_bruto != ''
              AND fonte != 'erro'
              AND processado = TRUE
        """))
        return {row[0] for row in result}


def salvar_batch(registros: list[dict]):
    if not registros:
        return
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO documents (
                titulo, autor, assunto, situacao, data_pub,
                url_pdf, tipo_arquivo, texto_bruto, fonte, processado, erro
            ) VALUES (
                :titulo, :autor, :assunto, :situacao, :data_pub,
                :url_pdf, :tipo_arquivo, :texto_bruto, :fonte, :processado, :erro
            )
            ON CONFLICT (url_pdf) DO UPDATE SET
                texto_bruto  = EXCLUDED.texto_bruto,
                fonte        = EXCLUDED.fonte,
                tipo_arquivo = EXCLUDED.tipo_arquivo,
                processado   = EXCLUDED.processado,
                erro         = EXCLUDED.erro
        """), [
            {
                "titulo":       r.get("titulo"),
                "autor":        r.get("autor"),
                "assunto":      r.get("assunto"),
                "situacao":     r.get("situacao"),
                "data_pub":     r.get("data_pub"),
                "url_pdf":      r.get("url_pdf"),
                "tipo_arquivo": r.get("tipo_arquivo"),
                "texto_bruto":  r.get("texto_bruto"),
                "fonte":        r.get("fonte"),
                "processado":   r.get("fonte") != "erro",
                "erro":         r.get("erro"),
            }
            for r in registros
        ])


def resetar_fallbacks():
    with engine.begin() as conn:
        result = conn.execute(text("""
            UPDATE documents
            SET processado = FALSE
            WHERE fonte IN ('ementa_fallback', 'erro')
        """))
        return result.rowcount
