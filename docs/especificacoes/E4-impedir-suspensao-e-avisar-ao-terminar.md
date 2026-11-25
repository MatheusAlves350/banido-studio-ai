# E4. Impedir que o Windows suspenda durante o processamento e avisar ao terminar

> Documento de especificação. Nenhum código foi alterado. Origem: proposta de 25/11/2026, frente E.

## 1. Problema

**Analogia:** é como deixar um bolo no forno e sair de casa. Se o forno desliga sozinho (suspensão), o bolo fica cru; se ninguém avisa quando fica pronto, ele queima.

Hoje o processamento pode ser interrompido porque o Windows entra em suspensão ou em modo de espera, e o usuário só descobre no dia seguinte.

## 2. Objetivo

1. **Manter o PC acordado** enquanto um job está ativo, sem alterar a configuração global de energia do Windows de forma permanente.
2. **Avisar ao terminar**, com notificação do Windows, e opcionalmente com mensagem em um canal do Discord por webhook.
3. **Avisar também em caso de falha**, com o motivo em linguagem simples (ligado a E6).

## 3. Como manter o PC acordado

- Usar a chamada do Windows `SetThreadExecutionState` com `ES_CONTINUOUS | ES_SYSTEM_REQUIRED`, ativada no início do job e liberada ao fim.
- A chamada é **temporária**: se o programa fechar, o Windows volta ao padrão sozinho. Não altera o plano de energia.
- O monitor pode apagar (não é o objetivo); apenas o sistema fica ativo.

## 4. Notificações

| Canal | Quando | Configuração |
| :-- | :-- | :-- |
| Notificação do Windows | Fim do job e falha | Automática, sem configuração |
| Discord por webhook | Fim do job (opcional) | Campo na interface com o link do webhook, nunca no código |

O link do webhook é tratado como segredo: aparece mascarado na interface, igual às chaves de API.

## 5. Riscos

- **Bloqueio indevido:** se o programa travar, a chamada continua ativa até o processo morrer. Mitigação: liberar sempre no bloco `finally` e ao encerrar o servidor.
- **Notificação não aparece:** pode ser bloqueada pelas configurações do Windows. Mitigação: o painel mostra o mesmo aviso.
- **Webhook vazado:** qualquer pessoa com o link pode postar no canal. Mitigação: mascarar e não registrar o link em logs.

## 6. Testes

- Teste automatizado da liberação da chamada em caso de erro (simulado).
- Teste manual em máquina real: iniciar um job curto, deixar o PC inativo e conferir que não suspende.

## 7. Estimativa

- **Esforço:** baixo (até 1 dia).
- **Risco:** baixo.

## 8. Relações

- Relaciona-se com **D4** (pausa com jogo aberto) e **E3** (fila noturna), que dependem do mesmo worker.
- Relaciona-se com **F3** (relatório) para registrar o motivo das falhas.

## 9. Perguntas em aberto

1. O Matheus quer o aviso no Discord, ou basta a notificação do Windows?
