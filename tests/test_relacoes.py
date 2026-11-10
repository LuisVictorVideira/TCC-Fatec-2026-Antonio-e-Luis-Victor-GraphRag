"""Testes da extracao de relacoes: candidatos, prompt, leitura e validacao da
resposta livre, chamadas sem esquema e com o modelo mantido na memoria,
carga do modelo antes da rodada, extracao com um Ollama falso, guarda de
tempo, consolidacao e as arestas novas no grafo da consulta.

Nenhum teste chama o Ollama de verdade.
"""
import json
from argparse import Namespace

import numpy as np
import pytest

import consulta_grafo as cg
import extrair_relacoes as rl

AQUECER_DE_VERDADE = rl.aquecer_modelo


@pytest.fixture(autouse=True)
def aquecimentos(monkeypatch):
    """Nenhum teste carrega modelo: a carga vira um registro das chamadas."""
    chamadas = []

    def falso(modelo, opcoes):
        chamadas.append(modelo)
        return {"segundos": 1.0, "carga_s": 0.5, "tokens_por_segundo": 30.0, "na_gpu_pct": 100}
    monkeypatch.setattr(rl, "aquecer_modelo", falso)
    return chamadas


def grafo_pequeno():
    nos = {"Document": ["d1"],
           "Chunk": ["d1#chunk0", "d1#chunk1", "d1#chunk2", "d1#chunk3"],
           "Concept": ["clustered indexes", "heaps", "DBCC"],
           "TSqlObject": ["DBCC", "DBCC_TSQL", "CREATE_INDEX_TSQL", "CREATE INDEX"]}
    arestas = {
        "HAS_CHUNK": [("d1", c) for c in nos["Chunk"]],
        "MENTIONS": [("d1#chunk0", "Concept", "clustered indexes", 1, 0),
                     ("d1#chunk0", "Concept", "heaps", 1, 0),
                     ("d1#chunk0", "TSqlObject", "CREATE INDEX", 1, 0),
                     ("d1#chunk0", "TSqlObject", "CREATE_INDEX_TSQL", 1, 0),
                     ("d1#chunk1", "Concept", "DBCC", 1, 0),
                     ("d1#chunk1", "TSqlObject", "DBCC", 1, 0),
                     ("d1#chunk1", "TSqlObject", "DBCC_TSQL", 1, 0),
                     ("d1#chunk2", "Concept", "heaps", 1, 0)],
    }
    return nos, arestas


def trechos_pequenos():
    secoes = {"d1#chunk0": "remarks", "d1#chunk1": "limitations", "d1#chunk2": "remarks",
              "d1#chunk3": "examples"}
    return [{"chunk_id": c, "doc_title": "Doc", "section_title": s.title(), "section_type": s,
             "text": f"Texto do {c}.", "text_matchable": f"Texto do {c}.", "token_count": 4, "code": []}
            for c, s in secoes.items()]


def gravar_entrada(tmp_path, nos, arestas, trechos):
    g = tmp_path / "grafo.jsonl"
    g.write_text("\n".join(cg.linhas_do_grafo(nos, arestas)) + "\n", encoding="utf-8")
    t = tmp_path / "trechos.jsonl"
    t.write_text("\n".join(json.dumps(x) for x in trechos) + "\n", encoding="utf-8")
    return g, t


def nomes_do_prompt(prompt: str) -> list[str]:
    bloco = prompt.rsplit("ENTITIES:\n", 1)[1].split("\n\nANSWER:")[0]
    return [l[2:] for l in bloco.splitlines() if l.startswith("- ")]


def test_candidatos_juntam_gemeos_e_separam_nome_repetido():
    _, arestas = grafo_pequeno()
    cands = rl.candidatos_por_trecho(arestas["MENTIONS"])
    c0 = {c["nome"]: c for c in cands["d1#chunk0"]}
    assert set(c0) == {"clustered indexes", "heaps", "CREATE INDEX"}
    assert c0["CREATE INDEX"]["nos"] == ["CREATE INDEX", "CREATE_INDEX_TSQL"]
    c1 = {c["nome"]: c for c in cands["d1#chunk1"]}
    assert set(c1) == {"DBCC", "DBCC (T-SQL)"}
    assert c1["DBCC"]["rotulo"] == "Concept" and c1["DBCC (T-SQL)"]["nos"] == ["DBCC", "DBCC_TSQL"]
    assert len(cands["d1#chunk2"]) == 1


def test_prompt_traz_o_trecho_e_so_os_candidatos():
    _, arestas = grafo_pequeno()
    cands = rl.candidatos_por_trecho(arestas["MENTIONS"])["d1#chunk0"]
    prompt = rl.montar_prompt(trechos_pequenos()[0], cands)
    assert prompt.startswith(rl.INSTRUCOES)
    assert "Texto do d1#chunk0." in prompt
    assert prompt.rstrip().endswith("- clustered indexes\n- CREATE INDEX\n- heaps\n\nANSWER:")
    assert nomes_do_prompt(prompt) == ["clustered indexes", "CREATE INDEX", "heaps"]


