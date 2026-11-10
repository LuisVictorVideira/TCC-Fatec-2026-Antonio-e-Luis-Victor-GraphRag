#!/usr/bin/env python3
r"""
Linhas de base de recuperacao.

    bm25      BM25 (variante do Lucene, k1 = 1,2 e b = 0,75) sobre os trechos
    vetorial  busca exata por cosseno no pgvector, com embeddings do Ollama
    hibrido   fusao de dois ou mais runs por Reciprocal Rank Fusion (k = 60)

Todos usam os mesmos trechos, com a mesma representacao de texto
(texto_para_indice: titulo do documento, titulo da secao e texto), e gravam
o resultado no formato de run do TREC:

    <id_pergunta> Q0 <chunk_id> <posicao> <pontuacao> <metodo>

Cada run vem com um manifesto JSON (parametros, versoes e impressao digital
dos trechos).

Uso:
    python src/baselines.py bm25 --trechos outputs/trechos.jsonl \
        --perguntas dados/perguntas_avaliacao.yaml --saida outputs/runs/bm25.run
    python src/baselines.py hibrido --runs outputs/runs/bm25.run \
        outputs/runs/grafo.run --saida outputs/runs/hibrido.run

A busca vetorial (subcomandos indexar-vetores e vetorial) esta implementada,
mas nao foi executada no artigo; ela usa as variaveis PG_* e OLLAMA_URL do
.env.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERRO: PyYAML nao instalado. Rode: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

try:
    from dotenv import find_dotenv, load_dotenv
    load_dotenv(find_dotenv(), override=False)
except ImportError:
    pass

# Mesma tabela do chunking_e_mentions.py, copiada para nao exigir
# NEO4J_PASSWORD na importacao. Diferenca deliberada: aqui o marcador e
# reconhecido com ou sem espaco depois de INCLUDE; o chunking so reconhece
# a forma com espaco.
INCLUDE_REPLACEMENTS = {
    "ssnoversion": "SQL Server", "sscurrent": "SQL Server",
    "sswinver": "SQL Server (Windows)", "ssde": "SQL Server Database Engine",
    "sssql11": "SQL Server 2012", "sssql14": "SQL Server 2014",
    "sssql15": "SQL Server 2016", "sssql16": "SQL Server 2017",
    "sssql17": "SQL Server 2017", "sssql19": "SQL Server 2019",
    "sssql22": "SQL Server 2022", "ssql19": "SQL Server 2019",
    "ssql22": "SQL Server 2022", "sssqlv14": "SQL Server 2017",
    "sssqlv15": "SQL Server 2019", "sssqlv16": "SQL Server 2022",
    "azuresqldb-current": "Azure SQL Database", "azuresqldb": "Azure SQL Database",
    "azuresqldb-mi-current": "Azure SQL Managed Instance",
    "asdb": "Azure SQL Database", "asdbmi": "Azure SQL Managed Instance",
    "sssqlfabric": "Fabric SQL Database", "sssdw": "Azure Synapse Analytics",
    "sssdwfull": "Azure Synapse Analytics (dedicated SQL pool)",
    "sspdw": "Azure Synapse Analytics",
    "ssmanstudiofull": "SQL Server Management Studio", "ssmanstudio": "SSMS",
    "ssanoversion": "Analysis Services", "ssisnoversion": "Integration Services",
    "ssrsnoversion": "Reporting Services",
}
REPRESENTACAO = "documento_secao_texto"
RE_INCLUDE = re.compile(r"\[!INCLUDE\s*\[([^\]]*)\]\(([^)]*)\)\]", re.IGNORECASE)
RE_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
RE_HTML = re.compile(r"<[^>]+>")
RE_DIRETIVA = re.compile(r"^\s*:::.*$", re.MULTILINE)


def _expandir(m: re.Match) -> str:
    nome = m.group(1).lower().strip()
    if nome.endswith("-md"):
        nome = nome[:-3]
    return INCLUDE_REPLACEMENTS.get(nome, " ")


def texto_para_indice(t: dict) -> str:
    """Texto que os baselines indexam (documento_secao_texto).

    titulo do documento + titulo da secao + texto bruto do trecho, com:
      - placeholders de INCLUDE expandidos (com ou sem espaco); os que nao
        estao na tabela sao removidos, porque o texto que eles renderizam
        nao esta no corpus;
      - links reduzidos ao rotulo ([texto](url) -> texto), sem a URL;
      - tags HTML e diretivas ::: (moniker, image, row) removidas;
      - blocos de codigo MANTIDOS: o BM25 e o embedding veem o mesmo que o
        LLM vai receber como contexto.
    O titulo do documento entra porque o trecho de uma secao H2 nao repete
    o assunto da pagina (ex.: '## Remarks').
    """
    corpo = RE_INCLUDE.sub(_expandir, t["text"])
    corpo = RE_LINK.sub(r"\1", corpo)
    corpo = RE_HTML.sub(" ", corpo)
    corpo = RE_DIRETIVA.sub(" ", corpo)
    return f"{t.get('doc_title') or ''}\n{t.get('section_title') or ''}\n{corpo}"


def carregar_trechos(caminho: Path) -> list[dict]:
    with caminho.open(encoding="utf-8") as fh:
        trechos = [json.loads(linha) for linha in fh if linha.strip()]
    ids = [t["chunk_id"] for t in trechos]
    if len(set(ids)) != len(ids):
        raise SystemExit("ERRO: chunk_id repetido no arquivo de trechos.")
    return trechos


def impressao_digital(trechos: list[dict]) -> str:
    """SHA-256 sobre (chunk_id, sha256 do texto) em ordem de chunk_id, como no
    reproduzir_sem_neo4j.py.
    """
    h = hashlib.sha256()
    for t in sorted(trechos, key=lambda x: x["chunk_id"]):
        h.update(t["chunk_id"].encode("utf-8"))
        h.update(b"\t")
        h.update(hashlib.sha256(t["text"].encode("utf-8")).hexdigest().encode())
        h.update(b"\n")
    return h.hexdigest()


def carregar_perguntas(caminho: Path, filtro: str) -> list[tuple[str, str]]:
    """Le o perguntas_avaliacao.yaml e devolve [(id, pergunta_en)].

    filtro: 'verificadas' (verificado == sim) ou 'todas' (exceto descartadas).
    As perguntas sao enviadas em ingles.
    """
    dados = yaml.safe_load(caminho.read_text(encoding="utf-8"))
    itens = dados["perguntas"] if isinstance(dados, dict) else dados
    saida = []
    for q in itens:
        estado = str(q.get("verificado", "nao")).strip().lower()
        if estado == "descartada":
            continue
        if filtro == "verificadas" and estado != "sim":
            continue
        texto = (q.get("pergunta_en") or "").strip()
        if not texto:
            raise SystemExit(f"ERRO: pergunta {q.get('id')} sem pergunta_en.")
        saida.append((str(q["id"]), texto))
    if not saida:
        raise SystemExit("ERRO: nenhuma pergunta selecionada pelo filtro.")
    return saida


def gravar_run(caminho: Path, resultados: dict, tag: str, manifesto: dict) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with caminho.open("w", encoding="utf-8") as fh:
        for qid in sorted(resultados):
            for pos, (cid, pont) in enumerate(resultados[qid], 1):
                fh.write(f"{qid} Q0 {cid} {pos} {pont:.6f} {tag}\n")
    manifesto = dict(manifesto, run=str(caminho), metodo=tag,
                     gerado_em=datetime.now().isoformat(timespec="seconds"),
                     perguntas=sorted(resultados))
    caminho.with_suffix(".manifesto.json").write_text(
        json.dumps(manifesto, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"[run] {sum(len(v) for v in resultados.values()):,} linhas, "
          f"{len(resultados)} perguntas -> {caminho}")


def ler_run(caminho: Path) -> dict[str, list[tuple[str, float]]]:
    porq = defaultdict(list)
    with caminho.open(encoding="utf-8") as fh:
        for linha in fh:
            p = linha.split()
            if len(p) < 6:
                continue
            porq[p[0]].append((int(p[3]), p[2], float(p[4])))
    return {q: [(cid, s) for _, cid, s in sorted(v)] for q, v in porq.items()}


def ordenar(pontuacoes: dict[str, float], k: int) -> list[tuple[str, float]]:
    """Ordem deterministica: maior pontuacao primeiro, empate por chunk_id."""
    return sorted(pontuacoes.items(), key=lambda x: (-x[1], x[0]))[:k]


BM25_STOPWORDS = "en"  # lista de 33 palavras do analisador ingles do Lucene


def cmd_bm25(args) -> int:
    try:
        import bm25s
        import numpy as np
        import Stemmer
    except ImportError as exc:
        print(f"ERRO: {exc}. Rode: pip install bm25s PyStemmer numpy", file=sys.stderr)
        return 1
    trechos = carregar_trechos(args.trechos)
    perguntas = carregar_perguntas(args.perguntas, args.perguntas_filtro)
    ids = np.array([t["chunk_id"] for t in trechos])
    stemmer = Stemmer.Stemmer("english")
    t0 = time.time()
    tokens = bm25s.tokenize([texto_para_indice(t) for t in trechos], lower=True,
                            stopwords=BM25_STOPWORDS, stemmer=stemmer,
                            show_progress=False)
    modelo = bm25s.BM25(method="lucene", k1=args.k1, b=args.b)
    modelo.index(tokens, show_progress=False)
    print(f"[bm25] {len(trechos):,} trechos indexados em {time.time() - t0:.1f}s")
    resultados = {}
    for qid, texto in perguntas:
        q = bm25s.tokenize([texto], lower=True, stopwords=BM25_STOPWORDS,
                           stemmer=stemmer, return_ids=False, show_progress=False)[0]
        pont = modelo.get_scores(q)
        idx = np.lexsort((ids, -pont))[:args.k]  # empate pelo chunk_id
        resultados[qid] = [(str(ids[i]), float(pont[i])) for i in idx if pont[i] > 0]
    gravar_run(args.saida, resultados, "bm25", {
        "metodo_detalhe": "BM25 variante Lucene (bm25s)",
        "k1": args.k1, "b": args.b, "stopwords": "lucene-en-33",
        "stemmer": "Snowball english (PyStemmer)",
        "token_pattern": r"(?u)\b\w\w+\b", "minusculas": True,
        "bm25s": bm25s.__version__, "representacao": REPRESENTACAO,
        "n_trechos": len(trechos), "impressao_digital_trechos": impressao_digital(trechos),
        "k": args.k, "filtro_perguntas": args.perguntas_filtro,
    })
    return 0


def dsn_postgres() -> str:
    import psycopg
    if os.environ.get("PG_DSN"):
        return os.environ["PG_DSN"]
    usuario = os.environ.get("PG_USER") or os.environ.get("POSTGRES_USER") or "postgres"
    return psycopg.conninfo.make_conninfo(
        host=os.environ.get("PG_HOST", "localhost"),
        port=os.environ.get("PG_PORT", "5433"),
        user=usuario,
        password=os.environ.get("PG_PASSWORD") or os.environ.get("POSTGRES_PASSWORD") or None,
        dbname=os.environ.get("PG_DATABASE") or os.environ.get("POSTGRES_DB") or usuario,
    )


def nome_tabela(modelo: str) -> str:
    return "trechos_vetores_" + re.sub(r"[^a-z0-9]+", "_", modelo.lower()).strip("_")


def url_ollama() -> str:
    return os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")


class ErroOllama(RuntimeError):
    pass


def ollama_embed(textos: list[str], modelo: str, num_ctx: int, truncar: bool) -> list[list[float]]:
    import requests
    r = requests.post(f"{url_ollama()}/api/embed", timeout=900, json={
        "model": modelo, "input": textos, "truncate": truncar,
        "options": {"num_ctx": num_ctx},
    })
    if r.status_code != 200:
        raise ErroOllama(f"HTTP {r.status_code}: {r.text[:300]}")
    emb = r.json().get("embeddings")
    if not emb or len(emb) != len(textos):
        raise ErroOllama("resposta sem embeddings ou com quantidade diferente da enviada")
    return emb


def identidade_do_modelo(modelo: str) -> dict:
    """Tag, digest do manifesto e digest do arquivo de pesos."""
    import requests
    info = {"modelo": modelo}
    try:
        tags = requests.get(f"{url_ollama()}/api/tags", timeout=30).json().get("models", [])
        for m in tags:
            if m.get("name") == modelo or m.get("model") == modelo or \
                    m.get("name") == f"{modelo}:latest":
                info["tag"] = m.get("name")
                info["digest_manifesto"] = m.get("digest")
        show = requests.post(f"{url_ollama()}/api/show", json={"model": modelo},
                             timeout=30).json()
        achado = re.search(r"sha256[-:]([0-9a-f]{64})", show.get("modelfile", ""))
        if achado:
            info["digest_pesos"] = "sha256-" + achado.group(1)
        info["versao_ollama"] = requests.get(f"{url_ollama()}/api/version",
                                             timeout=30).json().get("version")
    except Exception as exc:
        info["aviso"] = f"identidade incompleta: {type(exc).__name__}: {exc}"
    return info


def normalizar(v):
    import numpy as np
    a = np.asarray(v, dtype=np.float64)
    n = np.linalg.norm(a)
    return (a / n if n > 0 else a).tolist()


def literal_vetor(v) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in v) + "]"


def embed_com_controle(textos: list[str], modelo: str, num_ctx: int) -> list[tuple[list[float], bool]]:
    """Embeda sem truncar; o que nao couber e refeito truncando e marcado.

    Truncamento silencioso e o risco R8. Aqui ele nunca e silencioso: cada
    trecho truncado fica com truncado = true na tabela e entra na contagem.
    """
    try:
        return [(normalizar(v), False) for v in ollama_embed(textos, modelo, num_ctx, False)]
    except ErroOllama:
        if len(textos) == 1:
            v = ollama_embed(textos, modelo, num_ctx, True)[0]
            return [(normalizar(v), True)]
    saida = []
    for t in textos:  # o lote falhou: descobre qual trecho passa do contexto
        saida.extend(embed_com_controle([t], modelo, num_ctx))
    return saida


def cmd_indexar_vetores(args) -> int:
    import psycopg
    trechos = carregar_trechos(args.trechos)
    tabela = nome_tabela(args.modelo)
    ident = identidade_do_modelo(args.modelo)
    with psycopg.connect(dsn_postgres()) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.execute("""CREATE TABLE IF NOT EXISTS indexacao_metadados (
                            tabela text, chave text, valor text,
                            PRIMARY KEY (tabela, chave))""")
        existe = conn.execute("SELECT to_regclass(%s)", (tabela,)).fetchone()[0]
        feitos = set()
        if existe:
            feitos = {r[0] for r in conn.execute(f"SELECT chunk_id FROM {tabela}")}
        pendentes = [t for t in trechos if t["chunk_id"] not in feitos]
        print(f"[vetores] tabela {tabela}: {len(feitos):,} ja indexados, "
              f"{len(pendentes):,} pendentes")
        t0 = time.time()
        for i in range(0, len(pendentes), args.lote):
            lote = pendentes[i:i + args.lote]
            vetores = embed_com_controle([texto_para_indice(t) for t in lote],
                                         args.modelo, args.num_ctx)
            if not existe:
                dim = len(vetores[0][0])
                conn.execute(f"""CREATE TABLE {tabela} (
                                     chunk_id text PRIMARY KEY,
                                     truncado boolean NOT NULL,
                                     embedding vector({dim}) NOT NULL)""")
                existe = True
            with conn.cursor() as cur:
                cur.executemany(
                    f"INSERT INTO {tabela} (chunk_id, truncado, embedding) "
                    f"VALUES (%s, %s, %s::vector) ON CONFLICT (chunk_id) DO NOTHING",
                    [(t["chunk_id"], tr, literal_vetor(v)) for t, (v, tr) in zip(lote, vetores)])
            conn.commit()  # grava a cada lote: uma queda nao refaz o que ja foi gravado
            feito = min(i + args.lote, len(pendentes))
            taxa = feito / max(time.time() - t0, 1e-9)
            print(f"  {feito:,}/{len(pendentes):,}  ({taxa:.1f} trechos/s)", flush=True)
        n, n_trunc, dim = conn.execute(
            f"SELECT count(*), count(*) FILTER (WHERE truncado), "
            f"max(vector_dims(embedding)) FROM {tabela}").fetchone()
        meta = dict(ident, num_ctx=args.num_ctx, dimensao=dim, n_trechos=n,
                    n_truncados=n_trunc, representacao=REPRESENTACAO,
                    impressao_digital_trechos=impressao_digital(trechos),
                    normalizado="L2", atualizado_em=datetime.now().isoformat(timespec="seconds"))
        for k, v in meta.items():
            conn.execute("""INSERT INTO indexacao_metadados (tabela, chave, valor)
                            VALUES (%s, %s, %s)
                            ON CONFLICT (tabela, chave) DO UPDATE SET valor = EXCLUDED.valor""",
                         (tabela, k, json.dumps(v, ensure_ascii=False)))
        conn.commit()
    print(f"[vetores] {n:,} trechos na tabela; {n_trunc:,} truncados "
          f"({100.0 * n_trunc / max(n, 1):.2f}%)")
    print(json.dumps(meta, indent=1, ensure_ascii=False))
    if n != len(trechos):
        print(f"[aviso] a tabela tem {n:,} vetores e o arquivo {len(trechos):,} trechos.",
              file=sys.stderr)
    return 0


def cmd_vetorial(args) -> int:
    import psycopg
    perguntas = carregar_perguntas(args.perguntas, args.perguntas_filtro)
    tabela = nome_tabela(args.modelo)
    with psycopg.connect(dsn_postgres()) as conn:
        meta = {k: json.loads(v) for k, v in conn.execute(
            "SELECT chave, valor FROM indexacao_metadados WHERE tabela = %s", (tabela,))}
        if not meta:
            print(f"ERRO: {tabela} sem metadados. Rode indexar-vetores antes.", file=sys.stderr)
            return 1
        ident = identidade_do_modelo(args.modelo)
        if meta.get("digest_pesos") and ident.get("digest_pesos") \
                and meta["digest_pesos"] != ident["digest_pesos"]:
            print("ERRO: o modelo do Ollama mudou desde a indexacao "
                  f"({meta['digest_pesos']} -> {ident['digest_pesos']}).", file=sys.stderr)
            return 1
        resultados = {}
        for qid, texto in perguntas:
            (v, truncado), = embed_com_controle([texto], args.modelo, meta.get("num_ctx", args.num_ctx))
            lit = literal_vetor(v)
            # Sem indice ANN: busca exata. Com 32.701 vetores o custo e pequeno, e a
            # ordem nao perde nada por aproximacao.
            linhas = conn.execute(
                f"SELECT chunk_id, 1 - (embedding <=> %s::vector) AS sim FROM {tabela} "
                f"ORDER BY embedding <=> %s::vector, chunk_id LIMIT %s",
                (lit, lit, args.k)).fetchall()
            resultados[qid] = [(cid, float(s)) for cid, s in linhas]
    gravar_run(args.saida, resultados, "vetorial", {
        "metodo_detalhe": "busca exata por cosseno no pgvector (sem indice ANN)",
        "tabela": tabela, "indexacao": meta, "modelo_consulta": ident,
        "k": args.k, "filtro_perguntas": args.perguntas_filtro,
    })
    return 0


def rrf(runs: list[dict], k_rrf: int, profundidade: int, k: int) -> dict:
    """Reciprocal Rank Fusion (Cormack, Clarke e Buettcher, 2009).

    pontuacao(d) = soma, em cada run, de 1 / (k_rrf + posicao de d)
    considerando so as `profundidade` primeiras posicoes de cada run.
    """
    saida = {}
    for qid in sorted(set().union(*runs)):
        acc = defaultdict(float)
        for run in runs:
            for pos, (cid, _) in enumerate(run.get(qid, [])[:profundidade], 1):
                acc[cid] += 1.0 / (k_rrf + pos)
        saida[qid] = ordenar(acc, k)
    return saida


def cmd_hibrido(args) -> int:
    runs = [ler_run(p) for p in args.runs]
    qids = [set(r) for r in runs]
    if any(q != qids[0] for q in qids):
        print("[aviso] os runs nao tem o mesmo conjunto de perguntas.", file=sys.stderr)
    resultados = rrf(runs, args.k_rrf, args.profundidade, args.k)
    manifestos = []
    for p in args.runs:
        m = Path(p).with_suffix(".manifesto.json")
        manifestos.append(json.loads(m.read_text(encoding="utf-8")) if m.exists() else str(p))
    gravar_run(args.saida, resultados, "hibrido_rrf", {
        "metodo_detalhe": "Reciprocal Rank Fusion", "k_rrf": args.k_rrf,
        "profundidade": args.profundidade, "k": args.k, "runs_de_entrada": manifestos,
    })
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def comuns(p, perguntas=True, saida=True):
        if perguntas:
            p.add_argument("--perguntas", type=Path, required=True,
                           help="perguntas_avaliacao.yaml")
            p.add_argument("--perguntas-filtro", choices=["verificadas", "todas"],
                           default="verificadas")
        if saida:
            p.add_argument("--saida", type=Path, required=True, help="arquivo .run")
        p.add_argument("--k", type=int, default=100, help="profundidade do run (padrao 100)")

    p = sub.add_parser("bm25")
    p.add_argument("--trechos", type=Path, required=True)
    p.add_argument("--k1", type=float, default=1.2)
    p.add_argument("--b", type=float, default=0.75)
    comuns(p)

    p = sub.add_parser("indexar-vetores")
    p.add_argument("--trechos", type=Path, required=True)
    p.add_argument("--modelo", default="bge-m3")
    p.add_argument("--num-ctx", type=int, default=8192)
    p.add_argument("--lote", type=int, default=16)

    p = sub.add_parser("vetorial")
    p.add_argument("--modelo", default="bge-m3")
    p.add_argument("--num-ctx", type=int, default=8192)
    comuns(p)

    p = sub.add_parser("hibrido")
    p.add_argument("--runs", type=Path, nargs="+", required=True)
    p.add_argument("--k-rrf", type=int, default=60)
    p.add_argument("--profundidade", type=int, default=100)
    comuns(p, perguntas=False)

    args = ap.parse_args()
    return {"bm25": cmd_bm25, "indexar-vetores": cmd_indexar_vetores,
            "vetorial": cmd_vetorial, "hibrido": cmd_hibrido}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
