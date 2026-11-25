# B8. Mosaicos adicionais (por hora e em mudanças de cena)

> Documento de especificação. Nenhum código foi alterado. Origem: proposta de 25/11/2026, frente B.

## 1. Problema

**Analogia:** 16 fotos para um dia inteiro de viagem deixam de fora a troca de cidade. Um álbum por hora mostra melhor a história.

Hoje o mosaico tem 16 quadros uniformes para a sessão inteira, entre 3% e 97% da duração. Trocas de jogo rápidas passam despercebidas, o que afeta `other_games`.

## 2. Objetivo

1. Um mosaico **por hora** de vídeo.
2. Quadros extras em **mudanças de cena** (detectadas pela diferença entre quadros).
3. No fluxo Claude Web, enviar o mosaico como **arquivo** para anexar.

## 3. Regras

- Limite de mosaicos por sessão (ex.: 12), para não encher o contexto da IA.
- Mosaicos ficam numerados com o horário inicial.

## 4. Riscos

- **Custo de tokens com imagens:** cada mosaico consome tokens. Mitigação: limite e aviso (B10).
- **Mudança de cena falsa:** uma tela de carregamento pode parecer troca. Mitigação: ignorar quadros quase pretos.

## 5. Dependências

Relaciona-se com **B10** (custo) e com **D5** (leitura única do vídeo, que facilita gerar vários mosaicos).

## 6. Estimativa

- **Esforço:** médio (3 a 4 dias). **Risco:** baixo.

## 7. Perguntas em aberto

1. O limite de 12 mosaicos por sessão atende?
