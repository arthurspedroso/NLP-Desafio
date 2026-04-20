import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = (
    f"postgresql://{os.getenv('DATABASE_USER', 'postgres')}"
    f":{os.getenv('DATABASE_PASSWORD', 'postgres')}"
    f"@{os.getenv('DATABASE_HOST', 'localhost')}"
    f":{os.getenv('DATABASE_PORT', '5432')}"
    f"/{os.getenv('DATABASE_NAME', 'aneel_rag')}"
)

engine = create_engine(DATABASE_URL)


def criar_tabela():
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS documents (
                id          SERIAL PRIMARY KEY,
                titulo      TEXT,
                autor       TEXT,
                assunto     TEXT,
                situacao    TEXT,
                data_pub    TEXT,
                url_pdf     TEXT UNIQUE,
                texto_bruto TEXT,
                fonte       TEXT,
                processado  BOOLEAN DEFAULT FALSE,
                erro        TEXT
            )
        """))


def ja_processado(url: str) -> bool:
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT 1 FROM documents WHERE url_pdf = :url AND processado = TRUE"),
            {"url": url}
        )
        return result.fetchone() is not None


def salvar_registro(dados: dict):
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO documents (titulo, autor, assunto, situacao, data_pub, url_pdf, texto_bruto, fonte, processado, erro)
            VALUES (:titulo, :autor, :assunto, :situacao, :data_pub, :url_pdf, :texto_bruto, :fonte, :processado, :erro)
            ON CONFLICT (url_pdf) DO UPDATE SET
                texto_bruto = EXCLUDED.texto_bruto,
                fonte       = EXCLUDED.fonte,
                processado  = EXCLUDED.processado,
                erro        = EXCLUDED.erro
        """), {
            "titulo":      dados.get("titulo"),
            "autor":       dados.get("autor"),
            "assunto":     dados.get("assunto"),
            "situacao":    dados.get("situacao"),
            "data_pub":    dados.get("data_pub"),
            "url_pdf":     dados.get("url_pdf"),
            "texto_bruto": dados.get("texto_bruto"),
            "fonte":       dados.get("fonte"),
            "processado":  dados.get("fonte") != "erro",
            "erro":        dados.get("erro"),
        })
