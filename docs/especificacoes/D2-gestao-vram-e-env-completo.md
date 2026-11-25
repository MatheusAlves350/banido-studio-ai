# D2. Gestão de VRAM entre etapas e configuração completa pelo .env

> Documento de especificação. Nenhum código foi alterado. Origem: proposta de 25/11/2026, frente D.

## 1. Problema

**Analogia:** a RTX 4060 de 8 GB é uma mesa pequena. O Whisper large-v3 ocupa quase toda a mesa; se o SpeechBrain ou o mosaico sentam ao mesmo tempo, algo cai no chão.

Hoje:
1. As etapas (Whisper, SpeechBrain, mosaico) não liberam a VRAM de forma explícita entre si.
2. Quando falta memória, o usuário precisa editar o `.env` à mão (o README orienta trocar para `medium` e `int8_float16`). Isso contraria a exigência de experiência sem configuração manual.
3. O `.env.example` cobre o básico, mas várias opções úteis só existem no código.

## 2. Objetivo

- **Liberar a VRAM** ao fim de cada etapa pesada (descarregar modelo e chamar a limpeza de cache da GPU).
- **Recuperar sozinho** de falta de memória: tentar de novo com precisão menor (`int8_float16`) e, se preciso, modelo menor, avisando o usuário.
- **Oferecer um `.env` quase completo**, com todas as opções que o usuário provavelmente quer, comentadas e agrupadas, para edição consciente. Ver seção 5.

## 3. Estratégia de recuperação de memória

| Passo | Ação | Aviso ao usuário |
| :-- | :-- | :-- |
| 1 | Tentar a configuração do `.env` | Nenhum |
| 2 | Em falta de VRAM, repetir com `int8_float16` | "Usando precisão reduzida para caber na placa." |
| 3 | Em nova falta, usar `medium` | "Usando modelo menor para caber na placa. A qualidade cai um pouco." |
| 4 | Se nada couber, usar CPU | "Sem memória na placa; processamento vai ficar mais lento." |

Cada tentativa é registrada no log e no relatório (ver F3). A tentativa bem-sucedida fica salva no histórico do job, para o usuário saber o que foi usado.

## 4. Liberação entre etapas

- Ordem do pipeline: Whisper, depois SpeechBrain, depois mosaico. Cada etapa descarrega o modelo anterior antes de carregar o próximo.
- Chamar a limpeza do cache de VRAM do PyTorch entre etapas.
- Medir a VRAM livre antes de carregar (valor mínimo a definir após medição em máquina real).

## 5. Proposta de `.env` quase completo

Em vez de só o básico, o `.env.example` passa a ter seções comentadas, com o padrão indicado em cada uma:

1. **Identidade:** `BANIDO_HOST_NAME`, `BANIDO_GROUP_NAME`.
2. **Pastas:** `BANIDO_BASE_DIR`, `BANIDO_OBS_DIR`, `BANIDO_CLIPS_DIR`, `BANIDO_TEMP_DIR` (com exemplo de SSD).
3. **Servidor:** `BANIDO_HOST`, `BANIDO_PORT`, `BANIDO_ACCESS_PASSWORD` (aviso de rede).
4. **Transcrição:** `BANIDO_WHISPER_MODEL`, `BANIDO_WHISPER_DEVICE`, `BANIDO_WHISPER_COMPUTE_TYPE`, `BANIDO_WHISPER_LANGUAGE`, `BANIDO_SEPARATE_TRACKS`.
5. **Memória (novo):** `BANIDO_VRAM_FALLBACK` (ligado por padrão) e `BANIDO_VRAM_MIN_FREE_MB` (a definir).
6. **Inteligência artificial:** `BANIDO_AI_PROVIDER`, `BANIDO_GOOGLE_MODEL`, `BANIDO_NVIDIA_MODEL`, chaves de API (vazias).
7. **Atualização:** `BANIDO_UPDATE_REPO`, `BANIDO_UPDATE_BRANCH`.
8. **Avançado (comentado):** opções de diagnóstico e limites de tokens da transcrição (relaciona-se com B10).

**Regra de ouro:** cada opção vem com uma linha de explicação em português e o valor padrão. Chaves de API nunca vêm preenchidas.

Relaciona-se com **E5**: a tela de configuração pode ler e gravar este mesmo arquivo, para o usuário não precisar abri-lo.

## 6. Riscos

- Descarregar modelo entre etapas aumenta o tempo de carregamento. Mitigação: medir o custo real.
- Recuperação automática pode mascarar problema real. Mitigação: aviso claro e registro no histórico.
- Um `.env` longo intimida. Mitigação: seções com nome claro e opções avançadas recolhidas.

## 7. Estimativa

- **Esforço:** médio (2 a 3 dias), com testes em máquina real.
- **Risco:** médio.

## 8. Perguntas em aberto

1. Qual é a VRAM livre mínima que o Matheus considera segura?
2. Os amigos com PCs mais fracos devem começar já em `medium`?
