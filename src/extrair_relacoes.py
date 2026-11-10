#!/usr/bin/env python3
r"""
Relacoes entre entidades propostas pelo llama3:8b (analise secundaria).

O modelo le cada trecho das secoes Remarks e Limitations que menciona ao
menos duas entidades e diz quais relacoes o trecho afirma entre elas. As
entidades candidatas sao as MENTIONS do trecho no grafo, depois do corte
por frequencia, e os tipos de relacao sao cinco, fixados antes da extracao:

    REQUIRES           a origem precisa do destino para funcionar, rodar ou
                       ser permitida
    AFFECTS            a origem altera, reconstroi, remove, bloqueia ou
                       invalida o destino
    PART_OF            a origem e componente, elemento ou opcao do destino
    INCOMPATIBLE_WITH  a origem nao pode ser usada com o destino, ou nao e
                       suportada com ele
    REPLACES           a origem deve ser usada no lugar do destino, ou o
                       destino esta obsoleto em favor da origem

O modelo responde em texto livre, e o formato JSON vem das instrucoes e dos
exemplos do prompt: restringir a saida por esquema deixava cada chamada
lenta demais. A resposta e conferida: entidade fora da lista do trecho, tipo
invalido, origem igual ao destino e repeticao sao descartados e contados.

O modelo e carregado antes da rodada, com tempo limite folgado, e fica na
memoria entre as chamadas. A rodada para sozinha se 3 das 5 primeiras
chamadas passarem de 60 segundos ou se houver 5 erros seguidos.

Subcomandos:
    extrair  chama o Ollama trecho a trecho, numa ordem sorteada (semente
             42), gravando a cada trecho e retomando de onde parou;
             --limite 50 faz um piloto com os 50 primeiros da mesma ordem
    resumo   mede a extracao e grava as arestas consolidadas, uma por
             (origem, tipo, destino), que o consulta_grafo.py le com
             --relacoes

Uso:
    python src/extrair_relacoes.py extrair --grafo outputs/grafo.jsonl \
        --trechos outputs/trechos.jsonl --saida outputs/relacoes/relacoes_llm.jsonl
    python src/extrair_relacoes.py resumo --extracao outputs/relacoes/relacoes_llm.jsonl
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from baselines import (carregar_trechos, identidade_do_modelo, impressao_digital,
                          texto_para_indice, url_ollama)
from consulta_grafo import ler_grafo
import chunking_e_mentions as chunking

MODELO = "llama3:8b"
OPCOES = {"temperature": 0, "seed": 42, "top_k": 1, "top_p": 1,
          "num_ctx": 8192, "num_predict": 512}
MODO_SAIDA = "livre"  # JSON pedido pelas instrucoes do prompt, sem esquema
SECOES = ("remarks", "limitations")
TIPOS = ("REQUIRES", "AFFECTS", "PART_OF", "INCOMPATIBLE_WITH", "REPLACES")
MAX_RELACOES = 20
SEMENTE_ORDEM = 42
TIMEOUT = 120  # segundos por chamada
TIMEOUT_CARGA = 900  # segundos para carregar o modelo antes da rodada
KEEP_ALIVE = "30m"  # o Ollama mantem o modelo na memoria entre as chamadas
PROMPT_AQUECIMENTO = "Count from 1 to 20, separated by spaces."
GUARDA_CHAMADAS = 5  # primeiras chamadas de cada rodada olhadas pela guarda de tempo
GUARDA_SEGUNDOS = 60  # mediana maxima dessas chamadas; erro conta como chamada lenta
GUARDA_ERROS_SEGUIDOS = 5  # erros seguidos, em qualquer ponto da rodada, que param a rodada

INSTRUCOES = """You extract relations between SQL Server entities from one documentation excerpt.

Relation types (direction: source -> target):
- REQUIRES: the source needs the target to work, to run or to be allowed (a permission, a setting, a feature or a condition).
- AFFECTS: the source changes, rebuilds, removes, locks or invalidates the target.
- PART_OF: the source is a component, element or option of the target.
- INCOMPATIBLE_WITH: the source cannot be used with the target, or is not supported with it.
- REPLACES: the source should be used instead of the target, or the target is deprecated in favor of the source.

Rules:
- Use only entity names from the ENTITIES list, copied exactly.
- Extract only relations that the excerpt states. Do not use outside knowledge.
- The source and the target must be different entities.
- If the excerpt states none of these relations, return an empty list.

