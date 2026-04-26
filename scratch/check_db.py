import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

def check_db():
    db_url = f"postgresql://{os.getenv('DATABASE_USER')}:{os.getenv('DATABASE_PASSWORD')}@{os.getenv('DATABASE_HOST')}:{os.getenv('DATABASE_PORT')}/{os.getenv('DATABASE_NAME')}"
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT count(*) FROM documents WHERE texto_limpo IS NOT NULL AND texto_limpo != ''"))
            count = result.scalar()
            print(f"Conexão com Banco de Dados: OK")
            print(f"Documentos prontos para processamento: {count}")
    except Exception as e:
        print(f"Erro ao conectar ao Banco de Dados: {e}")

if __name__ == "__main__":
    check_db()
