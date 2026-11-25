# B3. Ranking híbrido de destaques (IA + telemetria de hype)

> Documento de especificação. Nenhum código foi alterado. Origem: proposta de 25/11/2026, frente B.

## 1. Problema

**Analogia:** é como escolher o melhor lance de um jogo juntando a opinião do narrador com o barulho da torcida. Um dos dois sozinho engana.

Hoje a nota de 0 a 100 vem só da IA. A telemetria de risada, caos de vozes e Soundpad (já calculada) entra apenas como texto no prompt.

## 2. Objetivo

Combinar as duas fontes em uma nota final, com peso configurável:

- **Nota final = peso da IA + peso da telemetria**, com valores padrão a definir (sugestão inicial: 60% IA, 40% telemetria).
- Mostrar as duas notas separadas na interface, para o usuário entender a decisão.

## 3. Regras

- A telemetria é normalizada entre 0 e 100 antes de somar.
- Destaque sem pico de hype recebe penalidade leve, não exclusão.
- Os pesos vão para o `.env` na seção de avançado (ligado a D2).

## 4. Riscos

- **Telemetria barulhenta:** risadas do jogo podem parecer hype. Mitigação: comparar com a fala.
- **Pesos errados:** mitigação com o conjunto de referência (A1).

## 5. Dependências

Depende de **A1** para calibrar os pesos. Relaciona-se com **B2**.

## 6. Estimativa

- **Esforço:** baixo (1 a 2 dias). **Risco:** baixo.

## 7. Perguntas em aberto

1. Qual peso o Matheus quer para a telemetria?
