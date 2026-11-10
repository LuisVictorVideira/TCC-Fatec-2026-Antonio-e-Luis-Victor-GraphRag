# Perguntas de avaliação

As 31 perguntas válidas, por grupo. Cada uma aparece na forma aberta, usada na avaliação da recuperação e das respostas, e na versão de múltipla escolha, que tem redação própria. O gabarito da múltipla escolha está em negrito. Os componentes da resposta e os trechos-ouro estão em `perguntas_avaliacao.yaml`; as fontes de cada gabarito, em `questoes_multipla_escolha.yaml`.

Escopo é o resultado da anotação: a resposta está em um trecho, em vários trechos do mesmo documento ou em documentos diferentes. Trivial é a pergunta que nomeia o comando ou repete o título do documento ou da seção onde está a resposta.

## Grupo A: controle

Perguntas de controle, sobre fatos pontuais.

### A01

**Pergunta aberta:** What permission does a user need to create an index on a table?

*Que permissão um usuário precisa ter para criar um índice em uma tabela?*

Escopo: um trecho. Classe: trivial.

**Múltipla escolha:** What permission is required to create an index on a table?

- A) SELECT permission on the table
- B) CONTROL permission on the database
- **C) ALTER permission on the table, or membership in the db_ddladmin role**
- D) Membership in the sysadmin server role

### A03

**Pergunta aberta:** A DBA wants to reduce page splits on inserts into an existing index. Which setting addresses this, and when does it take effect?

*Um DBA quer reduzir as divisões de página em inserções num índice existente. Que configuração trata disso, e quando ela passa a valer?*

Escopo: vários trechos do mesmo documento. Classe: não trivial.

**Múltipla escolha:** When is the FILLFACTOR setting of an index applied?

- A) Continuously, as new rows are inserted into the table
- B) Every time the index statistics are updated
- C) Only during a REORGANIZE operation
- **D) Only when the index is created or rebuilt**

### A04

**Pergunta aberta:** What is the maximum key size for a nonclustered index in SQL Server 2016 and later?

*Qual é o limite de tamanho da chave de um índice nonclustered no SQL Server 2016 e posteriores?*

Escopo: um trecho. Classe: trivial.

**Múltipla escolha:** What is the maximum key size for a nonclustered index in SQL Server 2016 and later?

- A) 900 bytes
- **B) 1700 bytes**
- C) 8000 bytes
- D) There is no byte limit, only a limit of 16 key columns

### A05

**Pergunta aberta:** What information does the sys.dm_db_index_usage_stats view provide?

*Que informação a view sys.dm_db_index_usage_stats fornece?*

Escopo: um trecho. Classe: trivial.

**Múltipla escolha:** Which information does sys.dm_db_index_usage_stats track?

- A) The physical fragmentation percentage of each index
- B) The estimated cost of each query plan that uses the index
- **C) The number of seeks, scans, lookups and updates performed on each index**
- D) The amount of disk space consumed by each index

### A06

**Pergunta aberta:** What does the DROP_EXISTING clause do in an index operation?

*O que a cláusula DROP_EXISTING faz em uma operação de índice?*

Escopo: um trecho. Classe: trivial.

**Múltipla escolha:** What is the main advantage of rebuilding a clustered index with DROP_EXISTING = ON instead of dropping and recreating it separately?

- A) It removes the requirement for ALTER permission on the table
- B) It automatically updates all statistics on the database
- C) It converts the index into a columnstore index in the process
- **D) The nonclustered indexes on the table are not rebuilt twice**

### A07

**Pergunta aberta:** What permission is required to run TRUNCATE TABLE?

*Que permissão é necessária para executar TRUNCATE TABLE?*

Escopo: um trecho. Classe: trivial.

**Múltipla escolha:** What permission is required to run TRUNCATE TABLE?

- **A) ALTER permission on the table**
- B) DELETE permission on the table
- C) CONTROL permission on the schema
- D) Membership in the db_datawriter role

### A08

**Pergunta aberta:** Which data types cannot be used as an index key column?

*Que tipos de dados não podem ser usados como coluna-chave de um índice?*

Escopo: um trecho. Classe: não trivial.

**Múltipla escolha:** Which of the following cannot be used as a key column in an index?

- A) A uniqueidentifier column
- **B) A varchar(max) column**
- C) A deterministic and precise computed column
- D) A varchar(100) column

### A09

**Pergunta aberta:** What does the PAD_INDEX option do?

*O que a opção PAD_INDEX faz?*

Escopo: um trecho. Classe: trivial.

**Múltipla escolha:** What does the PAD_INDEX option do?

