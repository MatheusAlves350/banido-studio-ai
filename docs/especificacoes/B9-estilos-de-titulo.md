# B9. Estilos de título e ajustes com um clique

> Documento de especificação. Nenhum código foi alterado. Origem: proposta de 25/11/2026, frente B.

## 1. Problema

**Analogia:** um cardápio com cinco pratos da mesma receita não dá escolha real. Vale oferecer sabores diferentes.

Hoje o sistema gera cinco títulos de um mesmo tom.

## 2. Objetivo

1. **Cinco títulos mantidos**, conforme pedido do Matheus, em tons diferentes: um engraçado, um curioso, um direto e dois livres.
2. Botões **"Mais curto"** e **"Outro tom"** que refazem um título só.

## 3. Regras

- Todo título continua começando com o nome do jogo principal e um hífen, sem colchetes (regra atual).
- O limite de 100 caracteres (B4) é respeitado.

## 4. Riscos

- **Mais tokens:** cada ajuste é uma chamada. Mitigação: uma chamada pequena, só com o título.

## 5. Dependências

Relaciona-se com **B4** (limite de caracteres) e **B7** (temperatura).

## 6. Estimativa

- **Esforço:** baixo (1 a 2 dias). **Risco:** baixo.

## 7. Perguntas em aberto

1. Os tons devem ser fixos ou o sistema escolhe?
