#!/usr/bin/env python3
r"""
Consulta ao grafo: PageRank personalizado sobre o grafo do Neo4j.

Para cada pergunta, devolve os trechos mais relevantes no formato de run do
TREC, o mesmo dos baselines, medido pelo mesmo avaliador.

    1. Sementes. As entidades (Concept e TSqlObject) encontradas na pergunta
       pelo mesmo casamento do chunking_e_mentions.py, menos as regras
       cortadas pela frequencia, com peso 1/(numero de trechos que mencionam
       a entidade), como no HippoRAG; as entidades somam 1. Os trechos do
       run do BM25 tambem sao sementes e somam 0,05 (fator do HippoRAG 2),
       na proporcao da pontuacao. Pergunta sem entidade fica so com os
       trechos do BM25.
    2. Grafo de consulta. As ligacoes MENTIONS, HAS_CHUNK, HAS_KEYWORD,
       DOCUMENTS e LINKS_TO, sem direcao e sem peso, montadas em memoria. Os
       TSqlObjects com a mesma forma de superficie (X e X_TSQL) viram um no.
    3. PageRank personalizado com amortecimento 0,5: em cada passo, metade
       da massa volta as sementes. Os trechos sao ordenados pela massa
       final; empate pelo chunk_id.

Antes de consultar, o script refaz as MENTIONS a partir dos trechos e
confere que sao as do grafo exportado. Nada e gravado no Neo4j.

As ablacoes sao opcoes do recuperar (--so-mentions, --sem-codigo,
--sem-bm25, --sem-fundir-gemeos, --normalizacao-bm25 maxima e
--normalizar-grau). Com --relacoes, as relacoes extraidas pela LLM
(extrair_relacoes.py) entram como arestas extras entre entidades, sem
direcao e sem peso.

Subcomandos:
    exportar-grafo  le nos e arestas do Neo4j e grava um JSONL com a
                    impressao digital do grafo; com --offline, refaz o mesmo
                    grafo a partir do corpus, sem Neo4j
    recuperar       sementes, PageRank personalizado e run

Uso:
    python src/consulta_grafo.py exportar-grafo --saida outputs/grafo.jsonl
    python src/consulta_grafo.py recuperar --grafo outputs/grafo.jsonl \
        --trechos outputs/trechos.jsonl --bm25 outputs/runs/bm25.run \
        --perguntas dados/perguntas_avaliacao.yaml --saida outputs/runs/grafo.run
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

try:
    from dotenv import find_dotenv, load_dotenv
    load_dotenv(find_dotenv(), override=False)
except ImportError:
    pass
SENHA_FICTICIA = "consulta-sem-conexao"
# O chunking_e_mentions.py exige NEO4J_PASSWORD na importacao, e o recuperar
# nao abre conexao. O .env e lido antes, para a senha real prevalecer.
os.environ.setdefault("NEO4J_PASSWORD", SENHA_FICTICIA)
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np

import chunking_e_mentions as chunking
from baselines import (carregar_perguntas, carregar_trechos, gravar_run,
                          impressao_digital, ler_run)

AMORTECIMENTO = 0.5  # HippoRAG 2, Tabela 13 (apendice G.1)
PESO_TRECHOS_BM25 = 0.05  # HippoRAG 2, secao 6.2
DF_MAX = 0.02  # o mesmo corte da fragmentacao (--df-max 0.02)
TIPOS_DE_ARESTA = ("HAS_CHUNK", "MENTIONS", "HAS_KEYWORD", "DOCUMENTS", "LINKS_TO")
ROTULO_PREFIXO = {"Document": "D", "Chunk": "C", "Concept": "K", "TSqlObject": "T"}


def _linha(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def linhas_do_grafo(nos: dict[str, list[str]], arestas: dict[str, list[tuple]]) -> list[str]:
    """Linhas canonicas e ordenadas do grafo; base da impressao digital.

    nos: rotulo -> nomes; arestas: tipo -> tuplas. MENTIONS e
    (chunk_id, rotulo_do_alvo, nome_do_alvo, count, count_code); as demais
    sao (origem, destino).
    """
    linhas = []
    for rotulo in sorted(nos):
        linhas += [_linha({"no": rotulo, "k": k}) for k in sorted(set(nos[rotulo]))]
    for tipo in sorted(arestas):
        for t in sorted(set(arestas[tipo])):
            if tipo == "MENTIONS":
                linhas.append(_linha({"t": tipo, "a": t[0], "r": t[1], "b": t[2],
                                      "n": int(t[3] or 0), "nc": int(t[4] or 0)}))
            else:
                linhas.append(_linha({"t": tipo, "a": t[0], "b": t[1]}))
    return linhas


def impressao_digital_do_grafo(linhas: list[str]) -> str:
    h = hashlib.sha256()
    for linha in linhas:
        h.update(linha.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def contagens(nos: dict, arestas: dict) -> dict:
    c = {f"no:{r}": len(set(v)) for r, v in nos.items()}
    for tipo, v in arestas.items():
        if tipo == "MENTIONS":
            for rot in ("Concept", "TSqlObject"):
                c[f"MENTIONS->{rot}"] = len({x for x in v if x[1] == rot})
        else:
            c[tipo] = len(set(v))
    return c


def grafo_do_neo4j() -> tuple[dict, dict]:
    try:
        from neo4j import GraphDatabase
    except ImportError as exc:
        raise SystemExit(f"ERRO: dependencia ausente ({exc}). Rode: pip install neo4j python-dotenv")
    senha = os.environ.get("NEO4J_PASSWORD")
    if not senha or senha == SENHA_FICTICIA:
        raise SystemExit("ERRO: NEO4J_PASSWORD nao definido no .env do projeto.")
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    usuario = os.environ.get("NEO4J_USER", "neo4j")
    banco = os.environ.get("NEO4J_DATABASE", "neo4j")
    consultas_nos = {
        "Document": "MATCH (n:Document) RETURN n.path AS k",
        "Chunk": "MATCH (n:Chunk) RETURN n.chunk_id AS k",
        "Concept": "MATCH (n:Concept) RETURN n.name AS k",
        "TSqlObject": "MATCH (n:TSqlObject) RETURN n.name AS k",
    }
    consultas_arestas = {
        "HAS_CHUNK": "MATCH (a:Document)-[:HAS_CHUNK]->(b:Chunk) RETURN a.path AS a, b.chunk_id AS b",
        "HAS_KEYWORD": "MATCH (a:Document)-[:HAS_KEYWORD]->(b:Concept) RETURN a.path AS a, b.name AS b",
        "DOCUMENTS": "MATCH (a:Document)-[:DOCUMENTS]->(b:TSqlObject) RETURN a.path AS a, b.name AS b",
        "LINKS_TO": "MATCH (a:Document)-[:LINKS_TO]->(b:Document) RETURN a.path AS a, b.path AS b",
    }
    nos, arestas = {}, {}
    with GraphDatabase.driver(uri, auth=(usuario, senha)) as driver:
        driver.verify_connectivity()
        with driver.session(database=banco) as sess:
            for rotulo, q in consultas_nos.items():
                nos[rotulo] = [r["k"] for r in sess.run(q)]
            for tipo, q in consultas_arestas.items():
                arestas[tipo] = [(r["a"], r["b"]) for r in sess.run(q)]
            arestas["MENTIONS"] = []
            for rot in ("Concept", "TSqlObject"):
                q = (f"MATCH (a:Chunk)-[m:MENTIONS]->(b:{rot}) "
                     "RETURN a.chunk_id AS a, b.name AS b, m.count AS n, m.count_code AS nc")
                arestas["MENTIONS"] += [(r["a"], rot, r["b"], r["n"], r["nc"]) for r in sess.run(q)]
    return nos, arestas


def grafo_offline(docs_root: Path) -> tuple[dict, dict]:
    """O mesmo grafo, refeito do corpus com as funcoes do construir_grafo.py e do
    chunking_e_mentions.py.
    """
    import reproduzir_sem_neo4j as rep
    r = rep.reproduzir(docs_root)
    g = r["grafo_metadados"]
    nos = {"Document": g["documentos"], "Chunk": [t["chunk_id"] for t in r["trechos"]],
           "Concept": g["conceitos"], "TSqlObject": g["objetos"]}
    arestas = {
        "HAS_CHUNK": [(t["doc_path"], t["chunk_id"]) for t in r["trechos"]],
        "HAS_KEYWORD": [tuple(x) for x in g["has_keyword"]],
        "DOCUMENTS": [tuple(x) for x in g["documents"]],
        "LINKS_TO": [tuple(x) for x in g["links_to"]],
        "MENTIONS": [(m["chunk_id"], m["label"], m["target"], m["count"], m["count_code"])
                     for m in r["mentions"]],
    }
    return nos, arestas


def cmd_exportar_grafo(args) -> int:
    t0 = time.time()
    if args.offline:
        docs_root = args.docs_root.resolve() if args.docs_root else chunking.get_docs_root()
        if not (docs_root / "relational-databases").exists() and \
                (docs_root / "docs" / "relational-databases").exists():
            docs_root = docs_root / "docs"  # recebeu a raiz do clone, e nao a pasta docs/
        print(f"[grafo] refazendo o grafo sem Neo4j a partir de {docs_root} ...", file=sys.stderr)
        nos, arestas = grafo_offline(docs_root)
        origem = "offline"
    else:
        print("[grafo] lendo nos e arestas do Neo4j ...", file=sys.stderr)
        nos, arestas = grafo_do_neo4j()
        origem = "neo4j"
    linhas = linhas_do_grafo(nos, arestas)
    args.saida.parent.mkdir(parents=True, exist_ok=True)
    with args.saida.open("w", encoding="utf-8") as fh:
        for linha in linhas:
            fh.write(linha + "\n")
    digital = impressao_digital_do_grafo(linhas)
    cont = contagens(nos, arestas)
    manifesto = {"origem": origem, "impressao_digital_grafo": digital, "contagens": cont,
                 "linhas": len(linhas), "gerado_em": datetime.now().isoformat(timespec="seconds")}
    args.saida.with_suffix(".manifesto.json").write_text(
        json.dumps(manifesto, indent=1, ensure_ascii=False), encoding="utf-8")
    for k, v in cont.items():
        print(f"[grafo]   {k:22} {v:>9,}")
    print(f"[grafo] {len(linhas):,} linhas em {time.time() - t0:.1f}s -> {args.saida}")
    print(f"[grafo] impressao digital do grafo: {digital}")
    return 0


def ler_grafo(caminho: Path) -> tuple[dict, dict, str]:
    nos, arestas, linhas = defaultdict(list), defaultdict(list), []
    with caminho.open(encoding="utf-8") as fh:
        for linha in fh:
            linha = linha.rstrip("\n")
            if not linha:
                continue
            linhas.append(linha)
            o = json.loads(linha)
            if "no" in o:
                nos[o["no"]].append(o["k"])
            elif o["t"] == "MENTIONS":
                arestas["MENTIONS"].append((o["a"], o["r"], o["b"], o["n"], o["nc"]))
            else:
                arestas[o["t"]].append((o["a"], o["b"]))
    return dict(nos), dict(arestas), impressao_digital_do_grafo(linhas)


def chave_tsql(nome: str, fundir_gemeos: bool) -> str:
    """No de um TSqlObject. Com a fusao, objetos com a mesma forma de
    superficie -- em geral o par X e X_TSQL -- viram um no so: no texto eles
    sao indistinguiveis, e o casamento liga os dois aos mesmos trechos.
    """
    if fundir_gemeos:
        forma = chunking.surface_form_of_tsql(nome)
        if forma:
            return "T|~" + forma.lower()
    return "T|" + nome


class Grafo:
    """Grafo sem direcao e sem peso, com indices inteiros para o PageRank."""

    def __init__(self, nos: dict, arestas: dict, *, so_mentions: bool = False,
                 sem_codigo: bool = False, fundir_gemeos: bool = True,
                 relacoes: list[dict] | None = None):
        self.fundir_gemeos = fundir_gemeos
        chaves: set[str] = set()
        chaves.update("D|" + k for k in nos.get("Document", []))
        chaves.update("C|" + k for k in nos.get("Chunk", []))
        chaves.update("K|" + k for k in nos.get("Concept", []))
        chaves.update(chave_tsql(k, fundir_gemeos) for k in nos.get("TSqlObject", []))
        self.chaves = sorted(chaves)
        self.indice = {k: i for i, k in enumerate(self.chaves)}

        def no_alvo(rotulo: str, nome: str) -> str:
            return "K|" + nome if rotulo == "Concept" else chave_tsql(nome, fundir_gemeos)

        pares: set[tuple[int, int]] = set()
        trechos_por_entidade: dict[int, set[int]] = defaultdict(set)
        self.n_por_tipo = {}
        for tipo in TIPOS_DE_ARESTA:
            if so_mentions and tipo != "MENTIONS":
                continue
            antes = len(pares)
            for e in arestas.get(tipo, []):
                if tipo == "MENTIONS":
                    if sem_codigo and not e[3]:
                        continue
                    a, b = "C|" + e[0], no_alvo(e[1], e[2])
                elif tipo == "HAS_CHUNK":
                    a, b = "D|" + e[0], "C|" + e[1]
                elif tipo == "HAS_KEYWORD":
                    a, b = "D|" + e[0], "K|" + e[1]
                elif tipo == "DOCUMENTS":
                    a, b = "D|" + e[0], chave_tsql(e[1], fundir_gemeos)
                else:
                    a, b = "D|" + e[0], "D|" + e[1]
                i, j = self.indice[a], self.indice[b]
                if i == j:
                    continue
                pares.add((min(i, j), max(i, j)))
                if tipo == "MENTIONS":
                    trechos_por_entidade[j].add(i)
            self.n_por_tipo[tipo] = len(pares) - antes
        # Relacoes da LLM: uma aresta entre as duas entidades de cada relacao; os
        # gemeos de TSqlObject ja chegam como uma lista de nos.
        if relacoes is not None:
            antes = len(pares)
            for rel in relacoes:
                lados = []
                for lado in ("origem", "destino"):
                    rotulo, nomes = rel[f"{lado}_rotulo"], rel[f"{lado}_nos"]
                    lados.append({self.indice[no_alvo(rotulo, nome)] for nome in nomes})
                for i in lados[0]:
                    for j in lados[1]:
                        if i != j:
                            pares.add((min(i, j), max(i, j)))
            self.n_por_tipo["RELACAO_LLM"] = len(pares) - antes
        p = np.array(sorted(pares), dtype=np.int64).reshape(-1, 2)
        self.origem = np.concatenate([p[:, 0], p[:, 1]])
        self.destino = np.concatenate([p[:, 1], p[:, 0]])
        self.n = len(self.chaves)
        self.grau = np.bincount(self.origem, minlength=self.n).astype(np.float64)
        self.n_arestas = len(pares)
        self.trechos_por_entidade = {i: len(v) for i, v in trechos_por_entidade.items()}
        self.e_trecho = np.array([k.startswith("C|") for k in self.chaves])

    def ppr(self, reinicio: np.ndarray, amortecimento: float = AMORTECIMENTO,
            tol: float = 1e-12, max_iter: int = 200) -> np.ndarray:
        """PageRank personalizado por iteracao de potencia.

        x <- (1 - a) r + a (A D^-1 x + massa_sem_saida * r). A massa de um no
        sem aresta volta para as sementes, como no tratamento usual.
        """
        r = reinicio / reinicio.sum()
        x = r.copy()
        sem_saida = self.grau == 0
        grau = np.where(sem_saida, 1.0, self.grau)
        for _ in range(max_iter):
            espalhado = np.bincount(self.destino, weights=(x / grau)[self.origem], minlength=self.n)
            novo = (1 - amortecimento) * r + amortecimento * (espalhado + x[sem_saida].sum() * r)
            if np.abs(novo - x).sum() < tol:
                return novo
            x = novo
        return x


def refazer_mencoes(trechos: list[dict], automato, automato_codigo,
                    df_max: float = DF_MAX) -> tuple[set, set]:
    """Refaz o casamento, o corte por frequencia documental e a agregacao do
    chunking_e_mentions.py sobre os trechos exportados, com as funcoes dele.

    Devolve as regras (rotulo, padrao) cortadas pelo limiar e as arestas
    MENTIONS resultantes, como (chunk_id, rotulo, alvo, count, count_code).
    As regras cortadas nao viram sementes. As arestas servem para conferir
    que os trechos e o grafo vieram da mesma rodada.
    """
    brutos = []
    for t in trechos:
        for (lab, alvo, pad), n in chunking.find_mentions(t["text_matchable"] or "", automato).items():
            brutos.append((t["chunk_id"], lab, alvo, pad, n, False))
        if automato_codigo is not None and t.get("code"):
            codigo = "\n".join(cb["content"] for cb in t["code"])
            for (lab, alvo, pad), n in chunking.find_mentions(codigo, automato_codigo).items():
                brutos.append((t["chunk_id"], lab, alvo, pad, n, True))
    df = chunking.medir_df(brutos)
    restantes, cortadas = chunking.filtrar_por_df(brutos, df, df_max * len(trechos))
    prosa: dict[tuple, int] = defaultdict(int)
    codigo: dict[tuple, int] = defaultdict(int)
    for cid, lab, alvo, _pad, n, de_codigo in restantes:
        (codigo if de_codigo else prosa)[(cid, lab, alvo)] += n
    mencoes = {(*k, prosa.get(k, 0), codigo.get(k, 0)) for k in set(prosa) | set(codigo)}
    return cortadas, mencoes


def padroes_cortados(trechos: list[dict], automato, automato_codigo, df_max: float = DF_MAX) -> set:
    """As regras (rotulo, padrao) cortadas pelo limiar, refeitas dos trechos."""
    return refazer_mencoes(trechos, automato, automato_codigo, df_max)[0]


def conferir_mencoes(mencoes: set, arestas_mentions: list[tuple]) -> dict:
    """Compara as MENTIONS refeitas dos trechos com as do grafo exportado.

    Arestas diferentes impedem a consulta: trechos de outra rodada do
    chunking dariam outras regras cortadas. Contagens diferentes em arestas
    iguais so geram aviso.
    """
    do_grafo = {(a, r, b, int(n or 0), int(nc or 0)) for a, r, b, n, nc in arestas_mentions}
    chaves_t = {m[:3] for m in mencoes}
    chaves_g = {m[:3] for m in do_grafo}
    return {"n_trechos": len(mencoes), "n_grafo": len(do_grafo),
            "so_nos_trechos": len(chaves_t - chaves_g), "so_no_grafo": len(chaves_g - chaves_t),
            "contagens_diferentes": len(mencoes - do_grafo) - len(chaves_t - chaves_g)}


def sementes_da_pergunta(texto: str, automato, cortadas: set, grafo: Grafo,
                         bm25: list[tuple[str, float]] | None,
                         peso_trechos: float = PESO_TRECHOS_BM25,
                         normalizacao: str = "soma") -> tuple[np.ndarray, dict]:
    """Vetor de reinicio do PageRank e o relatorio das sementes.

    Entidades: peso 1/(trechos que mencionam o no), normalizado para somar 1.
    Trechos do BM25: o fator (0,05) vezes a pontuacao dividida pela soma das
    pontuacoes (normalizacao "soma", a principal: o grupo do BM25 soma 0,05
    em toda pergunta) ou pela maior delas (normalizacao "maxima", ablacao: o
    primeiro trecho recebe 0,05, como no HippoRAG 2). O vetor final e
    normalizado; sem entidade, ficam so os trechos do BM25.
    """
    entidades: dict[str, float] = {}
    n_trechos: dict[str, int] = {}
    for (lab, alvo, pad) in chunking.find_mentions(texto, automato):
        if (lab, pad) in cortadas:
            continue
        chave = "K|" + alvo if lab == "Concept" else chave_tsql(alvo, grafo.fundir_gemeos)
        if chave not in grafo.indice:
            continue
        n_trechos[chave] = grafo.trechos_por_entidade.get(grafo.indice[chave], 0)
        entidades[chave] = 1.0 / max(n_trechos[chave], 1)
    r = np.zeros(grafo.n)
    if entidades:
        soma = sum(entidades.values())
        entidades = {k: w / soma for k, w in entidades.items()}
        for chave, w in entidades.items():
            r[grafo.indice[chave]] += w
    if normalizacao not in ("soma", "maxima"):
        raise ValueError(f"normalizacao desconhecida: {normalizacao!r}")
    trechos_bm25: dict[str, float] = {}
    positivos = [(cid, s) for cid, s in (bm25 or []) if s > 0 and "C|" + cid in grafo.indice]
    if positivos:
        pontuacoes = [s for _, s in positivos]
        base = sum(pontuacoes) if normalizacao == "soma" else max(pontuacoes)
        for cid, s in positivos:
            trechos_bm25[cid] = peso_trechos * s / base
            r[grafo.indice["C|" + cid]] += trechos_bm25[cid]
    relatorio = {
        "entidades": {k: round(v, 8) for k, v in sorted(entidades.items(), key=lambda x: (-x[1], x[0]))},
        "trechos_que_mencionam": {k: n_trechos[k] for k in sorted(n_trechos)},
        "n_entidades": len(entidades),
        "n_trechos_bm25": len(trechos_bm25),
        "massa_trechos_bm25": round(sum(trechos_bm25.values()), 8),
    }
    return r, relatorio


def ranquear(grafo: Grafo, x: np.ndarray, k: int, por_grau: bool = False) -> list[tuple[str, float]]:
    """Os k trechos com mais massa, com empate decidido pelo chunk_id.

    por_grau (ablacao): ordena pela massa dividida pelo grau do trecho, como
    na varredura de Andersen, Chung e Lang (2006). Num grafo sem direcao, a
    massa do PageRank cresce com o grau, e o trecho longo, com muitas
    entidades, sobe por ter muitas ligacoes."""
    idx = np.nonzero(grafo.e_trecho & (x > 0))[0]
    if por_grau:
        valores = x[idx] / np.maximum(grafo.grau[idx], 1.0)
    else:
        valores = x[idx]
    pares = [(grafo.chaves[i][2:], float(v)) for i, v in zip(idx, valores)]
    return sorted(pares, key=lambda p: (-p[1], p[0]))[:k]


def cmd_recuperar(args) -> int:
    t0 = time.time()
    nos, arestas, digital_grafo = ler_grafo(args.grafo)
    trechos = carregar_trechos(args.trechos)
    digital_trechos = impressao_digital(trechos)
    ids_trechos = {t["chunk_id"] for t in trechos}
    if ids_trechos != set(nos.get("Chunk", [])):
        raise SystemExit("ERRO: os trechos do arquivo e os do grafo nao sao os mesmos. "
                         "Exporte os dois do mesmo Neo4j.")
    perguntas = carregar_perguntas(args.perguntas, args.perguntas_filtro)

    run_bm25, manifesto_bm25 = {}, None
    if not args.sem_bm25:
        run_bm25 = ler_run(args.bm25)
        m = args.bm25.with_suffix(".manifesto.json")
        if m.exists():
            manifesto_bm25 = json.loads(m.read_text(encoding="utf-8"))
            if manifesto_bm25.get("impressao_digital_trechos") != digital_trechos:
                raise SystemExit("ERRO: o run do BM25 foi feito sobre outros trechos "
                                 "(impressao digital diferente).")

    relacoes, sha_relacoes = None, None
    if args.relacoes is not None:
        bruto = args.relacoes.read_bytes()
        sha_relacoes = hashlib.sha256(bruto).hexdigest()
        relacoes = [json.loads(l) for l in bruto.decode("utf-8").splitlines() if l.strip()]
        print(f"[relacoes] {len(relacoes):,} relacoes da LLM ({args.relacoes}, SHA-256 "
              f"{sha_relacoes[:8]}...)", file=sys.stderr)
    grafo = Grafo(nos, arestas, so_mentions=args.so_mentions, sem_codigo=args.sem_codigo,
                  fundir_gemeos=not args.sem_fundir_gemeos, relacoes=relacoes)
    print(f"[grafo] {grafo.n:,} nos, {grafo.n_arestas:,} arestas sem direcao "
          f"({', '.join(f'{k} {v:,}' for k, v in grafo.n_por_tipo.items())})", file=sys.stderr)

    automato, automato_codigo, _ = chunking.build_matcher(sorted(set(nos["Concept"])),
                                                     sorted(set(nos["TSqlObject"])))
    n_blocos = sum(len(t.get("code") or []) for t in trechos)
    cortadas, mencoes = refazer_mencoes(trechos, automato, automato_codigo)
    conf = conferir_mencoes(mencoes, arestas.get("MENTIONS", []))
    if conf["so_nos_trechos"] or conf["so_no_grafo"]:
        raise SystemExit(
            f"ERRO: as MENTIONS refeitas a partir dos trechos ({conf['n_trechos']:,}) nao sao as "
            f"do grafo ({conf['n_grafo']:,}): {conf['so_nos_trechos']:,} so nos trechos e "
            f"{conf['so_no_grafo']:,} so no grafo. Os trechos e o grafo precisam vir da mesma "
            "rodada do chunking: exporte os dois de novo do Neo4j (trechos.jsonl).")
    if conf["contagens_diferentes"]:
        print(f"[aviso] {conf['contagens_diferentes']:,} arestas com contagens diferentes "
              "entre os trechos e o grafo", file=sys.stderr)
    print(f"[conferencia] {len(trechos):,} trechos com {n_blocos:,} blocos de codigo; as "
          f"{conf['n_trechos']:,} MENTIONS refeitas dos trechos sao as do grafo", file=sys.stderr)
    print(f"[sementes] {len(cortadas)} regras cortadas pela frequencia documental "
          f"(a configuracao de referencia corta 45)", file=sys.stderr)

    resultados, relatorios = {}, {}
    for qid, texto in perguntas:
        r, rel = sementes_da_pergunta(texto, automato, cortadas, grafo,
                                      None if args.sem_bm25 else run_bm25.get(qid, []),
                                      normalizacao=args.normalizacao_bm25)
        relatorios[qid] = rel
        if r.sum() == 0:
            resultados[qid] = []
            print(f"  {qid}: sem semente", file=sys.stderr)
            continue
        x = grafo.ppr(r, args.amortecimento)
        resultados[qid] = ranquear(grafo, x, args.k, por_grau=args.normalizar_grau)
        print(f"  {qid}: {rel['n_entidades']} entidades, {rel['n_trechos_bm25']} trechos do BM25",
              file=sys.stderr)

    tag = args.tag or "grafo_ppr" + "".join(s for s, ok in (
        ("_so_mentions", args.so_mentions), ("_sem_codigo", args.sem_codigo),
        ("_sem_bm25", args.sem_bm25), ("_sem_fundir_gemeos", args.sem_fundir_gemeos),
        ("_bm25_maxima", args.normalizacao_bm25 == "maxima"),
        ("_por_grau", args.normalizar_grau), ("_llm", args.relacoes is not None)) if ok)
    gravar_run(args.saida, resultados, tag, {
        "metodo_detalhe": "PageRank personalizado sobre o grafo do Neo4j",
        "amortecimento": args.amortecimento, "peso_trechos_bm25": PESO_TRECHOS_BM25,
        "normalizacao_bm25": None if args.sem_bm25 else args.normalizacao_bm25,
        "ranking": "massa/grau" if args.normalizar_grau else "massa",
        "peso_entidade": "1/(trechos que mencionam o no), normalizado para somar 1",
        "df_max_sementes": DF_MAX, "n_regras_cortadas": len(cortadas),
        "conferencia_mentions": conf, "n_blocos_de_codigo": n_blocos,
        "arestas": [t for t in TIPOS_DE_ARESTA if not args.so_mentions or t == "MENTIONS"]
        + (["RELACAO_LLM"] if relacoes is not None else []),
        "relacoes_llm": None if relacoes is None else {"arquivo": str(args.relacoes),
                                                       "sha256": sha_relacoes, "n": len(relacoes)},
        "so_mentions": args.so_mentions, "sem_codigo": args.sem_codigo,
        "sem_bm25": args.sem_bm25, "fundir_gemeos": not args.sem_fundir_gemeos,
        "n_nos": grafo.n, "n_arestas": grafo.n_arestas, "arestas_por_tipo": grafo.n_por_tipo,
        "impressao_digital_grafo": digital_grafo, "impressao_digital_trechos": digital_trechos,
        "run_bm25": None if args.sem_bm25 else str(args.bm25),
        "k": args.k, "filtro_perguntas": args.perguntas_filtro,
        "numpy": np.__version__, "segundos": round(time.time() - t0, 1),
    })
    args.saida.with_suffix(".sementes.json").write_text(
        json.dumps(relatorios, indent=1, ensure_ascii=False), encoding="utf-8")
    sem = [q for q, rel in relatorios.items() if rel["n_entidades"] == 0]
    print(f"[sementes] perguntas sem entidade: {len(sem)} ({', '.join(sem) or '-'})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("exportar-grafo", help="le o grafo do Neo4j (ou refaz sem Neo4j)")
    p.add_argument("--saida", type=Path, required=True)
    p.add_argument("--offline", action="store_true", help="refaz o grafo a partir do corpus")
    p.add_argument("--docs-root", type=Path, default=None)
    p.set_defaults(func=cmd_exportar_grafo)

    p = sub.add_parser("recuperar", help="PageRank personalizado -> run")
    p.add_argument("--grafo", type=Path, required=True)
    p.add_argument("--trechos", type=Path, required=True)
    p.add_argument("--perguntas", type=Path, required=True)
    p.add_argument("--perguntas-filtro", choices=["verificadas", "todas"], default="verificadas")
    p.add_argument("--bm25", type=Path, default=None, help="run do BM25 (sementes de trecho)")
    p.add_argument("--saida", type=Path, required=True)
    p.add_argument("--k", type=int, default=100)
    p.add_argument("--amortecimento", type=float, default=AMORTECIMENTO)
    p.add_argument("--tag", default=None)
    p.add_argument("--so-mentions", action="store_true", help="ablacao: so MENTIONS")
    p.add_argument("--sem-codigo", action="store_true", help="ablacao: so MENTIONS com prosa")
    p.add_argument("--sem-bm25", action="store_true", help="ablacao: so entidades como sementes")
    p.add_argument("--sem-fundir-gemeos", action="store_true", help="ablacao: X e X_TSQL separados")
    p.add_argument("--normalizacao-bm25", choices=["soma", "maxima"], default="soma",
                   help="peso dos trechos do BM25: 0,05 x pontuacao/soma (principal) ou "
                        "0,05 x pontuacao/maxima (ablacao)")
    p.add_argument("--normalizar-grau", action="store_true",
                   help="ablacao: ordena os trechos pela massa dividida pelo grau")
    p.add_argument("--relacoes", type=Path, default=None,
                   help="arestas consolidadas das relacoes extraidas pela LLM")
    p.set_defaults(func=cmd_recuperar)

    args = ap.parse_args()
    if getattr(args, "cmd", None) == "recuperar" and not args.sem_bm25 and args.bm25 is None:
        ap.error("--bm25 e obrigatorio (ou use --sem-bm25 para a ablacao)")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
