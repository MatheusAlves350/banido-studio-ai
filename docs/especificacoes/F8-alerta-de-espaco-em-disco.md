# F8. Alerta de espaço em disco antes de começar

> Documento de especificação. Nenhum código foi alterado. Origem: proposta de 25/11/2026, frente F.

## 1. Problema

**Analogia:** é como sair de viagem sem olhar o tanque de gasolina. O carro até sai, mas pode parar no meio do caminho.

O C: do Matheus tem pouco espaço. Um job pode precisar de dezenas de GB para blocos WAV, MKV normalizado e cortes. Se o disco enche no meio, o job falha e pode deixar arquivos parciais.

## 2. Objetivo

1. **Estimar** o espaço necessário antes de começar.
2. **Comparar** com o espaço livre nas pastas de trabalho (`BANIDO_TEMP_DIR`, `BANIDO_CLIPS_DIR`, pasta de gravações).
3. **Avisar** se faltar espaço, com sugestão clara do que fazer.

## 3. Estimativa de uso

| Item | Como estimar |
| :-- | :-- |
| Blocos WAV temporários | Duração do vídeo x canais de áudio x taxa de amostragem |
| MKV normalizado | Próximo ao tamanho do original |
| Cortes | Soma dos trechos pedidos, em modo cópia |

A estimativa é conservadora e mostra a margem usada.

## 4. Mensagens

- Aviso amarelo: "Espaço apertado. Libere cerca de X GB em Y para evitar falha."
- Bloqueio vermelho: "Espaço insuficiente em Y. Escolha outra pasta de temporários nas configurações."

Ambas com botão para abrir a configuração da pasta (ligado a E5/D2).

## 5. Riscos

- **Estimativa errada:** mitigada por margem de segurança e por medição real após os primeiros blocos.
- **Espaço consumido por outro programa durante o job:** o monitor de espaço (F1) registra e avisa.

## 6. Estimativa

- **Esforço:** baixo (1 a 2 dias).
- **Risco:** baixo.

## 7. Perguntas em aberto

1. Qual margem mínima de segurança o Matheus prefere? Sugestão: 10% ou 5 GB, o que for maior.
