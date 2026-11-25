# A6. Editor de transcrição na interface

> Documento de especificação. Nenhum código foi alterado. Origem: proposta de 25/11/2026, frente A.

## 1. Problema

**Analogia:** é como uma ata de reunião sem a possibilidade de corrigir. Quem escreveu errou um nome, e o erro fica para sempre.

Pelo README, hoje só os nomes dos falantes são editáveis. Erros de palavras na transcrição exigem editar o `.srt` à mão (a confirmar no `app.js`).

## 2. Objetivo

1. Editar o texto de cada segmento na interface.
2. Ao salvar, **regenerar o `.srt`** e atualizar a busca e a descrição, quando afetadas.
3. Manter o original, para desfazer.

## 3. Regras

- Editar não altera o áudio nem o vídeo.
- Cada edição fica registrada com data, para desfazer.
- Sugestão automática para o glossário (A5) quando uma palavra for corrigida mais de uma vez.

## 4. Riscos

- **Perda de correção:** se o pipeline rodar de novo, a edição pode ser sobrescrita. Mitigação: a edição vai para um arquivo separado.
- **Editor pesado no `app.js`:** o arquivo já é grande (issue #14). Mitigação: fazer em módulo próprio.

## 5. Estimativa

- **Esforço:** médio (3 a 4 dias). **Risco:** médio.

## 6. Perguntas em aberto

1. A edição deve ficar só no app, ou também ser gravada no `.srt` original?