- A) It adds padding bytes to each row so that rows align to page boundaries
- B) It reserves additional transaction log space during index creation
- **C) It applies the fill factor percentage to the intermediate-level pages of the index, not only to the leaf level**
- D) It pads short key values to a fixed length before storing them

### A10

**Pergunta aberta:** What value does @@ROWCOUNT return after a SELECT statement?

*Qual é o valor de retorno de @@ROWCOUNT depois de um comando SELECT?*

Escopo: um trecho. Classe: trivial.

**Múltipla escolha:** Which function should be used instead of @@ROWCOUNT when a statement may affect more than 2,147,483,647 rows?

- A) @@CURSOR_ROWS
- B) COUNT_BIG()
- C) SET ROWCOUNT
- **D) ROWCOUNT_BIG()**

## Grupo B: impacto e dependência

O efeito de uma operação sobre outro objeto ou recurso; núcleo da hipótese do trabalho.

### B01

**Pergunta aberta:** If I remove the clustered index from a table, what happens to its storage structure?

*Se eu remover o índice clustered de uma tabela, o que acontece com a estrutura de armazenamento dela?*

Escopo: um trecho. Classe: não trivial.

**Múltipla escolha:** When a clustered index is dropped from a table, what happens to the table's storage structure?

- **A) The table becomes a heap**
- B) The table is dropped along with the index
- C) The table is automatically converted to a clustered columnstore
- D) The table becomes read-only until a new clustered index is created

### B02

**Pergunta aberta:** Does removing a table's clustered index affect the existing nonclustered indexes?

*Remover o índice clustered de uma tabela afeta os índices nonclustered que já existem nela?*

Escopo: um trecho. Classe: não trivial.

**Múltipla escolha:** When a clustered index is dropped, what happens to the existing nonclustered indexes on the same table?

- A) They are dropped together with the clustered index
- B) They remain unchanged and continue to reference the clustered key
- **C) They are rebuilt so that their row locators use a row identifier instead of the clustered key**
- D) They are disabled until they are manually rebuilt

### B03

**Pergunta aberta:** Why does DROP INDEX fail on an index that was created automatically by a constraint?

*Por que o comando DROP INDEX falha em um índice que foi criado automaticamente por uma constraint?*

Escopo: um trecho. Classe: trivial.

**Múltipla escolha:** Why does DROP INDEX fail on an index that enforces a PRIMARY KEY constraint?

- A) The index is system-owned and cannot be modified by any statement
- B) DROP INDEX only operates on nonclustered indexes
- C) The index must first be disabled with ALTER INDEX
- **D) Constraint indexes must be removed with ALTER TABLE DROP CONSTRAINT**

### B04

**Pergunta aberta:** What happens to a table's statistics when an index is rebuilt?

*O que acontece com as estatísticas de uma tabela quando eu faço rebuild de um índice?*

Escopo: um trecho. Classe: não trivial.

**Múltipla escolha:** How do rebuilding and reorganizing an index differ in their effect on statistics?

- A) Both operations update statistics with a full scan
- **B) Rebuilding updates statistics with a full scan; reorganizing does not update statistics**
- C) Reorganizing updates statistics; rebuilding leaves them unchanged
- D) Neither operation has any effect on statistics

### B05

**Pergunta aberta:** What happens if I try to drop a column that is part of an index?

*O que acontece se eu tentar remover uma coluna que faz parte de um índice?*

Escopo: um trecho. Classe: não trivial.

**Múltipla escolha:** What happens when you attempt to drop a column that is part of an index?

- **A) The statement fails; the index must be dropped first**
- B) The column is dropped and the index is silently adjusted
- C) The column is dropped and the index is automatically rebuilt
- D) The column is marked as unused but remains part of the index

### B06

**Pergunta aberta:** Does the choice of clustered index key affect the size of the nonclustered indexes on the same table?

*A escolha da chave do índice clustered influencia o tamanho ocupado pelos índices nonclustered da mesma tabela?*

Escopo: um trecho. Classe: não trivial.

**Múltipla escolha:** How does choosing a wide clustered index key affect the nonclustered indexes on the same table?

- A) It has no effect, because nonclustered indexes are stored independently
- B) It reduces their size, because rows can be located more efficiently
- C) It forces them to be rebuilt after every insert
- **D) It increases their size, because the clustered key is stored in every nonclustered index row**

### B07

**Pergunta aberta:** Can I create an index on a computed column? What conditions must the column meet?

*Posso criar um índice sobre uma coluna computada? Que condições a coluna precisa satisfazer?*

Escopo: vários trechos do mesmo documento. Classe: trivial.

**Múltipla escolha:** What must be true about a computed column for it to be used as an index key column?

