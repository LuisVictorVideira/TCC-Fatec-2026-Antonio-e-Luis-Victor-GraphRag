"""Testes dos baselines e do avaliador: representacao, RRF, leitura das perguntas e metricas."""
import pytest
import yaml

import avaliar_recuperacao as av
import baselines as bl


def trecho(texto, titulo="DROP INDEX (Transact-SQL)", secao="Remarks"):
    return {"chunk_id": "x#chunk0", "doc_title": titulo, "section_title": secao, "text": texto}


def test_placeholder_expandido_com_e_sem_espaco_e_desconhecido_removido():
    t = trecho("A [!INCLUDE [ssnoversion](../i/ssnoversion-md.md)] e "
               "B [!INCLUDE[ssde](../i/ssde-md.md)] e C [!INCLUDE [sql-b-tree](../i/x.md)] fim")
    txt = bl.texto_para_indice(t)
    assert "A SQL Server e" in txt
    assert "B SQL Server Database Engine e" in txt
    assert "INCLUDE" not in txt and "sql-b-tree" not in txt


def test_links_html_e_diretivas():
    t = trecho("Veja [heaps](../indexes/heaps.md) e <a id=\"x\"></a>texto\n"
               ":::image type=\"content\" source=\"a.png\":::\nfim")
    txt = bl.texto_para_indice(t)
    assert "Veja heaps e" in txt and "../indexes" not in txt
    assert "<a" not in txt and ":::image" not in txt and "fim" in txt


def test_titulos_prefixados_e_codigo_mantido():
    t = trecho("## Remarks\n\n```sql\nDROP INDEX ix ON t;\n```\n")
    txt = bl.texto_para_indice(t)
    assert txt.startswith("DROP INDEX (Transact-SQL)\nRemarks\n")
    assert "DROP INDEX ix ON t;" in txt


def test_rrf_soma_inverso_das_posicoes_e_desempata_por_id():
    a = {"Q1": [("d1", 9.0), ("d2", 8.0), ("d3", 7.0)]}
    b = {"Q1": [("d2", 0.9), ("d3", 0.8), ("d4", 0.7)]}
    r = bl.rrf([a, b], k_rrf=60, profundidade=100, k=10)["Q1"]
    pont = dict(r)
    assert pont["d2"] == pytest.approx(1 / 62 + 1 / 61)
    assert pont["d1"] == pytest.approx(1 / 61)
    assert pont["d4"] == pytest.approx(1 / 63)
    assert [d for d, _ in r][:2] == ["d2", "d3"]
    c = {"Q1": [("zz", 1.0)]}
    d = {"Q1": [("aa", 1.0)]}
    assert [x for x, _ in bl.rrf([c, d], 60, 100, 10)["Q1"]] == ["aa", "zz"]


def test_run_ida_e_volta(tmp_path):
    res = {"B02": [("a#chunk1", 3.5), ("b#chunk2p0", 1.25)]}
    p = tmp_path / "x.run"
    bl.gravar_run(p, res, "teste", {"k": 2})
    assert bl.ler_run(p) == {"B02": [("a#chunk1", 3.5), ("b#chunk2p0", 1.25)]}
    assert av.ler_run(p) == {"B02": ["a#chunk1", "b#chunk2p0"]}
    assert p.with_suffix(".manifesto.json").exists()


def test_filtro_de_perguntas(tmp_path):
    y = tmp_path / "p.yaml"
    y.write_text(yaml.safe_dump({"perguntas": [
        {"id": "A01", "pergunta_en": "q1", "verificado": "sim"},
        {"id": "A02", "pergunta_en": "q2", "verificado": "descartada"},
        {"id": "A03", "pergunta_en": "q3", "verificado": "nao"},
    ]}), encoding="utf-8")
    assert bl.carregar_perguntas(y, "verificadas") == [("A01", "q1")]
    assert bl.carregar_perguntas(y, "todas") == [("A01", "q1"), ("A03", "q3")]


def gab(componentes, escopo="um_trecho", trivial=False):
    return {"componentes": componentes,
            "ouro": {t for c in componentes for t in c["trechos"]},
            "apoio": set(), "escopo": escopo, "grupo": "B", "trivial": trivial}


def test_componente_com_trechos_alternativos_basta_um():
    g = gab([{"nome": "c1", "essencial": True, "trechos": ["x", "y"]}])
    m = av.metricas(["a", "y", "b"], g, (1, 3))
    assert m["R@1"] == 0 and m["R@3"] == 1.0
    assert m["MRR"] == pytest.approx(0.5)
    assert m["P@3"] == pytest.approx(1 / 3)


def test_dois_componentes_cobertura_parcial():
    g = gab([{"nome": "c1", "essencial": True, "trechos": ["x"]},
             {"nome": "c2", "essencial": True, "trechos": ["y", "z"]}])
    m = av.metricas(["x", "a", "b", "z"], g, (3, 5))
    assert m["R@3"] == 0.5 and m["R@5"] == 1.0
    assert m["posicoes_ouro"] == {"x": 1, "y": None, "z": 4}


def test_lista_plana_vira_um_componente_por_trecho():
    q = {"id": "A03", "chunks_ouro": ["a", "b"]}
    comps = av.componentes_da_pergunta(q)
    assert [c["trechos"] for c in comps] == [["a"], ["b"]]


def test_teste_de_aleatorizacao_exato():
    assert av.teste_aleatorizacao([1.0, 1.0, 1.0]) == pytest.approx(2 / 8)
    assert av.teste_aleatorizacao([0.0, 0.0]) == pytest.approx(1.0)
