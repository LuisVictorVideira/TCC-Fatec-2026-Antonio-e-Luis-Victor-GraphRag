#!/usr/bin/env python3
"""
Grafo de metadados no Neo4j, sem LLM.

Le o cabecalho de cada arquivo Markdown do corpus (helpviewer_keywords e
f1_keywords) e os links entre os arquivos, e grava no Neo4j:

    :Document     um por arquivo .md
    :Concept      um por helpviewer_keyword distinta (em minusculas)
    :TSqlObject   um por f1_keyword distinta (classificada por padrao)

    (:Document)-[:HAS_KEYWORD]->(:Concept)
    (:Document)-[:DOCUMENTS]->(:TSqlObject)
    (:Document)-[:LINKS_TO]->(:Document)

Usa MERGE em tudo: rodar de novo nao duplica dados. A pasta
relational-databases/errors-events/includes/ fica de fora.

Uso:
    python src/construir_grafo.py               # carga completa
    python src/construir_grafo.py --limit 200   # amostra
    python src/construir_grafo.py --reset       # apaga o grafo antes (pede confirmacao)
    python src/construir_grafo.py --ping        # so testa a conexao
    python src/construir_grafo.py --skip-parse  # so o relatorio do grafo atual

Configuracao no .env (ver .env.example): NEO4J_URI, NEO4J_USER,
NEO4J_PASSWORD, NEO4J_DATABASE e SQL_DOCS_ROOT.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

try:
    import yaml
except ImportError:
    print("ERRO: PyYAML nao instalado. Rode: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

try:
    from dotenv import find_dotenv, load_dotenv
except ImportError:
    print("ERRO: python-dotenv nao instalado. Rode: pip install python-dotenv", file=sys.stderr)
    sys.exit(1)

try:
    from neo4j import GraphDatabase
    from neo4j.exceptions import AuthError, ServiceUnavailable
except ImportError:
    print("ERRO: neo4j nao instalado. Rode: pip install neo4j", file=sys.stderr)
    sys.exit(1)


load_dotenv(find_dotenv(), override=False)

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD")
NEO4J_DATABASE = os.environ.get("NEO4J_DATABASE", "neo4j")
_SQL_DOCS_ROOT_RAW = os.environ.get("SQL_DOCS_ROOT")

if not NEO4J_PASSWORD or NEO4J_PASSWORD in ("change_me", ""):
    print("ERRO: NEO4J_PASSWORD nao definido no .env (ou ainda esta como 'change_me').",
          file=sys.stderr)
    sys.exit(1)


def get_docs_root() -> Path:
    """Valida e retorna o SQL_DOCS_ROOT, auto-ajustando se necessario."""
    if not _SQL_DOCS_ROOT_RAW:
        print("ERRO: SQL_DOCS_ROOT nao definido no .env.", file=sys.stderr)
        sys.exit(1)
    root = Path(_SQL_DOCS_ROOT_RAW).resolve()
    if not root.exists():
        print(f"ERRO: SQL_DOCS_ROOT nao existe: {root}", file=sys.stderr)
        sys.exit(1)
    if (root / "relational-databases").exists():
        return root
    # Aceita tambem a raiz do clone (.../sql-docs) no lugar de .../sql-docs/docs.
    if (root / "docs" / "relational-databases").exists():
        adjusted = root / "docs"
        print(f"[aviso] SQL_DOCS_ROOT={root} nao contem relational-databases direto,",
              file=sys.stderr)
        print(f"        ajustando automaticamente para {adjusted}",
              file=sys.stderr)
        print("        (voce pode atualizar seu .env para eliminar este aviso)",
              file=sys.stderr)
        return adjusted
    print(f"ERRO: {root} nao contem relational-databases nem docs/relational-databases.",
          file=sys.stderr)
    print("      Confira o valor de SQL_DOCS_ROOT no .env.", file=sys.stderr)
    sys.exit(1)


RE_FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
RE_MD_LINK = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
RE_MONIKER = re.compile(r":::\s*moniker\s+range=[\"']?([^\"'\s:]+)", re.IGNORECASE)


def classify_f1_keyword(kw: str) -> str:
    r"""Retorna o object_type inferido para uma f1_keyword.

    A ordem das regras importa: a mais especifica vem primeiro.

        sp_*, xp_*        -> procedimento de sistema (antes do _TSQL, porque
                             'sp_help_TSQL' e procedimento, nao statement)
        <STMT>_TSQL       -> statement/keyword T-SQL
        sys.*             -> objeto do catalogo do sistema
        @@*               -> variavel global
        @*                -> variavel local
        $*                -> pseudocoluna / variavel de sistema ($partition)
        sql\d+.swb.*      -> chave de UI do SSMS
        so digitos        -> codigo de erro
        comeca c/ simbolo -> operador (!=, %=, <>)
        so maiusculas     -> keyword/statement sem sufixo (ADD FILE, ABS)
        tem ponto         -> identificador qualificado
    """
    k = kw.strip()
    if not k:
        return "unknown"
    if re.match(r"^(sp|xp)_", k, re.IGNORECASE):
        return "system_procedure"
    if k.endswith("_TSQL"):
        return "tsql_statement"
    if k.startswith("sys."):
        return "system_object"
    if k.startswith("@@"):
        return "tsql_global_var"
    if k.startswith("@"):
        return "tsql_variable"
    if k.startswith("$"):
        return "tsql_system_var"
    if re.match(r"^sql\d+\.swb\.", k):
        return "ssms_ui"
    if k.isdigit():
        return "error_code"
    if k[0] in "!<>=*+-/%&|^~":
        return "tsql_operator"
    letras = [c for c in k if c.isalpha()]
    if letras and all(c.isupper() for c in letras):
        return "tsql_statement"
    if "." in k and " " not in k:
        return "dotted_identifier"
    return "unknown"


@dataclass
class DocRecord:
    rel_path: str  # caminho relativo a raiz do corpus; identifica o documento
    abs_path: str
    title: str = ""
    description: str = ""
    ms_service: str = ""
    ms_subservice: str = ""
    ms_topic: str = ""
    ms_date: str = ""
    ms_author: str = ""
    url_slug: str = ""
    monikers: list[str] = field(default_factory=list)
    helpviewer_keywords: list[str] = field(default_factory=list)
    f1_keywords: list[str] = field(default_factory=list)
    outbound_links: list[str] = field(default_factory=list)


def parse_frontmatter(content: str) -> tuple[dict, str]:
    m = RE_FRONTMATTER.match(content)
    if not m:
        return {}, content
    try:
        data = yaml.safe_load(m.group(1))
        if not isinstance(data, dict):
            data = {}
    except yaml.YAMLError:
        data = {}
    return data, content[m.end():]


def normalize_yaml_list(value) -> list[str]:
    """Aceita list, str, ou None (comum em campos MS Docs)."""
    if isinstance(value, list):
        out = []
        for x in value:
            if x is None:
                continue
            s = str(x).strip()
            if s:
                out.append(s)
        return out
    if isinstance(value, str):
        s = value.strip()
        return [s] if s else []
    return []


def resolve_link(source_file_abs: Path, target: str, docs_root: Path) -> Optional[str]:
    """Resolve alvo de link Markdown para um rel_path canonico a partir de
    docs_root. Retorna None para links externos, ancoras, imagens, etc.
    """
    if not target:
        return None
    if target.startswith(("http://", "https://", "mailto:", "ftp://", "#", "//")):
        return None
    target_clean = target.split("#", 1)[0].split("?", 1)[0].strip()
    if not target_clean or not target_clean.endswith(".md"):
        return None
    try:
        resolved = (source_file_abs.parent / target_clean).resolve()
    except (OSError, ValueError):
        return None
    try:
        rel = resolved.relative_to(docs_root)
    except ValueError:
        return None
    return rel.as_posix()


def parse_document(abs_path: Path, docs_root: Path) -> Optional[DocRecord]:
    try:
        content = abs_path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as exc:
        print(f"[aviso] pulei {abs_path}: {exc}", file=sys.stderr)
        return None

    try:
        rel = abs_path.relative_to(docs_root).as_posix()
    except ValueError:
        rel = str(abs_path)

    fm, body = parse_frontmatter(content)
    url_slug = rel[:-3] if rel.endswith(".md") else rel

    rec = DocRecord(
        rel_path=rel,
        abs_path=str(abs_path),
        title=str(fm.get("title", "")).strip(),
        description=str(fm.get("description", "")).strip(),
        ms_service=str(fm.get("ms.service", "")).strip(),
        ms_subservice=str(fm.get("ms.subservice", "")).strip(),
        ms_topic=str(fm.get("ms.topic", "")).strip(),
        ms_date=str(fm.get("ms.date", "")).strip(),
        ms_author=str(fm.get("ms.author", "")).strip(),
        url_slug=url_slug,
        helpviewer_keywords=normalize_yaml_list(fm.get("helpviewer_keywords")),
        f1_keywords=normalize_yaml_list(fm.get("f1_keywords")),
    )

    mr = fm.get("monikerRange")
    if isinstance(mr, str) and mr.strip():
        rec.monikers.append(mr.strip())

    for m in RE_MONIKER.finditer(body):
        val = m.group(1).strip('"').strip("'")
        if val and val not in rec.monikers:
            rec.monikers.append(val)

    seen = set()
    for m in RE_MD_LINK.finditer(body):
        target = m.group(2)
        resolved = resolve_link(abs_path, target, docs_root)
        if resolved and resolved != rel and resolved not in seen:
            seen.add(resolved)
            rec.outbound_links.append(resolved)

    return rec


CONSTRAINTS = [
    "CREATE CONSTRAINT doc_path IF NOT EXISTS FOR (d:Document) REQUIRE d.path IS UNIQUE",
    "CREATE CONSTRAINT concept_name IF NOT EXISTS FOR (c:Concept) REQUIRE c.name IS UNIQUE",
    "CREATE CONSTRAINT tsql_name IF NOT EXISTS FOR (t:TSqlObject) REQUIRE t.name IS UNIQUE",
]

INDEXES = [
    "CREATE INDEX doc_title IF NOT EXISTS FOR (d:Document) ON (d.title)",
    "CREATE INDEX doc_service IF NOT EXISTS FOR (d:Document) ON (d.ms_service)",
    "CREATE INDEX tsql_type IF NOT EXISTS FOR (t:TSqlObject) ON (t.object_type)",
]


def ensure_schema(driver, database: str) -> None:
    print("[schema] criando constraints e indexes (idempotente)...", file=sys.stderr)
    with driver.session(database=database) as sess:
        for cql in CONSTRAINTS + INDEXES:
            sess.run(cql).consume()
    print("[schema] ok", file=sys.stderr)


def reset_graph(driver, database: str) -> None:
    """Apaga em lotes todos os nos criados por este script (e suas arestas)."""
    with driver.session(database=database) as sess:
        total = sess.run(
            "MATCH (n) WHERE n:Document OR n:Concept OR n:TSqlObject "
            "RETURN count(n) AS c"
        ).single()["c"]
        print(f"[reset] {total:,} nos a apagar", file=sys.stderr)
        while True:
            result = sess.run(
                "MATCH (n) WHERE n:Document OR n:Concept OR n:TSqlObject "
                "WITH n LIMIT 5000 "
                "DETACH DELETE n"
            )
            deleted = result.consume().counters.nodes_deleted
            if deleted == 0:
                break
            print(f"[reset] apagados {deleted} em lote", file=sys.stderr)
    print("[reset] concluido", file=sys.stderr)


def _write(sess, query: str, params: dict) -> None:
    """Executa um write com retry automatico do driver."""
    sess.execute_write(lambda tx: tx.run(query, **params).consume())


def batch_documents(driver, database: str, records: list[DocRecord], batch_size: int) -> None:
    query = """
    UNWIND $batch AS row
    MERGE (d:Document {path: row.path})
    SET d.title         = row.title,
        d.description   = row.description,
        d.ms_service    = row.ms_service,
        d.ms_subservice = row.ms_subservice,
        d.ms_topic      = row.ms_topic,
        d.ms_date       = row.ms_date,
        d.ms_author     = row.ms_author,
        d.url_slug      = row.url_slug,
        d.monikers      = row.monikers,
        d.abs_path      = row.abs_path
    """
    print(f"[docs] gravando {len(records):,} Document ...", file=sys.stderr)
    with driver.session(database=database) as sess:
        for i in range(0, len(records), batch_size):
            batch = [
                {
                    "path": r.rel_path,
                    "title": r.title,
                    "description": r.description,
                    "ms_service": r.ms_service,
                    "ms_subservice": r.ms_subservice,
                    "ms_topic": r.ms_topic,
                    "ms_date": r.ms_date,
                    "ms_author": r.ms_author,
                    "url_slug": r.url_slug,
                    "monikers": r.monikers,
                    "abs_path": r.abs_path,
                }
                for r in records[i:i + batch_size]
            ]
            _write(sess, query, {"batch": batch})
            print(f"  {min(i + batch_size, len(records)):,}/{len(records):,}",
                  file=sys.stderr)


def batch_concepts(driver, database: str, records: list[DocRecord], batch_size: int) -> None:
    query = """
    UNWIND $batch AS row
    MERGE (c:Concept {name: row.name})
      ON CREATE SET c.display_name = row.display_name,
                    c.source       = 'helpviewer'
    WITH c, row
    MATCH (d:Document {path: row.doc_path})
    MERGE (d)-[:HAS_KEYWORD]->(c)
    """
    pairs = []
    for r in records:
        for kw in r.helpviewer_keywords:
            name = kw.lower().strip()
            if not name:
                continue
            pairs.append({
                "name": name,
                "display_name": kw,
                "doc_path": r.rel_path,
            })
    print(f"[concepts] gravando {len(pairs):,} pares (Concept, HAS_KEYWORD) ...",
          file=sys.stderr)
    with driver.session(database=database) as sess:
        for i in range(0, len(pairs), batch_size):
            batch = pairs[i:i + batch_size]
            _write(sess, query, {"batch": batch})
            print(f"  {min(i + batch_size, len(pairs)):,}/{len(pairs):,}",
                  file=sys.stderr)


def batch_tsql_objects(driver, database: str, records: list[DocRecord], batch_size: int) -> None:
    query = """
    UNWIND $batch AS row
    MERGE (t:TSqlObject {name: row.name})
      ON CREATE SET t.object_type = row.object_type,
                    t.source      = 'f1_keyword'
    WITH t, row
    MATCH (d:Document {path: row.doc_path})
    MERGE (d)-[:DOCUMENTS]->(t)
    """
    pairs = []
    for r in records:
        for kw in r.f1_keywords:
            name = kw.strip()
            if not name:
                continue
            pairs.append({
                "name": name,
                "object_type": classify_f1_keyword(name),
                "doc_path": r.rel_path,
            })
    print(f"[tsql] gravando {len(pairs):,} pares (TSqlObject, DOCUMENTS) ...",
          file=sys.stderr)
    with driver.session(database=database) as sess:
        for i in range(0, len(pairs), batch_size):
            batch = pairs[i:i + batch_size]
            _write(sess, query, {"batch": batch})
            print(f"  {min(i + batch_size, len(pairs)):,}/{len(pairs):,}",
                  file=sys.stderr)


def batch_links(driver, database: str, records: list[DocRecord], batch_size: int) -> tuple[int, int]:
    """Cria arestas LINKS_TO. Filtra alvos que nao existem como Document.

    Retorna (arestas_inseridas, links_quebrados_ignorados).
    """
    query = """
    UNWIND $batch AS row
    MATCH (src:Document {path: row.src})
    MATCH (dst:Document {path: row.dst})
    MERGE (src)-[:LINKS_TO]->(dst)
    """
    known = {r.rel_path for r in records}
    pairs = []
    broken = 0
    for r in records:
        for target in r.outbound_links:
            if target in known:
                pairs.append({"src": r.rel_path, "dst": target})
            else:
                broken += 1
    print(f"[links] gravando {len(pairs):,} arestas LINKS_TO "
          f"({broken:,} links quebrados descartados) ...", file=sys.stderr)
    with driver.session(database=database) as sess:
        for i in range(0, len(pairs), batch_size):
            batch = pairs[i:i + batch_size]
            _write(sess, query, {"batch": batch})
            print(f"  {min(i + batch_size, len(pairs)):,}/{len(pairs):,}",
                  file=sys.stderr)
    return len(pairs), broken


def report_graph(driver, database: str) -> None:
    sep = "=" * 72
    print("\n" + sep)
    print("RELATORIO DO GRAFO (Neo4j)")
    print(sep)

    with driver.session(database=database) as sess:
        print("\n[1] Nos por label")
        result = sess.run("""
            MATCH (n)
            UNWIND labels(n) AS label
            RETURN label, count(*) AS c
            ORDER BY c DESC
        """)
        for r in result:
            print(f"    {r['label']:20} {r['c']:>10,}")

        print("\n[2] Arestas por tipo")
        result = sess.run("""
            MATCH ()-[r]->()
            RETURN type(r) AS t, count(*) AS c
            ORDER BY c DESC
        """)
        for r in result:
            print(f"    {r['t']:20} {r['c']:>10,}")

        print("\n[3] Top 10 Documents por out-degree (LINKS_TO ->)")
        result = sess.run("""
            MATCH (d:Document)-[:LINKS_TO]->()
            RETURN d.path AS path, d.title AS title, count(*) AS deg
            ORDER BY deg DESC LIMIT 10
        """)
        for r in result:
            title = (r["title"] or "(sem titulo)")[:55]
            print(f"    {r['deg']:>5}  {title}")
            print(f"           {r['path']}")

        print("\n[4] Top 10 Documents por in-degree (<- LINKS_TO)")
        result = sess.run("""
            MATCH ()-[:LINKS_TO]->(d:Document)
            RETURN d.path AS path, d.title AS title, count(*) AS deg
            ORDER BY deg DESC LIMIT 10
        """)
        for r in result:
            title = (r["title"] or "(sem titulo)")[:55]
            print(f"    {r['deg']:>5}  {title}")
            print(f"           {r['path']}")

        print("\n[5] Top 20 Concepts por num de Documents que os declaram")
        result = sess.run("""
            MATCH (c:Concept)<-[:HAS_KEYWORD]-(d:Document)
            RETURN c.name AS name, count(d) AS docs
            ORDER BY docs DESC LIMIT 20
        """)
        for r in result:
            print(f"    {r['docs']:>5}  {r['name']}")

        print("\n[6] TSqlObject por object_type")
        result = sess.run("""
            MATCH (t:TSqlObject)
            RETURN t.object_type AS type, count(*) AS c
            ORDER BY c DESC
        """)
        for r in result:
            print(f"    {r['type']:22} {r['c']:>8,}")

        print("\n[7] Documents isolados no grafo de links")
        row = sess.run("""
            MATCH (d:Document)
            WHERE NOT (d)-[:LINKS_TO]-()
            RETURN count(d) AS iso
        """).single()
        print(f"    {row['iso']:,} documentos sem nenhum link (in ou out)")

        print("\n[8] Densidade do subgrafo de links")
        row = sess.run("""
            MATCH (d:Document)
            OPTIONAL MATCH (d)-[r:LINKS_TO]->()
            WITH count(DISTINCT d) AS n, count(r) AS m
            RETURN n, m, toFloat(m)/n AS avg_out
        """).single()
        print(f"    N (Document)     = {row['n']:,}")
        print(f"    M (LINKS_TO)     = {row['m']:,}")
        print(f"    grau medio out   = {row['avg_out']:.2f}")

    print("\n" + sep)
    print("Consultas uteis no browser Neo4j (http://localhost:7474)")
    print(sep)
    print("""
    // 1) panorama
    MATCH (n) RETURN labels(n)[0] AS label, count(*) ORDER BY count(*) DESC;

    // 2) docs que declaram algum conceito de 'index'
    MATCH (c:Concept) WHERE c.name CONTAINS 'index'
    OPTIONAL MATCH (d:Document)-[:HAS_KEYWORD]->(c)
    RETURN c.name, count(d) AS docs ORDER BY docs DESC LIMIT 20;

    // 3) statements T-SQL
    MATCH (t:TSqlObject {object_type: 'tsql_statement'})
    RETURN t.name ORDER BY t.name LIMIT 50;

    // 4) vizinhanca de 'CREATE INDEX' ate 2 hops
    MATCH (t:TSqlObject) WHERE t.name CONTAINS 'INDEX'
    OPTIONAL MATCH p = (t)<-[:DOCUMENTS]-(d:Document)-[:LINKS_TO*..2]-(other)
    RETURN p LIMIT 100;
    """)


# Fora do corpus: as tabelas de mensagens de erro de errors-events/includes/,
# referencia tabular repetida para cinco versoes do SQL Server, sem prosa.
# As paginas individuais de erro ficam.
# Manter igual no chunking_e_mentions.py.
EXCLUDED_PATH_PREFIXES = (
    "relational-databases/errors-events/includes/",
)


def is_excluded(rel_path: str) -> bool:
    return rel_path.startswith(EXCLUDED_PATH_PREFIXES)


def iter_markdown_files(root: Path) -> Iterable[Path]:
    for p in root.rglob("*.md"):
        if not is_excluded(p.relative_to(root).as_posix()):
            yield p


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--limit", type=int, default=0,
                        help="Processa so os primeiros N arquivos (0 = todos)")
    parser.add_argument("--reset", action="store_true",
                        help="Apaga Document/Concept/TSqlObject antes de inserir")
    parser.add_argument("--ping", action="store_true",
                        help="So testa conexao com Neo4j e sai")
    parser.add_argument("--skip-parse", action="store_true",
                        help="Nao re-parseia arquivos; so imprime o relatorio")
    parser.add_argument("--batch-size", type=int, default=1000,
                        help="Tamanho do batch UNWIND (default 1000)")
    args = parser.parse_args()

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    try:
        try:
            driver.verify_connectivity()
        except ServiceUnavailable as exc:
            print(f"ERRO: nao conectei em {NEO4J_URI}: {exc}", file=sys.stderr)
            print("      Confira `docker compose ps`.", file=sys.stderr)
            return 1
        except AuthError as exc:
            print(f"ERRO: falha de autenticacao no Neo4j: {exc}", file=sys.stderr)
            print("      Confira NEO4J_USER/NEO4J_PASSWORD no .env.", file=sys.stderr)
            return 1
        print(f"[neo4j] conectado em {NEO4J_URI} (db={NEO4J_DATABASE})",
              file=sys.stderr)

        if args.ping:
            print("[ping] ok, saindo.", file=sys.stderr)
            return 0

        if args.skip_parse:
            report_graph(driver, NEO4J_DATABASE)
            return 0

        ensure_schema(driver, NEO4J_DATABASE)

        if args.reset:
            print("\n>>> --reset vai apagar TODOS os Document/Concept/TSqlObject do grafo.",
                  file=sys.stderr)
            try:
                confirm = input(">>> Digite 'sim' para continuar: ").strip().lower()
            except EOFError:
                confirm = ""
            if confirm != "sim":
                print("Cancelado.", file=sys.stderr)
                return 0
            reset_graph(driver, NEO4J_DATABASE)

        docs_root = get_docs_root()
        files = list(iter_markdown_files(docs_root))
        if args.limit:
            files = files[:args.limit]
        print(f"\n[parse] processando {len(files):,} arquivos .md em {docs_root} ...",
              file=sys.stderr)

        t0 = time.time()
        records: list[DocRecord] = []
        for i, f in enumerate(files, 1):
            rec = parse_document(f, docs_root)
            if rec:
                records.append(rec)
            if i % 500 == 0:
                print(f"  {i:,}/{len(files):,}", file=sys.stderr)
        t_parse = time.time() - t0
        print(f"[parse] {len(records):,} docs parseados em {t_parse:.1f}s",
              file=sys.stderr)

        print("", file=sys.stderr)
        t0 = time.time()
        batch_documents(driver, NEO4J_DATABASE, records, args.batch_size)
        batch_concepts(driver, NEO4J_DATABASE, records, args.batch_size)
        batch_tsql_objects(driver, NEO4J_DATABASE, records, args.batch_size)
        edges_ok, edges_broken = batch_links(
            driver, NEO4J_DATABASE, records, args.batch_size * 2
        )
        t_write = time.time() - t0
        print(f"\n[write] concluido em {t_write:.1f}s", file=sys.stderr)
        print(f"        LINKS_TO: {edges_ok:,} inseridos, "
              f"{edges_broken:,} descartados (alvos fora do corpus)",
              file=sys.stderr)

        report_graph(driver, NEO4J_DATABASE)
        return 0

    finally:
        driver.close()


if __name__ == "__main__":
    sys.exit(main())