Answer with JSON only, in this form: {"relations": [{"source": "...", "type": "...", "target": "..."}]}

Examples:

EXCERPT: BACKUP DATABASE with the ENCRYPTION option requires a certificate or an asymmetric key.
ENTITIES:
- BACKUP DATABASE
- backup encryption
- certificates
- asymmetric keys
ANSWER: {"relations": [{"source": "BACKUP DATABASE", "type": "REQUIRES", "target": "certificates"}, {"source": "BACKUP DATABASE", "type": "REQUIRES", "target": "asymmetric keys"}]}

EXCERPT: sp_addlinkedserver can't be executed within a user-defined transaction.
ENTITIES:
- sp_addlinkedserver
- user-defined transactions
- linked servers
ANSWER: {"relations": [{"source": "sp_addlinkedserver", "type": "INCOMPATIBLE_WITH", "target": "user-defined transactions"}]}

EXCERPT: SQL Trace is deprecated. Use Extended Events instead.
ENTITIES:
- SQL Trace
- Extended Events
ANSWER: {"relations": [{"source": "Extended Events", "type": "REPLACES", "target": "SQL Trace"}]}

EXCERPT: Turning on the READ_COMMITTED_SNAPSHOT option changes the behavior of the READ COMMITTED isolation level.
ENTITIES:
- READ_COMMITTED_SNAPSHOT
- READ COMMITTED isolation level
- row versioning
ANSWER: {"relations": [{"source": "READ_COMMITTED_SNAPSHOT", "type": "AFFECTS", "target": "READ COMMITTED isolation level"}]}

EXCERPT: Each availability group contains one or more availability databases.
ENTITIES:
- availability groups
- availability databases
ANSWER: {"relations": [{"source": "availability databases", "type": "PART_OF", "target": "availability groups"}]}

EXCERPT: The following example returns the name of the current database.
ENTITIES:
- DB_NAME
- databases
ANSWER: {"relations": []}

Now the real excerpt."""

TEMPLATE = """{instrucoes}

EXCERPT:
{trecho}

ENTITIES:
{entidades}

