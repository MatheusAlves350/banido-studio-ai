# E5. Conferir e ajustar o OBS pela interface, com edição local antes de aplicar

> Documento de especificação. Nenhum código foi alterado. Origem: proposta de 25/11/2026, frente E.

## 1. Problema

**Analogia:** hoje o OBS é como um painel de avião que só o piloto sabe ler. O `obs_fixer.py` faz a configuração sozinho, por linha de comando, e o amigo leigo não tem como saber o que foi mudado.

O `obs_fixer.py` cria backup da cena e aplica o roteamento de 6 faixas. Ele funciona, mas:
1. Exige o terminal.
2. Não mostra o que será alterado antes de alterar.
3. Não permite escolher configurações.

## 2. Restrição definida pelo Matheus

**O `obs_fixer.py` permanece como está.** A nova tela é uma camada por cima, que não substitui o script.

## 3. Requisito principal: escolher e editar antes de aplicar

Antes de aplicar qualquer alteração, a pessoa deve poder:

1. **Escolher as configurações** em uma lista com a explicação de cada uma (ex.: layout de 6 faixas, 2 faixas ou 1 faixa; nome de cada fonte de áudio).
2. **Editar localmente** os valores propostos, incluindo nomes de cenas e fontes, sem tocar no OBS ainda.
3. **Ver o diff**: lista do que mudará, lado a lado com o estado atual.
4. **Aplicar ou cancelar.** Nada muda sem o clique em "Aplicar".

**Analogia:** é como o rascunho de uma carta. Você escreve, revisa, compara com a versão anterior, e só então envia.

## 4. Fluxo proposto

| Etapa | O que o usuário vê | O que acontece por trás |
| :-- | :-- | :-- |
| 1. Conferir | Lista do estado atual do OBS | Leitura pelo WebSocket, ou por leitura da coleção de cenas |
| 2. Escolher | Layout desejado com explicação | Nenhuma alteração |
| 3. Editar | Campos editáveis em rascunho | Nenhuma alteração |
| 4. Revisar | Diferenças em destaque | Cálculo do diff |
| 5. Aplicar | Botão de confirmação | Chama o `obs_fixer.py` com os valores escolhidos |

## 5. Fonte de dados do OBS

- Usar o WebSocket do OBS, se estiver ativo. Se não estiver, mostrar instrução em português para ativar, com link para a tela do OBS.
- Sem o WebSocket, a tela funciona só em modo leitura da configuração salva.

Relaciona-se com **D4**: a mesma fonte de estado pode indicar se o OBS está gravando.

## 6. Backup

- Todo "Aplicar" cria backup antes, como o `obs_fixer.py` já faz.
- Botão "Desfazer" restaura o último backup.

## 7. Riscos

- **Alteração indesejada na cena:** mitigado pelo diff e pela confirmação.
- **Versão do OBS:** o WebSocket muda entre versões. Mitigação: checar versão e avisar.
- **Dupla escrita:** se o usuário alterar o OBS durante a edição, o rascunho fica desatualizado. Mitigação: reler o estado antes de aplicar e avisar se mudou.

## 8. Testes

- Teste de que o diff mostra exatamente o que o `obs_fixer.py` aplicaria.
- Teste de que nada é aplicado sem confirmação.
- Teste manual com o OBS real.

## 9. Estimativa

- **Esforço:** médio (3 a 5 dias).
- **Risco:** médio, pela dependência do WebSocket.

## 10. Perguntas em aberto

1. O Matheus aceita ativar o WebSocket do OBS como passo único de configuração? Isso é uma configuração manual, mas feita uma vez e com instrução na tela.
