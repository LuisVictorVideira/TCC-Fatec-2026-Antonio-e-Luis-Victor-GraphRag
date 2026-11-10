"""Testes da consulta ao grafo e das respostas: grafo, sementes, PageRank
personalizado, respostas do Ollama (com um servidor falso), planilha as
cegas e calculo.

Nenhum teste abre o Neo4j ou o Ollama de verdade.
"""
import csv
import json

import numpy as np
import pytest
import yaml

import avaliar_recuperacao as av
import chunking_e_mentions as chunking
import consulta_grafo as cg
import responder as rp


def grafo_exemplo():
    nos = {"Document": ["d1", "d2"], "Chunk": ["d1#chunk0", "d1#chunk1", "d2#chunk0"],
           "Concept": ["clustered index", "heap tables"], "TSqlObject": ["DBCC", "DBCC_TSQL"]}
    arestas = {
        "HAS_CHUNK": [("d1", "d1#chunk0"), ("d1", "d1#chunk1"), ("d2", "d2#chunk0")],
        "MENTIONS": [("d1#chunk0", "Concept", "clustered index", 2, 0),
                     ("d1#chunk1", "Concept", "heap tables", 1, 0),
                     ("d2#chunk0", "Concept", "heap tables", 0, 1),
                     ("d2#chunk0", "TSqlObject", "DBCC", 1, 0),
                     ("d2#chunk0", "TSqlObject", "DBCC_TSQL", 1, 0)],
        "HAS_KEYWORD": [("d1", "clustered index")],
        "DOCUMENTS": [("d2", "DBCC"), ("d2", "DBCC_TSQL")],
        "LINKS_TO": [("d1", "d2"), ("d2", "d1")],
    }
    return nos, arestas


def test_gemeos_viram_um_no_e_a_ablacao_os_separa():
    assert cg.chave_tsql("DBCC", True) == cg.chave_tsql("DBCC_TSQL", True)
    assert cg.chave_tsql("sp_help", True) == cg.chave_tsql("sp_help_TSQL", True)
    assert cg.chave_tsql("CREATE INDEX", True) == cg.chave_tsql("CREATE_INDEX_TSQL", True)
    assert cg.chave_tsql("DBCC", False) != cg.chave_tsql("DBCC_TSQL", False)
    nos, arestas = grafo_exemplo()
    g = cg.Grafo(nos, arestas)
    assert sum(k.startswith("T|") for k in g.chaves) == 1
    g2 = cg.Grafo(nos, arestas, fundir_gemeos=False)
    assert sum(k.startswith("T|") for k in g2.chaves) == 2


def test_arestas_sem_direcao_sem_repeticao_e_ablacoes():
    nos, arestas = grafo_exemplo()
    g = cg.Grafo(nos, arestas)
    assert g.n_por_tipo == {"HAS_CHUNK": 3, "MENTIONS": 4, "HAS_KEYWORD": 1,
                            "DOCUMENTS": 1, "LINKS_TO": 1}
    so = cg.Grafo(nos, arestas, so_mentions=True)
    assert set(so.n_por_tipo) == {"MENTIONS"}
    prosa = cg.Grafo(nos, arestas, sem_codigo=True)
    assert prosa.n_por_tipo["MENTIONS"] == 3
    assert g.trechos_por_entidade[g.indice["K|heap tables"]] == 2
    assert prosa.trechos_por_entidade[prosa.indice["K|heap tables"]] == 1


def test_ppr_bate_com_a_solucao_exata_e_soma_um():
    nos, arestas = grafo_exemplo()
    g = cg.Grafo(nos, arestas)
    r = np.zeros(g.n)
    r[g.indice["K|clustered index"]] = 1.0
    x = g.ppr(r, 0.5)
    assert x.sum() == pytest.approx(1.0)
    A = np.zeros((g.n, g.n))
    A[g.destino, g.origem] = 1.0
    P = A / np.where(g.grau == 0, 1, g.grau)
    exata = 0.5 * np.linalg.solve(np.eye(g.n) - 0.5 * P, r)
    assert np.allclose(x, exata, atol=1e-10)
    assert x[g.indice["C|d1#chunk0"]] > x[g.indice["C|d1#chunk1"]] > 0
    assert x[g.indice["C|d1#chunk0"]] > x[g.indice["C|d2#chunk0"]]