ANSWER:"""


def candidatos_por_trecho(arestas_mentions: list[tuple]) -> dict[str, list[dict]]:
    """As entidades que cada trecho menciona no grafo de referencia (MENTIONS
    depois do corte por frequencia), com um nome para o modelo ler.

    TSqlObjects com a mesma forma de superficie (os gemeos X e X_TSQL) viram
    um candidato so, com a forma de superficie como nome. Se um Concept e um
    TSqlObject ficarem com o mesmo nome, o do TSqlObject ganha o sufixo
    " (T-SQL)". A ordem e alfabetica, sem diferenca de caixa.
    """
    por_trecho: dict[str, dict[tuple, set]] = defaultdict(lambda: defaultdict(set))
    for cid, rotulo, alvo, *_ in arestas_mentions:
        if rotulo == "TSqlObject":
            forma = chunking.surface_form_of_tsql(alvo) or alvo
            por_trecho[cid][("TSqlObject", forma.lower(), forma)].add(alvo)
        else:
            por_trecho[cid][("Concept", alvo, alvo)].add(alvo)
    saida = {}
    for cid, grupos in por_trecho.items():
        conceitos = {k[2].lower() for k in grupos if k[0] == "Concept"}
        lista = []
        vistos_tsql = {}
        for (rotulo, chave, nome), nos in grupos.items():
            if rotulo == "TSqlObject":
                if chave in vistos_tsql:
                    vistos_tsql[chave]["nos"] = sorted(set(vistos_tsql[chave]["nos"]) | nos)
                    continue
                exibido = nome + " (T-SQL)" if nome.lower() in conceitos else nome
                item = {"nome": exibido, "rotulo": rotulo, "nos": sorted(nos)}
                vistos_tsql[chave] = item
                lista.append(item)
            else:
                lista.append({"nome": nome, "rotulo": rotulo, "nos": sorted(nos)})
        lista.sort(key=lambda c: (c["nome"].lower(), c["nome"]))
        saida[cid] = lista
    return saida


def montar_prompt(trecho: dict, candidatos: list[dict]) -> str:
    entidades = "\n".join(f"- {c['nome']}" for c in candidatos)
    return TEMPLATE.format(instrucoes=INSTRUCOES, trecho=texto_para_indice(trecho).strip(),
                           entidades=entidades)


def extrair_objeto_json(texto: str):
    """O primeiro objeto JSON do texto da resposta. O modelo pode escrever uma
    frase antes ou cercar o JSON com ``` ; o que vem depois do objeto e
    ignorado. Levanta ValueError se nao houver objeto JSON legivel."""
    inicio = texto.find("{")
    while inicio != -1:
        try:
            obj, _ = json.JSONDecoder().raw_decode(texto[inicio:])
            return obj
        except json.JSONDecodeError:
            inicio = texto.find("{", inicio + 1)
    raise ValueError("nenhum objeto JSON na resposta")


def achar_candidato(nome: str, por_nome: dict, por_caixa: dict) -> tuple[dict | None, bool]:
    """O candidato com esse nome: exato ou, se nao houver, com outra caixa e sem
    ambiguidade. Devolve (candidato ou None, se precisou da normalizacao)."""
    if nome in por_nome:
        return por_nome[nome], False
    achados = por_caixa.get(nome.lower(), [])
    if len(achados) == 1:
        return achados[0], True
    return None, False


def validar(texto: str, candidatos: list[dict]) -> dict:
    """Confere a resposta do modelo. Devolve as relacoes validas e as
    descartadas, com o motivo."""
    por_nome = {c["nome"]: c for c in candidatos}
    por_caixa: dict[str, list[dict]] = defaultdict(list)
    for c in candidatos:
        por_caixa[c["nome"].lower()].append(c)
    try:
        dados = extrair_objeto_json(texto)
        itens = dados["relations"]
        if not isinstance(itens, list):
            raise TypeError("relations nao e lista")
    except Exception as exc:
        return {"json_valido": False, "erro_json": f"{type(exc).__name__}: {exc}",
                "relacoes": [], "descartadas": []}
    validas, descartadas, vistas = [], [], set()
    for item in itens[:MAX_RELACOES]:
        if not isinstance(item, dict) or not all(isinstance(item.get(k), str)
                                                 for k in ("source", "type", "target")):
            descartadas.append({"item": item, "motivo": "formato"})
            continue
        co, no = achar_candidato(item["source"].strip().strip("`\"'"), por_nome, por_caixa)
        cd, nd = achar_candidato(item["target"].strip().strip("`\"'"), por_nome, por_caixa)
        tp = item["type"].strip().upper()
        if co is None or cd is None:
            descartadas.append({"item": item, "motivo": "entidade_fora_da_lista"})
        elif tp not in TIPOS:
            descartadas.append({"item": item, "motivo": "tipo_invalido"})
        elif co is cd:
            descartadas.append({"item": item, "motivo": "origem_igual_ao_destino"})
        elif (co["nome"], tp, cd["nome"]) in vistas:
            descartadas.append({"item": item, "motivo": "repetida"})
        else:
            vistas.add((co["nome"], tp, cd["nome"]))
            validas.append({"origem": co["nome"], "tipo": tp, "destino": cd["nome"],
                            "origem_rotulo": co["rotulo"], "origem_nos": co["nos"],
                            "destino_rotulo": cd["rotulo"], "destino_nos": cd["nos"],
                            "nome_normalizado": bool(no or nd)})
    if len(itens) > MAX_RELACOES:
        descartadas.append({"item": f"{len(itens) - MAX_RELACOES} itens", "motivo": "acima_do_maximo"})
    return {"json_valido": True, "erro_json": None, "relacoes": validas, "descartadas": descartadas}


def chamar_ollama(prompt: str, modelo: str, opcoes: dict, timeout: float = TIMEOUT) -> dict:
    """Uma chamada ao /api/generate, sem o parametro format (a restricao por
    esquema deixava cada chamada lenta demais), pedindo que o modelo fique na
    memoria depois dela.
    """
    import requests
    t0 = time.perf_counter()
    r = requests.post(f"{url_ollama()}/api/generate", timeout=timeout, json={
        "model": modelo, "prompt": prompt, "stream": False, "options": opcoes,
        "keep_alive": KEEP_ALIVE})
    r.raise_for_status()
    d = r.json()
    d["_segundos"] = round(time.perf_counter() - t0, 2)
    return d


def tokens_por_segundo(resp: dict) -> float | None:
    """A velocidade de geracao que o proprio Ollama mede (eval_count em eval_duration)."""
    n, ns = resp.get("eval_count"), resp.get("eval_duration")
    return round(n / (ns / 1e9), 1) if n and ns else None


