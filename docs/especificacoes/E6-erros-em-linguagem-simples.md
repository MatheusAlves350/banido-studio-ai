# E6. Mensagens de erro em linguagem simples com "o que fazer agora"

> Documento de especificação. Nenhum código foi alterado. Origem: proposta de 25/11/2026, frente E.

## 1. Problema

**Analogia:** uma luz vermelha no painel do carro sem explicação deixa o motorista parado. Um aviso "pressão baixa no pneu dianteiro esquerdo: calibre para 2,3 bar" resolve o problema.

Hoje os erros técnicos aparecem como texto de exceção do Python ou do FFmpeg. Um amigo leigo não sabe o que fazer.

## 2. Objetivo

Traduzir os erros conhecidos em três partes:
1. **O que aconteceu**, em uma frase.
2. **O que fazer agora**, em um passo.
3. **Quando procurar ajuda**, com o botão de relatório (F3).

## 3. Exemplos

| Erro técnico | Mensagem proposta |
| :-- | :-- |
| `CUDA out of memory` | "A placa de vídeo ficou sem memória. Vamos tentar de novo com menos memória. Se falhar, feche outros programas e tente outra vez." |
| `ffmpeg não encontrado` | "Falta um componente de vídeo (FFmpeg). Reinstale o programa pelo instalador oficial." |
| Espaço insuficiente (`OSError 28`) | "Acabou o espaço no disco. Libere cerca de X GB em Y." |
| Porta ocupada | "Outro programa está usando a porta 8765. Feche a janela anterior do Banido Studio." |

## 4. Como funciona

- Tabela de erros conhecidos, mantida em um arquivo próprio, fácil de estender.
- Erro desconhecido cai em uma mensagem genérica, com o botão de relatório.
- A tradução não esconde o erro original: ele fica disponível em "Detalhes técnicos", recolhido.

## 5. Riscos

- **Tabela desatualizada:** cada erro novo precisa de linha própria. Mitigação: teste que lista erros sem tradução.
- **Mensagem enganosa:** uma tradução errada pode mandar o usuário ao lugar errado. Mitigação: revisão de cada mensagem.

## 6. Testes

- Teste automatizado: cada erro da tabela gera a mensagem esperada.
- Teste de que o erro original aparece em "Detalhes técnicos".

## 7. Estimativa

- **Esforço:** médio (2 a 3 dias), sendo a maior parte a revisão das mensagens.
- **Risco:** baixo.

## 8. Perguntas em aberto

1. Qual a lista inicial dos erros que mais aparecem para os amigos?