def test_ranking_so_de_trechos_e_empate_por_chunk_id():
    nos = {"Document": ["d"], "Chunk": ["d#b", "d#a"], "Concept": ["x conceito"], "TSqlObject": []}
    arestas = {"MENTIONS": [("d#a", "Concept", "x conceito", 1, 0),
                            ("d#b", "Concept", "x conceito", 1, 0)]}
    g = cg.Grafo(nos, arestas)
    r = np.zeros(g.n)
    r[g.indice["K|x conceito"]] = 1.0
    ranking = cg.ranquear(g, g.ppr(r), 10)
    assert [c for c, _ in ranking] == ["d#a", "d#b"]


def test_ablacao_ordena_pela_massa_dividida_pelo_grau():
    nos, arestas = grafo_exemplo()
    g = cg.Grafo(nos, arestas)
    r = np.zeros(g.n)
    r[g.indice["K|heap tables"]] = 1.0
    x = g.ppr(r)
    por_massa = dict(cg.ranquear(g, x, 10))
    por_grau = dict(cg.ranquear(g, x, 10, por_grau=True))
    assert set(por_massa) == set(por_grau) == {"d1#chunk0", "d1#chunk1", "d2#chunk0"}
    for cid, v in por_grau.items():
        i = g.indice["C|" + cid]
        assert por_massa[cid] == pytest.approx(x[i])
        assert v == pytest.approx(x[i] / g.grau[i])
    assert g.grau[g.indice["C|d2#chunk0"]] == 3


def test_sementes_pesam_pela_especificidade_e_o_bm25_soma_005():
    nos, arestas = grafo_exemplo()
    g = cg.Grafo(nos, arestas)
    A, _, _ = chunking.build_matcher(nos["Concept"], nos["TSqlObject"])
    texto = "Is a clustered index better than heap tables?"
    bm25 = [("d2#chunk0", 3.0), ("d1#chunk1", 1.0)]
    r, rel = cg.sementes_da_pergunta(texto, A, set(), g, bm25)
    assert rel["n_entidades"] == 2 and rel["n_trechos_bm25"] == 2
    assert rel["entidades"]["K|clustered index"] == pytest.approx(2 / 3)
    assert rel["entidades"]["K|heap tables"] == pytest.approx(1 / 3)
    assert r[g.indice["C|d2#chunk0"]] == pytest.approx(0.05 * 3 / 4)
    assert r.sum() == pytest.approx(1.05)


def test_ablacao_normaliza_o_bm25_pela_maxima():
    nos, arestas = grafo_exemplo()
    g = cg.Grafo(nos, arestas)
    A, _, _ = chunking.build_matcher(nos["Concept"], nos["TSqlObject"])
    bm25 = [("d2#chunk0", 3.0), ("d1#chunk1", 1.0)]
    r, rel = cg.sementes_da_pergunta("nothing here", A, set(), g, bm25, normalizacao="maxima")
    assert r[g.indice["C|d2#chunk0"]] == pytest.approx(0.05)
    assert r[g.indice["C|d1#chunk1"]] == pytest.approx(0.05 / 3)
    assert rel["massa_trechos_bm25"] == pytest.approx(0.05 * 4 / 3)
    _, rel_soma = cg.sementes_da_pergunta("nothing here", A, set(), g, bm25)
    assert rel_soma["massa_trechos_bm25"] == pytest.approx(0.05)
    with pytest.raises(ValueError):
        cg.sementes_da_pergunta("x", A, set(), g, bm25, normalizacao="media")


def test_padrao_cortado_nao_vira_semente_e_sem_entidade_fica_o_bm25():
    nos, arestas = grafo_exemplo()
    g = cg.Grafo(nos, arestas)
    A, _, _ = chunking.build_matcher(nos["Concept"], nos["TSqlObject"])
    cortadas = {("Concept", "heap tables")}
    _, rel = cg.sementes_da_pergunta("about heap tables", A, cortadas, g, [("d1#chunk1", 2.0)])
    assert rel["n_entidades"] == 0 and rel["n_trechos_bm25"] == 1
    r, _ = cg.sementes_da_pergunta("nothing here", A, set(), g, None)
    assert r.sum() == 0


