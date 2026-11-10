"""Testes da amostra de precisao das relacoes: populacao das relacoes
validas, sorteio fixo, planilha sem a chave, intervalo de Wilson, copia dos
julgamentos do juiz de IA e calculo.
"""
import json
from argparse import Namespace

import pytest

import precisao_relacoes as pr
from baselines import texto_para_indice


def rel(o, t, d, norm=False):
    return {"origem": o, "tipo": t, "destino": d, "origem_rotulo": "Concept", "destino_rotulo": "Concept",
            "origem_nos": [o], "destino_nos": [d], "nome_normalizado": norm}


def extracao():
    return [
        {"chunk_id": "d1#chunk1", "erro": "ReadTimeout: ...", "relacoes": []},
        {"chunk_id": "d1#chunk1", "erro": None, "relacoes": [rel("a", "REQUIRES", "b"), rel("c", "AFFECTS", "d")]},
        {"chunk_id": "d2#chunk0", "erro": None, "relacoes": [rel("e", "PART_OF", "f", norm=True)]},
        {"chunk_id": "d3#chunk2", "erro": "ReadTimeout: ...", "relacoes": []},
        {"chunk_id": "d4#chunk3", "erro": None, "json_valido": False, "relacoes": []},
        {"chunk_id": "d5#chunk1", "erro": None, "relacoes": [rel("g", "REPLACES", "h"), rel("i", "REQUIRES", "j"),
                                                            rel("k", "INCOMPATIBLE_WITH", "l")]},
    ]


def gravar_entrada(tmp_path):
    ex = tmp_path / "rel.jsonl"
    ex.write_text("\n".join(json.dumps(r) for r in extracao()) + "\n", encoding="utf-8")
    trechos = [{"chunk_id": c, "doc_title": f"Doc {c[:2]}", "section_title": "Remarks", "section_type": "remarks",
                "text": f"Texto do [link](http://x) {c}.", "token_count": 5}
               for c in ("d1#chunk1", "d2#chunk0", "d3#chunk2", "d4#chunk3", "d5#chunk1")]
    tr = tmp_path / "trechos.jsonl"
    tr.write_text("\n".join(json.dumps(t) for t in trechos) + "\n", encoding="utf-8")
    return ex, tr, {t["chunk_id"]: t for t in trechos}


def test_populacao_usa_o_registro_mais_recente_sem_erro_em_ordem_fixa():
    itens = pr.relacoes_validas(extracao())
    assert [(i["chunk_id"], i["indice"], i["origem"]) for i in itens] == [
        ("d1#chunk1", 0, "a"), ("d1#chunk1", 1, "c"), ("d2#chunk0", 0, "e"),
        ("d5#chunk1", 0, "g"), ("d5#chunk1", 1, "i"), ("d5#chunk1", 2, "k")]
    assert [i["nome_normalizado"] for i in itens] == [False, False, True, False, False, False]
    with pytest.raises(SystemExit):
        pr.sortear(itens, n=7)


def test_amostra_e_fixa_e_a_planilha_so_tem_o_trecho_e_a_relacao(tmp_path):
    ex, tr, por_id = gravar_entrada(tmp_path)
    shas = []
    for _ in range(2):
        a = Namespace(extracao=ex, trechos=tr, saida=tmp_path / "p.csv", chave=tmp_path / "k.json", n=4, semente=42)
        assert pr.cmd_amostra(a) == 0
        shas.append(pr.sha_arquivo(a.saida))
    assert shas[0] == shas[1]
    linhas = pr.ler_planilha(tmp_path / "p.csv")
    chave = json.loads((tmp_path / "k.json").read_text(encoding="utf-8"))
    assert list(linhas[0]) == pr.COLUNAS and [l["id"] for l in linhas] == ["R01", "R02", "R03", "R04"]
    assert all(l["julgamento"] == "" and l["justificativa"] == "" for l in linhas)
    assert (chave["n"], chave["populacao"], chave["semente"]) == (4, 6, 42)
    assert chave["sha256_planilha"] == shas[0] and chave["sha256_extracao"] == pr.sha_arquivo(ex)
    for linha, item in zip(linhas, chave["itens"]):
        assert linha["trecho"] == texto_para_indice(por_id[item["chunk_id"]]).strip()
        assert "http" not in linha["trecho"] and (linha["origem"], linha["destino"]) == (item["origem"],
                                                                                        item["destino"])
    assert chave["itens"] == [dict(i, id=f"R{k:02d}") for k, i in
                              enumerate(pr.sortear(pr.relacoes_validas(extracao()), 4, 42), 1)]


def test_intervalo_de_wilson():
    lo, hi = pr.wilson(45, 60)
    assert round(lo, 4) == 0.6277 and round(hi, 4) == 0.8422
    assert pr.wilson(0, 10)[0] == pytest.approx(0.0) and pr.wilson(10, 10)[1] == pytest.approx(1.0)
    lo, hi = pr.wilson(30, 60)
    assert abs((lo + hi) / 2 - 0.5) < 1e-12


def test_julgamentos_do_juiz_sao_conferidos_copiados_e_calculados(tmp_path):
    ex, tr, _ = gravar_entrada(tmp_path)
    a = Namespace(extracao=ex, trechos=tr, saida=tmp_path / "p.csv", chave=tmp_path / "k.json", n=5, semente=42)
    pr.cmd_amostra(a)
    ids = [l["id"] for l in pr.ler_planilha(a.saida)]
    js = tmp_path / "j.json"
    copia = Namespace(planilha=a.saida, julgamentos=js, saida=tmp_path / "p_ia.csv")
    for ruim in ([{"id": i, "julgamento": "S", "justificativa": "x"} for i in reversed(ids)],
                 [{"id": i, "julgamento": "Talvez", "justificativa": "x"} for i in ids],
                 [{"id": i, "julgamento": "S", "justificativa": " "} for i in ids]):
        js.write_text(json.dumps(ruim), encoding="utf-8")
        with pytest.raises(SystemExit):
            pr.cmd_planilha_ia(copia)
    valores = ["S", "S", "P", "N", "S"]
    js.write_text(json.dumps([{"id": i, "julgamento": v, "justificativa": f"motivo {i}"}
                              for i, v in zip(ids, valores)]), encoding="utf-8")
    assert pr.cmd_planilha_ia(copia) == 0
    julgada = pr.ler_planilha(copia.saida)
    assert [l["julgamento"] for l in julgada] == valores and julgada[0]["justificativa"] == f"motivo {ids[0]}"
    assert pr.cmd_calcular(Namespace(planilha=copia.saida, chave=a.chave, saida=None)) == 0
    res = json.loads(copia.saida.with_suffix(".resultado.json").read_text(encoding="utf-8"))
    assert res["contagem"] == {"S": 3, "P": 1, "N": 1} and res["n"] == 5
    assert res["proporcao_S"] == 0.6 and res["proporcao_S_ou_P"] == 0.8
    assert list(res["ic95_S"]) == list(pr.wilson(3, 5))
    assert sum(sum(v.values()) for v in res["por_tipo"].values()) == 5
    linhas = pr.ler_planilha(copia.saida)
    linhas[0]["destino"] = "outro"
    pr.gravar_planilha(copia.saida, linhas)
    with pytest.raises(SystemExit):
        pr.cmd_calcular(Namespace(planilha=copia.saida, chave=a.chave, saida=None))
