# D4. Pausar ou reduzir prioridade do processamento enquanto o jogo está aberto

> Documento de especificação. Nenhum código foi alterado. Origem: proposta de melhorias de 25/11/2026, frente D (desempenho e GPU de 8 GB).

## 1. Problema

**Analogia:** o PC é, ao mesmo tempo, a cozinha do restaurante e a sala de jantar. Enquanto o cliente (o jogo) está comendo, a cozinha (o Whisper e o FFmpeg) faz barulho e ocupa a mesa, e a comida chega fria e a sala fica lenta.

Hoje o pipeline roda com prioridade normal enquanto o jogo gravado pelo OBS está ativo. Consequências:

1. **Queda de quadros por segundo** na partida, o que aparece na gravação e na experiência de jogo.
2. **Disputa pela GPU**: o Whisper large-v3 ocupa a maior parte dos 8 GB de VRAM da RTX 4060. O jogo também precisa de VRAM, e a falta de memória pode derrubar o processo (ligado à questão do `0xC0000409` na memória do projeto).
3. **Disputa por disco**: a extração de blocos WAV e a gravação do MKV normalizado competem com a gravação do OBS, que também usa o disco.

## 2. Objetivo

Garantir que a partida tenha prioridade, sem o usuário precisar configurar nada.

**Critérios de sucesso:**
- Durante uma partida, a taxa de quadros do jogo não cai mais de um valor a ser medido (proposta: no máximo 5% em relação a sem processamento).
- O processamento continua sozinho quando o jogo fecha, sem ação do usuário.
- Nenhum vídeo é perdido ou corrompido por causa da pausa.

## 3. Detecção de "jogo aberto"

Três fontes possíveis, em ordem de preferência:

| Fonte | Como funciona | Vantagem | Limitação |
| :-- | :-- | :-- | :-- |
| **A. OBS em gravação** | Consulta o estado do OBS (WebSocket do OBS, se ativo) | Sinal exato: se o OBS grava, o jogo está em sessão | Exige o WebSocket ativado no OBS |
| **B. Processo do jogo** | Lista de processos observados (padrão: vazio; o usuário ou a interface podem marcar) | Simples de entender | Exige cadastro do jogo |
| **C. Uso de GPU/CPU** | Lê o uso de GPU por processos de outros programas | Não depende de cadastro | Mais complexo e mais sujeito a falsos positivos |

**Recomendação:** começar com **A**, e usar **B** como plano alternativo. Deixar **C** para depois.

## 4. Estratégias de ação quando o jogo está aberto

1. **Pausa total (padrão recomendado):** o worker entra em espera entre blocos e continua quando o jogo fecha. Mais simples e mais seguro.
2. **Redução de prioridade:** o processo do worker recebe prioridade "abaixo do normal" no Windows (`BELOW_NORMAL_PRIORITY_CLASS`). Mantém o progresso, mas ainda disputa GPU e disco.
3. **Modo misto:** reduz prioridade e pausa apenas a etapa de GPU (Whisper). Mais complexo; fica como opção futura.

## 5. Pontos de pausa seguros

A pausa só pode acontecer **entre blocos** de 5 minutos, nunca no meio de um bloco. Isso respeita os checkpoints JSON já existentes: se o processo for encerrado à força, o vídeo retoma de onde parou.

**Analogia:** é como pausar um filme nos intervalos do capítulo, não no meio de uma frase.

## 6. Interface

- Um indicador discreto na barra de progresso: "Pausado: jogo em gravação. Retoma automaticamente ao fim da partida."
- Um botão "Continuar mesmo assim", para quem quer processar durante a partida (ex.: gravação curta ou computador mais forte).
- Configuração no painel, com padrão **ligado**. Não é necessário editar o `.env`.

## 7. Riscos e cuidados

- **Falso positivo:** o sistema pode pensar que o jogo está aberto quando não está, e o processamento fica parado sem motivo. Mitigação: tempo mínimo de confirmação e aviso visível na interface.
- **Pausa indefinida:** se o jogo nunca fechar, o vídeo não termina. Mitigação: botão "Continuar mesmo assim".
- **Compatibilidade:** o WebSocket do OBS depende da versão do OBS. Mitigação: checar a versão e cair para a fonte B se não houver resposta.
- **Testes:** é necessário testar em máquina real (a memória indica que a maior parte dos testes atuais não roda na máquina do usuário). Propor issue própria para o teste manual.

## 8. Dependências

- Relaciona-se com **E4** (impedir suspensão do Windows) e **E3** (fila noturna), que tratam do mesmo worker.
- Relaciona-se com **D2** (gestão de VRAM).

## 9. Estimativa

- **Esforço:** médio (1 a 3 dias), sendo a parte maior a detecção confiável e os testes na máquina real.
- **Risco:** médio, pelo potencial de pausa indevida.

## 10. Perguntas em aberto

1. O OBS do usuário já usa o WebSocket? Se não, a fonte A exige configuração manual, o que contraria o princípio do projeto.
2. Qual o limiar aceitável de queda de quadros para o Matheus?
3. A pausa deve valer também para a etapa de normalização em passada única (#12)?
