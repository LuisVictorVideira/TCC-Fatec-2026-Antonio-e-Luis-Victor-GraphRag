#!/usr/bin/env python3
r"""
Respostas do llama3:8b, pelo Ollama, e julgamento as cegas.

Gera as respostas em tres condicoes:

    sem_contexto  a pergunta sozinha
    bm25          a pergunta com os trechos do run do BM25
    grafo         a pergunta com os trechos do run do grafo

Contexto: ate 5 trechos, na ordem do run, enquanto a soma ficar em ate 6.000
tokens estimados (palavras x 1,3), para caber na janela de 8.192 tokens. Se
o Ollama indicar que o prompt nao coube, o ultimo trecho sai e a pergunta e
refeita; nada e cortado em silencio.

Subcomandos:
    responder  chama o Ollama e grava as respostas em JSONL; retoma de onde
               parou
    planilha   monta a planilha de julgamento as cegas (CSV) e um arquivo de
               chave separado, que so se abre depois do julgamento
    calcular   le a planilha julgada e calcula os resultados por condicao,
               com as comparacoes pareadas

Uso:
    python src/responder.py responder --perguntas dados/perguntas_avaliacao.yaml \
        --trechos outputs/trechos.jsonl \
        --run bm25=outputs/runs/bm25.run --run grafo=outputs/runs/grafo.run \
        --saida outputs/respostas/respostas.jsonl
    python src/responder.py planilha --perguntas dados/perguntas_avaliacao.yaml \
        --respostas outputs/respostas/respostas.jsonl \
        --saida outputs/respostas/julgamento.csv
    python src/responder.py calcular --perguntas dados/perguntas_avaliacao.yaml \
        --planilha outputs/respostas/julgamento_ia.csv \
        --chave outputs/respostas/julgamento_chave.json
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import random
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from avaliar_recuperacao import bootstrap_ic, holm, teste_aleatorizacao
from baselines import (carregar_perguntas, carregar_trechos, identidade_do_modelo,
                          impressao_digital, ler_run, texto_para_indice, url_ollama)

import yaml

MODELO = "llama3:8b"
OPCOES = {"temperature": 0, "seed": 42, "top_k": 1, "top_p": 1,
          "num_ctx": 8192, "num_predict": 400}
MAX_TRECHOS = 5
ORCAMENTO_TOKENS = 6000
TIMEOUT = 900
SEMENTE_PLANILHA = 42
VALORES = {"S": 1.0, "SIM": 1.0, "P": 0.5, "PARCIAL": 0.5, "N": 0.0, "NAO": 0.0, "NÃO": 0.0}

TEMPLATE_COM_CONTEXTO = """You are helping a database administrator. Answer the question using only the documentation excerpts below. If the excerpts do not contain the answer, say that the excerpts do not answer the question.

Documentation excerpts:

{trechos}

Question: {pergunta}

Answer in English, in at most 150 words."""

TEMPLATE_SEM_CONTEXTO = """You are helping a database administrator. Answer the following question about Microsoft SQL Server.

Question: {pergunta}

