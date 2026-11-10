#!/usr/bin/env python3
r"""
Metricas de recuperacao contra os trechos-ouro e comparacao entre metodos.

Le o perguntas_avaliacao.yaml (gabarito) e um ou mais runs no formato TREC
e calcula, por pergunta e por metodo:

    R@k   fracao dos componentes essenciais cobertos pelos k primeiros trechos
    P@k   fracao dos k primeiros trechos que pertencem a algum componente
    MRR   1 / posicao do primeiro trecho que cobre um componente essencial

Cada componente e uma informacao de que a resposta precisa, com a lista de
trechos que a contem; os trechos de um componente sao alternativos, e basta
recuperar um. So entram as perguntas com verificado: sim. Os agregados saem
por escopo, por grupo (A a D) e com e sem as perguntas triviais.

Com --comparar A B [C ...], o metodo A e comparado a cada um dos outros na
metrica de --metrica, por teste de aleatorizacao pareado (troca de sinais,
exato ate 20 perguntas) e intervalo de 95% por bootstrap da diferenca media,
em dois recortes: as perguntas nao triviais (analise principal) e todas.
Com mais de uma comparacao, o valor-p tambem sai corrigido por Holm.

Uso:
    python src/avaliar_recuperacao.py --perguntas dados/perguntas_avaliacao.yaml \
        --runs outputs/runs/bm25.run outputs/runs/grafo.run \
        --saida outputs/avaliacao/recuperacao --comparar grafo bm25
"""
from __future__ import annotations

import argparse
import csv
import itertools
import json
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import yaml

KS_PADRAO = (1, 3, 5, 10)


def componentes_da_pergunta(q: dict) -> list[dict]:
    if q.get("componentes"):
        comps = []
        for c in q["componentes"]:
            trechos = [str(t) for t in (c.get("trechos") or [])]
            if not trechos:
                raise SystemExit(f"ERRO: {q['id']}: componente '{c.get('nome')}' sem trechos.")
            comps.append({"nome": c.get("nome", ""), "essencial": bool(c.get("essencial", True)),
                          "trechos": trechos})
        return comps
    return [{"nome": t, "essencial": True, "trechos": [t]} for t in (q.get("chunks_ouro") or [])]


def carregar_gabarito(caminho: Path) -> dict:
    dados = yaml.safe_load(caminho.read_text(encoding="utf-8"))
    itens = dados["perguntas"] if isinstance(dados, dict) else dados
    gab = {}
    for q in itens:
        if str(q.get("verificado", "")).strip().lower() != "sim":
            continue
        comps = componentes_da_pergunta(q)
        essenciais = [c for c in comps if c["essencial"]]
        if not essenciais:
            raise SystemExit(f"ERRO: {q['id']} verificada sem nenhum componente essencial.")
        gab[str(q["id"])] = {
            "componentes": essenciais,
            "ouro": {t for c in essenciais for t in c["trechos"]},
            "apoio": set(q.get("chunks_apoio") or []),
            "escopo": q.get("escopo") or "sem_escopo",
            "grupo": str(q["id"])[0],
            "trivial": str(q.get("trivial_lexical", "nao")).strip().lower() in ("sim", "true", "yes"),
        }
    return gab


def ler_run(caminho: Path) -> dict[str, list[str]]:
    porq = defaultdict(list)
    with caminho.open(encoding="utf-8") as fh:
        for linha in fh:
            p = linha.split()
            if len(p) >= 6:
                porq[p[0]].append((int(p[3]), p[2]))
    return {q: [cid for _, cid in sorted(v)] for q, v in porq.items()}


def metricas(ranking: list[str], g: dict, ks) -> dict:
    m = {}
    for k in ks:
        topo = ranking[:k]
        cobertos = sum(1 for c in g["componentes"] if any(t in topo for t in c["trechos"]))
        m[f"R@{k}"] = cobertos / len(g["componentes"])
        m[f"P@{k}"] = sum(1 for t in topo if t in g["ouro"]) / k
    pos = next((i for i, t in enumerate(ranking, 1) if t in g["ouro"]), None)
    m["MRR"] = 1.0 / pos if pos else 0.0
    m["pos_primeiro_ouro"] = pos
    m["posicoes_ouro"] = {t: (ranking.index(t) + 1 if t in ranking else None)
                          for t in sorted(g["ouro"])}
    return m


