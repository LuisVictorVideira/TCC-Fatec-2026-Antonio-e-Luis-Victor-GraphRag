# Pré-registro do julgamento das respostas por uma LLM

Gravado em 16/10/2026, às 02:41 (horário de Brasília), antes de qualquer julgamento. Texto editado para o repositório: foram retiradas as referências a documentos internos do projeto. As regras e as instruções ao juiz são as gravadas; as instruções estão na íntegra.

## Por que existe

O plano previa o autor como juiz das 93 respostas, às cegas. Depois da geração das respostas, em 16/10/2026, o autor pediu que uma sessão de IA julgasse no lugar dele. Este documento fixa como o julgamento é feito antes de ele acontecer.

## O que a sessão principal já tinha visto

- **A chave da planilha.** O autor anexou o `julgamento_chave.json` ao chat, junto com a planilha, e o arquivo foi lido.
- **O `respostas.jsonl`,** só para conferências mecânicas: erros, motivo de parada, tamanho do prompt e da resposta.
- **As métricas de recuperação** (R@5 por método e ablações).

Por isso a sessão principal não julga nem altera nenhum julgamento.

## O juiz

- Um subagente, sessão nova, sem o histórico da conversa, com o modelo da sessão (configurado `claude-opus-5-5`; o modelo que de fato responde pode variar).
- Recebe só a planilha (`julgamento.csv`, o mesmo arquivo gerado na máquina do autor), copiada para uma pasta própria, e as instruções abaixo, sem mudança.
- Não recebe a chave, o `respostas.jsonl`, os runs nem as métricas.

## Instruções ao juiz (texto integral)

```
Você vai julgar 93 respostas de um modelo de linguagem (llama3:8b) a perguntas sobre a documentação do Microsoft SQL Server. É o julgamento de um trabalho de conclusão de curso, e o resultado entra na dissertação. Trabalhe com cuidado, linha por linha, sem pressa.

ENTRADA

O arquivo /home/claude/work/juiz06/julgamento.csv, em CSV com separador ponto e vírgula, codificado em UTF-8 com BOM. Cada linha é uma resposta, com as colunas:
- codigo: código aleatório da resposta (R e três dígitos);
- pergunta: identificador da pergunta (A01, B02...);
- pergunta_en e pergunta_pt: a pergunta, em inglês (a que o modelo recebeu) e em português;
- resposta_de_referencia: o resumo da resposta correta, em português;
- resposta: a resposta do modelo, em inglês;
- componente_1 a componente_5: as informações que a resposta precisa conter; só as não vazias contam;
- julgamento_1 a julgamento_5, afirma_algo_errado e observacao: em branco.

As linhas estão agrupadas por pergunta, com três respostas por pergunta, geradas em condições diferentes. Você não sabe, e não deve tentar descobrir, de que condição vem cada resposta.

REGRAS DE ACESSO

Leia só esse arquivo e grave só o arquivo de saída. Não abra nenhum outro arquivo, não liste pastas, não procure arquivos com "chave", "respostas", "run" ou "avaliacao" no nome e não use a internet. O julgamento precisa ser às cegas.

CRITÉRIO

Para cada componente não vazio da linha:
- S: a resposta afirma a informação do componente, sem erro, mesmo com outras palavras;
- P: afirma só parte da informação, ou de forma vaga, ou com uma ressalva que a enfraquece;
- N: não menciona, nega, ou afirma algo errado sobre o ponto.

A referência é a resposta_de_referencia junto com o texto do componente; as duas seguem a documentação atual do SQL Server. Se a resposta reproduzir uma versão desatualizada (por exemplo, um limite antigo), o componente leva N, com "trecho desatualizado" na observação. Uma resposta que diz que os trechos não respondem à pergunta leva N nos componentes que ela não traz; isso não é afirmação errada.

afirma_algo_errado: S quando a resposta contém alguma afirmação falsa sobre o SQL Server, relevante para a pergunta, mesmo fora dos componentes; N caso contrário. Na dúvida, N, com a dúvida explicada na observação.

Julgue cada resposta pelo que ela diz, sem compará-la com as outras duas da mesma pergunta e sem favorecer respostas longas, respostas que citem trechos ou respostas que digam que os trechos não respondem.

SAÍDA

Grave /home/claude/work/juiz06/julgamentos_ia.json: uma lista JSON com um objeto por linha da planilha, na mesma ordem das linhas, neste formato:

{"codigo": "R000", "julgamentos": ["S", "N"], "justificativas": ["...", "..."], "afirma_algo_errado": "N", "observacao": ""}

- julgamentos e justificativas têm um item por componente não vazio, na ordem componente_1, componente_2 e assim por diante;
- cada justificativa é uma frase curta, em português, que diz o que a resposta afirma (ou deixa de afirmar) sobre o componente;
- observacao fica vazia ou traz uma nota curta, como "trecho desatualizado" ou a dúvida sobre afirma_algo_errado.

Ao terminar, confira que o arquivo tem 93 objetos, todos os códigos da planilha e um julgamento por componente não vazio. Responda só com o número de objetos gravados e a contagem total de S, P e N.
```

## Depois do julgamento

- A sessão principal confere só o formato: 93 objetos, os códigos da planilha, um julgamento por componente não vazio e valores S, P ou N. Se o formato falhar, o juiz é chamado de novo só para corrigi-lo.
- Os julgamentos vão para a planilha (`julgamento_ia.csv`) sem nenhuma alteração, pelo `planilha_ia.py`. O `calcular` do `responder.py`, sem mudança, produz o resultado pela análise fixada antes da geração das respostas:
  - nota média por resposta;
  - as 14 perguntas não triviais na análise principal;
  - o grafo contra o BM25 e contra a resposta sem contexto;
  - aleatorização pareada, *bootstrap* e correção de Holm;
  - efeito mínimo de interesse de 0,10.
- O resultado é reportado como julgamento por modelo de linguagem, com essa limitação declarada.
