# D6. Modo de teste comparando modelos de transcrição

> Documento de especificação. Nenhum código foi alterado. Origem: proposta de 25/11/2026, frente D.

## 1. Problema

**Analogia:** escolher o motor de um carro só pela propaganda é arriscado. Um teste na pista mostra a diferença real.

Hoje a escolha entre `large-v3` e `large-v3-turbo` (ou `medium`) é feita por palpite. O Matheus prioriza qualidade, e isso precisa de número.

## 2. Objetivo

Um modo de teste que roda o mesmo trecho com modelos diferentes e mostra, lado a lado:
1. **Taxa de erro de palavras (WER)** contra o conjunto de referência (A1).
2. **Tempo de processamento.**
3. **Uso de VRAM.**
4. **Acerto de identificação de voz** no mesmo trecho.

## 3. Fluxo

1. Escolher o trecho de referência (A1).
2. Escolher os modelos a comparar.
3. Rodar e ver a tabela com os quatro números.
4. Escolher o modelo padrão, que grava no `.env` com um clique.

## 4. Dependência

Depende de **A1** (conjunto de referência). Sem ele, o teste mede tempo e memória, mas não qualidade.

## 5. Riscos

- **Tempo do teste:** rodar três modelos em um trecho de 10 minutos leva algum tempo. Mitigação: trecho curto por padrão.
- **Interpretação errada:** um WER menor pode vir de um trecho pequeno. Mitigação: mostrar o tamanho do trecho ao lado do resultado.

## 6. Estimativa

- **Esforço:** baixo a médio (2 a 3 dias), depois de A1.
- **Risco:** baixo.

## 7. Perguntas em aberto

1. Quais modelos devem entrar na primeira comparação?
