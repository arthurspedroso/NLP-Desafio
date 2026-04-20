#!/bin/bash
set -e

echo "=== Atualizando pacotes ==="
sudo apt update

echo "=== Instalando dependencias do sistema ==="
sudo apt install -y tesseract-ocr tesseract-ocr-por poppler-utils git python3-pip python3-venv python3-full

echo "=== Criando virtualenv ==="
python3 -m venv venv
source venv/bin/activate

echo "=== Instalando dependencias Python ==="
pip install -r requirements-etl.txt

echo "=== Configurando variaveis de ambiente ==="
cp .env.example .env
echo "Edite o arquivo .env com suas credenciais antes de continuar."

echo "=== Setup concluido! Para rodar o ETL: ==="
echo "source venv/bin/activate"
echo "python -m etl.run_etl"
