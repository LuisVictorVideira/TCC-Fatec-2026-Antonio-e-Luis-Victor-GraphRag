"""Testes de regressao do construir_grafo.py."""
import pytest

import construir_grafo as metadados


@pytest.mark.parametrize("kw, tipo", [
    ("sp_help_TSQL", "system_procedure"),
    ("xp_cmdshell", "system_procedure"),
    ("CREATE_INDEX_TSQL", "tsql_statement"),
    ("sys.indexes", "system_object"),
    ("@@ROWCOUNT", "tsql_global_var"),
    ("@local", "tsql_variable"),
    ("$PARTITION", "tsql_system_var"),
    ("sql13.swb.index.general.f1", "ssms_ui"),
    ("8152", "error_code"),
    ("!=", "tsql_operator"),
    ("ADD FILE", "tsql_statement"),
    ("dbo.MyTable", "dotted_identifier"),
    ("nome qualquer", "unknown"),
])
def test_classificacao_de_f1_keywords(kw, tipo):
    assert metadados.classify_f1_keyword(kw) == tipo


def test_links_resolvidos_relativos_ao_docs_root(tmp_path):
    raiz = tmp_path / "docs"
    (raiz / "t-sql" / "statements").mkdir(parents=True)
    (raiz / "relational-databases" / "indexes").mkdir(parents=True)
    alvo = raiz / "relational-databases" / "indexes" / "heaps.md"
    alvo.write_text("---\ntitle: Heaps\n---\n# Heaps\n", encoding="utf-8")
    origem = raiz / "t-sql" / "statements" / "drop-index.md"
    origem.write_text(
        "---\ntitle: DROP INDEX\nhelpviewer_keywords:\n  - \"DROP INDEX statement\"\n"
        "f1_keywords:\n  - \"DROP_INDEX_TSQL\"\n---\n"
        "See [heaps](../../relational-databases/indexes/heaps.md#x), "
        "[externo](https://example.com/a.md), [ancora](#remarks) e "
        "[fora](../../../fora.md).\n",
        encoding="utf-8")
    rec = metadados.parse_document(origem, raiz)
    assert rec.rel_path == "t-sql/statements/drop-index.md"
    assert rec.outbound_links == ["relational-databases/indexes/heaps.md"]
    assert rec.helpviewer_keywords == ["DROP INDEX statement"]
    assert rec.f1_keywords == ["DROP_INDEX_TSQL"]


def test_exclusao_identica_nos_dois_scripts():
    """EXCLUDED_PATH_PREFIXES precisa ser igual no construir_grafo.py e no
    chunking_e_mentions.py.
    """
    import chunking_e_mentions as chunking
    assert metadados.EXCLUDED_PATH_PREFIXES == chunking.EXCLUDED_PATH_PREFIXES
    assert metadados.is_excluded("relational-databases/errors-events/includes/x.md")
    assert not metadados.is_excluded("relational-databases/errors-events/mssqlserver-8993-database-engine-error.md")
