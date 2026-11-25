# E3. Fila de vários vídeos e processamento noturno

> Documento de especificação. Nenhum código foi alterado. Origem: proposta de 25/11/2026, frente E.

## 1. Problema

**Analogia:** hoje a lavanderia lava uma roupa por vez e você precisa estar lá para trocar. Com uma fila, você deixa tudo de manhã e busca de tarde.

Hoje há trava de concorrência (resposta `409`) e o processamento começa um vídeo por vez, manualmente.

## 2. Objetivo

1. Marcar várias gravações e processá-las em **sequência automática**.
2. Escolher quando começar (agora ou em um horário).
3. Opcionalmente, **desligar ou suspender o PC** ao final, com aviso e prazo para cancelar.

## 3. Fluxo do usuário

1. Selecionar gravações na lista.
2. Escolher "Processar agora" ou "Processar à noite (ex.: 23h)".
3. Escolher o que fazer ao final: nada, suspender ou desligar.
4. Ver a fila com estado de cada item: aguardando, em andamento, concluído ou falhou.

## 4. Regras da fila

- Um vídeo por vez, para respeitar a GPU de 8 GB.
- Se um vídeo falhar, a fila segue para o próximo e registra a falha (E6 traduz o motivo).
- Ao reiniciar o programa, a fila é retomada a partir dos checkpoints existentes.

## 5. Desligar ou suspender

- Antes de desligar, aviso de 60 segundos com botão "Cancelar".
- A ação usa o comando padrão do Windows e exige confirmação explícita na primeira vez.

## 6. Riscos

- **Desligamento com trabalho pendente:** mitigado pela checagem de fila vazia e pelo aviso.
- **Horário errado:** o relógio do PC é a referência; mostrar a hora prevista na tela.
- **Concorrência com o OBS:** se o jogo estiver ativo à noite, aplicar D4.

## 7. Relações

- Depende de **E4** (manter o PC acordado) para o modo noturno.
- Relaciona-se com **D4** (pausa com jogo aberto).

## 8. Estimativa

- **Esforço:** médio (3 a 4 dias).
- **Risco:** médio, pela ação de desligar.

## 9. Perguntas em aberto

1. A opção de desligar deve vir desmarcada por padrão? Recomendo sim.
2. Qual horário padrão faz sentido para os amigos?
