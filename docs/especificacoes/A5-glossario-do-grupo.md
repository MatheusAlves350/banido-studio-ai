# A5. Glossário do grupo (apelidos, gírias e nomes de jogos)

> Documento de especificação. Nenhum código foi alterado. Origem: proposta de 25/11/2026, frente A.

## 1. Problema

**Analogia:** é como ensinar o estagiário os nomes internos da empresa. Sem a lista, ele escreve tudo certo, mas com nomes errados.

O Whisper erra nomes de personagens, apelidos e gírias do grupo. O `initial_prompt` já recebe o jogo, mas não uma lista de termos.

## 2. Objetivo

1. Um arquivo de **glossário** editável pela interface, com termos e grafia correta.
2. O glossário entra no `initial_prompt` do Whisper, com limite de tamanho.
3. Ao corrigir um termo na transcrição (A6), a correção pode ser sugerida para o glossário.

## 3. Regras

- Limite de cerca de 200 termos, para não ocupar o contexto do Whisper.
- Termos da lista de vozes (banco de vozes) entram automaticamente.

## 4. Riscos

- **Glossário inflado:** mitigado pelo limite e pelo aviso quando passa do tamanho.
- **Termo errado persistido:** mitigado pela revisão antes de salvar.

## 5. Dependências

Relaciona-se com **A6** (editor) e com **D6** (medir o efeito pelo conjunto de referência).

## 6. Estimativa

- **Esforço:** baixo (2 dias). **Risco:** baixo.

## 7. Perguntas em aberto

1. Quais são os termos mais errados hoje? O Matheus pode listar uma primeira versão.
