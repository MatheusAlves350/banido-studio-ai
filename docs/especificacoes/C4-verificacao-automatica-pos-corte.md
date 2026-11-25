# C4. Verificação automática após cada corte

> Documento de especificação. Nenhum código foi alterado. Origem: proposta de 25/11/2026, frente C.

## 1. Problema

**Analogia:** é conferir o troco no caixa antes de sair da loja. Depois que sai, a correção é mais difícil.

A issue **#13** pede conferência manual de sincronia. Não há checagem automática da duração, do número de faixas nem do início do vídeo depois de cada corte.

## 2. Objetivo

Após cada corte, rodar `ffprobe` e comparar com o esperado:

1. **Duração**: dentro de ±1 segundo do pedido.
2. **Faixas de áudio**: mesmo número do original, quando a opção "manter faixas" estiver marcada.
3. **Início do vídeo**: primeiro quadro próximo do tempo pedido (tolerância da #13).
4. **Arquivo íntegro**: sem erro de leitura.

## 3. Resultado para o usuário

- Verde: "Corte conferido."
- Amarelo: "Corte com diferença de X segundos. Pode ser normal; confira antes de publicar."
- Vermelho: arquivo mantido, mas marcado como suspeito, com botão para refazer.

## 4. Riscos

- **Tolerância mal calibrada:** começar com tolerância ampla e apertar após medição.
- **Custo de tempo:** `ffprobe` é rápido; o custo é desprezível.

## 5. Relações

Complementa **C1** (pré-visualização) e a issue **#13**.

## 6. Estimativa

- **Esforço:** baixo (1 a 2 dias). **Risco:** baixo.

## 7. Perguntas em aberto

1. Qual tolerância de sincronia o Matheus considera aceitável?