def agregar(por_pergunta: dict, gab: dict, chaves: list[str]) -> list[dict]:
    recortes = {"todas": lambda g: True, "sem_triviais": lambda g: not g["trivial"]}
    for e in sorted({g["escopo"] for g in gab.values()}):
        recortes[f"escopo={e}"] = (lambda e: lambda g: g["escopo"] == e)(e)
    for gr in sorted({g["grupo"] for g in gab.values()}):
        recortes[f"grupo={gr}"] = (lambda gr: lambda g: g["grupo"] == gr)(gr)
    linhas = []
    for nome, filtro in recortes.items():
        qs = [q for q in por_pergunta if filtro(gab[q])]
        if not qs:
            continue
        linha = {"recorte": nome, "n": len(qs)}
        for c in chaves:
            linha[c] = statistics.mean(por_pergunta[q][c] for q in qs)
        linhas.append(linha)
    return linhas


def teste_aleatorizacao(dif: list[float], n_perm: int = 100_000, semente: int = 42) -> float:
    """p bicaudal do teste de aleatorizacao pareado (troca de sinais)."""
    obs = abs(sum(dif))
    if len(dif) <= 20:
        extremos = sum(1 for sinais in itertools.product((1, -1), repeat=len(dif))
                       if abs(sum(s * d for s, d in zip(sinais, dif))) >= obs - 1e-12)
        return extremos / 2 ** len(dif)
    rnd = random.Random(semente)
    extremos = sum(1 for _ in range(n_perm)
                   if abs(sum(d if rnd.random() < 0.5 else -d for d in dif)) >= obs - 1e-12)
    return (extremos + 1) / (n_perm + 1)


def bootstrap_ic(dif: list[float], n_boot: int = 10_000, semente: int = 42) -> tuple[float, float]:
    rnd = random.Random(semente)
    medias = sorted(statistics.mean(rnd.choices(dif, k=len(dif))) for _ in range(n_boot))
    return medias[int(0.025 * n_boot)], medias[int(0.975 * n_boot) - 1]


def holm(ps: list[float]) -> list[float]:
    """Valores-p ajustados pelo metodo de Holm (1979), na ordem de entrada.

    O menor p e multiplicado por m, o segundo por m - 1, e assim por diante;
    o ajustado nunca fica menor que o anterior na ordem crescente."""
    m = len(ps)
    ajustados = [0.0] * m
    maximo = 0.0
    for pos, i in enumerate(sorted(range(m), key=lambda j: ps[j])):
        maximo = max(maximo, min(1.0, (m - pos) * ps[i]))
        ajustados[i] = maximo
    return ajustados


RECORTES_COMPARACAO = (("sem_triviais", "analise principal"), ("todas", "secundaria"))


