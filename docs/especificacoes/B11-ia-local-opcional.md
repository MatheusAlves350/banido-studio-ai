# B11. IA local opcional (Ollama)

> Documento de especificação. Nenhum código foi alterado. Origem: proposta de 25/11/2026, frente B.

## 1. Problema

**Analogia:** cozinhar em casa é mais privado e não tem custo por refeição, mas um chef de restaurante cozinha melhor. A decisão depende do que você valoriza.

Hoje a transcrição vai para Gemini ou NVIDIA na nuvem. O Matheus respondeu que isso não é problema. A IA local seria uma opção para quem quer privacidade total ou quer evitar custo por uso.

## 2. Objetivo

Oferecer, como **opção**, um modelo local via Ollama, sem substituir a nuvem por padrão.

## 3. Restrições da GPU de 8 GB

- O Whisper ocupa grande parte da VRAM. A IA local precisa rodar **depois** do Whisper, após liberar a memória (D2).
- Modelos que cabem em 8 GB tendem a ser mais fracos em português e em humor. Essa diferença precisa ser medida com o conjunto de referência (A1).

## 4. Riscos

- **Qualidade menor:** risco principal, com títulos e capítulos piores.
- **Configuração manual:** instalar o Ollama e baixar o modelo exige passos. Contraria o princípio de não exigir configuração manual, a menos que um assistente faça isso.

## 5. Recomendação

Deixar para a **Onda 3**, depois de medir a qualidade (A1 e D6). Não é prioridade enquanto a nuvem atende.

## 6. Estimativa

- **Esforço:** alto (1 a 2 semanas). **Risco:** alto.

## 7. Perguntas em aberto

1. Há interesse real em privacidade total, ou o objetivo é só evitar custo?
