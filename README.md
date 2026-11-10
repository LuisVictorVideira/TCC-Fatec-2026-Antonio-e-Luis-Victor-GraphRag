# GraphRAG sobre a documentação do SQL Server

Código do trabalho de graduação *Desenvolvimento de uma arquitetura GraphRAG para recuperação de informações em documentação técnica*, do curso de Tecnologia em Banco de Dados da FATEC Bauru.

Autores: Antonio Ranazzi Neto e Luis Victor Rosa de Almeida Santana Videira. Orientador: Prof. Me. Luis Alexandre da Silva.

O sistema organiza 6.151 páginas da documentação oficial do Microsoft SQL Server num grafo no Neo4j, construído por regras fixas, sem extração por LLM. Depois compara a busca pelo grafo (PageRank personalizado) com a busca por palavras (BM25), tanto nos trechos recuperados quanto nas respostas de um mesmo modelo de linguagem (llama3:8b). Uma análise secundária usa a LLM para propor relações entre as entidades do grafo.

O repositório traz o código, os testes, as perguntas de avaliação e os pré-registros. O corpus é baixado por script, e todos os resultados saem dos comandos abaixo, na pasta `outputs/`, que fica fora do controle de versão.

## Estrutura

```
src/                 scripts do pipeline
tests/               testes automatizados (pytest)
dados/               perguntas de avaliação e questões de múltipla escolha
docs/                pré-registros da anotação e dos julgamentos
scripts/             obtenção do corpus
.env.example         modelo das credenciais e dos caminhos
docker-compose.yml   Neo4j 5.19.0 (e, opcional, PostgreSQL com pgvector)
requirements.txt     dependências Python
```

| Script | Função |
| --- | --- |
| `explorar_corpus.py` | Caracteriza o corpus: arquivos, palavras, títulos, blocos de código e cabeçalhos |
| `construir_grafo.py` | Grava no Neo4j os documentos, os conceitos (helpviewer_keywords), os objetos T-SQL (f1_keywords) e os links |
| `chunking_e_mentions.py` | Divide os documentos em trechos por seção, separa os blocos de código e cria as arestas MENTIONS |
| `exportar_trechos.py` | Exporta os trechos do Neo4j para JSONL, com a impressão digital |
| `reproduzir_sem_neo4j.py` | Refaz o grafo de metadados e a fragmentação a partir do corpus, sem banco de dados, e confere os números de referência |
| `baselines.py` | BM25, busca vetorial e fusão de listas por RRF |
| `consulta_grafo.py` | Exporta o grafo e faz a busca pelo grafo (sementes e PageRank personalizado) |
| `avaliar_recuperacao.py` | R@k, P@k e MRR contra os trechos-ouro, e as comparações entre métodos |
| `responder.py` | Respostas do llama3:8b nas três condições, planilha de julgamento às cegas e cálculo das notas |
| `planilha_ia.py` | Copia para a planilha os julgamentos gravados pelo juiz de IA, sem alterar valores |
| `analise_respostas.py` | Análise descritiva das notas por pergunta, cruzada com o contexto recebido |
| `extrair_relacoes.py` | Relações entre entidades propostas pelo llama3:8b, e as arestas consolidadas |
| `precisao_relacoes.py` | Amostra, planilha às cegas e cálculo da precisão das relações |

Cada script explica, no começo do arquivo, o que faz e como se usa; `python src/<script>.py --help` mostra as opções.

## Requisitos

- Python 3.12 (a versão usada no trabalho).
- Docker com o Docker Compose, para o Neo4j 5.19.0.
- Ollama 0.33.2 com o modelo `llama3:8b`, para as respostas e a extração de relações.
- Git, para baixar o corpus.

## Configuração