def test_exemplos_do_prompt_nao_tocam_os_temas_das_perguntas():
    texto = rl.INSTRUCOES.lower()
    for termo in ("index", "partition", "columnstore", "truncate", "filestream", "change data capture",
                  "memory-optimized", "rowcount", "fill factor", "view"):
        assert termo not in texto


def test_validacao_le_a_resposta_livre_e_conta_cada_motivo():
    _, arestas = grafo_pequeno()
    cands = rl.candidatos_por_trecho(arestas["MENTIONS"])["d1#chunk0"]
    itens = [{"source": "CREATE INDEX", "type": "affects", "target": "heaps"},
             {"source": "CREATE INDEX", "type": "AFFECTS", "target": "heaps"},
             {"source": "Heaps", "type": "PART_OF", "target": "clustered indexes"},
             {"source": "CREATE INDEX", "type": "REQUIRES", "target": "ALTER permission"},
             {"source": "heaps", "type": "CAUSES", "target": "clustered indexes"},
             {"source": "heaps", "type": "PART_OF", "target": "HEAPS"},
             {"source": "heaps", "type": "PART_OF"}]
    resposta = "Here is the answer:\n```json\n" + json.dumps({"relations": itens}) + "\n```\nDone."
    v = rl.validar(resposta, cands)
    assert v["json_valido"] and len(v["relacoes"]) == 2
    r1, r2 = v["relacoes"]
    assert (r1["origem"], r1["tipo"], r1["destino"]) == ("CREATE INDEX", "AFFECTS", "heaps")
    assert r1["origem_nos"] == ["CREATE INDEX", "CREATE_INDEX_TSQL"] and not r1["nome_normalizado"]
    assert (r2["origem"], r2["destino"], r2["nome_normalizado"]) == ("heaps", "clustered indexes", True)
    assert sorted(d["motivo"] for d in v["descartadas"]) == [
        "entidade_fora_da_lista", "formato", "origem_igual_ao_destino", "repetida", "tipo_invalido"]
    assert rl.validar('{"relations": [', cands)["json_valido"] is False
    assert rl.validar("No relations here.", cands)["json_valido"] is False
    assert rl.validar('{"outra": []}', cands)["json_valido"] is False
    assert rl.validar('{"relations": []} {"relations": [1]}', cands)["relacoes"] == []
    muitas = {"relations": [{"source": "heaps", "type": "PART_OF", "target": "clustered indexes"}] * 25}
    v = rl.validar(json.dumps(muitas), cands)
    assert {d["motivo"] for d in v["descartadas"]} == {"repetida", "acima_do_maximo"}


def test_chamadas_ao_ollama_vao_sem_esquema_e_mantem_o_modelo_carregado(monkeypatch):
    enviados, limites = [], []

    class Resp:
        def __init__(self, dados):
            self.dados = dados

        def raise_for_status(self):
            pass

        def json(self):
            return self.dados

    gerado = {"response": "1 2 3", "done_reason": "stop", "load_duration": 148_100_000_000,
              "eval_count": 55, "eval_duration": 2_000_000_000}
    em_memoria = {"models": [{"name": "llama3:8b", "model": "llama3:8b", "size": 5_600, "size_vram": 5_600,
                              "context_length": 8192}]}
    import requests
    monkeypatch.setattr(requests, "post", lambda url, timeout, json: (
        enviados.append(json), limites.append(timeout), Resp(gerado))[-1])
    monkeypatch.setattr(requests, "get", lambda url, timeout: Resp(em_memoria))
    r = rl.chamar_ollama("prompt", "llama3:8b", rl.OPCOES)
    assert r["response"] == "1 2 3" and "_segundos" in r and limites[0] == rl.TIMEOUT
    assert "format" not in enviados[0] and enviados[0]["options"]["temperature"] == 0
    assert enviados[0]["keep_alive"] == rl.KEEP_ALIVE == "30m"
    assert rl.tokens_por_segundo(r) == 27.5
    aq = AQUECER_DE_VERDADE("llama3:8b", rl.OPCOES)
    assert enviados[1]["prompt"] == rl.PROMPT_AQUECIMENTO and enviados[1]["options"] == rl.OPCOES
    assert limites[1] == rl.TIMEOUT_CARGA > rl.TIMEOUT and enviados[1]["keep_alive"] == "30m"
    assert (aq["carga_s"], aq["tokens_por_segundo"], aq["na_gpu_pct"], aq["contexto"]) == (148.1, 27.5, 100, 8192)
    assert rl.descrever_aquecimento(aq).startswith("pronto em ")


