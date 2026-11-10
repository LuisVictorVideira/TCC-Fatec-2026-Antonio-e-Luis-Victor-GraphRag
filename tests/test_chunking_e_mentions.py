"""Testes de regressao do chunking_e_mentions.py.

Cada teste prende uma regra da fragmentacao ou do casamento, para que uma
mudanca futura nao a quebre sem aviso. Os testes usam a biblioteca
pyahocorasick de verdade, e nao um substituto.

O teste marcado com xfail(strict=True) documenta uma limitacao conhecida: a
janela deslizante junta as palavras numa linha so. Se o comportamento for
corrigido, o teste passa a XPASS e a suite falha de proposito.

Rodar:
    python -m pytest tests -q
"""
from collections import Counter

import pytest

import chunking_e_mentions as chunking


def alvos(text, automaton):
    """Conjunto de (rotulo, alvo) casados num texto."""
    return {(lab, tgt) for (lab, tgt, _pat) in chunking.find_mentions(text, automaton)}


def test_fronteira_de_palavra_usa_o_tamanho_do_padrao():
    """'int' nao pode casar dentro de 'print'.

    A forma de superficie de CREATE_INDEX_TSQL e 'CREATE INDEX' (12
    caracteres), menor que o nome do no (17). A fronteira precisa ser
    calculada com o tamanho do padrao casado.
    """
    A, _, _ = chunking.build_matcher([], ["int", "CREATE_INDEX_TSQL"])
    assert alvos("Use print to show the value.", A) == set()
    assert alvos("An int column.", A) == {("TSqlObject", "int")}
    assert alvos("Run CREATE INDEX now.", A) == {("TSqlObject", "CREATE_INDEX_TSQL")}
    assert alvos("Run CREATE INDEXES now.", A) == set()


def test_chave_compartilhada_mantem_todos_os_alvos():
    """Concept e TSqlObject com a mesma forma recebem a mencao: o automato
    guarda todos os alvos de uma chave, e nao so o ultimo inserido.
    """
    A, _, counts = chunking.build_matcher(
        ["sp_changedistpublisher"],
        ["sp_changedistpublisher", "sp_changedistpublisher_TSQL"],
    )
    texto = "Execute sp_changedistpublisher at the Distributor."
    assert alvos(texto, A) == {
        ("Concept", "sp_changedistpublisher"),
        ("TSqlObject", "sp_changedistpublisher"),
        ("TSqlObject", "sp_changedistpublisher_TSQL"),
    }
    assert counts["shared_concept_tsql"] == 1
    assert counts["tsql_multi_target"] == 1


@pytest.mark.parametrize("nome, forma", [
    ("sp_changedistpublisher_TSQL", "sp_changedistpublisher"),
    ("CREATE_INDEX_TSQL", "CREATE INDEX"),
    ("ALTER_DATABASE_TSQL", "ALTER DATABASE"),
    ("sys.dm_db_index_usage_stats", "sys.dm_db_index_usage_stats"),
])
def test_underscore_so_vira_espaco_em_nome_todo_maiusculo(nome, forma):
    assert chunking.surface_form_of_tsql(nome) == forma


def test_ruido_contado_no_ponto_de_descarte():
    """O contador de ruido nao pode absorver os gemeos com e sem _TSQL."""
    _, _, counts = chunking.build_matcher([], ["FOR_TSQL", "!=", "sp_help", "sp_help_TSQL"])
    assert counts["tsql_filtered"] == 2
    assert counts["tsql_patterns"] == 1
    assert counts["tsql_multi_target"] == 1


def test_caixa_avaliada_por_alvo():
    """A mesma chave serve a um Concept (insensivel) e a um keyword (CAIXA ALTA)."""
    A, _, _ = chunking.build_matcher(["alter"], ["ALTER_TSQL", "NOT_TSQL", "int"])
    assert alvos("You can alter the table.", A) == {("Concept", "alter")}
    assert alvos("Use ALTER to change it.", A) == {("Concept", "alter"),
                                                  ("TSqlObject", "ALTER_TSQL")}
    assert alvos("The row does not exist.", A) == set()
    assert alvos("Use the NOT operator.", A) == {("TSqlObject", "NOT_TSQL")}
    assert alvos("An INT value.", A) == {("TSqlObject", "int")}


