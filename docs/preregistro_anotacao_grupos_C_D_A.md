# Pré-registro da anotação dos grupos C e D e de A06 a A10

Gravado em 15/10/2026, por volta das 02:25 (horário de Brasília), antes de qualquer busca de anotação destas quinze perguntas. Texto editado para o repositório: foram retiradas as referências a documentos internos do projeto e acrescentadas as notas sobre as regras 3 e 6. As regras e as previsões são as gravadas.

## Regras que valem para as quinze perguntas

1. **Componentes essenciais.** Saem da pergunta como está escrita: essencial é aquilo sem o qual a resposta fica incorreta ou incompleta.
   - Quando a `justificativa_multihop` da redação (11/08/2026) nomeia uma informação que responde à pergunta, ela é essencial.
   - Quando nomeia algo que não responde à pergunta como está escrita, é apoio. Exemplo: SCHEMABINDING na C02, porque não é opção de sessão.
2. **Componentes condicionais.** Um componente marcado aqui como "essencial se existir no corpus" só entra se a busca no corpus inteiro o encontrar. Se não encontrar, sai, e a ausência fica registrada. Nenhum componente condicional é criado depois da busca.
3. **Escopo.** É decidido só pela evidência e calculado por dois critérios, porque a escolha entre eles ainda estava pendente:
   - **critério de necessidade:** `multi_documento` só se nenhum documento sozinho bastar;
   - **menor conjunto absoluto:** o conjunto com menos trechos, qualquer que seja o número de documentos.

   Os dois resultados ficam registrados. Nenhum dos dois pode ser escolhido depois de ver o resultado desta rodada.

   Nota: valeu o critério de necessidade, que é o do `dados/perguntas_avaliacao.yaml`. Nas quinze perguntas, os dois critérios deram o mesmo escopo.
4. **Ausência.** Afirmação de ausência só depois de busca no corpus inteiro, com variantes de redação. A busca é feita por frase e também por trecho.
5. **Reescrita.** Nenhuma reescrita por motivo do lado do corpus. Se a pergunta nomear o mecanismo ou repetir o título do documento, isso fica registrado, mas ela não é reescrita.
6. **`trivial_lexical`.** Vale o critério de redação usado até aqui: a pergunta nomeia o comando ou repete o título do documento do ouro.

   Nota: a definição usada no conjunto final inclui também o título da seção onde está a resposta.
7. **Identificadores.** Só de consulta: os `chunk_id` saem da fragmentação do corpus, e o autor confere cada um com uma consulta Cypher.
8. **Descarte.** Se o corpus não responder à pergunta, ela é descartada, e não adaptada.

## Contaminação declarada

Antes deste pré-registro, o anotador (uma sessão de IA) leu trechos ligados a estas perguntas em dois momentos.

**Na conferência do gabarito da múltipla escolha**, cujas questões cobrem os mesmos temas:

| Pergunta | Trechos já lidos |
|---|---|
| C01 | `create-columnstore-index#chunk7` |
| C02 | `create-indexed-views#chunk0` |
| C03 | `perform-index-operations-online#chunk1` e `guidelines-for-online-index-operations#chunk0` |
| C04 | `sys-sp-cdc-enable-table#chunk5` |
| C05 | `introduction-to-memory-optimized-tables#chunk2` e `plan-your-adoption-of-in-memory-oltp-features-in-sql-server#chunk4` |
| D01 | `alter-index#chunk2p0` e `reorganize-and-rebuild-indexes#chunk2` |
| D02 | `create-unique-indexes#chunk0` e `drop-index#chunk8` |
| D03 | `create-filtered-indexes#chunk2` |
| D04 | `truncate-table#chunk3` |
| D05 | `create-indexes-with-included-columns#chunk1`, `create-index#chunk11` e `sql-server-index-design-guide#chunk4` |
| A06 | `create-index#chunk15` e `sys-dm-db-index-physical-stats#chunk9` |
| A07 | `truncate-table#chunk5` |
| A08 | `create-index#chunk2p0` |
| A09 | `alter-index#chunk2p1` |
| A10 | `rowcount#chunk0` |

**Na anotação do grupo B:**

- `reorganize-and-rebuild-indexes#chunk2` e `#chunk3`, e `alter-index#chunk11`: ligados à D01;
- `truncate-table#chunk2`, `#chunk3` e `#chunk4`: ligados à D04;
- `set-statements-transact-sql#chunk1` e `indexes-on-computed-columns#chunk5`, que listam as opções SET: ligados à C02;
- `create-index#chunk2p0`, `#chunk12` e `#chunk15`: ligados a A06 e A08;
- `sql-server-index-design-guide#chunk2` a `#chunk4`: ligados à D05;
- `drop-index#chunk0`: ligado à D02.

