#!/usr/bin/env python3
"""
Exporta os trechos do Neo4j para um arquivo JSONL.

Le os nos :Chunk, com o titulo do documento e os blocos de codigo, e grava
um trecho por linha. E a entrada dos baselines, da consulta ao grafo e da
geracao: todos usam exatamente os trechos que o grafo tem.

Ao final imprime a impressao digital dos trechos (SHA-256 sobre chunk_id e
texto), que deve ser igual a do reproduzir_sem_neo4j.py.

Uso:
    python src/exportar_trechos.py --saida outputs/trechos.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

try:
    from dotenv import find_dotenv, load_dotenv
    from neo4j import GraphDatabase
except ImportError as exc:
    print(f"ERRO: dependencia ausente ({exc}). Rode: pip install neo4j python-dotenv",
          file=sys.stderr)
    sys.exit(1)

sys.path.insert(0, str(Path(__file__).resolve().parent))
load_dotenv(find_dotenv(), override=False)

CONSULTA = """
MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk)
OPTIONAL MATCH (c)-[:HAS_CODE]->(cb:CodeBlock)
WITH d, c, cb
ORDER BY cb.order
WITH d, c, collect(cb {.order, .language, .content}) AS code
RETURN c.chunk_id AS chunk_id, d.path AS doc_path, d.title AS doc_title,
       c.order AS order, c.part_index AS part_index,
       c.section_type AS section_type, c.section_title AS section_title,
       c.token_count AS token_count, c.text AS text,
       c.text_matchable AS text_matchable, code
ORDER BY doc_path, order, coalesce(part_index, -1)
"""


def registro_para_trecho(r) -> dict:
    """Mesmo formato e mesma ordem de chaves do reproduzir_sem_neo4j.py."""
    return {
        "chunk_id": r["chunk_id"], "doc_path": r["doc_path"],
        "doc_title": r["doc_title"] or "",
        "order": r["order"], "part_index": r["part_index"],
        "section_type": r["section_type"], "section_title": r["section_title"],
        "token_count": r["token_count"],
        "text": r["text"], "text_matchable": r["text_matchable"],
        "code": [{"order": cb["order"], "language": cb["language"],
                  "content": cb["content"]}
                 for cb in sorted(r["code"] or [], key=lambda x: x["order"])],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--saida", type=Path, required=True, help="arquivo JSONL de saida")
    args = ap.parse_args()

    senha = os.environ.get("NEO4J_PASSWORD")
    if not senha:
        print("ERRO: NEO4J_PASSWORD nao definido no .env.", file=sys.stderr)
        return 1
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    usuario = os.environ.get("NEO4J_USER", "neo4j")
    banco = os.environ.get("NEO4J_DATABASE", "neo4j")

    from reproduzir_sem_neo4j import impressao_digital

    t0 = time.time()
    trechos = []
    with GraphDatabase.driver(uri, auth=(usuario, senha)) as driver:
        driver.verify_connectivity()
        with driver.session(database=banco) as sess:
            for r in sess.run(CONSULTA):
                trechos.append(registro_para_trecho(r))
    args.saida.parent.mkdir(parents=True, exist_ok=True)
    with args.saida.open("w", encoding="utf-8") as fh:
        for t in trechos:
            fh.write(json.dumps(t, ensure_ascii=False) + "\n")
    n_codigo = sum(len(t["code"]) for t in trechos)
    print(f"[export] {len(trechos):,} trechos e {n_codigo:,} blocos de codigo "
          f"em {time.time() - t0:.1f}s -> {args.saida}")
    print(f"[export] impressao digital dos trechos: {impressao_digital(trechos)}")
    if len(trechos) != 32701:
        print("[aviso] a configuracao de referencia tem 32.701 trechos. O grafo mudou?",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
