# E8. Tour inicial de quatro passos e dicas contextuais

> Documento de especificação. Nenhum código foi alterado. Origem: proposta de 25/11/2026, frente E.

## 1. Problema

**Analogia:** é como um hotel novo: quem chega precisa de um mapa na recepção, não de um manual de 40 páginas.

Hoje a interface apresenta muitas opções de uma vez. Um amigo leigo não sabe por onde começar.

## 2. Objetivo

1. Um **tour de quatro passos** na primeira abertura, reaproveitando a faixa de etapas já existente (jogo, processar, vozes, publicar).
2. **Dicas contextuais** pequenas, que aparecem uma vez por tela e podem ser dispensadas.
3. Um botão **"Mostrar o tour de novo"** no menu.

## 3. Os quatro passos sugeridos

1. Colocar as gravações na pasta do OBS.
2. Escolher o jogo e processar.
3. Confirmar os nomes das vozes.
4. Copiar título, descrição e capítulos para o YouTube.

## 4. Regras

- Mostrar uma vez; não reaparecer depois de fechado.
- Cada dica tem um botão "Entendi".
- Textos curtos, em português, sem jargão.

## 5. Armazenamento

- O estado "tour visto" fica no navegador (preferência local, sem dado pessoal) ou em `settings.json`. Nenhuma das duas guarda dado sensível.

## 6. Riscos

- **Tour longo demais:** mitigado pelo limite de quatro passos.
- **Dica que some sem ser lida:** aceitável; a dica é ajuda, não obrigação.

## 7. Estimativa

- **Esforço:** baixo (1 a 2 dias).
- **Risco:** baixo.

## 8. Perguntas em aberto

1. Os textos devem ser revisados pelo próprio Matheus antes de virar código? Sugestão: sim, porque ele conhece os amigos.