@pytest.mark.parametrize("conceito, esperado", [
    ("availability groups [sql server], monitoring", ["availability groups"]),
    ("sys.sql_dependencies catalog view", ["sys.sql_dependencies"]),
    ("deny statement", []),
    ("names [sql server], columns with", []),
])
def test_aliases_da_notacao_de_indice_e_do_sufixo_de_tipo(conceito, esperado):
    assert chunking.concept_aliases(conceito) == esperado


def test_nome_do_no_nao_muda():
    """Alias existe so no automato; o alvo continua sendo o nome original."""
    nome = "availability groups [sql server], monitoring"
    A, _, _ = chunking.build_matcher([nome], [])
    assert alvos("Monitor availability groups daily.", A) == {("Concept", nome)}


def test_frequencia_medida_por_rotulo_e_padrao():
    hits = [
        ("c1", "Concept", "alter", "alter", 1, False),
        ("c2", "Concept", "alter", "alter", 1, False),
        ("c1", "TSqlObject", "ALTER_TSQL", "alter", 1, False),
    ]
    df = chunking.medir_df(hits)
    assert df == Counter({("Concept", "alter"): 2, ("TSqlObject", "alter"): 1})
    restantes, cortadas = chunking.filtrar_por_df(hits, df, limite_chunks=1.5)
    assert cortadas == {("Concept", "alter")}
    assert restantes == [hits[2]]


def test_tabela_markdown_gigante_respeita_o_limite():
    """Tabela sem linha em branco conta como um paragrafo so."""
    linhas = ["| coluna_a | coluna_b | descricao |", "| --- | --- | --- |"]
    linhas += [f"| valor{i} | outro{i} | texto descritivo da linha {i} |" for i in range(1500)]
    secao = "## Tabela\n\n" + "\n".join(linhas) + "\n"
    partes = chunking.build_chunks_for_section("x/doc.md", 3, "other", "Tabela", secao)
    assert len(partes) > 1
    assert all(p.token_count <= chunking.MAX_CHUNK_TOKENS for p in partes)
    assert [p.chunk_id for p in partes] == [f"x/doc.md#chunk3p{i}" for i in range(len(partes))]


def test_convencao_de_chunk_id(tmp_path):
    raiz = tmp_path / "docs"
    (raiz / "t-sql").mkdir(parents=True)
    corpo_grande = "\n\n".join(f"Paragrafo {i} " + "palavra " * 300 for i in range(12))
    doc = raiz / "t-sql" / "exemplo.md"
    doc.write_text(
        "---\ntitle: Exemplo\n---\n# Exemplo\n\n" + "Introducao " * 40 + "\n\n"
        "## Syntax\n\n```syntaxsql\nDROP INDEX x\n```\n\n"
        "## Remarks\n\n" + corpo_grande + "\n",
        encoding="utf-8")
    trechos = chunking.chunk_document(doc, raiz)
    ids = [c.chunk_id for c in trechos]
    assert ids[0] == "t-sql/exemplo.md#chunk0" and trechos[0].section_type == "intro"
    assert ids[1] == "t-sql/exemplo.md#chunk1" and trechos[1].section_type == "syntax"
    assert len(trechos[1].code_blocks) == 1
    assert all(i.startswith("t-sql/exemplo.md#chunk2p") for i in ids[2:])
    assert all(c.part_index is not None for c in trechos[2:])


def test_placeholder_expandido_antes_do_casamento():
    texto = "Applies to [!INCLUDE [ssnoversion](../includes/ssnoversion-md.md)] only."
    assert chunking.expand_placeholders(texto) == "Applies to SQL Server only."


def test_cerca_com_espaco_depois_da_linguagem():
    blocos, prosa = chunking.extract_code_blocks("Texto.\n\n```sql  \nSELECT 1;\n```\n\nFim.\n")
    assert len(blocos) == 1 and "SELECT" not in prosa
    assert blocos[0].language == "sql"


def test_cerca_recuada_em_lista():
    blocos, _ = chunking.extract_code_blocks("1. Passo:\n\n   ```sql\n   SELECT 1;\n   ```\n")
    assert len(blocos) == 1


