#!/usr/bin/env python3
"""
Caracterizacao do corpus Markdown.

Conta arquivos, tamanho, palavras fora dos blocos de codigo, titulos, blocos
de codigo, tabelas, links, alertas e campos do cabecalho (frontmatter), e
imprime um relatorio. Esses numeros orientaram a fragmentacao e a construcao
do grafo.

Uso:
    python src/explorar_corpus.py corpus/relational-databases corpus/t-sql
    python src/explorar_corpus.py corpus --limit 200
    python src/explorar_corpus.py corpus --output exploracao.json

O PyYAML e opcional: sem ele, o cabecalho dos arquivos nao e lido.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


RE_FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
RE_HEADER = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
RE_CODE_FENCE = re.compile(r"^```(\w*)\n(.*?)^```", re.MULTILINE | re.DOTALL)
RE_TABLE_ROW = re.compile(r"^\|.+\|\s*$", re.MULTILINE)
RE_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
RE_ALERT = re.compile(r"^>\s*\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]", re.MULTILINE)
RE_MONIKER = re.compile(r":::\s*moniker\s+range=", re.IGNORECASE)
# Palavra: letras, inclusive acentuadas, e underscore, com 3 ou mais caracteres.
RE_WORD = re.compile(r"[A-Za-z\u00c0-\u00ff][A-Za-z\u00c0-\u00ff_]{2,}")

# Stopwords minimas em ingles, a lingua da documentacao.
STOPWORDS = {
    "the", "and", "for", "with", "you", "your", "this", "that", "from",
    "are", "was", "were", "have", "has", "had", "not", "but", "any",
    "can", "will", "may", "must", "when", "which", "these", "those",
    "there", "their", "they", "them", "than", "into", "such", "also",
    "some", "one", "two", "all", "more", "only", "other", "each", "use",
    "using", "used", "see", "example", "following", "specify", "specified",
    "returns", "return", "value", "values", "name", "names", "type", "types",
    "default", "true", "false", "null", "note", "notes",
}


@dataclass
class FileStats:
    path: str
    size_bytes: int = 0
    line_count: int = 0
    char_count: int = 0
    word_count: int = 0
    approx_tokens: int = 0  # estimativa: palavras x 1,3
    frontmatter_keys: list = field(default_factory=list)
    ms_keywords: list = field(default_factory=list)
    headers_by_level: Counter = field(default_factory=Counter)
    header_titles: list = field(default_factory=list)
    code_blocks: int = 0
    code_langs: Counter = field(default_factory=Counter)
    code_total_chars: int = 0
    table_rows: int = 0  # linhas de tabela, e nao numero de tabelas
    links_total: int = 0
    links_external: int = 0
    alerts: Counter = field(default_factory=Counter)
    has_moniker: bool = False
    body_words: Counter = field(default_factory=Counter)  # sem codigo e sem stopwords


def analyze_file(path: Path) -> FileStats | None:
    try:
        content = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as exc:
        print(f"[aviso] pulei {path}: {exc}", file=sys.stderr)
        return None

    stats = FileStats(path=str(path))
    stats.size_bytes = path.stat().st_size
    stats.char_count = len(content)
    stats.line_count = content.count("\n") + 1

    body = content
    fm_match = RE_FRONTMATTER.match(content)
    if fm_match:
        body = content[fm_match.end():]
        if HAS_YAML:
            try:
                parsed = yaml.safe_load(fm_match.group(1))
                if isinstance(parsed, dict):
                    stats.frontmatter_keys = sorted(parsed.keys())
                    kw = parsed.get("helpviewer_keywords") or parsed.get("keywords")
                    if isinstance(kw, list):
                        stats.ms_keywords = [str(k).strip() for k in kw if k]
                    elif isinstance(kw, str):
                        stats.ms_keywords = [kw.strip()]
            except yaml.YAMLError:
                pass

    for m in RE_CODE_FENCE.finditer(body):
        lang = (m.group(1) or "").lower() or "(sem_lang)"
        stats.code_blocks += 1
        stats.code_langs[lang] += 1
        stats.code_total_chars += len(m.group(2))

    body_no_code = RE_CODE_FENCE.sub(" ", body)

    for m in RE_HEADER.finditer(body_no_code):
        level = len(m.group(1))
        title = m.group(2).strip()
        stats.headers_by_level[level] += 1
        stats.header_titles.append((level, title))

    stats.table_rows = len(RE_TABLE_ROW.findall(body_no_code))

    for m in RE_LINK.finditer(body_no_code):
        stats.links_total += 1
        target = m.group(2)
        if target.startswith(("http://", "https://")):
            stats.links_external += 1

    for m in RE_ALERT.finditer(body_no_code):
        stats.alerts[m.group(1)] += 1

    stats.has_moniker = bool(RE_MONIKER.search(body_no_code))

    words = [w.lower() for w in RE_WORD.findall(body_no_code)]
    stats.word_count = len(words)
    stats.approx_tokens = int(len(words) * 1.3)
    stats.body_words.update(
        w for w in words if w not in STOPWORDS and len(w) > 3
    )

    return stats


def iter_markdown_files(paths: Iterable[Path]) -> Iterable[Path]:
    for base in paths:
        if not base.exists():
            print(f"[aviso] caminho inexistente: {base}", file=sys.stderr)
            continue
        if base.is_file() and base.suffix == ".md":
            yield base
        elif base.is_dir():
            yield from base.rglob("*.md")


def humanize_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def percentiles(values: list[int]) -> dict[str, int]:
    if not values:
        return {}
    values = sorted(values)
    n = len(values)

    def at(p: float) -> int:
        idx = min(n - 1, max(0, int(p * n)))
        return values[idx]

    return {
        "min": values[0],
        "p25": at(0.25),
        "p50": at(0.50),
        "p75": at(0.75),
        "p90": at(0.90),
        "p99": at(0.99),
        "max": values[-1],
    }


def print_report(all_stats: list[FileStats]) -> None:
    n = len(all_stats)
    if n == 0:
        print("Nenhum arquivo .md encontrado.")
        return

    sep = "=" * 72
    print(sep)
    print("RELATORIO DE EXPLORACAO DO CORPUS")
    print(sep)

    total_bytes = sum(s.size_bytes for s in all_stats)
    total_words = sum(s.word_count for s in all_stats)
    total_tokens = sum(s.approx_tokens for s in all_stats)
    print("\n[1] Panorama geral")
    print(f"    Arquivos .md analisados      : {n:,}")
    print(f"    Tamanho total em disco       : {humanize_bytes(total_bytes)}")
    print(f"    Palavras (texto, sem codigo) : {total_words:,}")
    print(f"    Tokens estimados             : {total_tokens:,}  (~= palavras * 1.3)")

    word_p = percentiles([s.word_count for s in all_stats])
    print("\n[2] Distribuicao de tamanho por arquivo (palavras)")
    for k, v in word_p.items():
        print(f"    {k:5}: {v:>7,}")
    print("    dica: se p90 < 800 palavras, chunk-por-arquivo funciona;")
    print("          se p90 > 2000, chunking por H2/H3 e obrigatorio.")

    header_totals: Counter = Counter()
    for s in all_stats:
        header_totals.update(s.headers_by_level)
    print("\n[3] Headers por nivel")
    for lvl in sorted(header_totals):
        print(f"    H{lvl}: {header_totals[lvl]:,}")

    h2 = Counter()
    h3 = Counter()
    for s in all_stats:
        for lvl, title in s.header_titles:
            key = title.lower()
            if lvl == 2:
                h2[key] += 1
            elif lvl == 3:
                h3[key] += 1
    print("\n[4] Top 15 titulos de H2 (candidatos a 'secao canonica')")
    for title, count in h2.most_common(15):
        print(f"    {count:>5}x  {title}")
    print("\n[5] Top 10 titulos de H3")
    for title, count in h3.most_common(10):
        print(f"    {count:>5}x  {title}")

    langs: Counter = Counter()
    total_code = 0
    total_code_chars = 0
    for s in all_stats:
        langs.update(s.code_langs)
        total_code += s.code_blocks
        total_code_chars += s.code_total_chars
    print("\n[6] Blocos de codigo")
    print(f"    Total de blocos              : {total_code:,}")
    if total_code:
        print(f"    Tamanho medio (chars)        : {total_code_chars / total_code:.0f}")
    print("    Linguagens (top 10):")
    for lang, count in langs.most_common(10):
        print(f"        {count:>6,}x  {lang}")

    total_table_rows = sum(s.table_rows for s in all_stats)
    print("\n[7] Tabelas")
    print(f"    Linhas de tabela (aprox.)    : {total_table_rows:,}")

    total_links = sum(s.links_total for s in all_stats)
    total_ext = sum(s.links_external for s in all_stats)
    print("\n[8] Links")
    print(f"    Total                        : {total_links:,}")
    print(f"    Externos (http/https)        : {total_ext:,}")
    print(f"    Internos (doc-a-doc)         : {total_links - total_ext:,}")
    print("    dica: links internos sao arestas explicitas ja anotadas")
    print("          pelo autor -- excelente sinal para o grafo (relacao MENTIONS).")

    alerts: Counter = Counter()
    for s in all_stats:
        alerts.update(s.alerts)
    if alerts:
        print("\n[9] Alertas MS Docs (> [!TIPO])")
        for t, count in alerts.most_common():
            print(f"    {count:>5}x  {t}")

    fm_key_counter: Counter = Counter()
    with_fm = 0
    kw_counter: Counter = Counter()
    for s in all_stats:
        if s.frontmatter_keys:
            with_fm += 1
            fm_key_counter.update(s.frontmatter_keys)
        kw_counter.update(k.lower() for k in s.ms_keywords)
    print("\n[10] Frontmatter YAML")
    print(f"    Arquivos com frontmatter     : {with_fm:,} / {n:,}")
    if HAS_YAML and fm_key_counter:
        print("    Chaves mais comuns (top 12):")
        for key, count in fm_key_counter.most_common(12):
            print(f"        {count:>6,}x  {key}")
    elif not HAS_YAML:
        print("    (PyYAML nao instalado -- rode `pip install pyyaml` para")
        print("     ver as chaves e helpviewer_keywords, que sao nos-candidatos)")

    if kw_counter:
        print("\n[11] Top 25 helpviewer_keywords (nos-candidatos ja curados pela MS)")
        for kw, count in kw_counter.most_common(25):
            print(f"    {count:>5}x  {kw}")

    with_moniker = sum(1 for s in all_stats if s.has_moniker)
    if with_moniker:
        print("\n[12] Versionamento MS Docs")
        print(f"    Arquivos com blocos :::moniker: {with_moniker:,}")
        print("    dica: cada moniker delimita conteudo por versao do SQL Server;")
        print("          decidir cedo se o grafo sera multi-versao ou versao-neutra.")

    vocab: Counter = Counter()
    for s in all_stats:
        vocab.update(s.body_words)
    print("\n[13] Top 40 termos do corpus (>=4 chars, sem stopwords)")
    for w, c in vocab.most_common(40):
        print(f"    {c:>7,}x  {w}")

    print("\n" + sep)
    print("SINTESE / PROXIMOS PASSOS")
    print(sep)
    print(" * Chunking: use os titulos H2 recorrentes acima como fronteiras")
    print("   'duras' de chunk (nunca cortar dentro de Syntax / Arguments /")
    print("   Remarks / Examples / See also). H3 vira fronteira 'macia'.")
    print(" * Ontologia: helpviewer_keywords + termos top-40 formam o")
    print("   vocabulario controlado inicial (nos-candidatos do grafo).")
    print(" * Prompt de extracao: os exemplos few-shot devem cobrir cada")
    print("   linguagem de codigo top-5 e cada tipo de alerta encontrado.")
    print(sep)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "paths", nargs="+", type=Path,
        help="Diretorios ou arquivos .md para analisar",
    )
    parser.add_argument(
        "--output", "-o", type=Path,
        help="Salva relatorio detalhado em JSON",
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Analisa apenas os primeiros N arquivos (0 = todos)",
    )
    args = parser.parse_args()

    files = list(iter_markdown_files(args.paths))
    if args.limit:
        files = files[:args.limit]

    print(f"Analisando {len(files):,} arquivos .md ...", file=sys.stderr)

    all_stats: list[FileStats] = []
    for i, f in enumerate(files, 1):
        result = analyze_file(f)
        if result:
            all_stats.append(result)
        if i % 500 == 0:
            print(f"  {i:,}/{len(files):,}", file=sys.stderr)

    print_report(all_stats)

    if args.output:
        # Serializacao manual: dataclasses.asdict() nao serve para os campos Counter.
        # Ele recria cada Counter a partir dos pares (chave, valor), e o Counter conta
        # cada par como um item: as chaves viram tuplas, que o json nao serializa.
        detailed = [
            {
                "path": s.path,
                "size_bytes": s.size_bytes,
                "line_count": s.line_count,
                "char_count": s.char_count,
                "word_count": s.word_count,
                "approx_tokens": s.approx_tokens,
                "frontmatter_keys": s.frontmatter_keys,
                "ms_keywords": s.ms_keywords,
                "headers_by_level": {str(k): v for k, v in s.headers_by_level.items()},
                "header_titles": [[lvl, title] for lvl, title in s.header_titles],
                "code_blocks": s.code_blocks,
                "code_langs": dict(s.code_langs),
                "code_total_chars": s.code_total_chars,
                "table_rows": s.table_rows,
                "links_total": s.links_total,
                "links_external": s.links_external,
                "alerts": dict(s.alerts),
                "has_moniker": s.has_moniker,
                "body_words": dict(s.body_words.most_common(50)),
            }
            for s in all_stats
        ]
        args.output.write_text(
            json.dumps(detailed, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"\nRelatorio JSON salvo em: {args.output}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
