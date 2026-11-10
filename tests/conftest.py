"""Configuracao comum dos testes.

Os scripts exigem NEO4J_PASSWORD na importacao, mas os testes nao abrem
conexao com o banco: usam so as funcoes de parsing, fragmentacao e
casamento. O valor abaixo so entra quando a variavel nao estiver definida.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("NEO4J_PASSWORD", "teste-sem-banco")

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
