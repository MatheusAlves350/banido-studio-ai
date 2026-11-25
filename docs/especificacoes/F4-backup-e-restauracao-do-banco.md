# F4. Backup e restauração do banco de vozes e do banco de dados

> Documento de especificação. Nenhum código foi alterado. Origem: proposta de 25/11/2026, frente F.

## 1. Problema

**Analogia:** o banco de vozes é o caderno de contatos do grupo. Se o caderno se perder, não basta comprar outro; é preciso ouvir cada amigo de novo.

Hoje o banco de vozes (`vozes_amigos/`, com amostras `.wav` e `embedding.npy`) e o SQLite (`banido_studio.db`) não têm cópia de segurança nem exportação. Uma formatação do PC ou uma troca de disco apaga anos de trabalho.

## 2. Objetivo

1. **Exportar** tudo o que é essencial em um único arquivo `.zip` datado.
2. **Importar** o `.zip` em outra instalação ou após uma perda.
3. **Backup automático** periódico, sem configuração (ex.: uma vez por semana, mantendo as 4 últimas cópias).

## 3. O que entra no backup

- Pasta `vozes_amigos/` (amostras e embeddings).
- Banco SQLite em modo seguro (com o `WAL` consolidado antes da cópia).
- `settings.json`, **sem chaves de API**.

**Não entra:** gravações `.mkv`, cortes e modelos (`models/`). São grandes e podem ser baixados de novo.

## 4. Restauração

- Ao importar, mostrar o que será substituído e a data do backup.
- Antes de substituir, criar um backup do estado atual.
- Sem nunca apagar arquivos sem confirmação.

## 5. Riscos

- **Restaurar a versão errada:** mitigado pela data e pelo backup prévio.
- **Backup corrompido:** mitigado por verificação de integridade do `.zip` e do SQLite.
- **Vazamento de dados de amigos:** o backup contém vozes. Avisar que o arquivo deve ser guardado com cuidado.

## 6. Estimativa

- **Esforço:** baixo (2 dias).
- **Risco:** baixo.

## 7. Perguntas em aberto

1. Onde guardar o backup automático? Sugestão: pasta escolhida pelo usuário, em outro disco se houver.
