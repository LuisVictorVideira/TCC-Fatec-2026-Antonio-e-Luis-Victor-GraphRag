# Pré-registro da precisão das relações extraídas pela LLM

Gravado em 18/10/2026, às 03:49 (horário de Brasília), antes do sorteio da amostra (03:50) e de qualquer julgamento. Texto editado para o repositório: foram retiradas as referências a documentos internos do projeto. O desenho da medida e as instruções ao juiz são os gravados; as instruções estão na íntegra.

Script: `src/precisao_relacoes.py`, com os testes em `tests/test_precisao_relacoes.py`.

## 1. O que se mede

A fração das relações extraídas pelo llama3:8b que o trecho de fato afirma. O desenho foi fixado em 16/10/2026, antes da extração: uma amostra sorteada de 60 relações válidas, com semente 42, julgada às cegas com três valores.

- `S`: o trecho afirma a relação, com o tipo e a direção certos;
- `P`: o trecho afirma a relação, mas o tipo ou a direção são discutíveis;
- `N`: o trecho não afirma a relação.

## 2. A amostra

- **População:** as 1.954 relações válidas da rodada completa, no arquivo `relacoes_llm.jsonl` gerado na máquina do autor (1.968 registros, nenhum com erro).
- **Unidade:** a relação no trecho em que foi extraída. A mesma relação afirmada em dois trechos conta duas vezes, cada uma com o seu trecho. As arestas consolidadas (1.851) não são a unidade, porque o juiz precisa de um trecho para julgar.
- **Sorteio:** `precisao_relacoes.py amostra`, com 60 relações e semente 42, sobre a lista em ordem fixa (trecho e posição da relação na resposta). A planilha segue a ordem do sorteio, com os códigos R01 a R60.
- **O trecho na planilha:** a mesma representação que o modelo leu: título do documento, título da seção e texto, com os links reduzidos ao rótulo.
- **A chave** (`precisao_chave.json`) fica num arquivo separado, com o trecho de origem de cada código.

## 3. O juiz

- **Escolha do autor (18/10/2026, por volta das 03:45):** uma sessão de IA às cegas, como no julgamento das respostas.
- **Quem é:** um subagente, sessão nova, sem o histórico da conversa, com o modelo da sessão. É de família diferente da do llama3:8b, que extraiu as relações.
- **O que recebe:** só a planilha `precisao.csv`, copiada para uma pasta própria, e as instruções da seção 4.
- **O que não recebe:** a chave, as respostas do modelo, as listas de candidatos, os resultados da busca e as demais partes deste documento.
- **Auditoria:** o registro da sessão do juiz é conferido depois, para mostrar que ela só leu a planilha e gravou o arquivo pedido.
- **Depois do juiz:** a sessão principal confere só o formato (60 objetos, os códigos na ordem da planilha, valores válidos). O `planilha-ia` copia os valores, sem alteração, e o `calcular` faz as contas.

## 4. Instruções ao juiz, na íntegra

```
Você vai julgar 60 relações entre entidades da documentação do SQL Server, uma por linha da planilha precisao.csv. Cada relação foi extraída de um trecho da documentação. Julgue cada relação só pelo trecho da mesma linha: não use conhecimento externo sobre o SQL Server.

Colunas da planilha:
- id: o código da relação, de R01 a R60;
- trecho: o título do documento, o título da seção e o texto do trecho;
- origem, tipo e destino: a relação, lida como "origem TIPO destino".

Os nomes das entidades vêm do índice da documentação e podem ter qualificadores, entre colchetes ou depois de vírgula, como "aggregate functions [sql server], user-defined". O sufixo " (T-SQL)" indica o comando ou objeto do Transact-SQL de mesmo nome. Julgue se o trecho afirma a relação entre as coisas que esses nomes designam, mesmo que o trecho use outras palavras para elas.

Os tipos, com a direção da origem para o destino:
- REQUIRES: a origem precisa do destino para funcionar, rodar ou ser permitida (uma permissão, uma opção, um recurso ou uma condição);
- AFFECTS: a origem altera, reconstrói, remove, bloqueia ou invalida o destino;
- PART_OF: a origem é componente, elemento ou opção do destino;
- INCOMPATIBLE_WITH: a origem não pode ser usada com o destino, ou não é suportada com ele;
- REPLACES: a origem deve ser usada no lugar do destino, ou o destino está obsoleto em favor da origem.

O julgamento:
- S: o trecho afirma a relação, com o tipo e a direção certos;
- P: o trecho afirma a relação, mas o tipo ou a direção são discutíveis;
- N: o trecho não afirma a relação.

Na dúvida entre S e P, use P; entre P e N, use N. Uma relação que só se deduz com conhecimento de fora do trecho é N.

Grave o resultado no arquivo precisao_julgamentos_ia.json, na mesma pasta da planilha: uma lista JSON de 60 objetos, na ordem da planilha, cada um com "id", "julgamento" (S, P ou N) e "justificativa" (uma frase curta, que pode citar o trecho). Leia só a planilha e grave só esse arquivo.
```

As regras de desempate (na dúvida, o valor mais baixo) e a regra do conhecimento de fora do trecho foram escritas neste registro, antes do julgamento, como detalhe das instruções. O desenho da seção 1 não muda.

## 5. Análise

- A proporção de `S` e a proporção de `S` ou `P`, cada uma com o intervalo de 95% de Wilson (1927).
- Por tipo de relação, só a contagem, sem intervalo: a amostra tem poucas relações de cada tipo.
- Sem teste de hipótese e sem limiar de aceitação: a medida é descritiva, como fixado no desenho.

## 6. O que já tinha sido visto antes deste registro

- **Pela sessão principal:**
  - os resumos do piloto e da rodada completa;
  - no arquivo do piloto, só o funcionamento: os nomes fora da lista e o JSON inválido, sem julgar nenhuma relação;
  - os resultados da busca com as relações acrescentadas ao grafo.
- **Por ninguém:** nenhuma relação foi julgada antes deste registro.

## 7. Limitações declaradas

- **Um juiz só, que é um modelo de linguagem,** sem medida de concordância com uma pessoa, como no julgamento das respostas.
- **A amostra é pequena:** com 60 relações, o intervalo de 95% tem perto de 25 pontos percentuais de largura numa proporção perto de 0,5.
- **Só as relações válidas** entram na amostra. As descartadas (2.450, das quais 2.412 com nome fora da lista) e os trechos sem relação não são julgados: a medida é de precisão, e não de cobertura.