Todos os comandos rodam a partir da raiz do repositório.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # troque NEO4J_PASSWORD (8 caracteres ou mais)
docker compose up -d          # Neo4j em bolt://localhost:7687; navegador em http://localhost:7474
bash scripts/obter_corpus.sh  # cria corpus/ com o commit 928a038d do sql-docs
ollama pull llama3:8b
```

Os scripts acham o `.env` subindo pelas pastas a partir de `src/`. O `SQL_DOCS_ROOT=corpus` do modelo é relativo à pasta de onde o comando roda, por isso a raiz do repositório.

O `scripts/obter_corpus.sh` baixa do [MicrosoftDocs/sql-docs](https://github.com/MicrosoftDocs/sql-docs) apenas as pastas `relational-databases/` e `t-sql/` do commit `928a038ddb0ec8229674c7d0dbe9e529cbd42024` (11/06/2026) e copia os 6.276 arquivos Markdown para `corpus/`, com as licenças. Os 125 arquivos de `relational-databases/errors-events/includes/` ficam fora do grafo, e restam 6.151. Para usar um clone que já existe, no mesmo commit: `bash scripts/obter_corpus.sh /caminho/do/sql-docs`.

## Execução

Os passos seguem a ordem do procedimento do artigo. Os arquivos gerados vão para `outputs/`.

**1. Caracterização do corpus**

```bash
python src/explorar_corpus.py corpus/relational-databases corpus/t-sql
```

**2. Grafo de metadados no Neo4j**

```bash
python src/construir_grafo.py
```

**3. Fragmentação e menções**

```bash
python src/chunking_e_mentions.py --df-max 0.02
```

**4. Exportação dos trechos e do grafo**

```bash
python src/exportar_trechos.py --saida outputs/trechos.jsonl
python src/consulta_grafo.py exportar-grafo --saida outputs/grafo.jsonl
```

Sem o Neo4j, os passos 2 a 4 podem ser refeitos direto do corpus, com as mesmas funções. O `--conferir` compara os números com os de referência:

```bash
python src/reproduzir_sem_neo4j.py --conferir --exportar outputs/trechos.jsonl
python src/consulta_grafo.py exportar-grafo --offline --saida outputs/grafo.jsonl
```

**5. Busca: BM25, grafo e fusão das duas listas**

```bash
python src/baselines.py bm25 --trechos outputs/trechos.jsonl \
    --perguntas dados/perguntas_avaliacao.yaml --saida outputs/runs/bm25.run
python src/consulta_grafo.py recuperar --grafo outputs/grafo.jsonl \
    --trechos outputs/trechos.jsonl --bm25 outputs/runs/bm25.run \
    --perguntas dados/perguntas_avaliacao.yaml --saida outputs/runs/grafo.run
python src/baselines.py hibrido --runs outputs/runs/bm25.run outputs/runs/grafo.run \
    --saida outputs/runs/hibrido.run
```

As ablações são opções do `recuperar`: `--so-mentions`, `--sem-codigo`, `--sem-bm25`, `--sem-fundir-gemeos`, `--normalizacao-bm25 maxima` e `--normalizar-grau`.

**6. Métricas de recuperação e comparação principal**

```bash
python src/avaliar_recuperacao.py --perguntas dados/perguntas_avaliacao.yaml \
    --runs outputs/runs/bm25.run outputs/runs/grafo.run outputs/runs/hibrido.run \
    --saida outputs/avaliacao/recuperacao --comparar grafo bm25
```

A comparação principal é grafo contra BM25, nas 14 perguntas não triviais, por teste de aleatorização pareado e intervalo de *bootstrap*. A fusão entra só como resultado descritivo.

**7. Respostas e julgamento às cegas**

```bash
python src/responder.py responder --perguntas dados/perguntas_avaliacao.yaml \
    --trechos outputs/trechos.jsonl \
    --run bm25=outputs/runs/bm25.run --run grafo=outputs/runs/grafo.run \
    --saida outputs/respostas/respostas.jsonl
python src/responder.py planilha --perguntas dados/perguntas_avaliacao.yaml \
    --respostas outputs/respostas/respostas.jsonl --saida outputs/respostas/julgamento.csv
```

O `planilha` grava a planilha sem a condição de cada resposta e, à parte, a chave (`julgamento_chave.json`), que só se abre depois do julgamento. No trabalho, a planilha foi julgada por uma LLM, com as instruções de `docs/preregistro_julgamento_respostas.md`, e os julgamentos foram copiados sem alteração:

```bash
python src/planilha_ia.py --planilha outputs/respostas/julgamento.csv \
    --julgamentos outputs/respostas/julgamentos_ia.json \
    --saida outputs/respostas/julgamento_ia.csv
python src/responder.py calcular --perguntas dados/perguntas_avaliacao.yaml \
    --planilha outputs/respostas/julgamento_ia.csv \
    --chave outputs/respostas/julgamento_chave.json
python src/analise_respostas.py --perguntas dados/perguntas_avaliacao.yaml \
    --planilha outputs/respostas/julgamento_ia.csv \
    --chave outputs/respostas/julgamento_chave.json \
    --respostas outputs/respostas/respostas.jsonl \
    --por-pergunta outputs/avaliacao/recuperacao.por_pergunta.csv \
    --resultado outputs/respostas/julgamento_ia_resultado.json \
    --saida outputs/respostas/analise_respostas.csv
