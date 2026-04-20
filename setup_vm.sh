#!/bin/bash
set -e

echo "=== Atualizando pacotes ==="
sudo apt update

echo "=== Instalando dependencias do sistema ==="
sudo apt install -y tesseract-ocr tesseract-ocr-por poppler-utils git python3-pip


echo "=== Instalando dependencias Python ==="
pip install -r requirements-etl.txt

echo "=== Configurando variaveis de ambiente ==="
cp .env.example .env
echo "Edite o arquivo .env com suas credenciais antes de continuar."

echo "=== Setup concluido! Para rodar o ETL: ==="
echo "python -m etl.run_etl"