def test_padroes_cortados_refazem_a_frequencia_do_chunking():
    A, A_cod, _ = chunking.build_matcher(["heap tables", "clustered index"], [])
    trechos = [{"chunk_id": f"d#chunk{i}", "text_matchable": "heap tables everywhere", "code": []}
               for i in range(60)]
    trechos += [{"chunk_id": f"e#chunk{i}", "text_matchable": "nada", "code": []} for i in range(40)]
    trechos.append({"chunk_id": "f#chunk0", "text_matchable": "a clustered index", "code": []})
    assert cg.padroes_cortados(trechos, A, A_cod) == {("Concept", "heap tables")}


def test_mencoes_refeitas_conferem_com_o_grafo():
    A, A_cod, _ = chunking.build_matcher(["clustered index"], ["sp_helpindex", "sp_helpindex_TSQL"])
    trechos = [
        {"chunk_id": "d#chunk0", "text_matchable": "a clustered index and a clustered index",
         "code": []},
        {"chunk_id": "d#chunk1", "text_matchable": "see the procedure",
         "code": [{"order": 0, "language": "sql", "content": "EXEC sp_helpindex 'T';"}]},
    ]
    _, mencoes = cg.refazer_mencoes(trechos, A, A_cod, df_max=1.0)
    assert ("d#chunk0", "Concept", "clustered index", 2, 0) in mencoes
    assert ("d#chunk1", "TSqlObject", "sp_helpindex", 0, 1) in mencoes
    assert ("d#chunk1", "TSqlObject", "sp_helpindex_TSQL", 0, 1) in mencoes
    grafo = [tuple(m) for m in sorted(mencoes)]
    conf = cg.conferir_mencoes(mencoes, grafo)
    assert conf["so_nos_trechos"] == conf["so_no_grafo"] == conf["contagens_diferentes"] == 0
    outro = [m for m in grafo if m[2] != "sp_helpindex_TSQL"]
    outro = [(a, r, b, 5 if b == "clustered index" else n, nc) for a, r, b, n, nc in outro]
    conf = cg.conferir_mencoes(mencoes, outro)
    assert conf["so_nos_trechos"] == 1 and conf["so_no_grafo"] == 0
    assert conf["contagens_diferentes"] == 1


def test_exportacao_canonica_ida_e_volta(tmp_path):
    nos, arestas = grafo_exemplo()
    linhas = cg.linhas_do_grafo(nos, arestas)
    invertido = {k: list(reversed(v)) for k, v in arestas.items()}
    assert cg.linhas_do_grafo(nos, invertido) == linhas
    p = tmp_path / "g.jsonl"
    p.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    nos2, arestas2, digital = cg.ler_grafo(p)
    assert digital == cg.impressao_digital_do_grafo(linhas)
    assert cg.linhas_do_grafo(nos2, arestas2) == linhas


def trechos_ficticios(n=8, palavras=1000):
    return {f"d#chunk{i}": {"chunk_id": f"d#chunk{i}", "doc_title": f"Doc {i}",
                            "section_title": "Remarks", "text": "palavra " * palavras,
                            "text_matchable": "", "token_count": 0, "code": []}
            for i in range(n)}


def test_contexto_ate_5_trechos_e_6000_tokens():
    por_id = trechos_ficticios(8, 1000)
    ranking = list(por_id)
    assert rp.escolher_trechos(ranking, por_id) == ranking[:4]
    curtos = trechos_ficticios(8, 10)
    assert len(rp.escolher_trechos(list(curtos), curtos)) == 5
    grande = trechos_ficticios(1, 5000)
    assert rp.escolher_trechos(list(grande), grande) == ["d#chunk0"]


def test_prompts_das_tres_situacoes():
    t = trechos_ficticios(1, 3)["d#chunk0"]
    sem = rp.montar_prompt("What is a heap?", None)
    com = rp.montar_prompt("What is a heap?", [t])
    vazio = rp.montar_prompt("What is a heap?", [])
    assert "excerpts" not in sem and "What is a heap?" in sem
    assert "[1] Doc 0\nRemarks\n" in com and "only the documentation excerpts" in com
    assert "(no excerpts were retrieved)" in vazio