def modelo_na_memoria(modelo: str) -> dict:
    """O que o /api/ps diz do modelo carregado: a fracao na placa de video e o
    contexto. So informativo; um erro aqui nao para nada."""
    import requests
    try:
        r = requests.get(f"{url_ollama()}/api/ps", timeout=30)
        r.raise_for_status()
        for m in r.json().get("models", []):
            if modelo in (m.get("name"), m.get("model")):
                tam, vram = m.get("size") or 0, m.get("size_vram") or 0
                return {"na_gpu_pct": round(100 * vram / tam) if tam else None,
                        "contexto": m.get("context_length")}
    except Exception:
        pass
    return {}


def aquecer_modelo(modelo: str, opcoes: dict) -> dict:
    """Carrega o modelo com as mesmas opcoes da extracao, com tempo limite
    folgado, numa resposta curta que tambem mede a velocidade de geracao. A
    primeira carga pode passar do limite de uma chamada; se o modelo ja esta
    carregado, leva poucos segundos.
    """
    resp = chamar_ollama(PROMPT_AQUECIMENTO, modelo, opcoes, timeout=TIMEOUT_CARGA)
    info = {"segundos": resp["_segundos"],
            "carga_s": round(resp["load_duration"] / 1e9, 1) if resp.get("load_duration") else None,
            "tokens_por_segundo": tokens_por_segundo(resp)}
    info.update(modelo_na_memoria(modelo))
    return info


def descrever_aquecimento(aq: dict) -> str:
    partes = [f"pronto em {aq['segundos']} s"]
    if aq.get("carga_s") is not None:
        partes.append(f"carga: {aq['carga_s']} s")
    if aq.get("tokens_por_segundo") is not None:
        partes.append(f"geracao: {aq['tokens_por_segundo']} tokens/s")
    if aq.get("na_gpu_pct") is not None:
        partes.append(f"na placa de video: {aq['na_gpu_pct']}%")
    if aq.get("contexto"):
        partes.append(f"contexto: {aq['contexto']}")
    return "; ".join(partes)


def ordem_de_extracao(elegiveis: list[str], semente: int = SEMENTE_ORDEM) -> list[str]:
    """A ordem dos trechos: sorteada com semente fixa, para o piloto (os
    primeiros da lista) ser uma amostra aleatoria do escopo."""
    ordem = sorted(elegiveis)
    random.Random(semente).shuffle(ordem)
    return ordem


def ler_jsonl(caminho: Path) -> list[dict]:
    if not caminho.exists():
        return []
    with caminho.open(encoding="utf-8") as fh:
        return [json.loads(linha) for linha in fh if linha.strip()]


