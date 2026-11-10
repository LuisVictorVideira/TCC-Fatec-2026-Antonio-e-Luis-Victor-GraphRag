# Pré-registro da anotação do grupo B (B04 a B12)

Gravado em 15/10/2026, antes das buscas de anotação destas perguntas. Texto editado para o repositório: foram retiradas as referências a documentos internos do projeto e acrescentada a nota sobre a regra 1. As regras e as previsões são as gravadas.

## Regras que valem para as nove perguntas

1. O escopo é decidido só pela evidência. É o menor conjunto de trechos que cobre todos os componentes essenciais:
   - `um_trecho`, se um único trecho cobre tudo;
   - `multi_trecho_mesmo_doc`, se são vários trechos do mesmo documento;
   - `multi_documento`, se são precisos documentos diferentes.

   Com trechos alternativos, conta o menor conjunto possível.
2. Essencial é aquilo sem o qual a resposta fica incorreta ou incompleta para a pergunta como está escrita. O resto é apoio.
3. Afirmação de ausência ("nenhum trecho diz X") só depois de busca no corpus inteiro, com variantes de redação.
4. Nenhuma reescrita por motivo do lado do corpus. Se a pergunta nomear o mecanismo ou repetir o título de um documento, isso fica registrado, mas ela não é reescrita.
5. Identificadores só de consulta: os `chunk_id` saem da fragmentação do corpus, e o autor confere cada um com uma consulta Cypher.

Nota sobre a regra 1: na B07, o menor conjunto absoluto tem dois trechos em dois documentos, e o menor conjunto dentro de um documento só tem três trechos. Valeu o critério de necessidade (`multi_documento` só quando nenhum documento sozinho basta), e a B07 ficou `multi_trecho_mesmo_doc`. Nas demais perguntas, os dois critérios coincidem.

## Contaminação declarada

Antes deste pré-registro, ao conferir o gabarito da múltipla escolha, o anotador (uma sessão de IA) leu trechos ligados a B04, B05, B06, B07, B08, B10, B11 e B12. Os principais foram:

- `alter-index#chunk11`;
- `alter-table#chunk5p2`;
- `sql-server-index-design-guide#chunk2` e `#chunk3`;
- `indexes-on-computed-columns#chunk3` e `#chunk6`;
- `truncate-table#chunk3` e `#chunk4`;
- `create-table#chunk2p2`;
- `sys-dm-db-index-usage-stats#chunk1`.

As previsões dessas perguntas não são previsões cegas. As regras, porém, foram fixadas antes da busca completa, e é delas que depende a decisão.

## Por pergunta

**B04**: What happens to a table's statistics when an index is rebuilt?
- C1 (essencial): o rebuild atualiza as estatísticas do índice (full scan no não particionado; amostragem padrão no particionado).
- C2 (essencial): as demais estatísticas de coluna da tabela não são atualizadas pelo rebuild. Fica como essencial porque a pergunta fala nas estatísticas da tabela, e não só nas do índice.
- Previsão: `um_trecho` (ALTER INDEX, seção Statistics), contaminada.

**B05**: What happens if I try to drop a column that is part of an index?
- C1 (essencial): o ALTER TABLE DROP COLUMN falha, porque a coluna não pode ser removida enquanto estiver num índice, como chave ou INCLUDE.
- C2 (apoio): remover o índice antes.
- Previsão: `um_trecho` (ALTER TABLE, argumento DROP COLUMN), contaminada.

**B06**: Does the choice of clustered index key affect the size of the nonclustered indexes on the same table?
- C1 (essencial): a chave do clustered é guardada em cada linha de todo índice nonclustered (localizador de linha); chave larga significa nonclustered maiores.
- Previsão: `um_trecho` (guia de design, propriedades do clustered), contaminada.

**B07**: Can I create an index on a computed column? What conditions must the column meet?
- C1 (essencial): sim, pode.
- C2 (essencial): determinismo.
- C3 (essencial): precisão, com a exceção da coluna PERSISTED.
- C4 (essencial): tipo de dado permitido como chave.
- C5 (essencial): opções SET obrigatórias.
- C6 (apoio): requisito de propriedade (dono das funções).
- Previsão: `multi_trecho_mesmo_doc` (Indexes on computed columns), ou `um_trecho` se uma seção do CREATE INDEX resumir tudo.

**B08**: Does running TRUNCATE TABLE on a table have any effect on its indexes?
- C1 (essencial): os índices continuam existindo (a estrutura da tabela, as constraints e os índices permanecem).
- C2 (essencial): as páginas de dados da tabela e dos índices são desalocadas, e os índices ficam vazios.
- Previsão: `um_trecho` ou `multi_trecho_mesmo_doc` (TRUNCATE TABLE), contaminada.

**B09**: Does partitioning an existing table affect the indexes already created on it?
- C1 (essencial): particionar uma tabela existente é feito criando ou reconstruindo o índice clustered num esquema de partição.
- C2 (essencial): os índices nonclustered existentes não são particionados nem alinhados automaticamente; continuam onde estavam até serem reconstruídos no esquema.
- C3 (apoio): índice alinhado e por que importa (troca de partições).
- Previsão: `multi_documento` (tabelas particionadas e CREATE INDEX, DROP_EXISTING). Não contaminada.

**B10**: Does enabling FILESTREAM on a table impose any requirement on that table's indexes?
- C1 (essencial): exige coluna uniqueidentifier com ROWGUIDCOL, NOT NULL, com constraint UNIQUE ou PRIMARY KEY de coluna única.
- C2 (apoio): a constraint UNIQUE ou PK é imposta por um índice único.
- C3 (essencial se existir no corpus): alguma restrição a mais sobre índices em tabela com FILESTREAM, por exemplo em tabela particionada. Por ser condicional, essa parte é decidida pela busca.
- Previsão: `um_trecho` (CREATE TABLE, FILESTREAM), contaminada.

**B11**: How can I identify indexes that no query is using, and decide whether they can be removed?
- C1 (essencial): a DMV sys.dm_db_index_usage_stats (seeks, scans e lookups contra user_updates).
- C2 (essencial): os contadores zeram quando a instância reinicia (e quando o banco é desanexado ou fechado); conferir o tempo desde o início.
- C3 (essencial): índices com custo de manutenção e pouca ou nenhuma leitura são candidatos à remoção.
- C4 (caso de fronteira). Regra fixada aqui, antes de reler: C4 só é essencial se algum trecho do corpus disser que um índice sem leituras pode continuar necessário (por exemplo, por impor uma PRIMARY KEY ou UNIQUE), porque aí a omissão levaria a uma decisão de remoção errada segundo o próprio corpus. Se o corpus só disser que DROP INDEX não remove índice de constraint, C4 é apoio: o comando falha e o erro protege o usuário.
- Previsão: `um_trecho` ou `multi_trecho_mesmo_doc` (a DMV), contaminada.

**B12**: Does adding an index to a table have any cost on write operations?
- C1 (essencial): sim. INSERT, UPDATE, DELETE e MERGE precisam manter os índices, o que custa escrita.
- C2 (apoio): espaço em disco; evitar excesso de índices em tabelas muito atualizadas.
- Previsão: `um_trecho` (guia de design, considerações de banco), contaminada.