def falso_ollama(chamadas, segundos=1.5):
    def falso(prompt, modelo, opcoes):
        nomes = nomes_do_prompt(prompt)
        chamadas.append(nomes)
        assert opcoes["temperature"] == 0 and opcoes["seed"] == 42
        return {"response": "Answer: " + json.dumps({"relations": [
            {"source": nomes[0], "type": "REQUIRES", "target": nomes[-1]}]}),
            "done_reason": "stop", "prompt_eval_count": 900, "eval_count": 30,
            "eval_duration": 1_000_000_000, "_segundos": segundos}
    return falso


def test_extracao_grava_retoma_faz_o_piloto_e_recusa_outros_pesos(tmp_path, monkeypatch, aquecimentos):
    g, t = gravar_entrada(tmp_path, *grafo_pequeno(), trechos_pequenos())
    chamadas = []
    monkeypatch.setattr(rl, "chamar_ollama", falso_ollama(chamadas))
    monkeypatch.setattr(rl, "identidade_do_modelo", lambda m: {"digest_pesos": "sha256-x"})
    saida = tmp_path / "rel.jsonl"
    a = Namespace(grafo=g, trechos=t, saida=saida, limite=1, modelo="llama3:8b", refazer=False)
    assert rl.cmd_extrair(a) == 0 and len(chamadas) == 1
    a.limite = None
    assert rl.cmd_extrair(a) == 0 and len(chamadas) == 2
    assert rl.cmd_extrair(a) == 0 and len(chamadas) == 2
    assert len(aquecimentos) == 2
    regs = rl.ler_jsonl(saida)
    assert sorted(r["chunk_id"] for r in regs) == ["d1#chunk0", "d1#chunk1"]
    assert {r["modo_saida"] for r in regs} == {"livre"}
    man = json.loads(saida.with_suffix(".manifesto.json").read_text(encoding="utf-8"))
    assert (man["n_no_escopo"], man["n_elegiveis"], man["n_um_candidato"]) == (3, 2, 1)
    assert man["modo_saida"] == "livre" and man["parou_pela_guarda"] is None
    assert man["keep_alive"] == "30m" and man["aquecimento"] is None and man["recargas"] == 0
    assert regs[0]["tokens_por_segundo"] == 30.0 and "load_duration" in regs[0]
    monkeypatch.setattr(rl, "identidade_do_modelo", lambda m: {"digest_pesos": "sha256-y"})
    with pytest.raises(SystemExit):
        rl.cmd_extrair(a)
    assert rl.cmd_resumo(Namespace(extracao=saida, saida=None)) == 0
    arestas_llm = rl.ler_jsonl(tmp_path / "rel.arestas.jsonl")
    assert len(arestas_llm) == 2 and all(x["tipo"] == "REQUIRES" for x in arestas_llm)
    resumo = json.loads((tmp_path / "rel.resumo.json").read_text(encoding="utf-8"))
    assert resumo["relacoes_validas"] == 2 and resumo["json_invalido"] == 0
    assert resumo["modos_de_saida"] == ["livre"] and resumo["tokens_por_segundo_mediana"] == 30.0


def test_arquivo_de_outro_modo_de_saida_nao_se_mistura(tmp_path, monkeypatch):
    g, t = gravar_entrada(tmp_path, *grafo_pequeno(), trechos_pequenos())
    saida = tmp_path / "rel.jsonl"
    antigo = {"chunk_id": "d1#chunk0", "erro": None, "relacoes": [], "digest_pesos": "sha256-x",
              "sha_instrucoes": rl.sha_texto(rl.INSTRUCOES)}
    saida.write_text(json.dumps(antigo) + "\n", encoding="utf-8")
    chamadas = []
    monkeypatch.setattr(rl, "chamar_ollama", falso_ollama(chamadas))
    monkeypatch.setattr(rl, "identidade_do_modelo", lambda m: {"digest_pesos": "sha256-x"})
    a = Namespace(grafo=g, trechos=t, saida=saida, limite=None, modelo="llama3:8b", refazer=False)
    with pytest.raises(SystemExit):
        rl.cmd_extrair(a)
    a.refazer = True
    assert rl.cmd_extrair(a) == 0 and len(chamadas) == 2