def sha_texto(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def cmd_extrair(args) -> int:
    nos, arestas, digital_grafo = ler_grafo(args.grafo)
    trechos = carregar_trechos(args.trechos)
    if {t["chunk_id"] for t in trechos} != set(nos.get("Chunk", [])):
        raise SystemExit("ERRO: os trechos do arquivo e os do grafo nao sao os mesmos.")
    por_id = {t["chunk_id"]: t for t in trechos}
    no_escopo = sorted(t["chunk_id"] for t in trechos if t.get("section_type") in SECOES)
    cands = candidatos_por_trecho(arestas.get("MENTIONS", []))
    elegiveis = [c for c in no_escopo if len(cands.get(c, [])) >= 2]
    ordem = ordem_de_extracao(elegiveis)
    if args.limite is not None:
        ordem = ordem[:args.limite]

    if args.refazer and args.saida.exists():
        args.saida.unlink()
    feitas = ler_jsonl(args.saida)
    ident = identidade_do_modelo(args.modelo)
    digests = {r.get("digest_pesos") for r in feitas} - {None}
    if digests and ident.get("digest_pesos") not in digests:
        raise SystemExit("ERRO: a extracao foi feita com outros pesos do modelo "
                         f"({sorted(digests)}); agora: {ident.get('digest_pesos')}. "
                         "Use --refazer para comecar de novo.")
    prompts = {r.get("sha_instrucoes") for r in feitas} - {None}
    if prompts and sha_texto(INSTRUCOES) not in prompts:
        raise SystemExit("ERRO: a extracao foi feita com outras instrucoes. Use --refazer.")
    modos = {r.get("modo_saida", "esquema") for r in feitas}
    if feitas and modos != {MODO_SAIDA}:
        raise SystemExit(f"ERRO: o arquivo tem trechos extraidos com outro modo de saida ({sorted(modos)}; "
                         f"esperado: "
                         f"{MODO_SAIDA!r}). Use --refazer para comecar de novo.")
    ja = {r["chunk_id"] for r in feitas if not r.get("erro")}
    pendentes = [c for c in ordem if c not in ja]
    args.saida.parent.mkdir(parents=True, exist_ok=True)
    print(f"[extrair] modelo {args.modelo} ({ident.get('digest_pesos')}; Ollama "
          f"{ident.get('versao_ollama')}); escopo: {len(no_escopo):,} trechos de "
          f"{' e '.join(SECOES)}, {len(elegiveis):,} com duas ou mais entidades; nesta rodada: "
          f"{len(ordem):,}; ja feitos: {len(ordem) - len(pendentes):,}")
    primeiros: list[float] = []
    parou_pela_guarda = None
    aquecimento, recargas, erros_seguidos = None, 0, 0
    if pendentes:
        # A carga do modelo fica fora das chamadas medidas pela guarda de tempo.
        print(f"[extrair] carregando o modelo (ate {TIMEOUT_CARGA // 60} minutos)...", flush=True)
        try:
            aquecimento = aquecer_modelo(args.modelo, OPCOES)
        except Exception as exc:
            raise SystemExit(f"ERRO: o Ollama nao respondeu a carga do modelo: {type(exc).__name__}: {exc}")
        print(f"[extrair] modelo {descrever_aquecimento(aquecimento)}")
    with args.saida.open("a", encoding="utf-8") as fh:
        for n, cid in enumerate(pendentes, 1):
            candidatos = cands[cid]
            reg = {"chunk_id": cid, "section_type": por_id[cid].get("section_type"),
                   "n_candidatos": len(candidatos), "candidatos": [c["nome"] for c in candidatos]}
            try:
                resp = chamar_ollama(montar_prompt(por_id[cid], candidatos), args.modelo, OPCOES)
                texto = resp.get("response", "")
                reg.update(validar(texto, candidatos))
                reg.update({"resposta": texto, "done_reason": resp.get("done_reason"),
                            "truncado": resp.get("done_reason") == "length",
                            "prompt_eval_count": resp.get("prompt_eval_count"),
                            "eval_count": resp.get("eval_count"), "segundos": resp.get("_segundos"),
                            "tokens_por_segundo": tokens_por_segundo(resp),
                            # tempos medidos pelo proprio Ollama, em nanossegundos
                            **{k: resp.get(k) for k in ("load_duration", "prompt_eval_duration",
                                                        "eval_duration", "total_duration")},
                            "erro": None})
            except Exception as exc:
                reg["erro"] = f"{type(exc).__name__}: {exc}"
            reg.update({"digest_pesos": ident.get("digest_pesos"), "sha_instrucoes": sha_texto(INSTRUCOES),
                        "modo_saida": MODO_SAIDA, "timestamp": datetime.now().isoformat(timespec="seconds")})
            fh.write(json.dumps(reg, ensure_ascii=False) + "\n")
            fh.flush()
            if reg.get("erro"):
                marca = "ERRO " + reg["erro"][:80]
            else:
                marca = (f"{len(reg['relacoes'])} relacoes, {len(reg['descartadas'])} descartadas"
                         + ("" if reg["json_valido"] else ", JSON invalido")
                         + (", truncado" if reg["truncado"] else "") + f", {reg['segundos']}s"
                         + (f", {reg['tokens_por_segundo']} tokens/s" if reg["tokens_por_segundo"] else ""))
            print(f"  [{n}/{len(pendentes)}] {cid}: {marca}", flush=True)
            # Guarda de tempo: a mediana das primeiras chamadas da rodada, com o erro
            # contado como chamada lenta. A mediana de 5 passa do limite quando 3
            # passam, e a rodada para ja na terceira, sem esperar as outras.
            if len(primeiros) < GUARDA_CHAMADAS:
                primeiros.append(math.inf if reg.get("erro") else float(reg.get("segundos") or 0))
                lentas = sum(1 for s in primeiros if s > GUARDA_SEGUNDOS)
                if lentas > GUARDA_CHAMADAS // 2:
                    parou_pela_guarda = (f"{lentas} das {len(primeiros)} primeiras chamadas passaram de "
                                         f"{GUARDA_SEGUNDOS} s ou deram erro, e a mediana das "
                                         f"{GUARDA_CHAMADAS} primeiras passaria de {GUARDA_SEGUNDOS} s")
            # Em qualquer ponto da rodada, erros seguidos tambem param a rodada.
            erros_seguidos = erros_seguidos + 1 if reg.get("erro") else 0
            if not parou_pela_guarda and erros_seguidos >= GUARDA_ERROS_SEGUIDOS:
                parou_pela_guarda = f"{erros_seguidos} chamadas seguidas deram erro"
            if parou_pela_guarda:
                print(f"[guarda] parei: {parou_pela_guarda}.")
                break
            if reg.get("erro"):
                # O modelo pode ter saido da memoria: carrega de novo, com tempo folgado.
                recargas += 1
                try:
                    print(f"    [recarga] modelo {descrever_aquecimento(aquecer_modelo(args.modelo, OPCOES))}",
                          flush=True)
                except Exception as exc:
                    print(f"    [recarga] falhou: {type(exc).__name__}: {str(exc)[:80]}", flush=True)
    registros = ler_jsonl(args.saida)
    manifesto = {
        "modelo": ident, "opcoes": OPCOES, "tipos": list(TIPOS), "secoes": list(SECOES),
        "max_relacoes_por_trecho": MAX_RELACOES, "semente_ordem": SEMENTE_ORDEM,
        "modo_saida": MODO_SAIDA, "timeout_s": TIMEOUT, "timeout_carga_s": TIMEOUT_CARGA,
        "keep_alive": KEEP_ALIVE, "aquecimento": aquecimento, "recargas": recargas,
        "guarda": {"chamadas": GUARDA_CHAMADAS, "mediana_maxima_s": GUARDA_SEGUNDOS,
                   "erro_conta_como_lenta": True, "erros_seguidos": GUARDA_ERROS_SEGUIDOS},
        "parou_pela_guarda": parou_pela_guarda,
        "instrucoes": INSTRUCOES, "sha_instrucoes": sha_texto(INSTRUCOES), "template": TEMPLATE,
        "candidatos": "MENTIONS do grafo de referencia, depois do corte",
        "n_no_escopo": len(no_escopo), "n_elegiveis": len(elegiveis),
        "n_sem_candidato": sum(1 for c in no_escopo if not cands.get(c)),
        "n_um_candidato": sum(1 for c in no_escopo if len(cands.get(c, [])) == 1),
        "n_registros": len(registros), "n_erros": sum(1 for r in registros if r.get("erro")),
        "impressao_digital_grafo": digital_grafo, "impressao_digital_trechos": impressao_digital(trechos),
        "atualizado_em": datetime.now().isoformat(timespec="seconds"),
    }
    args.saida.with_suffix(".manifesto.json").write_text(
        json.dumps(manifesto, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"[extrair] {len(registros):,} trechos no arquivo, {manifesto['n_erros']} com erro -> {args.saida}")
    if parou_pela_guarda:
        return 3
    return 0 if manifesto["n_erros"] == 0 else 2


def consolidar(registros: list[dict]) -> list[dict]:
    """Uma aresta por (origem, tipo, destino), com os trechos que a afirmam."""
    arestas: dict[tuple, dict] = {}
    for r in registros:
        if r.get("erro"):
            continue
        for rel in r.get("relacoes", []):
            chave = (rel["origem_rotulo"], rel["origem"], rel["tipo"], rel["destino_rotulo"], rel["destino"])
            a = arestas.setdefault(chave, {
                "origem": rel["origem"], "origem_rotulo": rel["origem_rotulo"], "origem_nos": rel["origem_nos"],
                "tipo": rel["tipo"],
                "destino": rel["destino"], "destino_rotulo": rel["destino_rotulo"],
                "destino_nos": rel["destino_nos"], "trechos": []})
            a["trechos"].append(r["chunk_id"])
    return [dict(a, trechos=sorted(set(a["trechos"]))) for _, a in sorted(arestas.items())]


def cmd_resumo(args) -> int:
    registros = [r for r in ler_jsonl(args.extracao)]
    ultimos = {}
    for r in registros:
        ultimos[r["chunk_id"]] = r  # vale o registro mais recente de cada trecho
    regs = list(ultimos.values())
    ok = [r for r in regs if not r.get("erro")]
    if not ok:
        raise SystemExit("ERRO: nenhum trecho extraido sem erro.")
    validas = [rel for r in ok for rel in r.get("relacoes", [])]
    descartes = Counter(d["motivo"] for r in ok for d in r.get("descartadas", []))
    por_trecho = [len(r.get("relacoes", [])) for r in ok]
    segundos = [r["segundos"] for r in ok if r.get("segundos") is not None]
    velocidades = [r["tokens_por_segundo"] for r in ok if r.get("tokens_por_segundo")]
    arestas = consolidar(ok)
    pares = {tuple(sorted([(a["origem_rotulo"], a["origem"]), (a["destino_rotulo"], a["destino"])]))
             for a in arestas}
    resumo = {
        "trechos": len(regs), "trechos_sem_erro": len(ok), "trechos_com_erro": len(regs) - len(ok),
        "json_invalido": sum(1 for r in ok if not r.get("json_valido")),
        "truncados": sum(1 for r in ok if r.get("truncado")),
        "relacoes_validas": len(validas),
        "descartadas": dict(sorted(descartes.items())),
        "relacoes_por_tipo": dict(sorted(Counter(rel["tipo"] for rel in validas).items())),
        "trechos_com_relacao": sum(1 for n in por_trecho if n > 0),
        "relacoes_por_trecho_mediana": statistics.median(por_trecho),
        "relacoes_por_trecho_max": max(por_trecho),
        "arestas_consolidadas": len(arestas), "pares_de_entidades": len(pares),
        "relacoes_com_nome_normalizado": sum(1 for rel in validas if rel.get("nome_normalizado")),
        "modos_de_saida": sorted({r.get("modo_saida", "esquema") for r in regs}),
        "segundos_total": round(sum(segundos), 1),
        "segundos_mediana": round(statistics.median(segundos), 1) if segundos else None,
        "tokens_por_segundo_mediana": round(statistics.median(velocidades), 1) if velocidades else None,
    }
    destino = args.saida or args.extracao.with_name(args.extracao.stem + ".arestas.jsonl")
    with destino.open("w", encoding="utf-8") as fh:
        for a in arestas:
            fh.write(json.dumps(a, ensure_ascii=False) + "\n")
    args.extracao.with_name(args.extracao.stem + ".resumo.json").write_text(
        json.dumps(resumo, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"[resumo] {resumo['trechos_sem_erro']:,} trechos sem erro ({resumo['trechos_com_erro']} com erro); "
          f"JSON invalido: {resumo['json_invalido']}; truncados: {resumo['truncados']}")
    print(f"[resumo] {resumo['relacoes_validas']:,} relacoes validas em {resumo['trechos_com_relacao']:,} "
          f"trechos; descartadas: {resumo['descartadas'] or 'nenhuma'}")
    print(f"[resumo] por tipo: {resumo['relacoes_por_tipo']}; com nome achado so pela caixa: "
          f"{resumo['relacoes_com_nome_normalizado']}; modo de saida: {', '.join(resumo['modos_de_saida'])}")
    print(f"[resumo] {resumo['arestas_consolidadas']:,} arestas (origem, tipo, destino) entre "
          f"{resumo['pares_de_entidades']:,} pares de entidades -> {destino}")
    print(f"[resumo] tempo: {resumo['segundos_total']:,} s no total, mediana de "
          f"{resumo['segundos_mediana']} s por trecho"
          + (f"; geracao a {resumo['tokens_por_segundo_mediana']} tokens/s (mediana)"
             if resumo["tokens_por_segundo_mediana"] else ""))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("extrair")
    p.add_argument("--grafo", type=Path, required=True)
    p.add_argument("--trechos", type=Path, required=True)
    p.add_argument("--saida", type=Path, required=True)
    p.add_argument("--limite", type=int, default=None, help="so os N primeiros da ordem (piloto)")
    p.add_argument("--modelo", default=MODELO)
    p.add_argument("--refazer", action="store_true")
    p.set_defaults(func=cmd_extrair)

    p = sub.add_parser("resumo")
    p.add_argument("--extracao", type=Path, required=True)
    p.add_argument("--saida", type=Path, default=None, help="arquivo das arestas consolidadas")
    p.set_defaults(func=cmd_resumo)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