def comparacoes(resultados: dict, gab: dict, a: str, outros: list[str], metrica: str) -> list[dict]:
    """O metodo a contra cada um dos outros, nos dois recortes: perguntas nao
    triviais (analise principal) e todas.
    """
    saida = []
    for recorte, papel in RECORTES_COMPARACAO:
        qs = sorted(q for q in gab if recorte == "todas" or not gab[q]["trivial"])
        bloco = []
        for b in outros:
            dif = [resultados[a][q][metrica] - resultados[b][q][metrica] for q in qs]
            lo, hi = bootstrap_ic(dif)
            bloco.append({"recorte": recorte, "papel": papel, "a": a, "b": b, "n": len(qs),
                          "metrica": metrica, "diferenca_media": statistics.mean(dif),
                          "ic95": [lo, hi], "p": teste_aleatorizacao(dif)})
        for item, p_aj in zip(bloco, holm([x["p"] for x in bloco])):
            item["p_holm"] = p_aj
        saida += bloco
    return saida


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--perguntas", type=Path, required=True)
    ap.add_argument("--runs", type=Path, nargs="+", required=True)
    ap.add_argument("--ks", type=int, nargs="+", default=list(KS_PADRAO))
    ap.add_argument("--saida", type=Path, required=True, help="prefixo dos arquivos de saida")
    ap.add_argument("--comparar", nargs="+", metavar="RUN",
                    help="nomes (arquivo sem extensao): o metodo avaliado e depois "
                         "os metodos de comparacao")
    ap.add_argument("--metrica", default="R@5")
    args = ap.parse_args()

    gab = carregar_gabarito(args.perguntas)
    if not gab:
        print("ERRO: nenhuma pergunta verificada no YAML.", file=sys.stderr)
        return 1
    chaves = [f"R@{k}" for k in args.ks] + [f"P@{k}" for k in args.ks] + ["MRR"]
    args.saida.parent.mkdir(parents=True, exist_ok=True)

    resultados = {}
    for caminho in args.runs:
        nome = caminho.stem
        run = ler_run(caminho)
        faltando = sorted(set(gab) - set(run))
        if faltando:
            print(f"[aviso] {nome}: sem resultado para {', '.join(faltando)} "
                  f"(contam como zero)", file=sys.stderr)
        resultados[nome] = {q: metricas(run.get(q, []), g, args.ks) for q, g in gab.items()}

    with args.saida.with_suffix(".por_pergunta.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["metodo", "pergunta", "escopo", "trivial", *chaves,
                    "pos_primeiro_ouro", "posicoes_ouro"])
        for nome, porq in resultados.items():
            for q in sorted(porq):
                m = porq[q]
                w.writerow([nome, q, gab[q]["escopo"], int(gab[q]["trivial"]),
                            *[f"{m[c]:.4f}" for c in chaves], m["pos_primeiro_ouro"] or "",
                            json.dumps(m["posicoes_ouro"], ensure_ascii=False)])

    linhas_md = ["| metodo | recorte | n | " + " | ".join(chaves) + " |",
                 "|---|---|---|" + "---|" * len(chaves)]
    with args.saida.with_suffix(".agregado.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["metodo", "recorte", "n", *chaves])
        for nome, porq in resultados.items():
            for linha in agregar(porq, gab, chaves):
                w.writerow([nome, linha["recorte"], linha["n"], *[f"{linha[c]:.4f}" for c in chaves]])
                linhas_md.append(f"| {nome} | {linha['recorte']} | {linha['n']} | "
                                 + " | ".join(f"{linha[c]:.3f}" for c in chaves) + " |")
    args.saida.with_suffix(".agregado.md").write_text("\n".join(linhas_md) + "\n", encoding="utf-8")
    print("\n".join(linhas_md))

    if args.comparar:
        if len(args.comparar) < 2:
            print("ERRO: --comparar precisa do metodo avaliado e de ao menos um outro.",
                  file=sys.stderr)
            return 1
        a, outros = args.comparar[0], args.comparar[1:]
        faltando = [n for n in args.comparar if n not in resultados]
        if faltando:
            print(f"ERRO: --comparar cita runs que nao estao em --runs: {faltando}",
                  file=sys.stderr)
            return 1
        comps = comparacoes(resultados, gab, a, outros, args.metrica)
        for c in comps:
            holm_txt = f"; p Holm = {c['p_holm']:.4f}" if len(outros) > 1 else ""
            print(f"\n[{c['recorte']}, {c['papel']}] {args.metrica}: {a} - {c['b']} = "
                  f"{c['diferenca_media']:+.3f} (IC95% bootstrap [{c['ic95'][0]:+.3f}, "
                  f"{c['ic95'][1]:+.3f}]; p = {c['p']:.4f}{holm_txt}; n = {c['n']})")
        args.saida.with_suffix(".comparacao.json").write_text(
            json.dumps(comps, indent=1, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