def test_guarda_de_tempo_para_a_rodada_lenta(tmp_path, monkeypatch, aquecimentos):
    nos = {"Document": ["d"], "Chunk": [f"d#chunk{i}" for i in range(12)],
           "Concept": ["alpha", "beta"], "TSqlObject": []}
    arestas = {"HAS_CHUNK": [("d", c) for c in nos["Chunk"]],
               "MENTIONS": [(c, "Concept", e, 1, 0) for c in nos["Chunk"] for e in ("alpha", "beta")]}
    trechos = [{"chunk_id": c, "doc_title": "D", "section_title": "Remarks", "section_type": "remarks",
                "text": "x", "text_matchable": "x", "token_count": 1, "code": []} for c in nos["Chunk"]]
    g, t = gravar_entrada(tmp_path, nos, arestas, trechos)
    monkeypatch.setattr(rl, "identidade_do_modelo", lambda m: {"digest_pesos": "sha256-x"})

    def rodada(tempos):
        """Uma rodada nova com um Ollama falso; None no lugar do tempo e uma chamada com erro."""
        chamadas, rapido = [], falso_ollama([], segundos=1.0)

        def falso(prompt, modelo, opcoes):
            s = tempos[len(chamadas)] if len(chamadas) < len(tempos) else 1.0
            chamadas.append(s)
            if s is None:
                raise TimeoutError("Read timed out")
            return dict(rapido(prompt, modelo, opcoes), _segundos=s)
        monkeypatch.setattr(rl, "chamar_ollama", falso)
        saida = tmp_path / "rel.jsonl"
        a = Namespace(grafo=g, trechos=t, saida=saida, limite=None, modelo="llama3:8b", refazer=True)
        aquecimentos.clear()
        codigo = rl.cmd_extrair(a)
        man = json.loads(saida.with_suffix(".manifesto.json").read_text(encoding="utf-8"))
        assert man["recargas"] == len(aquecimentos) - 1
        return codigo, len(chamadas), len(rl.ler_jsonl(saida)), man["parou_pela_guarda"]

    codigo, n, gravados, motivo = rodada([100.0] * 12)
    assert (codigo, n, gravados) == (3, 3, 3) and motivo.startswith("3 das 3 primeiras chamadas")
    codigo, n, gravados, motivo = rodada([None, 2.0, None, None])
    assert (codigo, n, gravados) == (3, 4, 4) and motivo.startswith("3 das 4 primeiras chamadas")
    assert len(aquecimentos) == 3
    codigo, n, gravados, motivo = rodada([100.0, 100.0, 2.0, 2.0, 2.0, 100.0, 100.0, 100.0])
    assert (codigo, n, gravados, motivo) == (0, 12, 12, None)
    codigo, n, gravados, motivo = rodada([2.0] * 5 + [None] * 7)
    assert (codigo, n, gravados, motivo) == (3, 10, 10, "5 chamadas seguidas deram erro")
    assert len(aquecimentos) == 5


def test_ordem_de_extracao_e_sorteada_e_fixa():
    ids = [f"d#chunk{i}" for i in range(40)]
    o = rl.ordem_de_extracao(ids)
    assert o == rl.ordem_de_extracao(list(reversed(ids))) and o != sorted(ids) and sorted(o) == sorted(ids)


def test_consolidacao_junta_a_mesma_relacao_de_trechos_diferentes():
    rel = {"origem": "CREATE INDEX", "origem_rotulo": "TSqlObject", "origem_nos": ["CREATE INDEX"],
           "tipo": "AFFECTS", "destino": "heaps", "destino_rotulo": "Concept", "destino_nos": ["heaps"]}
    regs = [{"chunk_id": "b", "relacoes": [rel]}, {"chunk_id": "a", "relacoes": [rel]},
            {"chunk_id": "c", "erro": "Timeout", "relacoes": [rel]}]
    arestas = rl.consolidar(regs)
    assert len(arestas) == 1 and arestas[0]["trechos"] == ["a", "b"]


def test_relacoes_viram_arestas_entre_entidades_no_grafo_da_consulta():
    nos, arestas = grafo_pequeno()
    base = cg.Grafo(nos, arestas)
    rels = [{"origem_rotulo": "TSqlObject", "origem_nos": ["CREATE INDEX", "CREATE_INDEX_TSQL"],
             "destino_rotulo": "Concept", "destino_nos": ["heaps"]},
            {"origem_rotulo": "Concept", "origem_nos": ["heaps"],
             "destino_rotulo": "TSqlObject", "destino_nos": ["CREATE_INDEX_TSQL"]}]
    com = cg.Grafo(nos, arestas, relacoes=rels)
    assert com.n_arestas == base.n_arestas + 1 and com.n_por_tipo["RELACAO_LLM"] == 1
    assert "RELACAO_LLM" not in base.n_por_tipo
    i = com.indice[cg.chave_tsql("CREATE INDEX", True)]
    j = com.indice["K|heaps"]
    assert com.grau[i] == base.grau[i] + 1 and com.grau[j] == base.grau[j] + 1
    sem_fusao = cg.Grafo(nos, arestas, fundir_gemeos=False, relacoes=rels)
    assert sem_fusao.n_por_tipo["RELACAO_LLM"] == 2
    x = com.ppr(np.eye(com.n)[j])
    assert x.sum() == pytest.approx(1.0)
