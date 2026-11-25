# F3. Botão "Gerar relatório de problema"

> Documento de especificação. Nenhum código foi alterado. Origem: proposta de 25/11/2026, frente F.

## 1. Problema

**Analogia:** quando o carro do amigo quebra, ele não sabe descrever o barulho. Um formulário com fotos do painel ajuda muito mais do que "não funcionou".

Hoje, quando algo dá errado com um amigo leigo, o relato chega como "deu erro". Faltam o arquivo de log, as versões e o contexto do job.

## 2. Objetivo

Um botão na interface, **"Gerar relatório de problema"**, que cria um arquivo `.zip` com o necessário para diagnóstico, e que o usuário envia por qualquer canal.

## 3. Conteúdo do `.zip`

1. Logs recentes (últimas 24 horas, ou os últimos 2 MB).
2. Resultado do Diagnóstico existente (`diagnostics.py`).
3. Versão do programa e versões das dependências principais.
4. Histórico dos últimos jobs: etapa, duração, resultado e mensagem de erro.
5. Registro do F1 (pilha, VRAM), se existir.
6. Configurações **sem segredos**.

## 4. O que nunca entra no relatório

- Chaves de API, senhas, link de webhook e o `BANIDO_ACCESS_PASSWORD`.
- Vídeos, áudios, transcrições e nomes de arquivos de gravação.
- Caminhos completos de pasta: são trocados por apelidos (`<BASE_DIR>`, `<OBS_DIR>`).

Uma verificação automática varre o `.zip` antes de liberar o download, procurando padrões de chave e de caminho, e bloqueia se encontrar algo.

## 5. Fluxo do usuário

1. Clicar em **"Gerar relatório de problema"**.
2. Ver a lista do que será incluído e um aviso: "Nenhum vídeo ou chave de API é incluído."
3. Baixar o `.zip` e enviá-lo.

## 6. Riscos

- **Vazamento acidental:** mitigado pela lista de exclusão e pela varredura final.
- **Relatório enorme:** mitigado pelo limite de tamanho.

## 7. Testes

- Teste automatizado de que nenhuma chave de teste aparece no `.zip`.
- Teste de que caminhos completos são substituídos.

## 8. Estimativa

- **Esforço:** baixo (até 2 dias).
- **Risco:** baixo, desde que a varredura seja testada.

## 9. Perguntas em aberto

1. O envio deve ser manual (baixar e enviar) ou o relatório pode ir direto para uma issue no GitHub? A segunda opção exige token e deve ser avaliada com cuidado.