```

**8. Relações extraídas pela LLM (análise secundária)**

```bash
python src/extrair_relacoes.py extrair --grafo outputs/grafo.jsonl \
    --trechos outputs/trechos.jsonl --saida outputs/relacoes/relacoes_llm.jsonl
python src/extrair_relacoes.py resumo --extracao outputs/relacoes/relacoes_llm.jsonl
python src/consulta_grafo.py recuperar --grafo outputs/grafo.jsonl \
    --trechos outputs/trechos.jsonl --bm25 outputs/runs/bm25.run \
    --perguntas dados/perguntas_avaliacao.yaml \
    --relacoes outputs/relacoes/relacoes_llm.arestas.jsonl \
    --saida outputs/runs/grafo_relacoes.run
python src/precisao_relacoes.py amostra --extracao outputs/relacoes/relacoes_llm.jsonl \
    --trechos outputs/trechos.jsonl --saida outputs/relacoes/precisao.csv \
    --chave outputs/relacoes/precisao_chave.json
python src/precisao_relacoes.py planilha-ia --planilha outputs/relacoes/precisao.csv \
    --julgamentos outputs/relacoes/precisao_julgamentos_ia.json \
    --saida outputs/relacoes/precisao_ia.csv
python src/precisao_relacoes.py calcular --planilha outputs/relacoes/precisao_ia.csv \
    --chave outputs/relacoes/precisao_chave.json
```

O `extrair` grava a cada trecho e retoma de onde parou; com `--limite 50`, faz um piloto com os 50 primeiros trechos da mesma ordem sorteada. A amostra de precisão foi julgada por uma LLM, com as instruções de `docs/preregistro_precisao_relacoes.md`.

## Conferência

Os passos 1 a 6 são determinísticos. Com o corpus no commit acima e as versões fixadas no `requirements.txt`, eles reproduzem os arquivos do trabalho:

| Arquivo | SHA-256 |
| --- | --- |
| `outputs/grafo.jsonl` | `1be4426e6d3a235842ba00e1088f4465dc9f13e670f18a787084c2accda530f5` |
| `outputs/runs/bm25.run` | `74367b285da99e3c3232e61fc6c9a4291788894b7e9d500f74e0b4e0c05e0e68` |
| `outputs/runs/grafo.run` | `011bb67741e6ae2e9a8febc8b22f50e2d76fa216a8840b5cc9b9e74bfbd69864` |
| `outputs/runs/hibrido.run` | `aa7de262e9bb31bc37640bf7aff1b369be24623fef9bcfe6dcb801a464df3540` |

A impressão digital dos trechos, que o `exportar_trechos.py` e o `reproduzir_sem_neo4j.py` imprimem, é `dfc1cb8893e7ad89b75df87b0da37983d59132d06670ce8364b683061e537b51`. O tamanho do corpus informado pelo `explorar_corpus.py` depende do fim de linha do checkout: 47,9 MB com LF e 48,7 MB com CRLF; as demais contagens não mudam.

As respostas e as relações dependem do Ollama. O modelo roda com temperatura 0, semente 42 e `top_k` 1, e os scripts gravam o digest dos pesos em cada execução e se recusam a continuar se ele mudar.

## Testes

```bash
python -m pytest tests -q
```

São 89 testes aprovados e uma falha esperada (`xfail`), que documenta uma limitação conhecida: a janela deslizante do divisor de seções junta as palavras numa linha só. Nenhum teste abre o Neo4j ou o Ollama.

## Dados

- `dados/perguntas_avaliacao.yaml`: as 31 perguntas válidas, em inglês e em português, com os componentes da resposta, os trechos-ouro, o escopo e a classificação em trivial ou não trivial. Das 32 redigidas, a A02 foi excluída porque a resposta está fora das duas pastas do corpus.
- `dados/questoes_multipla_escolha.yaml`: a versão de múltipla escolha das mesmas 31 perguntas, por grupo, com quatro alternativas, o gabarito e os trechos que o sustentam.
- `dados/perguntas.md`: as duas formas lado a lado, por grupo, para leitura.

Os grupos são A (controle), B (impacto e dependência), C (pré-requisitos e restrições) e D (comparação).

## Pré-registros

A pasta `docs/` reúne as regras gravadas antes de cada etapa que dependia de julgamento: a anotação das perguntas, o julgamento das respostas e o julgamento da precisão das relações.

## Licenças do corpus

O corpus é do repositório [MicrosoftDocs/sql-docs](https://github.com/MicrosoftDocs/sql-docs): o texto sob a licença CC BY 4.0 e o código sob a licença MIT. O `scripts/obter_corpus.sh` copia os dois arquivos de licença para `corpus/`.
