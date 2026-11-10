#!/usr/bin/env python3
"""
Refaz o grafo de metadados e a fragmentacao a partir do corpus, sem Neo4j.

Usa as proprias funcoes do construir_grafo.py e do chunking_e_mentions.py,
sem reimplementar nada, e simula o MERGE do Neo4j com conjuntos. Serve para
conferir os numeros do grafo sem banco de dados e para exportar os trechos
no mesmo formato do exportar_trechos.py.

Uso:
    python src/reproduzir_sem_neo4j.py --conferir
    python src/reproduzir_sem_neo4j.py --conferir --exportar outputs/trechos.jsonl

--conferir compara com os numeros de referencia do corpus no commit
928a038d do sql-docs. A pasta do corpus vem do SQL_DOCS_ROOT do .env ou de
--docs-root.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

# Os scripts importados exigem NEO4J_PASSWORD na importacao; este script nao
# abre conexao, entao qualquer valor serve.
os.environ.setdefault("NEO4J_PASSWORD", "sem-conexao")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import construir_grafo as metadados
import chunking_e_mentions as chunking

# Configuracao de referencia: corpus no commit 928a038d do sql-docs,
# fragmentado com --df-max 0.02. IMPRESSAO_DIGITAL_TRECHOS resume os trechos
# (chunk_id e texto) e REFERENCIA guarda os numeros do grafo de metadados e
# da fragmentacao.
DF_MAX_REFERENCIA = 0.02

IMPRESSAO_DIGITAL_TRECHOS = "dfc1cb8893e7ad89b75df87b0da37983d59132d06670ce8364b683061e537b51"

REFERENCIA = {
    "metadados": {
        "documentos": 6151, "conceitos": 12009, "objetos_tsql": 7541,
        "has_keyword": 14780, "documents": 7665, "links_to": 34726,
        "documentos_isolados": 342,
    },
    "fragmentacao": {
        "trechos": 32701, "blocos_de_codigo": 14414,
        "casamentos_brutos": 469199, "casamentos_em_codigo": 17698,
        "regras_com_casamento": 9326, "regras_cortadas": 45,
        "casamentos_apos_corte": 362075,
        "mentions_concept": 227266, "mentions_tsql": 125910,
        "mentions_so_por_codigo": 13966,
        "conceitos_cobertos": 6993, "objetos_cobertos": 5942,
        "partes_do_splitter": 314, "secoes_divididas": 83,
    },
}


def impressao_digital(trechos: list[dict]) -> str:
    """SHA-256 sobre (chunk_id, sha256 do texto) em ordem de chunk_id.

    Duas exportacoes com a mesma impressao digital tem exatamente os mesmos
    trechos com exatamente o mesmo texto bruto. O exportar_trechos.py
    calcula a mesma coisa a partir do Neo4j.
    """
    h = hashlib.sha256()
    for t in sorted(trechos, key=lambda x: x["chunk_id"]):
        h.update(t["chunk_id"].encode("utf-8"))
        h.update(b"\t")
        h.update(hashlib.sha256(t["text"].encode("utf-8")).hexdigest().encode())
        h.update(b"\n")
    return h.hexdigest()


def reproduzir(docs_root: Path) -> dict:
    t0 = time.time()
    records = [r for r in (metadados.parse_document(f, docs_root)
                           for f in metadados.iter_markdown_files(docs_root)) if r]
    docs = {r.rel_path for r in records}
    titulos = {r.rel_path: r.title for r in records}
    conceitos: set[str] = set()
    has_kw: set[tuple] = set()
    for r in records:
        for kw in r.helpviewer_keywords:
            nome = kw.lower().strip()
            if nome:
                conceitos.add(nome)
                has_kw.add((r.rel_path, nome))
    objetos: set[str] = set()
    documents: set[tuple] = set()
    for r in records:
        for kw in r.f1_keywords:
            nome = kw.strip()
            if nome:
                objetos.add(nome)
                documents.add((r.rel_path, nome))
    links: set[tuple] = set()
    for r in records:
        for alvo in r.outbound_links:
            if alvo in docs:
                links.add((r.rel_path, alvo))
    ligados = {a for a, _ in links} | {b for _, b in links}

    automato, automato_codigo, _ = chunking.build_matcher(sorted(conceitos), sorted(objetos))
    trechos = []
    brutos = []
    em_codigo = 0
    for f in chunking.iter_markdown_files(docs_root):
        for c in chunking.chunk_document(f, docs_root):
            trechos.append(c)
            for (lab, alvo, pad), n in chunking.find_mentions(c.text_matchable, automato).items():
                brutos.append((c.chunk_id, lab, alvo, pad, n, False))
            if automato_codigo is not None and c.code_blocks:
                codigo = "\n".join(cb.content for cb in c.code_blocks)
                for (lab, alvo, pad), n in chunking.find_mentions(codigo, automato_codigo).items():
                    brutos.append((c.chunk_id, lab, alvo, pad, n, True))
                    em_codigo += 1
    df = chunking.medir_df(brutos)
    apos, cortadas = chunking.filtrar_por_df(brutos, df, DF_MAX_REFERENCIA * len(trechos))
    prosa, codigo = Counter(), Counter()
    for cid, lab, alvo, _pad, n, veio_de_codigo in apos:
        (codigo if veio_de_codigo else prosa)[(cid, lab, alvo)] += n
    arestas = set(prosa) | set(codigo)
    partes = [c for c in trechos if c.part_index is not None]

    resultado = {
        "metadados": {
            "documentos": len(docs), "conceitos": len(conceitos),
            "objetos_tsql": len(objetos), "has_keyword": len(has_kw),
            "documents": len(documents), "links_to": len(links),
            "documentos_isolados": len(docs - ligados),
        },
        "fragmentacao": {
            "trechos": len(trechos),
            "blocos_de_codigo": sum(len(c.code_blocks) for c in trechos),
            "casamentos_brutos": len(brutos), "casamentos_em_codigo": em_codigo,
            "regras_com_casamento": len(df), "regras_cortadas": len(cortadas),
            "casamentos_apos_corte": len(apos),
            "mentions_concept": sum(1 for k in arestas if k[1] == "Concept"),
            "mentions_tsql": sum(1 for k in arestas if k[1] == "TSqlObject"),
            "mentions_so_por_codigo": sum(1 for k in arestas if prosa.get(k, 0) == 0),
            "conceitos_cobertos": len({k[2] for k in arestas if k[1] == "Concept"}),
            "objetos_cobertos": len({k[2] for k in arestas if k[1] == "TSqlObject"}),
            "partes_do_splitter": len(partes),
            "secoes_divididas": len({(c.doc_path, c.order) for c in partes}),
        },
    }
    export = [{
        "chunk_id": c.chunk_id, "doc_path": c.doc_path,
        "doc_title": titulos.get(c.doc_path, ""),
        "order": c.order, "part_index": c.part_index,
        "section_type": c.section_type, "section_title": c.section_title,
        "token_count": c.token_count,
        "text": c.text, "text_matchable": c.text_matchable,
        "code": [{"order": cb.order, "language": cb.language, "content": cb.content}
                 for cb in c.code_blocks],
    } for c in trechos]
    export.sort(key=lambda t: (t["doc_path"], t["order"],
                               -1 if t["part_index"] is None else t["part_index"]))
    mentions = [{"chunk_id": k[0], "label": k[1], "target": k[2],
                 "count": prosa.get(k, 0), "count_code": codigo.get(k, 0)}
                for k in sorted(arestas)]
    resultado["impressao_digital_trechos"] = impressao_digital(export)
    resultado["segundos"] = round(time.time() - t0, 1)
    # Nos e arestas do grafo de metadados, para exportar o grafo sem Neo4j
    # (consulta_grafo.py exportar-grafo --offline).
    grafo_metadados = {
        "documentos": sorted(docs), "conceitos": sorted(conceitos),
        "objetos": sorted(objetos), "has_keyword": sorted(has_kw),
        "documents": sorted(documents), "links_to": sorted(links),
    }
    return {"numeros": resultado, "trechos": export, "mentions": mentions,
            "grafo_metadados": grafo_metadados}


def conferir(numeros: dict) -> bool:
    ok = True
    for etapa, ref in REFERENCIA.items():
        for chave, esperado in ref.items():
            obtido = numeros[etapa][chave]
            marca = "ok " if obtido == esperado else "DIFERENTE"
            ok &= obtido == esperado
            print(f"  {marca:9} {etapa}.{chave:24} esperado={esperado:>9,}  obtido={obtido:>9,}")
    igual = numeros["impressao_digital_trechos"] == IMPRESSAO_DIGITAL_TRECHOS
    ok &= igual
    print(f"  {'ok ' if igual else 'DIFERENTE':9} impressao digital dos trechos igual a de referencia "
          f"({IMPRESSAO_DIGITAL_TRECHOS[:12]}...)")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--docs-root", type=Path, default=None,
                    help="pasta docs/ do clone do sql-docs (padrao: SQL_DOCS_ROOT do .env)")
    ap.add_argument("--conferir", action="store_true",
                    help="compara com os numeros de referencia")
    ap.add_argument("--exportar", type=Path, default=None, metavar="ARQ.jsonl",
                    help="grava os trechos; as arestas vao para ARQ_mentions.jsonl")
    ap.add_argument("--json", type=Path, default=None, help="grava os numeros em JSON")
    args = ap.parse_args()

    docs_root = args.docs_root.resolve() if args.docs_root else chunking.get_docs_root()
    print(f"[sem neo4j] docs_root = {docs_root}", file=sys.stderr)
    r = reproduzir(docs_root)
    n = r["numeros"]
    print(json.dumps(n, indent=1, ensure_ascii=False))
    if args.json:
        args.json.write_text(json.dumps(n, indent=1, ensure_ascii=False), encoding="utf-8")
    if args.exportar:
        args.exportar.parent.mkdir(parents=True, exist_ok=True)
        with args.exportar.open("w", encoding="utf-8") as fh:
            for t in r["trechos"]:
                fh.write(json.dumps(t, ensure_ascii=False) + "\n")
        mpath = args.exportar.with_name(args.exportar.stem + "_mentions.jsonl")
        with mpath.open("w", encoding="utf-8") as fh:
            for m in r["mentions"]:
                fh.write(json.dumps(m, ensure_ascii=False) + "\n")
        print(f"[sem neo4j] {len(r['trechos']):,} trechos -> {args.exportar}", file=sys.stderr)
        print(f"[sem neo4j] {len(r['mentions']):,} arestas -> {mpath}", file=sys.stderr)
    if args.conferir:
        print("\nConferencia com os numeros de referencia:")
        if conferir(n):
            print("\nIDENTICO a referencia em todos os numeros conferidos, "
                  "com os mesmos trechos.")
        else:
            print("\nDIFERENTE da referencia. Confira o commit do corpus (git log -1), "
                  "se ha arquivos locais alterados (git status) e a versao do "
                  "markdown-it-py (4.0.0).")
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
