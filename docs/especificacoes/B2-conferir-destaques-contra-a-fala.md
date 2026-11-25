# B2. Conferir destaques e capítulos contra a transcrição

> Documento de especificação. Nenhum código foi alterado. Origem: proposta de 25/11/2026, frente B.

## 1. Problema

**Analogia:** hoje o revisor confere só se a página existe, não se o trecho citado está no livro. Um destaque pode estar dentro da duração e ainda assim cortar uma piada no meio.

O `normalize_ai_data` (em `ai_director.py`) só ajusta datas à duração do vídeo. Não verifica se há fala naquele trecho, nem onde a frase começa e termina.

## 2. Objetivo

1. **Encaixar o início** na frase mais próxima, usando os tempos por palavra do Whisper (`word_timestamps`), com meio segundo de folga antes.
2. **Encaixar o fim** no fim da última palavra, com um segundo de folga depois.
3. **Descartar** destaques sem fala na janela, ou sem pico de hype (B3).
4. **Registrar** o ajuste feito, para transparência.

## 3. Regras

- Destaque final entre 15 e 180 segundos (já previsto no prompt).
- Ajuste máximo de 10 segundos; se precisar de mais, o destaque é marcado para revisão.

## 4. Riscos

- **Corte curto demais:** a folga de um segundo mitiga.
- **Descarte de piada boa:** destaque sem fala pode ser só uma jogada. Mitigação: manter com aviso "sem fala, só jogada" em vez de descartar.

## 5. Dependências

Relaciona-se com **C1** (pré-visualização) e com a issue **#13** (corte inteligente). As decisões devem ser tomadas juntas.

## 6. Estimativa

- **Esforço:** médio (2 a 3 dias). **Risco:** baixo.

## 7. Perguntas em aberto

1. O Matheus prefere descartar ou manter destaques sem fala?
