# B4. Validador de metadados do YouTube

> Documento de especificação. Nenhum código foi alterado. Origem: proposta de 25/11/2026, frente B.

## 1. Problema

**Analogia:** é a revisão ortográfica antes de imprimir o cartaz. Ninguém quer descobrir o erro depois de colar 50 cartazes.

Hoje o sistema limita a descrição (5000 caracteres) e as tags (480). Não confere o limite de **100 caracteres do título**, não avisa quando o título passa de cerca de 70 (corte na busca), e mantém tags fixas como "4K 60fps" e "Discord" mesmo quando não correspondem à gravação.

## 2. Objetivo

Um validador que roda antes da tela de publicação e mostra avisos claros:

1. **Título:** limite de 100 caracteres (bloqueio) e aviso acima de 70.
2. **Descrição:** os primeiros 150 caracteres úteis, com o resumo logo no início.
3. **Tags:** sem repetição e sem tags fixas que não batem com a gravação (ex.: "4K 60fps" só se a gravação for 4K 60).
4. **Capítulos:** primeiro em 00:00:00 e intervalo mínimo de 10 segundos (já existe; a tela apenas mostra o resultado).

## 3. Como as tags fixas são verificadas

- A resolução e os quadros por segundo são lidos do `ffprobe`, não assumidos.
- A tag só aparece se o dado real confirmar.

## 4. Riscos

- **Falso aviso de tag:** mitigação com lista de exceções editável.
- **Título acima de 100 bloqueado sem motivo:** mostrar contagem exata.

## 5. Estimativa

- **Esforço:** baixo (1 a 2 dias). **Risco:** baixo.

## 6. Perguntas em aberto

1. O Matheus quer manter a tag "Grupo Banido" fixa?