Por isso, nenhuma previsão abaixo é cega. As regras, porém, foram fixadas antes das buscas completas, e é delas que depende a decisão.

## Por pergunta

### Grupo C: pré-requisitos

**C01**: What conditions must a table meet before it can have a clustered columnstore index?
- C1 (essencial): tipos de dado que impedem o columnstore clustered.
- C2 (essencial): recursos da tabela incompatíveis com o columnstore clustered, como colunas esparsas, FILESTREAM, replicação, change tracking ou CDC. Vale o que o corpus listar.
- C3 (apoio): limite de colunas; uma tabela tem no máximo um índice columnstore; PRIMARY KEY, UNIQUE e FOREIGN KEY são permitidas.
- Previsão: `um_trecho` (CREATE COLUMNSTORE INDEX, limitações). Contaminada.

**C02**: Which session options must be set to create an index on a view?
- C1 (essencial): a lista das opções SET exigidas, isto é, quais ficam em ON e quais em OFF.
- C2 (essencial se existir no corpus): ANSI_NULLS e QUOTED_IDENTIFIER precisam ter estado em ON também quando a view e as tabelas foram criadas.
- C3 (apoio): a view precisa de SCHEMABINDING, e o primeiro índice precisa ser clustered único. Não são opções de sessão.
- Previsão: `um_trecho` (Create indexed views, opções SET). Contaminada.

**C03**: What are the requirements for running an index operation without blocking ongoing queries?
- C1 (essencial): a operação online (`ONLINE = ON`).
- C2 (essencial): requisito de edição do produto.
- C3 (essencial): restrições de tipo de coluna, como LOB, para operações online.
- C4 (apoio): a operação online ainda pega bloqueios curtos no início e no fim; espaço em disco e tempdb; WAIT_AT_LOW_PRIORITY; operação retomável.
- Previsão: `multi_trecho_mesmo_doc` (Guidelines for online index operations), ou `multi_documento` se a edição e as restrições estiverem em documentos diferentes. Contaminada.

**C04**: What prerequisites and permissions are needed to enable Change Data Capture on a table?
- C1 (essencial): o CDC precisa estar habilitado no banco antes da tabela.
- C2 (essencial): a permissão para habilitar na tabela.
- C3 (apoio): a permissão para habilitar no banco. É a permissão da etapa anterior, e não a de habilitar na tabela.
- C4 (apoio): o SQL Server Agent precisa estar em execução para os jobs de captura; chave primária ou índice único se houver suporte a *net changes*; papel de acesso (`@role_name`); edição.
- Previsão: `multi_trecho_mesmo_doc` (sys.sp_cdc_enable_table: Remarks e Permissions). Contaminada.

**C05**: Can a memory-optimized table have the same index types as a regular table?
- C1 (essencial): não os mesmos. Os índices de tabela memory-optimized são próprios, hash e nonclustered de memória, e diferentes dos índices em disco.
- C2 (essencial se existir no corpus): o columnstore clustered também é aceito em tabela memory-optimized, e é o tipo em comum com a tabela comum.
- C3 (apoio): os índices existem só em memória e são reconstruídos no início; toda tabela memory-optimized precisa de pelo menos um índice; limites de quantidade.
- Previsão: `um_trecho` ou `multi_trecho_mesmo_doc` (Indexes on memory-optimized tables). Contaminada.

### Grupo D: comparação

**D01**: What is the difference between rebuilding and reorganizing an index, in terms of locking and statistics updates?
- C1 (essencial): bloqueio. O reorganize é sempre online, com bloqueios curtos. O rebuild é offline por padrão, com bloqueios longos, a menos que use `ONLINE = ON`.
- C2 (essencial): estatísticas. O rebuild atualiza as estatísticas do índice; o reorganize não atualiza.
- C3 (apoio): o rebuild recria o índice, e o reorganize desfragmenta o nível folha; diferenças no columnstore; operação retomável.
- Previsão: `multi_trecho_mesmo_doc` (manutenção de índices) ou `um_trecho`. Contaminada.