- A) It must be nullable and persisted
- B) It must reference only numeric columns from the same table
- **C) It must be deterministic, and precise unless it is PERSISTED**
- D) It must have a default constraint defined on it

### B08

**Pergunta aberta:** Does running TRUNCATE TABLE on a table have any effect on its indexes?

*Executar TRUNCATE TABLE em uma tabela tem algum efeito sobre os índices dela?*

Escopo: um trecho. Classe: trivial.

**Múltipla escolha:** Which condition prevents TRUNCATE TABLE from being executed on a table?

- **A) The table is referenced by a FOREIGN KEY constraint from another table**
- B) The table has a clustered index defined on it
- C) The table contains computed columns
- D) The table has more than one nonclustered index

### B09

**Pergunta aberta:** Does partitioning an existing table affect the indexes already created on it?

*Particionar uma tabela existente afeta os índices já criados sobre ela?*

Escopo: vários trechos do mesmo documento. Classe: não trivial.

**Múltipla escolha:** In the context of partitioned tables, what is an aligned index?

- A) An index whose key columns appear in the same order as the columns of the table
- **B) An index built on the same partition scheme as its base table, so that its partitions correspond to the table's partitions**
- C) An index that has been defragmented so that its pages are physically contiguous
- D) An index whose fill factor matches the fill factor of the base table

### B10

**Pergunta aberta:** Does enabling FILESTREAM on a table impose any requirement on that table's indexes?

*Habilitar FILESTREAM em uma tabela impõe algum requisito sobre os índices dessa tabela?*

Escopo: um trecho. Classe: não trivial.

**Múltipla escolha:** What does a table require before FILESTREAM data can be stored in it?

- A) A clustered columnstore index
- B) A nonclustered index on the FILESTREAM column
- C) A partition scheme defined on the primary filegroup
- **D) A uniqueidentifier column with the ROWGUIDCOL property and a UNIQUE constraint**

### B11

**Pergunta aberta:** How can I identify indexes that no query is using, and decide whether they can be removed?

*Como identificar índices que não estão sendo usados por nenhuma consulta e avaliar se podem ser removidos?*

Escopo: um trecho. Classe: não trivial.

**Múltipla escolha:** When using sys.dm_db_index_usage_stats to find unused indexes, what limitation must be taken into account?

- A) It tracks only indexes on user tables, ignoring indexes on system tables
- B) It records seek operations but not scans or lookups
- **C) Its counters are reset when the instance restarts, so a short uptime can make a used index appear unused**
- D) It requires the database to be in the full recovery model

### B12

**Pergunta aberta:** Does adding an index to a table have any cost on write operations?

*Adicionar um índice a uma tabela tem algum custo sobre operações de escrita?*

Escopo: um trecho. Classe: não trivial.

**Múltipla escolha:** What is the main trade-off of adding a nonclustered index to a frequently modified table?

- **A) Every INSERT, UPDATE and DELETE must also maintain the index, increasing write cost and storage**
- B) The index consumes CPU only during SELECT operations
- C) The table becomes read-only while the index is in place
- D) Existing queries must be rewritten in order to use the new index

## Grupo C: pré-requisitos e restrições

O que precisa existir, ou ser permitido, para uma operação funcionar.

### C01

**Pergunta aberta:** What conditions must a table meet before it can have a clustered columnstore index?

*Que condições uma tabela precisa satisfazer para receber um índice columnstore clustered?*

Escopo: um trecho. Classe: trivial.

**Múltipla escolha:** Which of the following prevents a clustered columnstore index from being created on a table?

- A) The table contains more than one million rows
- **B) The table contains columns of unsupported data types such as ntext or sql_variant**
- C) The table already has a primary key defined
- D) The database uses the full recovery model

### C02

**Pergunta aberta:** Which session options must be set to create an index on a view?

*Que opções de sessão precisam estar configuradas para criar um índice sobre uma view?*

Escopo: um trecho. Classe: trivial.

**Múltipla escolha:** What must be created first before any other index can be created on a view?

- A) A nonclustered index on the view's key column
- B) A filtered index matching the view definition
- C) A columnstore index covering all view columns
- **D) A unique clustered index**

### C03

**Pergunta aberta:** What are the requirements for running an index operation without blocking ongoing queries?

*Que requisitos existem para executar uma operação de índice sem bloquear as consultas em andamento?*

Escopo: um trecho. Classe: não trivial.

**Múltipla escolha:** A DBA needs to rebuild a large index without blocking queries against the table. Which option makes this possible, and what is its main constraint?

- **A) WITH (ONLINE = ON); it is not available in every edition and has restrictions on certain column types**
- B) WITH (SORT_IN_TEMPDB = ON); it requires additional tempdb space
- C) WITH (MAXDOP = 1); it limits parallelism in order to reduce contention
- D) WITH (DROP_EXISTING = ON); it keeps the previous index available during the operation

