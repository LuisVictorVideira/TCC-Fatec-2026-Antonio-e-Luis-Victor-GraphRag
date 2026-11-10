#!/usr/bin/env python3
r"""
Precisao das relacoes extraidas pela LLM, numa amostra julgada as cegas.

Subcomandos:
    amostra      sorteia 60 relacoes validas da extracao (semente 42) e grava
                 a planilha do juiz, so com o trecho e a relacao, e a chave,
                 num arquivo separado
    planilha-ia  copia para a planilha os julgamentos do juiz de IA,
                 conferindo o formato, sem mudar nada
    calcular     le a planilha julgada e a chave e calcula a proporcao de S e
                 a de S ou P, com o intervalo de 95% de Wilson; por tipo, so
                 descritivo

Julgamento:
    S  o trecho afirma a relacao, com o tipo e a direcao certos
    P  o trecho afirma a relacao, mas o tipo ou a direcao sao discutiveis
    N  o trecho nao afirma a relacao

A populacao sao as relacoes validas de cada trecho, e nao as arestas
consolidadas: a mesma relacao afirmada em dois trechos conta duas vezes. O
trecho vai ao juiz na mesma representacao que o modelo leu.

Uso:
    python src/precisao_relacoes.py amostra --extracao outputs/relacoes/relacoes_llm.jsonl \
        --trechos outputs/trechos.jsonl --saida outputs/relacoes/precisao.csv \
        --chave outputs/relacoes/precisao_chave.json
    python src/precisao_relacoes.py planilha-ia --planilha outputs/relacoes/precisao.csv \
        --julgamentos outputs/relacoes/precisao_julgamentos_ia.json \
        --saida outputs/relacoes/precisao_ia.csv
    python src/precisao_relacoes.py calcular --planilha outputs/relacoes/precisao_ia.csv \
        --chave outputs/relacoes/precisao_chave.json
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from baselines import carregar_trechos, impressao_digital, texto_para_indice

N_AMOSTRA = 60
SEMENTE = 42
VALORES = ("S", "P", "N")
COLUNAS = ["id", "trecho", "origem", "tipo", "destino", "julgamento", "justificativa"]
Z95 = 1.959963984540054


def sha_arquivo(caminho: Path) -> str:
    h = hashlib.sha256()
    with caminho.open("rb") as fh:
        for bloco in iter(lambda: fh.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def ler_jsonl(caminho: Path) -> list[dict]:
    with caminho.open(encoding="utf-8") as fh:
        return [json.loads(linha) for linha in fh if linha.strip()]


def relacoes_validas(registros: list[dict]) -> list[dict]:
    """Uma entrada por relacao valida, do registro mais recente de cada trecho
    sem erro, em ordem fixa (trecho e posicao na resposta)."""
    ultimos = {}
    for r in registros:
        ultimos[r["chunk_id"]] = r
    itens = []
    for cid in sorted(ultimos):
        r = ultimos[cid]
        if r.get("erro"):
            continue
        for i, rel in enumerate(r.get("relacoes", [])):
            itens.append({"chunk_id": cid, "indice": i, "origem": rel["origem"], "tipo": rel["tipo"],
                          "destino": rel["destino"], "origem_rotulo": rel["origem_rotulo"],
                          "destino_rotulo": rel["destino_rotulo"],
                          "nome_normalizado": bool(rel.get("nome_normalizado"))})
    return itens


def sortear(itens: list[dict], n: int = N_AMOSTRA, semente: int = SEMENTE) -> list[dict]:
    if len(itens) < n:
        raise SystemExit(f"ERRO: so ha {len(itens)} relacoes validas, menos que a amostra de {n}.")
    return random.Random(semente).sample(itens, n)


def wilson(sucessos: int, n: int, z: float = Z95) -> tuple[float, float]:
    """Intervalo de Wilson (1927) para uma proporcao."""
    if n == 0:
        return (math.nan, math.nan)
    p = sucessos / n
    den = 1 + z * z / n
    centro = (p + z * z / (2 * n)) / den
    meia = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, centro - meia), min(1.0, centro + meia))


def ler_planilha(caminho: Path) -> list[dict]:
    with caminho.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def gravar_planilha(caminho: Path, linhas: list[dict]) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with caminho.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUNAS)
        w.writeheader()
        for linha in linhas:
            w.writerow({k: linha.get(k, "") for k in COLUNAS})


def cmd_amostra(args) -> int:
    registros = ler_jsonl(args.extracao)
    trechos = carregar_trechos(args.trechos)
    por_id = {t["chunk_id"]: t for t in trechos}
    itens = relacoes_validas(registros)
    faltando = sorted({i["chunk_id"] for i in itens} - set(por_id))
    if faltando:
        raise SystemExit(f"ERRO: {len(faltando)} trechos da extracao nao estao no arquivo de trechos.")
    amostra = sortear(itens, args.n, args.semente)
    linhas, chave = [], []
    for k, item in enumerate(amostra, 1):
        codigo = f"R{k:02d}"
        linhas.append({"id": codigo, "trecho": texto_para_indice(por_id[item["chunk_id"]]).strip(),
                       "origem": item["origem"], "tipo": item["tipo"], "destino": item["destino"]})
        chave.append(dict(item, id=codigo))
    gravar_planilha(args.saida, linhas)
    args.chave.parent.mkdir(parents=True, exist_ok=True)
    args.chave.write_text(json.dumps({
        "semente": args.semente, "n": args.n, "populacao": len(itens),
        "extracao": str(args.extracao), "sha256_extracao": sha_arquivo(args.extracao),
        "impressao_digital_trechos": impressao_digital(trechos),
        "sha256_planilha": sha_arquivo(args.saida),
        "gerado_em": datetime.now().isoformat(timespec="seconds"), "itens": chave},
        indent=1, ensure_ascii=False), encoding="utf-8")
    tipos = Counter(i["tipo"] for i in amostra)
    print(f"[amostra] {args.n} de {len(itens):,} relacoes validas (semente {args.semente}); "
          f"por tipo: {dict(sorted(tipos.items()))}")
    print(f"[amostra] planilha -> {args.saida} (SHA-256 {sha_arquivo(args.saida)[:16]}...); chave -> {args.chave}")
    return 0


def cmd_planilha_ia(args) -> int:
    linhas = ler_planilha(args.planilha)
    julgados = json.loads(args.julgamentos.read_text(encoding="utf-8"))
    if not isinstance(julgados, list):
        raise SystemExit("ERRO: o arquivo do juiz nao e uma lista.")
    if [j.get("id") for j in julgados] != [l["id"] for l in linhas]:
        raise SystemExit("ERRO: os codigos do juiz nao sao os da planilha, na mesma ordem.")
    for j in julgados:
        if j.get("julgamento") not in VALORES:
            raise SystemExit(f"ERRO: {j.get('id')}: julgamento {j.get('julgamento')!r} fora de S, P e N.")
        if not isinstance(j.get("justificativa"), str) or not j["justificativa"].strip():
            raise SystemExit(f"ERRO: {j.get('id')}: sem justificativa.")
    for linha, j in zip(linhas, julgados):
        linha["julgamento"], linha["justificativa"] = j["julgamento"], j["justificativa"].strip()
    gravar_planilha(args.saida, linhas)
    print(f"[planilha-ia] {len(linhas)} julgamentos copiados: "
          f"{dict(sorted(Counter(l['julgamento'] for l in linhas).items()))} -> {args.saida}")
    return 0


def cmd_calcular(args) -> int:
    linhas = ler_planilha(args.planilha)
    chave = json.loads(args.chave.read_text(encoding="utf-8"))
    itens = {i["id"]: i for i in chave["itens"]}
    if [l["id"] for l in linhas] != [i["id"] for i in chave["itens"]]:
        raise SystemExit("ERRO: a planilha e a chave nao tem os mesmos codigos, na mesma ordem.")
    for l in linhas:
        if l["julgamento"].strip().upper() not in VALORES:
            raise SystemExit(f"ERRO: {l['id']}: julgamento {l['julgamento']!r} fora de S, P e N.")
        if (l["origem"], l["tipo"], l["destino"]) != (itens[l["id"]]["origem"], itens[l["id"]]["tipo"],
                                                      itens[l["id"]]["destino"]):
            raise SystemExit(f"ERRO: {l['id']}: a relacao da planilha nao e a da chave.")
    valores = [l["julgamento"].strip().upper() for l in linhas]
    n = len(valores)
    cont = Counter(valores)
    s, sp = cont["S"], cont["S"] + cont["P"]
    resultado = {
        "n": n, "contagem": {v: cont[v] for v in VALORES},
        "proporcao_S": s / n, "ic95_S": wilson(s, n),
        "proporcao_S_ou_P": sp / n, "ic95_S_ou_P": wilson(sp, n),
        "metodo_ic": "Wilson (1927), 95%",
        "por_tipo": {t: {v: sum(1 for l, x in zip(linhas, valores) if l["tipo"] == t and x == v) for v in VALORES}
                     for t in sorted({l["tipo"] for l in linhas})},
        "planilha": str(args.planilha), "sha256_planilha": sha_arquivo(args.planilha),
    }
    destino = args.saida or args.planilha.with_suffix(".resultado.json")
    destino.write_text(json.dumps(resultado, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"[calcular] {n} relacoes: S {cont['S']}, P {cont['P']}, N {cont['N']}")
    print(f"[calcular] S: {s / n:.3f} (IC95% Wilson [{resultado['ic95_S'][0]:.3f}, {resultado['ic95_S'][1]:.3f}])")
    print(f"[calcular] S ou P: {sp / n:.3f} (IC95% Wilson [{resultado['ic95_S_ou_P'][0]:.3f}, "
          f"{resultado['ic95_S_ou_P'][1]:.3f}])")
    print(f"[calcular] por tipo: {resultado['por_tipo']} -> {destino}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("amostra")
    p.add_argument("--extracao", type=Path, required=True)
    p.add_argument("--trechos", type=Path, required=True)
    p.add_argument("--saida", type=Path, required=True, help="planilha do juiz (CSV)")
    p.add_argument("--chave", type=Path, required=True)
    p.add_argument("--n", type=int, default=N_AMOSTRA)
    p.add_argument("--semente", type=int, default=SEMENTE)
    p.set_defaults(func=cmd_amostra)

    p = sub.add_parser("planilha-ia")
    p.add_argument("--planilha", type=Path, required=True)
    p.add_argument("--julgamentos", type=Path, required=True)
    p.add_argument("--saida", type=Path, required=True)
    p.set_defaults(func=cmd_planilha_ia)

    p = sub.add_parser("calcular")
    p.add_argument("--planilha", type=Path, required=True)
    p.add_argument("--chave", type=Path, required=True)
    p.add_argument("--saida", type=Path, default=None)
    p.set_defaults(func=cmd_calcular)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