**D02**: What is the difference between creating a unique index and creating a UNIQUE constraint on the same column?
- C1 (essencial): a estrutura e a validação são as mesmas; o otimizador não distingue os dois.
- C2 (essencial): diferença de metadado. A constraint fica registrada como constraint e deixa explícito o objetivo.
- C3 (essencial): diferença de remoção. O índice criado pela constraint não sai com DROP INDEX, só com ALTER TABLE ... DROP CONSTRAINT.
- C4 (apoio): opções que só o índice avulso aceita, como filtro e INCLUDE.
- Previsão: `multi_documento` (índices únicos com DROP INDEX ou ALTER TABLE). Contaminada.

**D03**: In what situations is a filtered index preferable to an indexed view?
- C1 (essencial): quando a condição é simples e numa tabela só. O índice filtrado só aceita comparações simples numa tabela, e lógica complexa ou com várias tabelas exige view.
- C2 (essencial): as vantagens do índice filtrado sobre a view indexada que o corpus listar, como menor custo de manutenção, melhor qualidade de plano, rebuild online e índice não único.
- Previsão: `um_trecho` (Create filtered indexes). Contaminada.

**D04**: What is the difference between DELETE and TRUNCATE TABLE regarding the transaction log and foreign key constraints?
- C1 (essencial): log. O DELETE registra cada linha removida; o TRUNCATE registra só a desalocação das páginas.
- C2 (essencial): chave estrangeira. O TRUNCATE não pode ser usado em tabela referenciada por FOREIGN KEY, salvo autorreferência; o DELETE pode, respeitando a constraint.
- C3 (apoio): o TRUNCATE reinicia a identidade; usa menos bloqueios; não dispara triggers de DELETE.
- Previsão: `multi_trecho_mesmo_doc` (TRUNCATE TABLE: Remarks e restrições). Contaminada.

**D05**: What is the difference between included columns in an index and columns that are part of the index key?
- C1 (essencial): onde ficam. As colunas de chave estão em todos os níveis da árvore e definem a ordem do índice; as incluídas ficam só no nível folha.
- C2 (essencial): as incluídas não contam para os limites da chave, nem em número de colunas nem em tamanho, e aceitam tipos que a chave não aceita.
- C3 (apoio): quando usar cada uma. As de chave servem para predicados, ordenação e agregação; as incluídas, para cobrir a consulta.
- Previsão: `um_trecho` ou `multi_trecho_mesmo_doc` (Create indexes with included columns). Contaminada.

### A06 a A10: controle, um salto

**A06**: What does the DROP_EXISTING clause do in an index operation?
- C1 (essencial): remove e recria o índice existente, de mesmo nome, numa operação só, com a definição nova.
- C2 (apoio): com DROP_EXISTING, os nonclustered são reconstruídos uma vez só, e só se a definição mudar, contra duas vezes com DROP seguido de CREATE.
- C3 (apoio): restrições, como as mudanças de tipo de índice que não são permitidas.
- Previsão: `um_trecho` (CREATE INDEX, argumento DROP_EXISTING). Contaminada.

**A07**: What permission is required to run TRUNCATE TABLE?
- C1 (essencial): ALTER na tabela, como permissão mínima.
- C2 (apoio): papéis que já têm a permissão; ela não é concedível em separado.
- Previsão: `um_trecho` (TRUNCATE TABLE, Permissions). Contaminada.

**A08**: Which data types cannot be used as an index key column?
- C1 (essencial): os tipos de objeto grande (LOB) que não podem ser chave.
- C2 (essencial se existir no corpus): outros tipos excluídos da chave fora dos LOB, por exemplo tipos CLR não ordenados ou tipos novos.
- C3 (apoio): os mesmos tipos podem ser colunas incluídas, salvo exceções.
- Previsão: `um_trecho` (CREATE INDEX, argumento de coluna). Contaminada.

**A09**: What does the PAD_INDEX option do?
- C1 (essencial): aplica o percentual de espaço livre do fill factor às páginas de nível intermediário do índice.
- C2 (apoio): padrão OFF; só tem efeito com FILLFACTOR; o mínimo de linhas por página intermediária.
- Previsão: `um_trecho`. Há vários trechos alternativos (CREATE INDEX, ALTER INDEX, ALTER TABLE). Contaminada.

**A10**: What value does @@ROWCOUNT return after a SELECT statement?
- C1 (essencial): o número de linhas devolvidas pelo SELECT.
- C2 (apoio): acima de 2 bilhões, usar ROWCOUNT_BIG; comandos que zeram o valor.
- Previsão: `um_trecho` (@@ROWCOUNT). Contaminada.