### C04

**Pergunta aberta:** What prerequisites and permissions are needed to enable Change Data Capture on a table?

*Que pré-requisitos e permissões são necessários para habilitar Change Data Capture em uma tabela?*

Escopo: vários trechos do mesmo documento. Classe: trivial.

**Múltipla escolha:** Before Change Data Capture can be enabled on a table, what must happen first?

- A) The table must have a clustered index defined
- B) The database must be set to the full recovery model
- **C) Change Data Capture must be enabled at the database level**
- D) SQL Server Agent must be stopped

### C05

**Pergunta aberta:** Can a memory-optimized table have the same index types as a regular table?

*Uma tabela memory-optimized pode ter os mesmos tipos de índice de uma tabela comum?*

Escopo: documentos diferentes. Classe: trivial.

**Múltipla escolha:** Which index types are supported on memory-optimized tables?

- A) The same clustered and nonclustered rowstore indexes used by disk-based tables
- **B) Hash indexes and nonclustered indexes designed specifically for memory-optimized tables**
- C) Clustered columnstore indexes only
- D) Filtered indexes only

## Grupo D: comparação

A diferença entre dois recursos ou opções.

### D01

**Pergunta aberta:** What is the difference between rebuilding and reorganizing an index, in terms of locking and statistics updates?

*Qual a diferença entre reconstruir e reorganizar um índice, em termos de bloqueio e de atualização de estatísticas?*

Escopo: um trecho. Classe: trivial.

**Múltipla escolha:** Which statement correctly describes the difference between ALTER INDEX REBUILD and ALTER INDEX REORGANIZE?

- A) REBUILD is always an online operation, whereas REORGANIZE is always offline
- B) REORGANIZE recreates the index from scratch, whereas REBUILD only compacts existing pages
- **C) REBUILD recreates the index and updates statistics, whereas REORGANIZE defragments the leaf level and does not update statistics**
- D) The two operations are identical apart from the syntax used

### D02

**Pergunta aberta:** What is the difference between creating a unique index and creating a UNIQUE constraint on the same column?

*Qual a diferença entre criar um índice único e criar uma constraint UNIQUE sobre a mesma coluna?*

Escopo: documentos diferentes. Classe: não trivial.

**Múltipla escolha:** What is the practical difference between creating a unique index and creating a UNIQUE constraint on the same column?

- **A) They enforce uniqueness in the same way; they differ in metadata and in how they are removed**
- B) The constraint enforces uniqueness, whereas the unique index only speeds up lookups
- C) The unique index permits multiple NULL values, whereas the constraint permits none
- D) The constraint always creates a clustered index, whereas a unique index cannot be clustered

### D03

**Pergunta aberta:** In what situations is a filtered index preferable to an indexed view?

*Em que situações um índice filtrado é preferível a uma view indexada?*

Escopo: um trecho. Classe: trivial.

**Múltipla escolha:** In which situation is a filtered index preferable to an indexed view?

- A) When the data to be indexed spans several tables that must be joined
- B) When aggregate values need to be precomputed and stored
- C) When the query must return rows from a remote server
- **D) When the subset of rows to index comes from a single table and can be defined by a simple WHERE predicate**

### D04

**Pergunta aberta:** What is the difference between DELETE and TRUNCATE TABLE regarding the transaction log and foreign key constraints?

*Qual a diferença entre DELETE e TRUNCATE TABLE quanto ao log de transações e às constraints de chave estrangeira?*

Escopo: vários trechos do mesmo documento. Classe: trivial.

**Múltipla escolha:** How do DELETE and TRUNCATE TABLE differ in terms of transaction logging?

- A) TRUNCATE cannot be rolled back, whereas DELETE can
- **B) DELETE logs each row deletion individually, whereas TRUNCATE logs only the page deallocations**
- C) Both log each removed row identically, but TRUNCATE runs in its own transaction
- D) TRUNCATE writes to the log only when the database uses the simple recovery model

### D05

**Pergunta aberta:** What is the difference between included columns in an index and columns that are part of the index key?

*Qual a diferença entre colunas incluídas em um índice e colunas que fazem parte da chave do índice?*

Escopo: um trecho. Classe: não trivial.

**Múltipla escolha:** How do included columns in a nonclustered index differ from key columns?

- **A) They are stored only at the leaf level, do not count toward the index key size limit, and cannot be used in seek predicates**
- B) They are stored at every level of the index, whereas key columns are stored only at the leaf level
- C) They must have the same data type as the key columns of the index
- D) They are limited to a maximum of two columns per index
