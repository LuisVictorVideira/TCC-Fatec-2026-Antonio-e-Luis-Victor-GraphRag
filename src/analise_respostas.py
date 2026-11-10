#!/usr/bin/env python3
r"""
Analise descritiva das respostas, por pergunta, sem teste estatistico.

Cruza a nota de cada resposta com o contexto que o modelo recebeu:

- quantos componentes do ouro estavam nos trechos do contexto;
- quantos estavam entre os 5 primeiros do run, mas ficaram fora do contexto
  pelo limite de 6.000 tokens estimados;
- se a resposta so diz que os trechos nao respondem (abstencao) ou diz isso
  e tenta responder assim mesmo (ressalva).

Com --resultado, confere que as notas medias, as respostas totalmente certas
e as com afirmacao errada batem com o resultado do calcular do responder.py.

Uso:
    python src/analise_respostas.py --perguntas dados/perguntas_avaliacao.yaml \
        --planilha outputs/respostas/julgamento_ia.csv \
        --chave outputs/respostas/julgamento_chave.json \
        --respostas outputs/respostas/respostas.jsonl \
        --por-pergunta outputs/avaliacao/recuperacao.por_pergunta.csv \
        --resultado outputs/respostas/julgamento_ia_resultado.json \
        --saida outputs/respostas/analise_respostas.csv
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from responder import VALORES, ler_planilha

import yaml

CONDICOES = ("sem_contexto", "bm25", "grafo")
COM_CONTEXTO = ("bm25", "grafo")
# O prompt com contexto pede: "If the excerpts do not contain the answer,
# say that the excerpts do not answer the question."
NAO_RESPONDE = re.compile(
    r"(excerpts?|documentation|provided (text|information|context))[^.]{0,120}?"
    r"(do not|does not|don't|doesn't|did not|not)\s+(\w+\s+){0,3}?"
    r"(answer|contain|provide|mention|address|specify|cover|include|explicitly)"
    r"|not (explicitly )?(mentioned|provided|specified|covered|addressed|stated) in the (provided )?"
    r"(documentation )?(excerpts|documentation)", re.I)
MAX_PALAVRAS_ABSTENCAO = 12


def tipo_de_resposta(texto: str) -> str:
    """abstencao: so diz que os trechos nao respondem; ressalva: diz isso e
    tenta responder assim mesmo; vazio: nenhum dos dois."""
    if not NAO_RESPONDE.search(texto):
        return ""
    return "abstencao" if len(texto.split()) <= MAX_PALAVRAS_ABSTENCAO else "ressalva"


def carregar_ouro(caminho: Path) -> dict:
    dados = yaml.safe_load(caminho.read_text(encoding="utf-8"))
    itens = dados["perguntas"] if isinstance(dados, dict) else dados
    ouro = {}
    for q in itens:
        if str(q.get("verificado", "")).strip().lower() != "sim":
            continue
        comps = [c for c in (q.get("componentes") or []) if c.get("essencial", True)]
        ouro[str(q["id"])] = {"trivial": str(q.get("trivial_lexical", "nao")).strip().lower() == "sim",
                              "escopo": q.get("escopo", ""),
                              "trechos": [set(c.get("trechos") or []) for c in comps]}
    return ouro


def montar_linhas(ouro: dict, planilha: list[dict], chave: dict, respostas: list[dict],
                  r5: dict) -> list[dict]:
    julg, errado, nota = defaultdict(dict), defaultdict(dict), defaultdict(dict)
    for linha in planilha:
        q, c = chave[linha["codigo"]]["id"], chave[linha["codigo"]]["condicao"]
        js = [(linha[f"julgamento_{k}"] or "").strip().upper() for k in range(1, len(ouro[q]["trechos"]) + 1)]
        julg[q][c] = [{1.0: "S", 0.5: "P", 0.0: "N"}[VALORES[j]] for j in js]
        nota[q][c] = statistics.mean(VALORES[j] for j in js)
        errado[q][c] = (linha.get("afirma_algo_errado") or "").strip().upper() in ("S", "SIM")
    contexto, texto = defaultdict(dict), defaultdict(dict)
    for r in respostas:
        contexto[r["id"]][r["condicao"]] = list(r.get("trechos") or [])
        texto[r["id"]][r["condicao"]] = r["resposta"]
    linhas = []
    for q in sorted(ouro):
        o = ouro[q]
        n = len(o["trechos"])
        reg = {"pergunta": q, "trivial": int(o["trivial"]), "escopo": o["escopo"], "n_componentes": n}
        for c in CONDICOES:
            reg[f"nota_{c}"] = nota[q][c]
            reg[f"julgamentos_{c}"] = "".join(julg[q][c])
            reg[f"errado_{c}"] = int(errado[q][c])
            reg[f"tipo_{c}"] = tipo_de_resposta(texto[q][c])
        for c in COM_CONTEXTO:
            usados = set(contexto[q][c])
            cobertos = [i for i, ts in enumerate(o["trechos"]) if usados & ts]
            no_top5 = round(r5[c][q] * n)
            reg[f"R@5_{c}"] = r5[c][q]
            reg[f"trechos_{c}"] = len(contexto[q][c])
            reg[f"no_contexto_{c}"] = len(cobertos)
            reg[f"fora_pelo_limite_{c}"] = no_top5 - len(cobertos)
            reg[f"julg_no_contexto_{c}"] = "".join(julg[q][c][i] for i in cobertos)
            reg[f"julg_fora_do_contexto_{c}"] = "".join(j for i, j in enumerate(julg[q][c]) if i not in cobertos)
        linhas.append(reg)
    return linhas


def media(v: list[float]) -> float:
    return statistics.mean(v) if v else float("nan")


def nota_de(julgamentos: str) -> float:
    return media([VALORES[j] for j in julgamentos]) if julgamentos else float("nan")


def relatorio(linhas: list[dict]) -> dict:
    """Imprime as tabelas e devolve os agregados por condicao e recorte."""
    recortes = {"todas": linhas,
                "sem_triviais": [r for r in linhas if not r["trivial"]],
                "triviais": [r for r in linhas if r["trivial"]]}
    agregados = {}
    print("| recorte | condicao | n | nota media | totalmente certas | nota zero | afirma algo errado |")
    print("|---|---|---|---|---|---|---|")
    for nome, rs in recortes.items():
        for c in CONDICOES:
            notas = [r[f"nota_{c}"] for r in rs]
            ag = {"n": len(rs), "nota_media": media(notas),
                  "totalmente_certas": sum(1 for x in notas if x == 1.0),
                  "nota_zero": sum(1 for x in notas if x == 0.0),
                  "afirma_algo_errado": sum(r[f"errado_{c}"] for r in rs)}
            agregados[f"{c}|{nome}"] = ag
            print(f"| {nome} | {c} | {ag['n']} | {ag['nota_media']:.3f} | {ag['totalmente_certas']} | "
                  f"{ag['nota_zero']} | {ag['afirma_algo_errado']} |")

    print("\n| recorte | run | R@5 | componentes no contexto |")
    print("|---|---|---|---|")
    for nome, rs in recortes.items():
        for c in COM_CONTEXTO:
            cob = media([r[f"no_contexto_{c}"] / r["n_componentes"] for r in rs])
            print(f"| {nome} | {c} | {media([r[f'R@5_{c}'] for r in rs]):.3f} | {cob:.3f} |")

    print("\nNota pela parte do ouro que chegou ao contexto (respostas com contexto):")
    print("| contexto | run | respostas | nota media | totalmente certas | afirma algo errado |")
    print("|---|---|---|---|---|---|")
    faixas = (("nenhum componente", lambda x: x == 0), ("parte", lambda x: 0 < x < 1),
              ("todos", lambda x: x == 1))
    for rotulo, cond in faixas:
        for runs in (("bm25",), ("grafo",), COM_CONTEXTO):
            sel = [(r, c) for r in linhas for c in runs if cond(r[f"no_contexto_{c}"] / r["n_componentes"])]
            if sel:
                notas = [r[f"nota_{c}"] for r, c in sel]
                print(f"| {rotulo} | {' + '.join(runs)} | {len(sel)} | {media(notas):.3f} | "
                      f"{sum(1 for x in notas if x == 1.0)} | {sum(r[f'errado_{c}'] for r, c in sel)} |")

    print("\nJulgamento por componente, conforme o componente estava ou nao no contexto:")
    print("| run | componente | n | S | P | N | nota |")
    print("|---|---|---|---|---|---|---|")
    for c in COM_CONTEXTO:
        for rotulo, campo in (("no contexto", "julg_no_contexto"), ("fora do contexto", "julg_fora_do_contexto")):
            s = "".join(r[f"{campo}_{c}"] for r in linhas)
            print(f"| {c} | {rotulo} | {len(s)} | {s.count('S')} | {s.count('P')} | {s.count('N')} | "
                  f"{nota_de(s):.3f} |")
    s = "".join(r["julgamentos_sem_contexto"] for r in linhas)
    print(f"| sem_contexto | todos | {len(s)} | {s.count('S')} | {s.count('P')} | {s.count('N')} | "
          f"{nota_de(s):.3f} |")

    print("\nRespostas que dizem que os trechos nao respondem:")
    print("| condicao | tipo | respostas | com algum componente no contexto | perguntas |")
    print("|---|---|---|---|---|")
    for c in CONDICOES:
        for tipo in ("abstencao", "ressalva"):
            rs = [r for r in linhas if r[f"tipo_{c}"] == tipo]
            if rs:
                com = sum(1 for r in rs if c in COM_CONTEXTO and r[f"no_contexto_{c}"] > 0)
                print(f"| {c} | {tipo} | {len(rs)} | {com} | {', '.join(r['pergunta'] for r in rs)} |")

    print("\nOuro do top 5 que ficou fora do contexto pelo limite de tokens:")
    for c in COM_CONTEXTO:
        rs = [r for r in linhas if r[f"fora_pelo_limite_{c}"] > 0]
        print(f"  {c}: " + (", ".join(f"{r['pergunta']} ({r[f'fora_pelo_limite_{c}']} de "
                                       f"{r['n_componentes']} componentes; {r[f'trechos_{c}']} trechos)"
                                       for r in rs) or "nenhum"))

    print("\nGrafo contra BM25, perguntas com nota diferente:")
    print("| pergunta | trivial | grafo - bm25 | componentes no contexto (bm25 / grafo) | R@5 (bm25 / grafo) |")
    print("|---|---|---|---|---|")
    mesma_cobertura = {"grafo": 0, "bm25": 0}
    outra_cobertura = 0
    for r in linhas:
        d = r["nota_grafo"] - r["nota_bm25"]
        if abs(d) > 1e-9:
            print(f"| {r['pergunta']} | {'sim' if r['trivial'] else 'nao'} | {d:+.2f} | "
                  f"{r['no_contexto_bm25']} / {r['no_contexto_grafo']} de {r['n_componentes']} | "
                  f"{r['R@5_bm25']:.2f} / {r['R@5_grafo']:.2f} |")
            if r["no_contexto_bm25"] == r["no_contexto_grafo"]:
                mesma_cobertura["grafo" if d > 0 else "bm25"] += 1
            else:
                outra_cobertura += 1
    print(f"  com cobertura diferente: {outra_cobertura}; com a mesma cobertura: "
          f"{sum(mesma_cobertura.values())} (grafo melhor em {mesma_cobertura['grafo']}, "
          f"bm25 melhor em {mesma_cobertura['bm25']})")

    print("\nAfirmacao errada, pareada por pergunta:")
    for a, b in (("sem_contexto", "bm25"), ("sem_contexto", "grafo"), ("bm25", "grafo")):
        so_a = sum(1 for r in linhas if r[f"errado_{a}"] and not r[f"errado_{b}"])
        so_b = sum(1 for r in linhas if r[f"errado_{b}"] and not r[f"errado_{a}"])
        print(f"  so em {a}: {so_a}; so em {b}: {so_b}")

    print("\nPor escopo:")
    print("| escopo | n | R@5 bm25 | R@5 grafo | nota sem_contexto | nota bm25 | nota grafo |")
    print("|---|---|---|---|---|---|---|")
    for esc in sorted({r["escopo"] for r in linhas}):
        rs = [r for r in linhas if r["escopo"] == esc]
        print(f"| {esc} | {len(rs)} | {media([r['R@5_bm25'] for r in rs]):.3f} | "
              f"{media([r['R@5_grafo'] for r in rs]):.3f} | "
              + " | ".join(f"{media([r[f'nota_{c}'] for r in rs]):.3f}" for c in CONDICOES) + " |")
    return agregados


def conferir_com_calcular(agregados: dict, resultado: dict) -> list[str]:
    problemas = []
    for chave_, esperado in resultado["por_condicao"].items():
        meu = agregados.get(chave_)
        if meu is None:
            problemas.append(f"{chave_}: ausente na analise")
            continue
        if abs(meu["nota_media"] - esperado["nota_media"]) > 1e-9:
            problemas.append(f"{chave_}: nota {meu['nota_media']} x {esperado['nota_media']}")
        for campo in ("n", "totalmente_certas", "afirma_algo_errado"):
            if meu[campo] != esperado[campo]:
                problemas.append(f"{chave_}: {campo} {meu[campo]} x {esperado[campo]}")
    return problemas


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--perguntas", type=Path, required=True)
    ap.add_argument("--planilha", type=Path, required=True, help="a planilha julgada")
    ap.add_argument("--chave", type=Path, required=True)
    ap.add_argument("--respostas", type=Path, required=True)
    ap.add_argument("--por-pergunta", type=Path, required=True,
                    help="<prefixo>.por_pergunta.csv gravado pelo avaliar_recuperacao.py")
    ap.add_argument("--resultado", type=Path, default=None,
                    help="o JSON do calcular, para conferir os agregados")
    ap.add_argument("--saida", type=Path, required=True)
    args = ap.parse_args()

    ouro = carregar_ouro(args.perguntas)
    chave = json.loads(args.chave.read_text(encoding="utf-8"))["codigos"]
    planilha = ler_planilha(args.planilha)
    respostas = [json.loads(l) for l in args.respostas.read_text(encoding="utf-8").splitlines() if l.strip()]
    r5 = defaultdict(dict)
    texto = args.por_pergunta.read_bytes().decode("utf-8-sig")
    for l in csv.DictReader(io.StringIO(texto, newline="")):
        if l["metodo"] in COM_CONTEXTO:
            r5[l["metodo"]][l["pergunta"]] = float(l["R@5"])

    linhas = montar_linhas(ouro, planilha, chave, respostas, r5)
    agregados = relatorio(linhas)
    if args.resultado:
        problemas = conferir_com_calcular(agregados, json.loads(args.resultado.read_text(encoding="utf-8")))
        if problemas:
            print("ERRO: a analise nao bate com o calcular:\n  " + "\n  ".join(problemas), file=sys.stderr)
            return 1
        print("\n[conferencia] notas medias, totalmente certas e afirmacoes erradas iguais as do calcular")
    args.saida.parent.mkdir(parents=True, exist_ok=True)
    with args.saida.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(linhas[0].keys()))
        w.writeheader()
        w.writerows(linhas)
    print(f"[analise] {len(linhas)} perguntas -> {args.saida}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