def test_cerca_em_lista_aninhada_com_quatro_espacos():
    """Caso real do corpus: item de lista '2.  ' com o conteudo na coluna 4.

    Com quatro espacos, a cerca so eh reconhecida por quem segue a estrutura
    da lista, como manda o CommonMark; uma regra de 'ate tres espacos'
    contada a partir da margem nao a acharia.
    """
    texto = ("2.  **Crie a chave:** use o comando abaixo.\n  \n"
             "    ```  \n    USE Master;\n    CREATE MASTER KEY ENCRYPTION BY PASSWORD = 'x';\n    ```  \n  \n"
             "3.  **Proximo passo.**\n")
    blocos, prosa = chunking.extract_code_blocks(texto)
    assert len(blocos) == 1 and blocos[0].language == "unknown"
    assert blocos[0].content.startswith("USE Master;")
    assert "CREATE MASTER KEY" not in prosa and "Proximo passo" in prosa


def test_cerca_nao_pareia_com_a_abertura_seguinte():
    """Uma abertura nao reconhecida faria o fechamento dela virar abertura: a
    prosa entre dois blocos iria para o codigo, e o codigo seguinte ficaria
    na prosa.
    """
    texto = ("Texto um.\n\n```sql  \nSELECT 1;\n```\n\n"
             "Prosa do meio.\n\n```sql\nSELECT 2;\n```\n")
    blocos, prosa = chunking.extract_code_blocks(texto)
    assert [b.content for b in blocos] == ["SELECT 1;\n", "SELECT 2;\n"]
    assert "Prosa do meio." in prosa and "SELECT" not in prosa


def test_linguagem_com_hifen_e_cerca_com_til():
    blocos, _ = chunking.extract_code_blocks("```azurepowershell-interactive\nGet-AzSqlServer\n```\n")
    assert [b.language for b in blocos] == ["azurepowershell-interactive"]
    blocos, _ = chunking.extract_code_blocks("~~~sql\nSELECT 1;\n~~~\n")
    assert [b.language for b in blocos] == ["sql"]


def test_cerca_em_citacao():
    texto = "> [!NOTE]\n> ```sql\n> SELECT 1;\n> ```\n\nDepois.\n"
    blocos, prosa = chunking.extract_code_blocks(texto)
    assert len(blocos) == 1 and blocos[0].content == "SELECT 1;\n"
    assert "SELECT" not in prosa and "Depois." in prosa


def test_cerca_sem_fechamento_vai_ate_o_fim():
    blocos, prosa = chunking.extract_code_blocks("Antes.\n\n```sql\nSELECT 1;\n")
    assert len(blocos) == 1 and "SELECT" not in prosa and "Antes." in prosa


def test_cerca_dentro_de_comentario_html_nao_e_codigo():
    """Pelo CommonMark, um comentario HTML vai ate o '-->', e o que esta
    dentro dele nao eh bloco de codigo (o site nem o publica)."""
    texto = "<!--Exemplo:\n\n```sql\nSELECT 1;\n```\n-->\n\nTexto.\n"
    blocos, _ = chunking.extract_code_blocks(texto)
    assert blocos == []


def test_prosa_preservada_com_cerca_simples():
    """Com uma cerca simples, o bloco vira um espaco e o resto da prosa nao muda."""
    blocos, prosa = chunking.extract_code_blocks("Antes.\n\n```sql\nSELECT 1;\n```\n\nDepois.\n")
    assert len(blocos) == 1 and blocos[0].is_syntax is False
    assert prosa == "Antes.\n\n \n\nDepois.\n"


def test_texto_sem_quebra_de_linha_nao_tem_bloco():
    """As partes da janela deslizante juntam as palavras numa linha so. Sem
    quebra de linha nao ha estrutura de bloco, e nada e removido.
    """
    parte = "```sql CREATE TABLE t (a int) seguido de prosa sem outra crase"
    assert chunking.extract_code_blocks(parte) == ([], parte)


@pytest.mark.xfail(strict=True, reason="a janela deslizante junta palavras com espaco e perde as quebras de linha")
def test_janela_deslizante_preserva_quebras_de_linha():
    texto = "\n".join(f"linha {i} " + "x " * 20 for i in range(400))
    partes = chunking.split_by_token_window(texto, chunking.TARGET_PART_TOKENS)
    assert all("\n" in p for _, p in partes)
