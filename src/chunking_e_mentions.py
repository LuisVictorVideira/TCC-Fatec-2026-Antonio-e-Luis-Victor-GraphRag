#!/usr/bin/env python3
"""
Fragmentacao dos documentos e mencoes (MENTIONS), sem LLM.

Para cada documento do grafo de metadados:

    1. Divide o texto por secao H2 em nos :Chunk (o documento inteiro, se
       nao houver H2). Secao acima de 3.000 tokens e dividida por janela
       deslizante, com alvo de 2.000 tokens e sobreposicao de 100.
    2. Separa cada bloco de codigo cercado em um no :CodeBlock, pelo
       analisador CommonMark do markdown-it-py.
    3. Troca os marcadores [!INCLUDE [nome](...)] pelo texto que a
       documentacao exibe (por exemplo, 'SQL Server').
    4. Procura no texto os nomes dos :Concept e :TSqlObject com o algoritmo
       Aho-Corasick e cria as arestas MENTIONS. Com --df-max, as regras que
       aparecem em mais que essa fracao dos trechos sao descartadas.

    :Chunk       chave chunk_id = '{doc_path}#chunk{order}'
    :CodeBlock   chave id = '{chunk_id}#code{order}'

    (:Document)-[:HAS_CHUNK {order}]->(:Chunk)
    (:Chunk)-[:HAS_CODE {order}]->(:CodeBlock)
    (:Chunk)-[:MENTIONS {count}]->(:Concept)
    (:Chunk)-[:MENTIONS {count}]->(:TSqlObject)

Antes de reinserir os trechos de um documento, apaga os anteriores dele:
rodar de novo substitui sem deixar residuo.

Uso:
    python src/chunking_e_mentions.py --df-max 0.02   # configuracao do artigo
    python src/chunking_e_mentions.py --limit 200     # amostra
    python src/chunking_e_mentions.py --reset         # apaga Chunk e CodeBlock
    python src/chunking_e_mentions.py --skip-parse    # so o relatorio
    python src/chunking_e_mentions.py --ping          # so testa a conexao

Requer o construir_grafo.py rodado antes e o mesmo .env.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

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

try:
    import ahocorasick
except ImportError:
    print("ERRO: pyahocorasick nao instalado.", file=sys.stderr)
    print("      Rode: pip install pyahocorasick", file=sys.stderr)
    sys.exit(1)

try:
    from markdown_it import MarkdownIt
    from markdown_it import __version__ as MARKDOWN_IT_VERSAO
except ImportError:
    print("ERRO: markdown-it-py nao instalado.", file=sys.stderr)
    print("      Rode: pip install markdown-it-py==4.0.0", file=sys.stderr)
    sys.exit(1)


load_dotenv(find_dotenv(), override=False)

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD")
NEO4J_DATABASE = os.environ.get("NEO4J_DATABASE", "neo4j")
_SQL_DOCS_ROOT_RAW = os.environ.get("SQL_DOCS_ROOT")

if not NEO4J_PASSWORD or NEO4J_PASSWORD in ("change_me", ""):
    print("ERRO: NEO4J_PASSWORD nao definido no .env.", file=sys.stderr)
    sys.exit(1)


def get_docs_root() -> Path:
    if not _SQL_DOCS_ROOT_RAW:
        print("ERRO: SQL_DOCS_ROOT nao definido no .env.", file=sys.stderr)
        sys.exit(1)
    root = Path(_SQL_DOCS_ROOT_RAW).resolve()
    if not root.exists():
        print(f"ERRO: SQL_DOCS_ROOT nao existe: {root}", file=sys.stderr)
        sys.exit(1)
    if (root / "relational-databases").exists():
        return root
    if (root / "docs" / "relational-databases").exists():
        adjusted = root / "docs"
        print(f"[aviso] auto-ajustando SQL_DOCS_ROOT para {adjusted}", file=sys.stderr)
        return adjusted
    print(f"ERRO: {root} nao contem relational-databases.", file=sys.stderr)
    sys.exit(1)


# Titulos H2 mais frequentes no corpus, mapeados para um tipo de secao; os
# demais viram "other".
SECTION_TYPE_MAP = {
    "syntax": "syntax",
    "arguments": "arguments",
    "argument": "arguments",
    "parameters": "arguments",
    "parameter": "arguments",
    "remarks": "remarks",
    "examples": "examples",
    "example": "examples",
    "permissions": "permissions",
    "return code values": "return_code_values",
    "return codes": "return_code_values",
    "return types": "return_types",
    "return type": "return_types",
    "return value": "return_types",
    "return values": "return_types",
    "result set": "result_set",
    "result sets": "result_set",
    "see also": "see_also",
    "related content": "related_content",
    "related contents": "related_content",
    "next steps": "next_steps",
    "next step": "next_steps",
    "prerequisites": "prerequisites",
    "prerequisite": "prerequisites",
    "limitations": "limitations",
    "limitations and restrictions": "limitations",
    "restrictions": "limitations",
    "in this section": "toc",
    "known issues": "known_issues",
    "user action": "user_action",
    "explanation": "explanation",
    "details": "details",
    "cause": "cause",
    "resolution": "resolution",
    "requirements": "requirements",
    "overview": "overview",
    "introduction": "overview",
    "summary": "overview",
    "security": "security",
    "best practices": "best_practices",
    "troubleshooting": "troubleshooting",
    "options": "options",
    "properties": "properties",
    "methods": "methods",
    "events": "events",
    "usage": "usage",
    "purpose": "purpose",
    "history": "history",
}

# Texto que o site da documentacao exibe no lugar de cada marcador
# [!INCLUDE [nome](caminho)].
INCLUDE_REPLACEMENTS = {
    "ssnoversion": "SQL Server",
    "sscurrent": "SQL Server",
    "sswinver": "SQL Server (Windows)",
    "ssde": "SQL Server Database Engine",
    "sssql11": "SQL Server 2012",
    "sssql14": "SQL Server 2014",
    "sssql15": "SQL Server 2016",
    "sssql16": "SQL Server 2017",
    "sssql17": "SQL Server 2017",
    "sssql19": "SQL Server 2019",
    "sssql22": "SQL Server 2022",
    "ssql19": "SQL Server 2019",
    "ssql22": "SQL Server 2022",
    "sssqlv14": "SQL Server 2017",
    "sssqlv15": "SQL Server 2019",
    "sssqlv16": "SQL Server 2022",
    "azuresqldb-current": "Azure SQL Database",
    "azuresqldb": "Azure SQL Database",
    "azuresqldb-mi-current": "Azure SQL Managed Instance",
    "asdb": "Azure SQL Database",
    "asdbmi": "Azure SQL Managed Instance",
    "sssqlfabric": "Fabric SQL Database",
    "sssdw": "Azure Synapse Analytics",
    "sssdwfull": "Azure Synapse Analytics (dedicated SQL pool)",
    "sspdw": "Azure Synapse Analytics",
    "ssmanstudiofull": "SQL Server Management Studio",
    "ssmanstudio": "SSMS",
    "ssanoversion": "Analysis Services",
    "ssisnoversion": "Integration Services",
    "ssrsnoversion": "Reporting Services",
}


# Formas de superficie que coincidem com palavras comuns do ingles ou sao
# genericas demais para dar sinal. Ficam fora do casamento, como as
# stopwords na recuperacao de informacao.
COMMON_STOPWORDS = {
    "for", "and", "or", "set", "all", "any", "use", "with",
    "into", "from", "on", "to", "in", "at", "by", "if",
    "table", "user", "type", "types", "column", "columns",
    "database", "databases", "procedure", "procedures",
    "schema", "security", "value", "values",
    "true", "false", "test", "external", "online", "internal",
    "limit", "pages", "guide", "in-memory", "note",
    "transact-sql", "sql database", "sql server",
    "sql server management studio", "ssms", "odbc", "ole db",
    "time", "objects", "query", "queries",
    "transact-sql syntax conventions",
}


# Qualificadores genericos de indice remissivo: servem para ordenar o
# indice, mas nao como termo de busca em texto corrido.
ALIAS_STOPWORDS = {
    "creating", "modifying", "removing", "dropping", "deleting",
    "monitoring", "configuring", "viewing", "managing", "adding",
    "changing", "defining", "enabling", "disabling", "updating",
    "specifying", "setting", "getting", "listing", "displaying",
    "described", "defined", "listed", "about", "overview",
    "functions", "names", "pages", "properties", "methods",
    "options", "settings", "statements", "commands", "objects",
    "files", "columns", "rows", "values", "types", "errors",
    "results", "parameters", "arguments", "examples", "syntax",
    "permissions", "components", "features", "elements", "items",
}

# Palavra que, no fim de um alias, indica fragmento sintatico e nao termo
# ("names [sql server], columns with" -> "columns with").
TRAILING_STOPWORDS = {
    "with", "of", "in", "for", "and", "or", "to", "on",
    "by", "from", "as", "at", "the", "a", "an", "into", "over",
}

# Alias que aponta para mais Concepts que isso e generico demais e e descartado.
MAX_ALIAS_TARGETS = 8

# Do mais longo para o mais curto, para o sufixo mais longo ser removido primeiro.
TYPE_SUFFIXES = [
    "dynamic management view", "dynamic management function",
    "database mail view", "system stored procedure",
    "catalog view", "system table", "system view", "system function",
    "extended stored procedure", "stored procedure",
    "information schema view", "data type", "table hint", "query hint",
    "global variable", "built-in function",
    "statement", "procedure", "function", "property", "operator",
    "keyword", "command", "clause", "method", "option", "role",
    "class", "event", "trigger", "constraint", "predicate",
    "view", "table",
]

# Limiar de frequencia documental, em fracao dos trechos: um padrao presente
# em mais trechos que isso nao discrimina nada (corte superior de Luhn, 1958).
# Zero desativa; a configuracao do artigo usa --df-max 0.02.
DEFAULT_DF_MAX = 0.0

# --no-stoplist desliga as listas escritas a mao, para medir se o corte por
# frequencia documental as substitui.
_STOPLISTS_ATIVAS = True


# A documentacao escreve tipos de dados em minusculas (int, nvarchar) e
# keywords em maiusculas (SELECT, ALTER); o casamento exige a caixa de cada caso.
TSQL_DATA_TYPES = {
    "bigint", "int", "smallint", "tinyint", "bit",
    "decimal", "numeric", "money", "smallmoney",
    "float", "real",
    "date", "datetime", "datetime2", "datetimeoffset",
    "smalldatetime", "time", "timestamp", "rowversion",
    "char", "varchar", "text", "nchar", "nvarchar", "ntext",
    "binary", "varbinary", "image",
    "cursor", "sql_variant", "table", "xml", "json",
    "uniqueidentifier", "hierarchyid", "geometry", "geography",
}


# Secao maior que MAX_CHUNK_TOKENS e dividida (por H3, por paragrafos ou por
# janela deslizante) ate cada parte caber em TARGET_PART_TOKENS. A janela do
# llama3:8b e de 8.192 tokens: o limite deixa espaco para o prompt e folga
# para o erro da estimativa de tokens.
MAX_CHUNK_TOKENS = 3000
TARGET_PART_TOKENS = 2000
OVERLAP_TOKENS = 100  # so na janela deslizante


RE_FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
RE_H2 = re.compile(r"^(##\s+)(.+?)$", re.MULTILINE)
RE_H3 = re.compile(r"^(###\s+)(.+?)$", re.MULTILINE)
RE_H1 = re.compile(r"^#\s+(.+?)$", re.MULTILINE)
RE_INCLUDE = re.compile(r"\[!INCLUDE\s+\[([^\]]+)\]\(([^)]*)\)\]", re.IGNORECASE)
RE_HTML_ANCHOR = re.compile(r"<a\s+name\s*=\s*[\"']?([^\"'\s>]+)[\"']?\s*>\s*</a>",
                            re.IGNORECASE)
RE_MD_LINK_IN_TITLE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
RE_BRACKET_SCOPE = re.compile(r"\s*\[[^\]]*\]")


@dataclass
class CodeBlockRecord:
    order: int
    language: str
    content: str
    is_syntax: bool
    char_count: int


@dataclass
class ChunkRecord:
    chunk_id: str
    doc_path: str
    order: int
    section_title: str
    section_type: str
    text: str  # markdown bruto do trecho
    text_matchable: str  # sem os blocos de codigo e com os marcadores de include expandidos
    token_count: int
    char_count: int
    code_blocks: list[CodeBlockRecord] = field(default_factory=list)
    part_index: Optional[int] = None  # preenchido quando a secao foi dividida
    token_count_matchable: int = 0  # tokens so do text_matchable


def expand_placeholders(text: str) -> str:
    """Substitui [!INCLUDE [nome](path)] pelo texto MS renderizado."""
    def sub(m):
        placeholder = m.group(1).lower().strip()
        if placeholder.endswith("-md"):
            placeholder = placeholder[:-3]
        return INCLUDE_REPLACEMENTS.get(placeholder, m.group(0))
    return RE_INCLUDE.sub(sub, text)


def clean_h2_title(raw: str) -> str:
    """Remove ancoras HTML e wrap de link markdown do titulo."""
    t = RE_HTML_ANCHOR.sub("", raw).strip()
    t = RE_MD_LINK_IN_TITLE.sub(lambda m: m.group(1), t)
    return t.strip()


def normalize_section_type(title: str) -> str:
    key = clean_h2_title(title).lower().strip()
    return SECTION_TYPE_MAP.get(key, "other")


# Os blocos de codigo sao achados pelo analisador CommonMark, a especificacao
# que o Microsoft Learn segue: ele reconhece cercas recuadas em listas,
# cercas em citacoes e nomes de linguagem com hifen. So a fase de blocos roda,
# porque so interessa o token fence.
_MD_BLOCOS = MarkdownIt("commonmark").disable("inline")


def _cercas_commonmark(texto: str) -> list[tuple[int, int, str, str]]:
    """Blocos cercados pelo CommonMark: (linha inicial, linha final exclusiva,
    texto de informacao, conteudo). As linhas contam a partir de 0 e sao
    separadas so pela quebra de linha, como no markdown-it."""
    return [(tok.map[0], tok.map[1], tok.info, tok.content)
            for tok in _MD_BLOCOS.parse(texto)
            if tok.type == "fence" and tok.map]


def extract_code_blocks(section_body: str) -> tuple[list[CodeBlockRecord], str]:
    """Retorna (lista de code blocks, corpo com cada bloco trocado por um espaco).

    - Linguagem: a primeira palavra do texto de informacao, em minusculas;
      "unknown" quando nao ha.
    - O bloco sai do texto com as linhas inteiras, da cerca de abertura a de
      fechamento, inclusive o recuo da lista ou o '>' da citacao. Bloco sem
      cerca de fechamento vai ate o fim do texto, como manda o CommonMark.
    - Texto sem quebra de linha nao tem estrutura de bloco: e o caso das
      partes da janela deslizante, que juntam as palavras com espaco.
      Nenhuma cerca e reconhecida nele.
    """
    if "\n" not in section_body:
        return [], section_body
    linhas = section_body.split("\n")
    inicio_da_linha = [0]
    for ln in linhas[:-1]:
        inicio_da_linha.append(inicio_da_linha[-1] + len(ln) + 1)

    blocks: list[CodeBlockRecord] = []
    pedacos: list[str] = []
    pos = 0
    for i, (l0, l1, info, content) in enumerate(_cercas_commonmark(section_body)):
        palavras = info.split()
        lang = palavras[0].lower() if palavras else "unknown"
        blocks.append(CodeBlockRecord(
            order=i,
            language=lang,
            content=content,
            is_syntax=(lang == "syntaxsql"),
            char_count=len(content),
        ))
        ini = inicio_da_linha[l0]
        fim = inicio_da_linha[l1 - 1] + len(linhas[l1 - 1])
        pedacos.append(section_body[pos:ini])
        pedacos.append(" ")
        pos = fim
    pedacos.append(section_body[pos:])
    return blocks, "".join(pedacos)


def _approx_tokens(text: str) -> int:
    """Estima tokens como ~1.3 * palavras (a mesma heuristica do explorar_corpus.py)."""
    return int(len(text.split()) * 1.3)


def split_by_h3(section_text: str) -> list[tuple[str, str]]:
    """Quebra a secao por H3. Retorna [(h3_title, sub_text), ...] ou [] se
    nao existir H3 no texto."""
    h3_matches = list(RE_H3.finditer(section_text))
    if not h3_matches:
        return []
    parts: list[tuple[str, str]] = []
    prefix = section_text[:h3_matches[0].start()]
    if prefix.strip():
        parts.append(("intro", prefix))
    for i, m in enumerate(h3_matches):
        start = m.start()
        end = h3_matches[i + 1].start() if i + 1 < len(h3_matches) else len(section_text)
        parts.append((clean_h2_title(m.group(2).strip()), section_text[start:end]))
    return parts


def split_by_paragraph_groups(section_text: str,
                              target_tokens: int) -> list[tuple[str, str]]:
    """Agrupa paragrafos consecutivos ate target_tokens por grupo.
    Paragrafos = separados por linha em branco."""
    paragraphs = re.split(r"\n\n+", section_text)
    if not paragraphs:
        return [("", section_text)]
    groups: list[tuple[str, str]] = []
    current: list[str] = []
    current_tokens = 0
    for p in paragraphs:
        p_tokens = _approx_tokens(p)
        if current and current_tokens + p_tokens > target_tokens:
            groups.append(("", "\n\n".join(current)))
            current = [p]
            current_tokens = p_tokens
        else:
            current.append(p)
            current_tokens += p_tokens
    if current:
        groups.append(("", "\n\n".join(current)))
    return groups if groups else [("", section_text)]


def split_by_token_window(section_text: str, target_tokens: int,
                          overlap_tokens: int = OVERLAP_TOKENS
                          ) -> list[tuple[str, str]]:
    """Fallback: janela deslizante em palavras com pequeno overlap para
    nao cortar frases exatamente no boundary. Usado so quando nem H3 nem
    paragrafo produzem partes suficientemente pequenas.
    LIMITACAO CONHECIDA: nao respeita fronteira de code fence -- se um
    bloco de codigo cair no boundary, pode ser quebrado ao meio.
    """
    words = section_text.split()
    if not words:
        return [("", section_text)]
    target_words = max(1, int(target_tokens / 1.3))
    overlap_words = max(0, int(overlap_tokens / 1.3))
    step = max(1, target_words - overlap_words)
    parts: list[tuple[str, str]] = []
    i = 0
    while i < len(words):
        window = words[i:i + target_words]
        if not window:
            break
        parts.append(("", " ".join(window)))
        if i + target_words >= len(words):
            break
        i += step
    return parts or [("", section_text)]


def _join_titles(titles: list[str]) -> str:
    """Titulo representativo de um grupo de partes fundidas."""
    named = [t for t in titles if t and t != "intro"]
    if not named:
        return ""
    if len(named) == 1:
        return named[0]
    if len(named) == 2:
        return f"{named[0]} + {named[1]}"
    return f"{named[0]} … {named[-1]}"


def _merge_small_parts(parts: list[tuple[str, str]],
                       target_tokens: int) -> list[tuple[str, str]]:
    """Funde partes consecutivas pequenas ate atingir target_tokens.

    Sem isso, uma secao de 3.100 tokens com 20 H3 curtos viraria 21 chunks
    de ~150 tokens (super-fragmentacao). A fusao gulosa preserva a ordem
    do documento e mantem cada grupo dentro do alvo.
    """
    if len(parts) <= 1:
        return parts
    merged: list[tuple[str, str]] = []
    cur_titles: list[str] = []
    cur_texts: list[str] = []
    cur_tokens = 0
    for title, text in parts:
        t = _approx_tokens(text)
        if cur_texts and cur_tokens + t > target_tokens:
            merged.append((_join_titles(cur_titles), "\n\n".join(cur_texts)))
            cur_titles, cur_texts, cur_tokens = [], [], 0
        cur_titles.append(title)
        cur_texts.append(text)
        cur_tokens += t
    if cur_texts:
        merged.append((_join_titles(cur_titles), "\n\n".join(cur_texts)))
    return merged


def _ensure_within_limit(parts: list[tuple[str, str]], max_tokens: int,
                         target_tokens: int) -> list[tuple[str, str]]:
    """Pos-condicao do splitter: NENHUMA parte acima de max_tokens.

    Qualquer parte que ainda estoure (tipicamente uma tabela Markdown
    gigante, que nao tem linha em branco entre linhas e portanto conta
    como um unico paragrafo) cai na janela deslizante.
    """
    out: list[tuple[str, str]] = []
    for title, text in parts:
        if _approx_tokens(text) <= max_tokens:
            out.append((title, text))
            continue
        windows = split_by_token_window(text, target_tokens)
        for j, (_, w) in enumerate(windows):
            out.append((f"{title} (cont. {j + 1})" if title else "", w))
    return out


def hierarchical_split(section_text: str, max_tokens: int,
                       target_tokens: int) -> list[tuple[str, str]]:
    """Aplica H3 -> paragrafo -> janela deslizante.

    Pos-condicao: toda parte retornada cabe em max_tokens (garantida por
    _ensure_within_limit em todos os caminhos). Retorna sempre >=1 parte.
    """
    h3_parts = split_by_h3(section_text)
    if h3_parts:
        parts = _merge_small_parts(h3_parts, target_tokens)
        parts = _ensure_within_limit(parts, max_tokens, target_tokens)
        return parts if parts else [("", section_text)]

    p_parts = split_by_paragraph_groups(section_text, target_tokens)
    if len(p_parts) > 1:
        return _ensure_within_limit(p_parts, max_tokens, target_tokens)

    return split_by_token_window(section_text, target_tokens)


def build_chunks_for_section(rel_path: str, base_order: int,
                             section_type: str, section_title: str,
                             section_body: str) -> list[ChunkRecord]:
    """Cria 1 chunk (se couber) ou N chunks (se exceder MAX_CHUNK_TOKENS).
    Code blocks sao re-extraidos por parte, entao ficam naturalmente
    redistribuidos para a parte onde cairem no texto original.
    """
    if _approx_tokens(section_body) <= MAX_CHUNK_TOKENS:
        code_blocks, body_no_code = extract_code_blocks(section_body)
        expanded = expand_placeholders(body_no_code)
        return [ChunkRecord(
            chunk_id=f"{rel_path}#chunk{base_order}",
            doc_path=rel_path,
            order=base_order,
            section_title=section_title,
            section_type=section_type,
            text=section_body,
            text_matchable=expanded,
            # token_count mede o texto bruto, com os blocos de codigo, que e o que a
            # LLM le; token_count_matchable mede so o texto do casamento.
            token_count=_approx_tokens(section_body),
            token_count_matchable=_approx_tokens(expanded),
            char_count=len(section_body),
            code_blocks=code_blocks,
            part_index=None,
        )]

    parts = hierarchical_split(section_body, MAX_CHUNK_TOKENS, TARGET_PART_TOKENS)
    chunks: list[ChunkRecord] = []
    n_parts = len(parts)
    for part_idx, (part_title, part_text) in enumerate(parts):
        code_blocks, body_no_code = extract_code_blocks(part_text)
        expanded = expand_placeholders(body_no_code)
        if part_title:
            title = f"{section_title} — {part_title}"
        else:
            title = f"{section_title} (part {part_idx + 1} of {n_parts})"
        chunks.append(ChunkRecord(
            chunk_id=f"{rel_path}#chunk{base_order}p{part_idx}",
            doc_path=rel_path,
            order=base_order,
            section_title=title,
            section_type=section_type,
            text=part_text,
            text_matchable=expanded,
            token_count=_approx_tokens(part_text),
            token_count_matchable=_approx_tokens(expanded),
            char_count=len(part_text),
            code_blocks=code_blocks,
            part_index=part_idx,
        ))
    return chunks


def chunk_document(abs_path: Path, docs_root: Path) -> list[ChunkRecord]:
    """Le e chunkeia um documento por H2, aplicando splitter secundario
    quando alguma secao passa de MAX_CHUNK_TOKENS."""
    try:
        content = abs_path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as exc:
        print(f"[aviso] pulei {abs_path}: {exc}", file=sys.stderr)
        return []

    try:
        rel = abs_path.relative_to(docs_root).as_posix()
    except ValueError:
        rel = str(abs_path)

    fm_match = RE_FRONTMATTER.match(content)
    body = content[fm_match.end():] if fm_match else content

    h2_matches = list(RE_H2.finditer(body))
    chunks: list[ChunkRecord] = []

    if not h2_matches:
        h1 = RE_H1.search(body)
        title = h1.group(1).strip() if h1 else ""
        chunks.extend(build_chunks_for_section(
            rel_path=rel, base_order=0,
            section_type="whole_doc", section_title=title,
            section_body=body,
        ))
        return chunks

    prefix = body[:h2_matches[0].start()]
    prefix_stripped = re.sub(r"^#\s+.+?$", "", prefix, count=1, flags=re.MULTILINE).strip()
    order_offset = 0
    # O texto antes do primeiro H2 vira o trecho 0 se tiver mais de 100 caracteres.
    if len(prefix_stripped) > 100:
        h1 = RE_H1.search(prefix)
        title = h1.group(1).strip() if h1 else ""
        chunks.extend(build_chunks_for_section(
            rel_path=rel, base_order=0,
            section_type="intro", section_title=title,
            section_body=prefix,
        ))
        order_offset = 1

    for i, m in enumerate(h2_matches):
        start = m.start()
        end = h2_matches[i + 1].start() if i + 1 < len(h2_matches) else len(body)
        section_body = body[start:end]
        h2_title_raw = m.group(2).strip()
        h2_title = clean_h2_title(h2_title_raw)
        order = i + order_offset
        chunks.extend(build_chunks_for_section(
            rel_path=rel, base_order=order,
            section_type=normalize_section_type(h2_title_raw),
            section_title=h2_title,
            section_body=section_body,
        ))

    return chunks


def surface_form_of_tsql(name: str) -> Optional[str]:
    """Converte um TSqlObject.name em uma forma de superficie procuravel
    no texto, ou None se nao vale a pena procurar.

    O sufixo _TSQL e removido. Depois, o underscore so vira espaco quando o
    restante e inteiramente em CAIXA ALTA -- e o caso dos statements de
    varias palavras (CREATE_INDEX -> CREATE INDEX). Quando ha letra
    minuscula, o restante e um identificador cujo underscore faz parte do
    nome (sp_changedistpublisher_TSQL -> sp_changedistpublisher): trocar
    esse underscore produziria uma forma que nunca aparece no texto.
    """
    s = name.strip()
    if s.endswith("_TSQL"):
        s = s[:-5]
        letras = [c for c in s if c.isalpha()]
        if letras and all(c.isupper() for c in letras):
            s = s.replace("_", " ")
    if len(s) < 3:
        return None
    if s.isdigit():
        return None
    if not any(c.isalpha() for c in s):
        return None
    if any(c in "!<>=*+-/%&|^~" for c in s):
        return None
    if s.lower() in COMMON_STOPWORDS and _STOPLISTS_ATIVAS:
        return None
    return s


def concept_is_matchable(name: str) -> bool:
    if len(name) < 4:
        return False
    if name.isdigit():
        return False
    if not any(c.isalpha() for c in name):
        return False
    if name in COMMON_STOPWORDS and _STOPLISTS_ATIVAS:
        return False
    return True


def strip_type_suffix(term: str) -> Optional[str]:
    """Remove o sufixo de tipo de objeto, se houver.

        'sys.sql_dependencies catalog view' -> 'sys.sql_dependencies'
        'allow_row_locks option'            -> 'allow_row_locks'
        'master key decryption'             -> None (nao tem sufixo)
    """
    low = term.lower()
    for suf in TYPE_SUFFIXES:
        if low.endswith(" " + suf):
            base = term[: -(len(suf) + 1)].strip()
            if base:
                return base
    return None


def concept_aliases(name: str) -> list[str]:
    """Deriva formas de superficie procuraveis a partir de um Concept.

    Duas transformacoes, aplicadas em conjunto:

    1. Notacao de indice remissivo -- remove escopo entre colchetes e
       separa por virgula:
           'availability groups [sql server], monitoring'
               -> ['availability groups']
    2. Sufixo de tipo de objeto:
           'sys.sql_dependencies catalog view'
               -> ['sys.sql_dependencies']

    Cada candidato passa por tres filtros: fragmento sintatico (termina em
    preposicao), genericidade (ALIAS_STOPWORDS) e comprimento minimo para
    palavra unica. Nao inclui o proprio nome, que ja entra como padrao
    exato.
    """
    stripped = RE_BRACKET_SCOPE.sub("", name).strip()
    if not stripped:
        return []

    candidatos: list[str] = []
    for part in stripped.split(","):
        p = part.strip()
        if not p:
            continue
        candidatos.append(p)
        base = strip_type_suffix(p)
        if base:
            candidatos.append(base)

    out: list[str] = []
    vistos: set[str] = set()
    for p in candidatos:
        if p == name or p.lower() in vistos:
            continue
        words = p.split()
        if not words:
            continue
        if words[-1].lower() in TRAILING_STOPWORDS:
            continue
        low = p.lower()
        if _STOPLISTS_ATIVAS and (low in ALIAS_STOPWORDS
                                  or low in COMMON_STOPWORDS):
            continue
        if len(words) == 1 and len(p) < 8:
            continue
        vistos.add(low)
        out.append(p)
    return out


def tsql_case_form(surf: str) -> Optional[str]:
    """Forma exata exigida no texto, ou None se o casamento e insensivel
    a caixa.

    Explora a convencao tipografica da documentacao Microsoft:
      - keyword T-SQL em CAIXA ALTA    -> 'the NOT operator', 'ALTER TABLE'
      - palavra inglesa em minuscula   -> 'does not exist', 'alter the table'
      - tipo de dado em minuscula      -> 'the int column', 'an xml value'

    Padroes multi-palavra e identificadores com simbolos (sp_help,
    sys.tables, @@rowcount) sao especificos o bastante para dispensar a
    verificacao.
    """
    if " " in surf:
        return None
    if surf.lower() in TSQL_DATA_TYPES:
        return None
    if not surf.isalpha():
        return None
    return surf.upper()


def is_identifier_pattern(pat: str) -> bool:
    """Padrao que parece nome de objeto de banco, e nao prosa.

    Criterio: sem espacos e com pelo menos um separador de identificador
    (`_`, `.` ou `@`). Cobre sp_changedistpublisher, sys.sql_dependencies,
    allow_row_locks, @@rowcount.

    So estes sao procurados dentro de blocos de codigo. Buscar termos de
    prosa ('availability groups') dentro de um SELECT geraria ruido, e
    keywords ubiquas em codigo (SELECT, FROM, WHERE) nao passam no
    criterio, o que evita inundar o grafo.
    """
    if " " in pat:
        return False
    return any(ch in pat for ch in "_.@")


def build_matcher(concepts: list[str], tsql_names: list[str]) -> tuple:
    """Constroi automaton Aho-Corasick com todos os padroes.

    Valor armazenado por padrao:
        (label, alvos, tamanho_do_padrao, forma_com_caixa)

    - alvos: tupla de nomes canonicos. Normalmente 1. Um alias derivado de
      notacao de indice pode apontar para varios Concepts que compartilham
      o mesmo termo principal ('xml indexes [sql server], creating' e
      '... , dropping' geram ambos o alias 'xml indexes').
    - tamanho_do_padrao: necessario porque a forma de superficie difere do
      nome do no. Sem ele o calculo de start_pos fica deslocado e a
      verificacao de word-boundary olha a posicao errada.
    - forma_com_caixa: quando nao-nulo, exige correspondencia exata de
      caixa naquela posicao do texto.

    Retorna (automaton, mapa_de_padroes, counts).
    """
    concept_patterns: dict[str, set[str]] = {}
    for c in concepts:
        if concept_is_matchable(c):
            concept_patterns.setdefault(c, set()).add(c)

    n_exact = len(concept_patterns)
    for c in concepts:
        for alias in concept_aliases(c):
            a = alias.lower()
            if not concept_is_matchable(a):
                continue
            concept_patterns.setdefault(a, set()).add(c)

    n_ambiguous = 0
    for pat in list(concept_patterns):
        if len(concept_patterns[pat]) > MAX_ALIAS_TARGETS:
            del concept_patterns[pat]
            n_ambiguous += 1

    n_alias = len(concept_patterns) - n_exact + n_ambiguous

    # Dois TSqlObjects podem gerar a mesma forma de superficie ('sp_help' e
    # 'sp_help_TSQL'); os dois recebem a mencao.
    tsql_patterns: dict[str, list[tuple[str, Optional[str]]]] = {}
    for name in tsql_names:
        surf = surface_form_of_tsql(name)
        if not surf:
            continue
        tsql_patterns.setdefault(surf.lower(), []).append(
            (name, tsql_case_form(surf)))

    # O automato guarda um valor por chave, e inserir a mesma chave de novo
    # substitui o valor. Por isso cada chave carrega a lista de todos os seus
    # alvos, cada um com a sua regra de caixa.
    entradas: dict[str, list[tuple[str, str, Optional[str]]]] = {}
    for pat, targets in concept_patterns.items():
        for t in sorted(targets):
            entradas.setdefault(pat, []).append(("Concept", t, None))
    for pat, lista in tsql_patterns.items():
        for name, case_form in lista:
            entradas.setdefault(pat, []).append(("TSqlObject", name, case_form))

    n_compartilhados = sum(1 for p in tsql_patterns if p in concept_patterns)
    n_tsql_multi = sum(1 for lista in tsql_patterns.values() if len(lista) > 1)

    A = ahocorasick.Automaton()
    A_code = ahocorasick.Automaton()
    n_ident = 0
    for pat, lista in entradas.items():
        valor = (pat, tuple(lista))
        A.add_word(pat, valor)
        if is_identifier_pattern(pat):
            A_code.add_word(pat, valor)
            n_ident += 1
    A.make_automaton()
    if n_ident:
        A_code.make_automaton()

    counts = {
        "concept_patterns": len(concept_patterns),
        "concept_exact": n_exact,
        "concept_alias": n_alias,
        "concept_ambiguous_dropped": n_ambiguous,
        "tsql_patterns": len(tsql_patterns),
        "concept_filtered": len(concepts) - n_exact,
        "tsql_filtered": len(tsql_names) - sum(len(v) for v in tsql_patterns.values()),
        "case_sensitive": sum(1 for lista in tsql_patterns.values()
                              for _, cf in lista if cf),
        "identifier_patterns": n_ident,
        "shared_concept_tsql": n_compartilhados,
        "tsql_multi_target": n_tsql_multi,
    }
    return A, (A_code if n_ident else None), counts


def _is_word_char(c: str) -> bool:
    return c.isalnum() or c == "_"


def find_mentions(text: str, automaton) -> Counter:
    """Retorna Counter[(label, target, padrao)] com contagem por alvo.

    O padrao faz parte da chave para permitir medir frequencia documental
    por padrao -- assim um Concept alcancado por varios padroes perde
    apenas o alias generico, mantendo os demais.

    Verificacoes por casamento:
      1. word-boundary, usando o tamanho REAL do padrao casado (uma vez);
      2. caixa exata, avaliada POR ALVO -- a mesma chave pode servir a um
         Concept (insensivel a caixa) e a um TSqlObject (que exige caixa).
    """
    text_lower = text.lower()
    counts: Counter = Counter()
    n = len(text_lower)
    for end_pos, (pattern, entradas) in automaton.iter(text_lower):
        start_pos = end_pos - len(pattern) + 1
        left_ok = start_pos == 0 or not _is_word_char(text_lower[start_pos - 1])
        right_ok = end_pos + 1 == n or not _is_word_char(text_lower[end_pos + 1])
        if not (left_ok and right_ok):
            continue
        trecho = text[start_pos:end_pos + 1]
        for label, target, case_form in entradas:
            if case_form is not None and trecho != case_form:
                continue
            counts[(label, target, pattern)] += 1
    return counts


CONSTRAINTS = [
    "CREATE CONSTRAINT chunk_id IF NOT EXISTS FOR (c:Chunk) REQUIRE c.chunk_id IS UNIQUE",
    "CREATE CONSTRAINT codeblock_id IF NOT EXISTS FOR (cb:CodeBlock) REQUIRE cb.id IS UNIQUE",
]

INDEXES = [
    "CREATE INDEX chunk_section_type IF NOT EXISTS FOR (c:Chunk) ON (c.section_type)",
    "CREATE INDEX codeblock_language IF NOT EXISTS FOR (cb:CodeBlock) ON (cb.language)",
]


def ensure_schema(driver, database: str) -> None:
    print("[schema] criando constraints/indexes de Chunk e CodeBlock...", file=sys.stderr)
    with driver.session(database=database) as sess:
        for cql in CONSTRAINTS + INDEXES:
            sess.run(cql).consume()
    print("[schema] ok", file=sys.stderr)


def load_existing_names(driver, database: str) -> tuple[list[str], list[str]]:
    """Puxa Concept.name e TSqlObject.name do grafo (criados pelo construir_grafo.py)."""
    print("[load] lendo Concepts e TSqlObjects do grafo...", file=sys.stderr)
    with driver.session(database=database) as sess:
        concepts = [r["n"] for r in sess.run("MATCH (c:Concept) RETURN c.name AS n")]
        tsql = [r["n"] for r in sess.run("MATCH (t:TSqlObject) RETURN t.name AS n")]
    print(f"[load] {len(concepts):,} Concepts, {len(tsql):,} TSqlObjects",
          file=sys.stderr)
    return concepts, tsql


def reset_chunks_only(driver, database: str) -> None:
    with driver.session(database=database) as sess:
        total = sess.run(
            "MATCH (n) WHERE n:Chunk OR n:CodeBlock RETURN count(n) AS c"
        ).single()["c"]
        print(f"[reset] apagando {total:,} Chunks/CodeBlocks...", file=sys.stderr)
        while True:
            r = sess.run(
                "MATCH (n) WHERE n:Chunk OR n:CodeBlock "
                "WITH n LIMIT 5000 DETACH DELETE n"
            ).consume()
            if r.counters.nodes_deleted == 0:
                break
            print(f"[reset] apagados {r.counters.nodes_deleted} em lote",
                  file=sys.stderr)
    print("[reset] concluido", file=sys.stderr)


def clean_chunks_for_docs(driver, database: str, doc_paths: list[str],
                          batch_size: int = 200) -> None:
    """Apaga Chunks/CodeBlocks associados aos docs no batch (idempotencia)."""
    query = """
    UNWIND $paths AS p
    MATCH (d:Document {path: p})-[:HAS_CHUNK]->(c:Chunk)
    OPTIONAL MATCH (c)-[:HAS_CODE]->(cb:CodeBlock)
    DETACH DELETE c, cb
    """
    with driver.session(database=database) as sess:
        for i in range(0, len(doc_paths), batch_size):
            batch = doc_paths[i:i + batch_size]
            sess.execute_write(lambda tx: tx.run(query, paths=batch).consume())


def _write(sess, query: str, params: dict) -> None:
    sess.execute_write(lambda tx: tx.run(query, **params).consume())


def batch_chunks(driver, database: str, chunks: list[ChunkRecord], batch_size: int) -> None:
    query = """
    UNWIND $batch AS row
    MATCH (d:Document {path: row.doc_path})
    MERGE (c:Chunk {chunk_id: row.chunk_id})
    SET c.section_type  = row.section_type,
        c.section_title = row.section_title,
        c.doc_path      = row.doc_path,
        c.text          = row.text,
        c.text_matchable = row.text_matchable,
        c.order         = row.order,
        c.part_index    = row.part_index,
        c.token_count   = row.token_count,
        c.token_count_matchable = row.token_count_matchable,
        c.char_count    = row.char_count
    MERGE (d)-[hc:HAS_CHUNK]->(c)
    SET hc.order = row.order
    """
    print(f"[chunks] gravando {len(chunks):,} Chunk...", file=sys.stderr)
    with driver.session(database=database) as sess:
        for i in range(0, len(chunks), batch_size):
            batch = [
                {
                    "chunk_id": c.chunk_id,
                    "doc_path": c.doc_path,
                    "section_type": c.section_type,
                    "section_title": c.section_title,
                    "text": c.text,
                    "text_matchable": c.text_matchable,
                    "order": c.order,
                    "part_index": c.part_index,
                    "token_count": c.token_count,
                    "token_count_matchable": c.token_count_matchable,
                    "char_count": c.char_count,
                }
                for c in chunks[i:i + batch_size]
            ]
            _write(sess, query, {"batch": batch})
            print(f"  {min(i + batch_size, len(chunks)):,}/{len(chunks):,}",
                  file=sys.stderr)


def batch_code_blocks(driver, database: str, chunks: list[ChunkRecord],
                      batch_size: int) -> int:
    query = """
    UNWIND $batch AS row
    MATCH (c:Chunk {chunk_id: row.chunk_id})
    MERGE (cb:CodeBlock {id: row.id})
    SET cb.language   = row.language,
        cb.content    = row.content,
        cb.order      = row.order,
        cb.is_syntax  = row.is_syntax,
        cb.char_count = row.char_count
    MERGE (c)-[hc:HAS_CODE]->(cb)
    SET hc.order = row.order
    """
    rows = []
    for c in chunks:
        for cb in c.code_blocks:
            rows.append({
                "id": f"{c.chunk_id}#code{cb.order}",
                "chunk_id": c.chunk_id,
                "language": cb.language,
                "content": cb.content,
                "order": cb.order,
                "is_syntax": cb.is_syntax,
                "char_count": cb.char_count,
            })
    print(f"[code] gravando {len(rows):,} CodeBlock...", file=sys.stderr)
    with driver.session(database=database) as sess:
        for i in range(0, len(rows), batch_size):
            batch = rows[i:i + batch_size]
            _write(sess, query, {"batch": batch})
            print(f"  {min(i + batch_size, len(rows)):,}/{len(rows):,}",
                  file=sys.stderr)
    return len(rows)


def batch_mentions(driver, database: str, mentions: list[dict],
                   target_label: str, batch_size: int) -> None:
    query = f"""
    UNWIND $batch AS row
    MATCH (c:Chunk {{chunk_id: row.chunk_id}})
    MATCH (t:{target_label} {{name: row.target_name}})
    MERGE (c)-[m:MENTIONS]->(t)
    SET m.count      = row.count,
        m.count_code = row.count_code,
        m.in_code    = row.count_code > 0
    """
    print(f"[mentions] gravando {len(mentions):,} MENTIONS -> {target_label}...",
          file=sys.stderr)
    with driver.session(database=database) as sess:
        for i in range(0, len(mentions), batch_size):
            batch = mentions[i:i + batch_size]
            _write(sess, query, {"batch": batch})
            print(f"  {min(i + batch_size, len(mentions)):,}/{len(mentions):,}",
                  file=sys.stderr)


def medir_df(raw_hits: list[tuple]) -> Counter:
    """Frequencia documental por (rotulo, padrao): em quantos chunks
    distintos cada regra de casamento produziu ao menos uma mencao.

    A chave inclui o rotulo porque a mesma cadeia pode ser padrao de um
    Concept (insensivel a caixa) e de um TSqlObject (que exige caixa alta).
    Medidas juntas, as ocorrencias minusculas do Concept inflariam a
    frequencia do TSqlObject, e o corte levaria os dois.

    raw_hits: (chunk_id, label, target, pattern, count, veio_de_codigo)
    """
    vistos = {(h[0], h[1], h[3]) for h in raw_hits}
    return Counter((label, pat) for (_chunk, label, pat) in vistos)


def filtrar_por_df(raw_hits: list[tuple], df: Counter,
                   limite_chunks: float) -> tuple[list[tuple], set]:
    """Descarta os casamentos cujas regras (rotulo, padrao) excedem o limite.
    Retorna (casamentos_restantes, chaves_descartadas)."""
    descartados = {k for k, d in df.items() if d > limite_chunks}
    restantes = [h for h in raw_hits if (h[1], h[3]) not in descartados]
    return restantes, descartados


def report_df(df: Counter, n_chunks: int, top_n: int) -> None:
    """Imprime o ranking de frequencia documental para escolha do limiar.

    Nao grava nada no grafo. Serve para decidir --df-max com dados na mao.
    """
    sep = "=" * 72
    print("\n" + sep)
    print(f"FREQUENCIA DOCUMENTAL -- top {top_n} padroes")
    print(sep)
    print(f"\nCorpus: {n_chunks:,} chunks. "
          f"{len(df):,} padroes com ao menos 1 casamento.\n")
    print(f"    {'#':>4}  {'chunks':>8}  {'df%':>6}  {'tipo':<11} padrao")
    print(f"    {'-'*4}  {'-'*8}  {'-'*6}  {'-'*11} {'-'*30}")
    for i, ((label, pat), d) in enumerate(df.most_common(top_n), 1):
        pct = 100.0 * d / n_chunks
        print(f"    {i:>4}  {d:>8,}  {pct:>5.1f}%  {label:<11} {pat[:44]}")

    print("\n" + "-" * 72)
    print("Quantos padroes seriam descartados por limiar:")
    print("-" * 72)
    print(f"    {'limiar':>8}  {'chunks':>9}  {'padroes':>9}  {'% do vocab':>11}")
    for lim in (0.01, 0.02, 0.03, 0.05, 0.075, 0.10, 0.15, 0.20):
        corte = lim * n_chunks
        n = sum(1 for d in df.values() if d > corte)
        print(f"    {lim:>7.1%}  {corte:>9,.0f}  {n:>9,}  "
              f"{100.0*n/max(len(df),1):>10.2f}%")

    print("\n" + "-" * 72)
    print("Distribuicao de df (percentis sobre os padroes com casamento):")
    print("-" * 72)
    vals = sorted(df.values())
    if vals:
        def pct_at(q):
            return vals[min(int(q * len(vals)), len(vals) - 1)]
        for q, nome in ((0.5, "p50"), (0.9, "p90"), (0.99, "p99"),
                        (0.999, "p99.9")):
            v = pct_at(q)
            print(f"    {nome:>6} = {v:>7,} chunks ({100.0*v/n_chunks:>5.2f}%)")
        print(f"    {'max':>6} = {vals[-1]:>7,} chunks "
              f"({100.0*vals[-1]/n_chunks:>5.2f}%)")

    print("\n" + sep)
    print("Escolha um limiar e rode com --df-max, por exemplo:")
    print("    python src/chunking_e_mentions.py --df-max 0.05 --reset")
    print(sep)


def report_graph(driver, database: str) -> None:
    sep = "=" * 72
    print("\n" + sep)
    print("RELATORIO DO GRAFO POS-TESTE-03")
    print(sep)

    with driver.session(database=database) as sess:
        print("\n[1] Nos por label")
        for r in sess.run("""
            MATCH (n) UNWIND labels(n) AS l
            RETURN l AS label, count(*) AS c ORDER BY c DESC
        """):
            print(f"    {r['label']:15} {r['c']:>10,}")

        print("\n[2] Arestas por tipo")
        for r in sess.run("""
            MATCH ()-[r]->()
            RETURN type(r) AS t, count(*) AS c ORDER BY c DESC
        """):
            print(f"    {r['t']:15} {r['c']:>10,}")

        print("\n[3] Chunks por section_type")
        for r in sess.run("""
            MATCH (c:Chunk)
            RETURN c.section_type AS st, count(*) AS c
            ORDER BY c DESC
        """):
            print(f"    {r['st']:22} {r['c']:>8,}")

        print("\n[4] CodeBlocks por linguagem (top 15)")
        for r in sess.run("""
            MATCH (cb:CodeBlock)
            RETURN cb.language AS lang, count(*) AS c
            ORDER BY c DESC LIMIT 15
        """):
            print(f"    {r['lang']:22} {r['c']:>8,}")

        print("\n[5] Distribuicao de chunks por documento")
        row = sess.run("""
            MATCH (d:Document)
            OPTIONAL MATCH (d)-[:HAS_CHUNK]->(c:Chunk)
            WITH d, count(c) AS n
            RETURN
                avg(n) AS avg,
                min(n) AS min_n,
                max(n) AS max_n,
                percentileCont(n, 0.5) AS p50,
                percentileCont(n, 0.9) AS p90,
                percentileCont(n, 0.99) AS p99
        """).single()
        print(f"    min={row['min_n']}  p50={row['p50']:.1f}  p90={row['p90']:.1f}  "
              f"p99={row['p99']:.1f}  max={row['max_n']}  avg={row['avg']:.2f}")

        print("\n[5b] Splitter secundario -- chunks resultantes de H2 oversized")
        row = sess.run("""
            MATCH (c:Chunk) WHERE c.part_index IS NOT NULL
            RETURN count(c) AS n_parts,
                   count(DISTINCT (c.doc_path + '#' + toString(c.order)))
                       AS n_source_sections
        """).single()
        print(f"    Chunks-parte gerados: {row['n_parts']:,}")
        print(f"    Vindas de: {row['n_source_sections']:,} secoes oversized")
        if row['n_source_sections']:
            avg_parts = row['n_parts'] / row['n_source_sections']
            print(f"    Media de partes por secao dividida: {avg_parts:.2f}")

        print("\n[5c] Top 10 chunks por token_count (verificacao pos-splitter)")
        for r in sess.run("""
            MATCH (c:Chunk)
            RETURN c.token_count AS tk, c.section_title AS t,
                   c.part_index AS pi
            ORDER BY tk DESC LIMIT 10
        """):
            title = (r['t'] or '')[:52]
            flag = "P" if r['pi'] is not None else " "
            print(f"    {flag} {r['tk']:>6}  {title}")

        over = sess.run("""
            MATCH (c:Chunk) WHERE c.token_count > $lim
            RETURN count(c) AS n
        """, lim=3000).single()["n"]
        print(f"\n    Chunks acima de 3.000 tokens: {over:,}")
        if over:
            print("    ATENCAO: splitter deixou passar chunks oversized.")
            for r in sess.run("""
                MATCH (c:Chunk) WHERE c.token_count > $lim
                RETURN c.chunk_id AS id, c.token_count AS tk
                ORDER BY tk DESC LIMIT 5
            """, lim=3000):
                print(f"      {r['tk']:>6}  {r['id']}")
        else:
            print("    OK: pos-condicao do splitter respeitada.")

        print("\n[6] Top 15 Concepts por # de Chunks que os mencionam")
        for r in sess.run("""
            MATCH (c:Chunk)-[:MENTIONS]->(t:Concept)
            RETURN t.name AS name, count(DISTINCT c) AS chunks,
                   sum(coalesce(t.count, 1)) AS total
            ORDER BY chunks DESC LIMIT 15
        """):
            print(f"    {r['chunks']:>6}  {r['name']}")

        print("\n[7] Top 15 TSqlObject por # de Chunks que os mencionam")
        for r in sess.run("""
            MATCH (c:Chunk)-[:MENTIONS]->(t:TSqlObject)
            RETURN t.name AS name, count(DISTINCT c) AS chunks
            ORDER BY chunks DESC LIMIT 15
        """):
            print(f"    {r['chunks']:>6}  {r['name']}")

        print("\n[8] COBERTURA -- quantos nos anotados ganham >=1 MENTIONS?")
        c_total = sess.run("MATCH (c:Concept) RETURN count(c) AS c").single()["c"]
        c_hit = sess.run("""
            MATCH (c:Concept) WHERE (:Chunk)-[:MENTIONS]->(c)
            RETURN count(c) AS c
        """).single()["c"]
        t_total = sess.run("MATCH (t:TSqlObject) RETURN count(t) AS c").single()["c"]
        t_hit = sess.run("""
            MATCH (t:TSqlObject) WHERE (:Chunk)-[:MENTIONS]->(t)
            RETURN count(t) AS c
        """).single()["c"]

        pct_c = (100.0 * c_hit / c_total) if c_total else 0.0
        pct_t = (100.0 * t_hit / t_total) if t_total else 0.0
        print(f"    Concept    : {c_hit:>6,} / {c_total:>6,} = {pct_c:.1f}%")
        print(f"    TSqlObject : {t_hit:>6,} / {t_total:>6,} = {pct_t:.1f}%")

        # Cobertura separada por populacao: somar Concepts e TSqlObjects e dividir
        # por uma das populacoes daria um numero sem significado.
        print("\n    Concept por forma do nome:")
        for r in sess.run("""
            MATCH (c:Concept)
            OPTIONAL MATCH (:Chunk)-[m:MENTIONS]->(c)
            WITH c,
                 (c.name CONTAINS '[' OR c.name CONTAINS ',') AS notacao,
                 count(m) AS n
            RETURN notacao,
                   count(c) AS total,
                   sum(CASE WHEN n > 0 THEN 1 ELSE 0 END) AS cobertos
            ORDER BY notacao
        """):
            forma = ("notacao de indice" if r["notacao"] else "texto limpo")
            tot, cob = r["total"], r["cobertos"]
            pct = (100.0 * cob / tot) if tot else 0.0
            print(f"      {forma:20}: {cob:>6,} / {tot:>6,} = {pct:.1f}%")

        print("\n[8b] DENSIDADE -- mentions por no coberto")
        for label in ("Concept", "TSqlObject"):
            row = sess.run(f"""
                MATCH (:Chunk)-[m:MENTIONS]->(t:{label})
                WITH t, count(m) AS n
                RETURN percentileCont(n, 0.5) AS p50,
                       percentileCont(n, 0.9) AS p90,
                       max(n) AS mx, avg(n) AS avg
            """).single()
            if row and row["p50"] is not None:
                print(f"    {label:11}: p50={row['p50']:.0f}  "
                      f"p90={row['p90']:.0f}  max={row['mx']:,}  "
                      f"media={row['avg']:.1f}")
        print("    (cobertura mede alcance; densidade mede concentracao.")
        print("     densidade alta com cobertura baixa = poucos nos dominando)")

        print("\n[8c] EVIDENCIA EM BLOCO DE CODIGO")
        row = sess.run("""
            MATCH (:Chunk)-[m:MENTIONS]->()
            RETURN count(m) AS total,
                   sum(CASE WHEN m.in_code THEN 1 ELSE 0 END) AS com_codigo,
                   sum(CASE WHEN m.count = 0 THEN 1 ELSE 0 END) AS so_codigo
        """).single()
        tot = row["total"] or 0
        if tot:
            print(f"    Arestas totais              : {tot:>8,}")
            print(f"    Com alguma evidencia em codigo: {row['com_codigo']:>8,} "
                  f"({100.0*row['com_codigo']/tot:.1f}%)")
            print(f"    Apenas por codigo (count=0) : {row['so_codigo']:>8,} "
                  f"({100.0*row['so_codigo']/tot:.1f}%)")

        # Nos sem nenhuma evidencia na prosa (soma de count = 0). Testar o in_code
        # da aresta deixaria de fora as arestas com prosa e codigo ao mesmo tempo.
        for label in ("Concept", "TSqlObject"):
            r = sess.run(f"""
                MATCH (:Chunk)-[m:MENTIONS]->(t:{label})
                WITH t, sum(m.count) AS prosa
                WHERE prosa = 0
                RETURN count(t) AS n
            """).single()
            print(f"    {label:11} sem nenhuma evidencia em prosa: {r['n']:>6,}")
        print("    (ablation: use 'WHERE m.count > 0' para restringir a prosa)")
        print("    (cobertura alta = keywords do frontmatter batem com o texto;")
        print("     baixa = o texto usa termos que os metadados nao cobrem)")

    print("\n" + sep)
    print("Consultas uteis no browser Neo4j (http://localhost:7474)")
    print(sep)
    print("""
    // 1) Panorama
    MATCH (n) RETURN labels(n)[0] AS label, count(*) ORDER BY count(*) DESC;

    // 2) Um doc de referencia com toda sua estrutura de chunks
    // (part_index != null = veio do splitter secundario)
    MATCH (d:Document {path: 't-sql/statements/create-index-transact-sql.md'})
          -[:HAS_CHUNK]->(c:Chunk)
    RETURN c.order, c.part_index, c.section_type, c.section_title, c.token_count
    ORDER BY c.order, coalesce(c.part_index, -1);

    // 3) Chunks do tipo 'remarks' que mencionam 'index' e o statement CREATE INDEX
    MATCH (c:Chunk {section_type: 'remarks'})-[:MENTIONS]->(cn:Concept)
    WHERE cn.name CONTAINS 'index'
    MATCH (c)-[:MENTIONS]->(t:TSqlObject {name: 'CREATE_INDEX_TSQL'})
    RETURN c.chunk_id, c.section_title LIMIT 10;

    // 4) Toda a vizinhanca de "remocao de indice" ate 2 hops de MENTIONS
    MATCH (cn:Concept) WHERE cn.name IN ['removing indexes', 'dropping indexes']
    MATCH (c:Chunk)-[:MENTIONS]->(cn)
    MATCH (c)-[:MENTIONS]->(other)
    RETURN cn, c, other LIMIT 100;
    """)


# Fora do corpus: as tabelas de mensagens de erro de errors-events/includes/,
# referencia tabular repetida para cinco versoes do SQL Server, sem prosa.
# As paginas individuais de erro ficam.
# Manter igual no construir_grafo.py.
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
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--reset", action="store_true",
                        help="Apaga TODOS Chunks/CodeBlocks (mantem Documents/Concepts/TSqlObjects)")
    parser.add_argument("--ping", action="store_true")
    parser.add_argument("--skip-parse", action="store_true",
                        help="So imprime o relatorio, nao re-parseia")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--mentions-batch-size", type=int, default=2000)
    parser.add_argument("--df-report", type=int, default=0, metavar="N",
                        help="So mede frequencia documental e imprime o top N. "
                             "Nao grava nada. Use para escolher --df-max.")
    parser.add_argument("--df-max", type=float, default=DEFAULT_DF_MAX,
                        metavar="FRACAO",
                        help="Descarta padroes que aparecem em mais que esta "
                             "fracao dos chunks (ex: 0.05 = 5%%). "
                             "0 desativa o corte.")
    parser.add_argument("--no-stoplist", action="store_true",
                        help="Desliga COMMON_STOPWORDS e ALIAS_STOPWORDS. "
                             "Use com --df-max para medir se o corte por "
                             "frequencia documental subsume as listas manuais.")
    parser.add_argument("--no-code-match", action="store_true",
                        help="Nao procura identificadores dentro de blocos de "
                             "codigo. Use para comparar com o casamento "
                             "so na prosa.")
    args = parser.parse_args()

    print(f"[config] blocos de codigo pelo CommonMark "
          f"(markdown-it-py {MARKDOWN_IT_VERSAO})", file=sys.stderr)
    if args.no_stoplist:
        global _STOPLISTS_ATIVAS
        _STOPLISTS_ATIVAS = False
        print("[config] stoplists manuais DESLIGADAS (--no-stoplist)",
              file=sys.stderr)

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    try:
        try:
            driver.verify_connectivity()
        except ServiceUnavailable as exc:
            print(f"ERRO: nao conectei em {NEO4J_URI}: {exc}", file=sys.stderr)
            return 1
        except AuthError as exc:
            print(f"ERRO: falha de auth: {exc}", file=sys.stderr)
            return 1
        print(f"[neo4j] conectado em {NEO4J_URI} (db={NEO4J_DATABASE})",
              file=sys.stderr)

        if args.ping:
            print("[ping] ok.", file=sys.stderr)
            return 0

        if args.skip_parse:
            report_graph(driver, NEO4J_DATABASE)
            return 0

        ensure_schema(driver, NEO4J_DATABASE)

        if args.reset:
            print("\n>>> --reset vai apagar TODOS os Chunks e CodeBlocks.",
                  file=sys.stderr)
            print(">>> Documents, Concepts, TSqlObjects (do construir_grafo.py) NAO sao afetados.",
                  file=sys.stderr)
            try:
                confirm = input(">>> Digite 'sim' para continuar: ").strip().lower()
            except EOFError:
                confirm = ""
            if confirm != "sim":
                print("Cancelado.", file=sys.stderr)
                return 0
            reset_chunks_only(driver, NEO4J_DATABASE)

        concepts, tsql_names = load_existing_names(driver, NEO4J_DATABASE)
        if not concepts and not tsql_names:
            print("ERRO: nenhum Concept/TSqlObject no grafo. "
                  "Rode o construir_grafo.py antes.", file=sys.stderr)
            return 1

        print("[matcher] construindo automaton Aho-Corasick...", file=sys.stderr)
        t0 = time.time()
        automaton, automaton_code, mcounts = build_matcher(concepts, tsql_names)
        print(f"[matcher] pronto em {time.time() - t0:.1f}s", file=sys.stderr)
        print(f"[matcher]   Concept   : {mcounts['concept_patterns']:,} padroes "
              f"({mcounts['concept_exact']:,} exatos + "
              f"{mcounts['concept_alias']:,} aliases; "
              f"{mcounts['concept_ambiguous_dropped']:,} descartados por "
              f"ambiguidade)", file=sys.stderr)
        print(f"[matcher]   TSqlObject: {mcounts['tsql_patterns']:,} padroes "
              f"({mcounts['case_sensitive']:,} exigem caixa exata; "
              f"{mcounts['tsql_filtered']:,} filtrados como ruido)",
              file=sys.stderr)
        print(f"[matcher]   colisoes  : {mcounts['shared_concept_tsql']:,} padroes "
              f"compartilhados entre Concept e TSqlObject; "
              f"{mcounts['tsql_multi_target']:,} padroes com 2+ TSqlObjects "
              f"(todos os alvos mantidos)",
              file=sys.stderr)
        if args.no_code_match:
            automaton_code = None
            print("[matcher]   codigo    : busca DESLIGADA (--no-code-match)",
                  file=sys.stderr)
        else:
            print(f"[matcher]   codigo    : {mcounts['identifier_patterns']:,} "
                  f"identificadores buscados dentro de blocos de codigo",
                  file=sys.stderr)

        docs_root = get_docs_root()
        files = list(iter_markdown_files(docs_root))
        if args.limit:
            files = files[:args.limit]
        print(f"\n[parse] chunkeando {len(files):,} arquivos em {docs_root} ...",
              file=sys.stderr)

        t0 = time.time()
        all_chunks: list[ChunkRecord] = []
        raw_hits: list[tuple] = []
        n_hits_code = 0

        for i, f in enumerate(files, 1):
            chunks = chunk_document(f, docs_root)
            for c in chunks:
                for (label, target, pattern), n in \
                        find_mentions(c.text_matchable, automaton).items():
                    raw_hits.append((c.chunk_id, label, target, pattern, n, False))

                if automaton_code is not None and c.code_blocks:
                    code_text = "\n".join(cb.content for cb in c.code_blocks)
                    for (label, target, pattern), n in \
                            find_mentions(code_text, automaton_code).items():
                        raw_hits.append((c.chunk_id, label, target, pattern, n, True))
                        n_hits_code += 1
            all_chunks.extend(chunks)
            if i % 500 == 0:
                print(f"  {i:,}/{len(files):,}  chunks={len(all_chunks):,}  "
                      f"hits={len(raw_hits):,}", file=sys.stderr)

        n_chunks = len(all_chunks)
        print(f"[parse] {n_chunks:,} chunks + {len(raw_hits):,} casamentos brutos "
              f"({n_hits_code:,} vindos de blocos de codigo) "
              f"em {time.time()-t0:.1f}s", file=sys.stderr)

        df = medir_df(raw_hits)

        if args.df_report:
            report_df(df, n_chunks, args.df_report)
            return 0

        descartados: set = set()
        if args.df_max > 0:
            limite = args.df_max * n_chunks
            hits_antes = len(raw_hits)
            raw_hits, descartados = filtrar_por_df(raw_hits, df, limite)
            print(f"\n[df] limiar = {args.df_max:.1%} de {n_chunks:,} chunks "
                  f"= {limite:,.0f} chunks", file=sys.stderr)
            print(f"[df] {len(descartados):,} padroes descartados de "
                  f"{len(df):,} com ao menos 1 casamento", file=sys.stderr)
            print(f"[df] casamentos: {hits_antes:,} -> {len(raw_hits):,} "
                  f"({100.0*(hits_antes-len(raw_hits))/max(hits_antes,1):.1f}% "
                  f"removidos)", file=sys.stderr)
            if descartados:
                top = sorted(descartados, key=lambda k: -df[k])[:15]
                print("[df] padroes descartados com maior frequencia:",
                      file=sys.stderr)
                for (label, p) in top:
                    print(f"       {df[(label, p)]:>6,} "
                          f"({100.0*df[(label, p)]/n_chunks:>5.1f}%)  "
                          f"{label:<10} {p}", file=sys.stderr)

        # count = ocorrencias na prosa; count_code = ocorrencias nos blocos de
        # codigo. A aresta existe se um dos dois for maior que zero.
        agg_prosa: Counter = Counter()
        agg_codigo: Counter = Counter()
        for chunk_id, label, target, _pat, n, veio_de_codigo in raw_hits:
            chave = (chunk_id, label, target)
            if veio_de_codigo:
                agg_codigo[chave] += n
            else:
                agg_prosa[chave] += n

        all_mentions_concept: list[dict] = []
        all_mentions_tsql: list[dict] = []
        n_so_codigo = 0
        for chave in set(agg_prosa) | set(agg_codigo):
            chunk_id, label, target = chave
            n_prosa = agg_prosa.get(chave, 0)
            n_codigo = agg_codigo.get(chave, 0)
            if n_prosa == 0:
                n_so_codigo += 1
            row = {
                "chunk_id": chunk_id,
                "target_name": target,
                "count": n_prosa,
                "count_code": n_codigo,
            }
            if label == "Concept":
                all_mentions_concept.append(row)
            else:
                all_mentions_tsql.append(row)

        total_arestas = len(all_mentions_concept) + len(all_mentions_tsql)
        print(f"[mentions] {len(all_mentions_concept):,} -> Concept, "
              f"{len(all_mentions_tsql):,} -> TSqlObject", file=sys.stderr)
        if total_arestas:
            print(f"[mentions] {n_so_codigo:,} arestas ({100.0*n_so_codigo/total_arestas:.1f}%) "
                  f"existem apenas por evidencia em bloco de codigo",
                  file=sys.stderr)

        affected_docs = sorted({c.doc_path for c in all_chunks})
        if affected_docs and not args.reset:
            print(f"\n[clean] apagando chunks pre-existentes de "
                  f"{len(affected_docs):,} docs...", file=sys.stderr)
            clean_chunks_for_docs(driver, NEO4J_DATABASE, affected_docs)

        print("", file=sys.stderr)
        t0 = time.time()
        batch_chunks(driver, NEO4J_DATABASE, all_chunks, args.batch_size)
        batch_code_blocks(driver, NEO4J_DATABASE, all_chunks, args.batch_size)
        batch_mentions(driver, NEO4J_DATABASE, all_mentions_concept,
                       "Concept", args.mentions_batch_size)
        batch_mentions(driver, NEO4J_DATABASE, all_mentions_tsql,
                       "TSqlObject", args.mentions_batch_size)
        print(f"\n[write] tudo gravado em {time.time()-t0:.1f}s", file=sys.stderr)

        report_graph(driver, NEO4J_DATABASE)
        return 0

    finally:
        driver.close()


if __name__ == "__main__":
    sys.exit(main())