def preparar(tmp_path, monkeypatch, respostas_falsas):
    por_id = trechos_ficticios(8, 1000)
    trechos = tmp_path / "t.jsonl"
    trechos.write_text("\n".join(json.dumps(t) for t in por_id.values()) + "\n", encoding="utf-8")
    perguntas = tmp_path / "p.yaml"
    perguntas.write_text(yaml.safe_dump({"perguntas": [
        {"id": "A01", "pergunta_en": "Q one?", "pergunta_pt": "P um?", "verificado": "sim",
         "trivial_lexical": "sim", "resposta_resumo": "R1",
         "componentes": [{"nome": "c1", "essencial": True, "trechos": ["d#chunk0"]}]},
        {"id": "B01", "pergunta_en": "Q two?", "pergunta_pt": "P dois?", "verificado": "sim",
         "trivial_lexical": "nao", "resposta_resumo": "R2",
         "componentes": [{"nome": "c2", "essencial": True, "trechos": ["d#chunk1"]},
                         {"nome": "c3", "essencial": True, "trechos": ["d#chunk2"]}]},
    ]}, allow_unicode=True), encoding="utf-8")
    run = tmp_path / "x.run"
    run.write_text("".join(f"{q} Q0 d#chunk{i} {i + 1} {9 - i}.0 x\n"
                           for q in ("A01", "B01") for i in range(8)), encoding="utf-8")
    chamadas = []

    def falso(prompt, modelo, opcoes):
        chamadas.append(prompt)
        return dict(respostas_falsas(prompt, len(chamadas)), _segundos=0.1)

    monkeypatch.setattr(rp, "chamar_ollama", falso)
    monkeypatch.setattr(rp, "identidade_do_modelo", lambda m: {"modelo": m, "digest_pesos": "sha256-x"})
    return trechos, perguntas, run, chamadas


class Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_responder_grava_retoma_e_recusa_outros_pesos(tmp_path, monkeypatch):
    trechos, perguntas, run, chamadas = preparar(
        tmp_path, monkeypatch, lambda p, n: {"response": f"resp {n}", "prompt_eval_count": 500})
    saida = tmp_path / "r.jsonl"
    a = Args(perguntas=perguntas, trechos=trechos, run=[f"grafo={run}", f"bm25={run}"],
             saida=saida, modelo="llama3:8b", refazer=False)
    assert rp.cmd_responder(a) == 0
    regs = rp.ler_jsonl(saida)
    assert len(regs) == 6 and {r["condicao"] for r in regs} == {"sem_contexto", "grafo", "bm25"}
    assert len(chamadas) == 6
    assert rp.cmd_responder(a) == 0 and len(chamadas) == 6
    monkeypatch.setattr(rp, "identidade_do_modelo", lambda m: {"digest_pesos": "sha256-y"})
    with pytest.raises(SystemExit):
        rp.cmd_responder(a)


def test_prompt_cortado_tira_o_ultimo_trecho(tmp_path, monkeypatch):
    def resp(prompt, n):
        cortado = prompt.count("\n[") >= 4
        return {"response": "ok", "prompt_eval_count": 8000 if cortado else 3000}
    trechos, perguntas, run, chamadas = preparar(tmp_path, monkeypatch, resp)
    por_id = {t["chunk_id"]: t for t in rp.carregar_trechos(trechos)}
    reg = rp.responder_uma("A01", "Q one?", "grafo", list(por_id), por_id, "m", rp.OPCOES)
    assert reg["tentativas"] == 2 and len(reg["trechos"]) == 3 and not reg["possivel_truncamento"]


def test_tamanho_do_prompt_nao_depende_do_cache():
    resp = {"prompt_eval_count": 300, "eval_count": 100, "context": list(range(7900))}
    assert rp.tokens_do_prompt(resp) == 7800
    assert rp.possivel_truncamento(resp, rp.OPCOES)
    assert not rp.possivel_truncamento({"prompt_eval_count": 300}, rp.OPCOES)
    assert rp.tokens_do_prompt({"prompt_eval_count": 300}) is None
    assert not rp.possivel_truncamento({}, rp.OPCOES)


