# Como desinstalar o Banido Studio AI

O desinstalador remove o programa (Python portátil, bibliotecas de IA, FFmpeg, MKVToolNix e o atalho da Área de Trabalho). **Por padrão, seus dados são mantidos**: gravações, cortes, vozes, banco de dados, modelos e o arquivo `.env`.

## Se você instalou pelo `BanidoStudio-Setup.exe`

Abra **Configurações do Windows > Aplicativos > Aplicativos instalados**, procure **Banido Studio AI** e clique em **Desinstalar**. Os dados são mantidos.

## Se você instalou pelo ZIP (`Instalar_Banido_Studio.bat`)

Dê dois cliques em `Desinstalar_Banido_Studio.bat`. Ele está dentro da pasta extraída do ZIP e também em `%LOCALAPPDATA%\BanidoStudio\codigo\`. Ele pergunta:

1. Se deseja remover o programa (S/N). Seus dados são mantidos.
2. Se deseja apagar **também** os dados. Para isso é preciso digitar a palavra `APAGAR`. Apenas apertar Enter mantém tudo.

## O que nunca é apagado

- Pastas de modelos que ficam **fora** da pasta de dados (por exemplo, de uma instalação antiga reaproveitada).
- Qualquer coisa fora da pasta `%LOCALAPPDATA%\BanidoStudio` e da pasta de dados escolhida na instalação.

## Reinstalar depois

Rodar o instalador de novo reconhece o `.env` e a pasta de dados mantidos, sem perguntar tudo outra vez.