Answer in English, in at most 150 words."""


def tokens_estimados(texto: str) -> int:
    """O mesmo estimador do chunking_e_mentions.py (palavras x 1,3)."""
    return int(len(texto.split()) * 1.3)


def escolher_trechos(ranking: list[str], por_id: dict, max_trechos: int = MAX_TRECHOS,
                     orcamento: int = ORCAMENTO_TOKENS) -> list[str]:
    """Os primeiros trechos do run, ate max_trechos e ate o orcamento de
    tokens. O primeiro entra sempre (nenhum trecho passa de 3.000 tokens)."""
    escolhidos, soma = [], 0
    for cid in ranking:
        if len(escolhidos) == max_trechos:
            break
        n = tokens_estimados(texto_para_indice(por_id[cid]))
        if escolhidos and soma + n > orcamento:
            break
        escolhidos.append(cid)
        soma += n
    return escolhidos


def montar_prompt(pergunta: str, trechos: list[dict] | None) -> str:
    """trechos=None: condicao sem contexto. Lista vazia: o metodo nao
    devolveu nada para a pergunta, e o prompt diz isso."""
    if trechos is None:
        return TEMPLATE_SEM_CONTEXTO.format(pergunta=pergunta)
    blocos = [f"[{i}] {texto_para_indice(t)}" for i, t in enumerate(trechos, 1)]
    corpo = "\n\n".join(blocos) if blocos else "(no excerpts were retrieved)"
    return TEMPLATE_COM_CONTEXTO.format(trechos=corpo, pergunta=pergunta)


def tokens_do_prompt(resp: dict) -> int | None:
    """Tamanho do prompt em tokens, medido pelo campo context da resposta.

    O prompt_eval_count conta so os tokens calculados nesta chamada: quando
    o comeco do prompt repete o da chamada anterior, o Ollama reaproveita o
    cache e conta menos. O context, quando o Ollama o devolve, traz os
    tokens do prompt mais os da resposta, sem esse desconto."""
    ctx = resp.get("context")
    if isinstance(ctx, list) and resp.get("eval_count") is not None:
        return len(ctx) - int(resp["eval_count"])
    return None


def possivel_truncamento(resp: dict, opcoes: dict) -> bool:
    """O Ollama corta o prompt que nao cabe em num_ctx. Um prompt perto do
    limite (sem espaco para a resposta) e tratado como cortado. Vale a maior
    das duas medidas disponiveis."""
    medidas = [n for n in (resp.get("prompt_eval_count"), tokens_do_prompt(resp)) if n is not None]
    return bool(medidas) and max(medidas) >= opcoes["num_ctx"] - opcoes["num_predict"] - 16


def chamar_ollama(prompt: str, modelo: str, opcoes: dict) -> dict:
    import requests
    t0 = time.perf_counter()
    r = requests.post(f"{url_ollama()}/api/generate", timeout=TIMEOUT, json={
        "model": modelo, "prompt": prompt, "stream": False, "options": opcoes})
    r.raise_for_status()
    d = r.json()
    d["_segundos"] = round(time.perf_counter() - t0, 2)
    return d


def responder_uma(qid: str, pergunta: str, condicao: str, ranking: list[str] | None,
                  por_id: dict, modelo: str, opcoes: dict) -> dict:
    trechos = None if ranking is None else escolher_trechos(ranking, por_id)
    tentativas = 0
    while True:
        tentativas += 1
        prompt = montar_prompt(pergunta, None if trechos is None else [por_id[c] for c in trechos])
        resp = chamar_ollama(prompt, modelo, opcoes)
        cortado = possivel_truncamento(resp, opcoes)
        if not cortado or not trechos or len(trechos) == 1:
            break
        trechos = trechos[:-1]
    return {
        "id": qid, "condicao": condicao, "modelo": modelo, "opcoes": opcoes,
        "trechos": trechos, "tokens_estimados_contexto":
            None if trechos is None else sum(tokens_estimados(texto_para_indice(por_id[c])) for c in trechos),
        "prompt": prompt, "resposta": resp.get("response", ""),
        "prompt_eval_count": resp.get("prompt_eval_count"), "tokens_prompt": tokens_do_prompt(resp),
        "eval_count": resp.get("eval_count"),
        "done_reason": resp.get("done_reason"), "possivel_truncamento": cortado,
        "tentativas": tentativas, "segundos": resp.get("_segundos"),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }


def ler_jsonl(caminho: Path) -> list[dict]:
    if not caminho.exists():
        return []
    with caminho.open(encoding="utf-8") as fh:
        return [json.loads(linha) for linha in fh if linha.strip()]


def cmd_responder(args) -> int:
    trechos = carregar_trechos(args.trechos)
    por_id = {t["chunk_id"]: t for t in trechos}
    perguntas = carregar_perguntas(args.perguntas, "verificadas")
    runs = {}
    for item in args.run:
        nome, _, caminho = item.partition("=")
        if not caminho:
            raise SystemExit(f"ERRO: use --run nome=arquivo (recebi {item!r}).")
        runs[nome] = ler_run(Path(caminho))
    condicoes = ["sem_contexto"] + list(runs)
    if args.refazer and args.saida.exists():
        args.saida.unlink()
    feitas = ler_jsonl(args.saida)
    ident = identidade_do_modelo(args.modelo)
    digests = {r.get("digest_pesos") for r in feitas} - {None}
    if digests and ident.get("digest_pesos") not in digests:
        raise SystemExit("ERRO: o arquivo de respostas foi feito com outros pesos do modelo "
                         f"({sorted(digests)}); agora: {ident.get('digest_pesos')}. "
                         "Use --refazer para comecar de novo.")
    ja = {(r["id"], r["condicao"]) for r in feitas}
    args.saida.parent.mkdir(parents=True, exist_ok=True)
    total = len(perguntas) * len(condicoes)
    print(f"[responder] modelo {args.modelo} ({ident.get('digest_pesos')}), "
          f"{len(perguntas)} perguntas x {len(condicoes)} condicoes = {total}; ja feitas: {len(ja)}")
    with args.saida.open("a", encoding="utf-8") as fh:
        for qid, texto in perguntas:
            for cond in condicoes:
                if (qid, cond) in ja:
                    continue
                if cond == "sem_contexto":
                    ranking = None
                else:
                    ranking = [c for c, _ in runs[cond].get(qid, [])]
                    if not ranking:
                        print(f"[aviso] {qid}/{cond}: o run nao tem trechos para a pergunta",
                              file=sys.stderr)
                try:
                    reg = responder_uma(qid, texto, cond, ranking, por_id, args.modelo, OPCOES)
                    reg["erro"] = None
                except Exception as exc:
                    reg = {"id": qid, "condicao": cond, "erro": f"{type(exc).__name__}: {exc}",
                           "timestamp": datetime.now().isoformat(timespec="seconds")}
                reg["digest_pesos"] = ident.get("digest_pesos")
                fh.write(json.dumps(reg, ensure_ascii=False) + "\n")
                fh.flush()
                marca = "ERRO" if reg.get("erro") else f"{reg.get('segundos')}s"
                print(f"  {qid} {cond:13} {marca}")
    registros = ler_jsonl(args.saida)
    manifesto = {
        "modelo": ident, "opcoes": OPCOES, "max_trechos": MAX_TRECHOS,
        "orcamento_tokens": ORCAMENTO_TOKENS, "template_com_contexto": TEMPLATE_COM_CONTEXTO,
        "template_sem_contexto": TEMPLATE_SEM_CONTEXTO, "condicoes": condicoes,
        "runs": {i.partition("=")[0]: i.partition("=")[2] for i in args.run},
        "impressao_digital_trechos": impressao_digital(trechos),
        "n_respostas": len(registros), "n_erros": sum(1 for r in registros if r.get("erro")),
        "n_possivel_truncamento": sum(1 for r in registros if r.get("possivel_truncamento")),
        "n_com_trecho_retirado": sum(1 for r in registros if (r.get("tentativas") or 1) > 1),
        "max_tokens_prompt": max((max(r.get("prompt_eval_count") or 0, r.get("tokens_prompt") or 0)
                                  for r in registros), default=0),
        "atualizado_em": datetime.now().isoformat(timespec="seconds"),
    }
    args.saida.with_suffix(".manifesto.json").write_text(
        json.dumps(manifesto, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"[responder] {manifesto['n_respostas']} respostas, {manifesto['n_erros']} erros, "
          f"{manifesto['n_possivel_truncamento']} com possivel truncamento, "
          f"{manifesto['n_com_trecho_retirado']} com trecho retirado; maior prompt: "
          f"{manifesto['max_tokens_prompt']:,} tokens -> {args.saida}")
    return 0 if manifesto["n_erros"] == 0 else 2


def carregar_gabarito_julgamento(caminho: Path) -> dict:
    dados = yaml.safe_load(caminho.read_text(encoding="utf-8"))
    itens = dados["perguntas"] if isinstance(dados, dict) else dados
    gab = {}
    for q in itens:
        if str(q.get("verificado", "")).strip().lower() != "sim":
            continue
        comps = [c["nome"] for c in (q.get("componentes") or []) if c.get("essencial", True)]
        gab[str(q["id"])] = {
            "pergunta_en": q.get("pergunta_en", ""), "pergunta_pt": q.get("pergunta_pt", ""),
            "resposta_resumo": " ".join(str(q.get("resposta_resumo") or "").split()),
            "componentes": comps,
            "trivial": str(q.get("trivial_lexical", "nao")).strip().lower() == "sim",
        }
    return gab


def texto_de_celula(texto: str) -> str:
    """O Excel toma por formula a celula de CSV que comeca com =, +, - ou @
    (uma resposta em lista comeca com "- "). Um espaco de largura zero na
    frente mantem o texto como texto, sem mudar o que se le."""
    return "\u200b" + texto if texto[:1] in ("=", "+", "-", "@") else texto


def cmd_planilha(args) -> int:
    gab = carregar_gabarito_julgamento(args.perguntas)
    registros = ler_jsonl(args.respostas)
    por_q = defaultdict(dict)
    for r in registros:
        por_q[r["id"]][r["condicao"]] = r
    condicoes = sorted({r["condicao"] for r in registros})
    faltam = [(q, c) for q in gab for c in condicoes
              if c not in por_q[q] or por_q[q][c].get("erro")]
    if faltam:
        raise SystemExit(f"ERRO: faltam respostas ou ha erros em {len(faltam)} casos, "
                         f"por exemplo {faltam[:5]}. Rode o responder de novo.")
    max_comp = max(len(g["componentes"]) for g in gab.values())
    rnd = random.Random(SEMENTE_PLANILHA)
    linhas, chave = [], {}
    codigos = rnd.sample(range(100, 1000), len(gab) * len(condicoes))
    i = 0
    for qid in sorted(gab):
        ordem = condicoes[:]
        rnd.shuffle(ordem)
        for cond in ordem:
            codigo = f"R{codigos[i]}"
            i += 1
            chave[codigo] = {"id": qid, "condicao": cond}
            g = gab[qid]
            linha = [codigo, qid, g["pergunta_en"], g["pergunta_pt"], texto_de_celula(g["resposta_resumo"]),
                     texto_de_celula(" ".join(por_q[qid][cond]["resposta"].split()))]
            for k in range(max_comp):
                linha += [g["componentes"][k] if k < len(g["componentes"]) else "", ""]
            linha += ["", ""]
            linhas.append(linha)
    cab = ["codigo", "pergunta", "pergunta_en", "pergunta_pt", "resposta_de_referencia", "resposta"]
    for k in range(1, max_comp + 1):
        cab += [f"componente_{k}", f"julgamento_{k}"]
    cab += ["afirma_algo_errado", "observacao"]
    args.saida.parent.mkdir(parents=True, exist_ok=True)
    with args.saida.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(cab)
        w.writerows(linhas)
    caminho_chave = args.saida.with_name(args.saida.stem + "_chave.json")
    caminho_chave.write_text(json.dumps({"semente": SEMENTE_PLANILHA, "codigos": chave},
                                        indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"[planilha] {len(linhas)} respostas de {len(gab)} perguntas -> {args.saida}")
    print(f"[planilha] chave -> {caminho_chave} (nao abra antes de terminar o julgamento)")
    return 0


def ler_planilha(caminho: Path) -> list[dict]:
    """Le a planilha julgada. O Excel pode salvar o CSV em UTF-8 ou na
    codificacao do Windows (cp1252), e com ponto e virgula ou virgula,
    conforme a configuracao regional; os quatro casos sao aceitos."""
    bruto = caminho.read_bytes()
    try:
        texto = bruto.decode("utf-8-sig")
    except UnicodeDecodeError:
        texto = bruto.decode("cp1252")
    primeira = texto.split("\n", 1)[0]
    separador = ";" if primeira.count(";") >= primeira.count(",") else ","
    return list(csv.DictReader(io.StringIO(texto, newline=""), delimiter=separador))


def nota_da_linha(linha: dict, n_comp: int) -> float:
    valores = []
    for k in range(1, n_comp + 1):
        bruto = (linha.get(f"julgamento_{k}") or "").strip().upper()
        if bruto not in VALORES:
            raise ValueError(f"{linha['codigo']}: julgamento_{k} = {bruto!r}; use S, P ou N")
        valores.append(VALORES[bruto])
    return statistics.mean(valores)


def comparar(notas: dict, a: str, bs: list[str], qs: list[str]) -> list[dict]:
    saida = []
    for b in bs:
        dif = [notas[q][a] - notas[q][b] for q in qs]
        lo, hi = bootstrap_ic(dif)
        saida.append({"a": a, "b": b, "n": len(qs), "diferenca_media": statistics.mean(dif),
                      "ic95": [lo, hi], "p": teste_aleatorizacao(dif)})
    for item, p_aj in zip(saida, holm([s["p"] for s in saida])):
        item["p_holm"] = p_aj
    return saida


def cmd_calcular(args) -> int:
    gab = carregar_gabarito_julgamento(args.perguntas)
    caminho_chave = args.chave or args.planilha.with_name(args.planilha.stem + "_chave.json")
    chave = json.loads(caminho_chave.read_text(encoding="utf-8"))["codigos"]
    linhas = ler_planilha(args.planilha)
    notas, erros_fatuais, problemas = defaultdict(dict), defaultdict(dict), []
    for linha in linhas:
        cod = linha["codigo"]
        q, cond = chave[cod]["id"], chave[cod]["condicao"]
        try:
            notas[q][cond] = nota_da_linha(linha, len(gab[q]["componentes"]))
        except ValueError as exc:
            problemas.append(str(exc))
        erro = (linha.get("afirma_algo_errado") or "").strip().upper()
        erros_fatuais[q][cond] = erro in ("S", "SIM")
    if problemas:
        print("ERRO: julgamentos faltando ou invalidos:\n  " + "\n  ".join(problemas[:20]),
              file=sys.stderr)
        return 1
    condicoes = sorted({c for v in notas.values() for c in v})
    recortes = {"sem_triviais": [q for q in sorted(notas) if not gab[q]["trivial"]],
                "todas": sorted(notas)}
    resultado = {"por_condicao": {}, "comparacoes": {}}
    print("| condicao | recorte | n | nota media | totalmente certas | afirma algo errado |")
    print("|---|---|---|---|---|---|")
    for cond in condicoes:
        for nome, qs in recortes.items():
            vals = [notas[q][cond] for q in qs]
            certas = sum(1 for v in vals if v == 1.0)
            errados = sum(1 for q in qs if erros_fatuais[q].get(cond))
            resultado["por_condicao"][f"{cond}|{nome}"] = {
                "n": len(qs), "nota_media": statistics.mean(vals),
                "totalmente_certas": certas, "afirma_algo_errado": errados}
            print(f"| {cond} | {nome} | {len(qs)} | {statistics.mean(vals):.3f} | "
                  f"{certas} | {errados} |")
    if "grafo" in condicoes:
        outras = [c for c in ("bm25", "sem_contexto") if c in condicoes]
        for nome, qs in recortes.items():
            comps = comparar(notas, "grafo", outras, qs)
            resultado["comparacoes"][nome] = comps
            for c in comps:
                print(f"\n[{nome}] grafo - {c['b']} = {c['diferenca_media']:+.3f} "
                      f"(IC95% [{c['ic95'][0]:+.3f}, {c['ic95'][1]:+.3f}]; p = {c['p']:.4f}; "
                      f"p Holm = {c['p_holm']:.4f}; n = {c['n']})")
    destino = args.planilha.with_name(args.planilha.stem + "_resultado.json")
    destino.write_text(json.dumps(resultado, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\n[calcular] -> {destino}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("responder")
    p.add_argument("--perguntas", type=Path, required=True)
    p.add_argument("--trechos", type=Path, required=True)
    p.add_argument("--run", action="append", default=[], metavar="NOME=ARQUIVO",
                   help="run que fornece o contexto; repita para cada condicao")
    p.add_argument("--saida", type=Path, required=True)
    p.add_argument("--modelo", default=MODELO)
    p.add_argument("--refazer", action="store_true")
    p.set_defaults(func=cmd_responder)

    p = sub.add_parser("planilha")
    p.add_argument("--perguntas", type=Path, required=True)
    p.add_argument("--respostas", type=Path, required=True)
    p.add_argument("--saida", type=Path, required=True)
    p.set_defaults(func=cmd_planilha)

    p = sub.add_parser("calcular")
    p.add_argument("--perguntas", type=Path, required=True)
    p.add_argument("--planilha", type=Path, required=True)
    p.add_argument("--chave", type=Path, default=None)
    p.set_defaults(func=cmd_calcular)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
