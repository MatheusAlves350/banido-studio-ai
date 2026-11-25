# B7. Temperatura menor e esquema de resposta mais rígido

> Documento de especificação. Nenhum código foi alterado. Origem: proposta de 25/11/2026, frente B.

## 1. Problema

**Analogia:** pedir a um contador que faça a folha de pagamento com a mesma liberdade de um poeta gera erros. Cada tarefa pede um nível de criatividade.

A temperatura é 0,7 para tudo: capítulos, timestamps e títulos. Timestamps e a estrutura JSON precisam de precisão; títulos se beneficiam de criatividade.

## 2. Objetivo

1. **Capítulos, timestamps e tópicos:** temperatura baixa (cerca de 0,2).
2. **Títulos e resumo:** manter criatividade (cerca de 0,8).
3. **Esquema rígido:** validar a resposta contra o formato esperado antes de aceitar; se falhar, pedir correção uma vez.

## 3. Como verificar

- Medir o número de respostas que precisam de correção, antes e depois.
- Usar o conjunto de referência (A1) quando existir.

## 4. Riscos

- **Respostas mais secas:** títulos podem ficar sem graça. Mitigação: a separação por tarefa.
- **Uma chamada a mais:** custo de tokens. Mitigação: só quando a validação falhar.

## 5. Estimativa

- **Esforço:** baixo (1 a 2 dias). **Risco:** baixo.

## 6. Perguntas em aberto

1. O Matheus prefere títulos mais ousados ou mais seguros?