def test_planilha_as_cegas_e_calculo(tmp_path, monkeypatch):
    trechos, perguntas, run, _ = preparar(
        tmp_path, monkeypatch, lambda p, n: {"response": f"resp {n}", "prompt_eval_count": 500})
    saida = tmp_path / "r.jsonl"
    rp.cmd_responder(Args(perguntas=perguntas, trechos=trechos,
                          run=[f"grafo={run}", f"bm25={run}"], saida=saida,
                          modelo="llama3:8b", refazer=False))
    planilha = tmp_path / "j.csv"
    assert rp.cmd_planilha(Args(perguntas=perguntas, respostas=saida, saida=planilha)) == 0
    texto = planilha.read_text(encoding="utf-8-sig")
    assert "sem_contexto" not in texto and "grafo" not in texto and "bm25" not in texto
    chave = json.loads((tmp_path / "j_chave.json").read_text(encoding="utf-8"))["codigos"]
    with planilha.open(encoding="utf-8-sig", newline="") as fh:
        linhas = list(csv.DictReader(fh, delimiter=";"))
    assert len(linhas) == 6 and len({l["codigo"] for l in linhas}) == 6
    nota = {"grafo": "S", "bm25": "P", "sem_contexto": "N"}
    for l in linhas:
        cond = chave[l["codigo"]]["condicao"]
        l["julgamento_1"] = nota[cond]
        if l["pergunta"] == "B01":
            l["julgamento_2"] = "S"
        l["afirma_algo_errado"] = "S" if cond == "sem_contexto" else "N"
    with planilha.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(linhas[0]), delimiter=";")
        w.writeheader()
        w.writerows(linhas)
    assert rp.cmd_calcular(Args(perguntas=perguntas, planilha=planilha, chave=None)) == 0
    res = json.loads((tmp_path / "j_resultado.json").read_text(encoding="utf-8"))
    assert res["por_condicao"]["grafo|todas"]["nota_media"] == pytest.approx(1.0)
    assert res["por_condicao"]["bm25|todas"]["nota_media"] == pytest.approx((0.5 + 0.75) / 2)
    assert res["por_condicao"]["sem_contexto|sem_triviais"]["n"] == 1
    assert res["por_condicao"]["sem_contexto|todas"]["afirma_algo_errado"] == 2


def test_resposta_em_lista_nao_vira_formula_no_excel():
    assert rp.texto_de_celula("- Use DROP INDEX") == "\u200b- Use DROP INDEX"
    assert rp.texto_de_celula("=SUM(A1)").startswith("\u200b=")
    assert rp.texto_de_celula("Use DROP INDEX") == "Use DROP INDEX"
    assert rp.texto_de_celula("") == ""


def test_planilha_salva_pelo_excel_em_cp1252_com_virgula(tmp_path):
    p = tmp_path / "j.csv"
    linhas = [["codigo", "pergunta", "julgamento_1", "observacao"],
              ["R101", "A01", "NÃO", "linha um\nlinha dois, com vírgula"],
              ["R102", "B01", "P", ""]]
    with p.open("w", encoding="cp1252", newline="") as fh:
        csv.writer(fh, delimiter=",").writerows(linhas)
    lidas = rp.ler_planilha(p)
    assert [l["codigo"] for l in lidas] == ["R101", "R102"]
    assert lidas[0]["julgamento_1"] == "NÃO" and "vírgula" in lidas[0]["observacao"]
    assert rp.nota_da_linha(lidas[0], 1) == 0.0 and rp.nota_da_linha(lidas[1], 1) == 0.5


def test_julgamento_invalido_impede_o_calculo(tmp_path, monkeypatch):
    trechos, perguntas, run, _ = preparar(
        tmp_path, monkeypatch, lambda p, n: {"response": "x", "prompt_eval_count": 500})
    saida = tmp_path / "r.jsonl"
    rp.cmd_responder(Args(perguntas=perguntas, trechos=trechos, run=[f"grafo={run}"],
                          saida=saida, modelo="m", refazer=False))
    planilha = tmp_path / "j.csv"
    rp.cmd_planilha(Args(perguntas=perguntas, respostas=saida, saida=planilha))
    assert rp.cmd_calcular(Args(perguntas=perguntas, planilha=planilha, chave=None)) == 1


def test_holm():
    assert av.holm([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])
    assert av.holm([0.2]) == pytest.approx([0.2])
    assert av.holm([0.9, 0.8]) == pytest.approx([1.0, 1.0])


def test_comparacao_nos_dois_recortes():
    gab = {"A01": {"trivial": True}, "B01": {"trivial": False}, "B02": {"trivial": False}}
    res = {"grafo": {q: {"R@5": v} for q, v in zip(gab, (1.0, 1.0, 0.5))},
           "bm25": {q: {"R@5": v} for q, v in zip(gab, (1.0, 0.5, 0.5))}}
    comps = av.comparacoes(res, gab, "grafo", ["bm25"], "R@5")
    principal = [c for c in comps if c["recorte"] == "sem_triviais"][0]
    assert principal["n"] == 2 and principal["diferenca_media"] == pytest.approx(0.25)
    assert [c["recorte"] for c in comps] == ["sem_triviais", "todas"]
