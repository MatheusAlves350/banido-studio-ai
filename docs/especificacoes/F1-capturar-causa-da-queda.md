# F1. Capturar a causa da queda do processo (0xC0000409)

> Documento de especificação. Nenhum código foi alterado. Origem: proposta de 25/11/2026, frente F.

## 1. Problema

**Analogia:** o processo cai como um carro que desliga no meio da estrada, sem luz no painel. Sem registrar o que aconteceu no motor, a oficina só pode chutar.

A memória do projeto registra que a queda do processo com código `0xC0000409` ocorre, mas a causa raiz não foi comprovada. Hoje não há registro suficiente do momento da queda.

**Sobre o código `0xC0000409`:** é uma falha do Windows (`STATUS_STACK_BUFFER_OVERRUN`), que costuma indicar corrupção de memória dentro de uma biblioteca nativa, como o FFmpeg, o CUDA ou o PyTorch. A origem exata só aparece com um registro do estado no momento da falha.

## 2. Objetivo

Registrar, no momento da queda, dados suficientes para diferenciar as causas prováveis:

1. **Pilha de execução do Python** no momento da falha (`faulthandler`).
2. **Uso de VRAM e de RAM** nos segundos antes da queda.
3. **Versões**: Python, PyTorch, CUDA, driver da placa, FFmpeg e faster-whisper.
4. **Etapa em curso**: Whisper, SpeechBrain, mosaico ou normalização.

## 3. Como registrar

- Ativar o `faulthandler` do Python gravando em arquivo dentro da pasta de logs (`BANIDO_BASE_DIR/logs`). Não é preciso configurar nada.
- Um monitor leve grava, a cada 5 segundos, o uso de VRAM e RAM em um arquivo circular dos últimos 2 minutos.
- No início de cada job, grava um cabeçalho com as versões.

## 4. Como usar o registro

- O registro alimenta o **relatório de problema** (F3), sem o usuário precisar procurar arquivos.
- O resultado é lido por quem for investigar, junto com o histórico de jobs.

## 5. Riscos

- **Overhead:** o monitor consome pouca CPU. Mitigação: medir antes de liberar.
- **Dados sensíveis:** o log pode conter caminhos de pasta com nomes pessoais. Mitigação: o relatório (F3) remove caminhos completos antes de exportar.

## 6. Critério de conclusão

- Uma queda reproduzida (ou simulada) gera um arquivo com pilha, versões e os últimos 2 minutos de VRAM.
- A causa provável passa a ser registrada em uma issue, com os dados coletados.

## 7. Estimativa

- **Esforço:** baixo (até 1 dia).
- **Risco:** baixo.

## 8. Perguntas em aberto

1. A queda ocorre sempre na mesma etapa? Se sim, o registro deve focar nela.
2. Os amigos com PCs mais fracos apresentam a mesma queda?
