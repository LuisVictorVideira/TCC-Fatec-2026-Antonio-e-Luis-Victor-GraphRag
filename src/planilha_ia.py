#!/usr/bin/env python3
r"""
Copia para a planilha os julgamentos gravados pelo juiz de IA, sem alterar
nenhum valor.

O juiz grava um JSON com um objeto por linha da planilha, na mesma ordem:

    {"codigo": "R754", "julgamentos": ["P"], "justificativas": ["..."],
     "afirma_algo_errado": "N", "observacao": ""}

O script confere o formato (mesma ordem e mesmos codigos da planilha, um
julgamento e uma justificativa por componente, valores S, P ou N e S ou N)
e grava a planilha preenchida, com cinco colunas de justificativa no fim. O
calcular do responder.py le as colunas pelo nome e ignora as justificativas.

Uso:
    python src/planilha_ia.py --planilha outputs/respostas/julgamento.csv \
        --julgamentos outputs/respostas/julgamentos_ia.json \
        --saida outputs/respostas/julgamento_ia.csv
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path


def conferir(cab: list[str], linhas: list[list[str]], julgamentos: list[dict]) -> list[str]:
    """Devolve a lista de problemas de formato; vazia se estiver tudo certo."""
    problemas = []
    if len(julgamentos) != len(linhas):
        return [f"{len(julgamentos)} julgamentos para {len(linhas)} linhas"]
    col = {nome: i for i, nome in enumerate(cab)}
    n_max = sum(1 for nome in cab if nome.startswith("componente_"))
    campos = {"codigo", "julgamentos", "justificativas", "afirma_algo_errado", "observacao"}
    for linha, j in zip(linhas, julgamentos):
        cod = linha[col["codigo"]]
        if set(j) != campos:
            problemas.append(f"{cod}: campos {sorted(j)}")
            continue
        if j["codigo"] != cod:
            problemas.append(f"ordem diferente: planilha {cod}, julgamento {j['codigo']}")
        n = sum(1 for k in range(1, n_max + 1) if linha[col[f"componente_{k}"]].strip())
        if len(j["julgamentos"]) != n or len(j["justificativas"]) != n:
            problemas.append(f"{cod}: {n} componentes, {len(j['julgamentos'])} julgamentos, "
                             f"{len(j['justificativas'])} justificativas")
        if any(v not in ("S", "P", "N") for v in j["julgamentos"]):
            problemas.append(f"{cod}: julgamentos {j['julgamentos']}")
        if j["afirma_algo_errado"] not in ("S", "N"):
            problemas.append(f"{cod}: afirma_algo_errado {j['afirma_algo_errado']!r}")
    return problemas


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--planilha", type=Path, required=True, help="a planilha em branco")
    ap.add_argument("--julgamentos", type=Path, required=True, help="o JSON do juiz")
    ap.add_argument("--saida", type=Path, required=True)
    args = ap.parse_args()

    with args.planilha.open(encoding="utf-8-sig", newline="") as fh:
        leitor = csv.reader(fh, delimiter=";")
        cab = next(leitor)
        linhas = list(leitor)
    julgamentos = json.loads(args.julgamentos.read_text(encoding="utf-8"))
    problemas = conferir(cab, linhas, julgamentos)
    if problemas:
        print("ERRO de formato:\n  " + "\n  ".join(problemas[:20]), file=sys.stderr)
        return 1

    col = {nome: i for i, nome in enumerate(cab)}
    n_max = sum(1 for nome in cab if nome.startswith("componente_"))
    saida = []
    for linha, j in zip(linhas, julgamentos):
        nova = list(linha)
        justificativas = [""] * n_max
        comps = [k for k in range(1, n_max + 1) if linha[col[f"componente_{k}"]].strip()]
        for k, valor, just in zip(comps, j["julgamentos"], j["justificativas"]):
            nova[col[f"julgamento_{k}"]] = valor
            justificativas[k - 1] = just
        nova[col["afirma_algo_errado"]] = j["afirma_algo_errado"]
        nova[col["observacao"]] = j["observacao"]
        saida.append(nova + justificativas)
    args.saida.parent.mkdir(parents=True, exist_ok=True)
    with args.saida.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(cab + [f"justificativa_{k}" for k in range(1, n_max + 1)])
        w.writerows(saida)
    valores = [v for j in julgamentos for v in j["julgamentos"]]
    print(f"[planilha-ia] {len(saida)} linhas, {len(valores)} componentes: "
          f"S {valores.count('S')}, P {valores.count('P')}, N {valores.count('N')}")
    print(f"[planilha-ia] -> {args.saida} "
          f"(SHA-256 {hashlib.sha256(args.saida.read_bytes()).hexdigest()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
