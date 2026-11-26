# Instalação simples no Windows (para quem não programa)

Este guia explica como instalar o Banido Studio AI **sem** instalar Python, FFmpeg, MKVToolNix ou CUDA manualmente.

## O que você precisa

1. Windows 10 ou 11 de 64 bits.
2. Placa de vídeo **NVIDIA** com o driver atualizado (recomendado: 8 GB de VRAM). Sem placa NVIDIA o programa roda, porém a transcrição fica muito mais lenta.
3. Cerca de **15 GB livres** no disco para o programa e os modelos (as gravações ocupam espaço à parte).
4. Conexão com a internet durante a instalação (downloads grandes).

## Opção A: instalador (.exe)

1. Abra a página **Releases** do repositório no GitHub e baixe `BanidoStudio-Setup.exe`.
2. Dê dois cliques. O Windows pode exibir o aviso azul "O Windows protegeu o computador" porque o instalador ainda não é assinado digitalmente: clique em **Mais informações** e depois em **Executar assim mesmo**.
3. Avance o assistente e aguarde. Uma janela preta mostrará o andamento (pode levar de 10 a 30 minutos).
4. Ao terminar, use o atalho **Banido Studio AI** na Área de Trabalho. O painel abre no navegador.

## Opção B: sem instalador (ZIP do GitHub)

1. No GitHub, clique em **Code > Download ZIP** e extraia a pasta.
2. Dê dois cliques em `Instalar_Banido_Studio.bat`.
3. O instalador mostra uma **lista de discos com o espaço livre** e indica o recomendado. Digite o número e aperte Enter (só Enter = opção recomendada), ou digite **P** para escolher uma pasta numa janela. Não é preciso digitar caminhos.
4. Use o atalho criado na Área de Trabalho.

Se já existe uma instalação em uma das opções, ela aparece marcada como "já instalado aqui (será atualizado)" e é a opção padrão. Nada é alterado no restante do Windows.

Para instalar uma cópia separada (por exemplo, de teste), escolha **outra pasta ou outro disco** na lista. A cópia ganha um atalho com nome próprio na Área de Trabalho.

## Primeiro uso

- Na primeira transcrição o modelo Whisper (cerca de 3 GB) é baixado automaticamente.
- Para escolher onde ficam as gravações e os cortes, edite o arquivo `%LOCALAPPDATA%\BanidoStudio\codigo\.env` (campos `BANIDO_OBS_DIR`, `BANIDO_CLIPS_DIR` e `BANIDO_BASE_DIR`). Recomenda-se um SSD com bastante espaço, não uma pasta sincronizada (Google Drive, OneDrive).
- As chaves de IA (Gemini, NVIDIA) podem ser colocadas pela tela de Configurações do painel.

## Atualizar

Baixe o instalador novo e execute-o de novo (ou rode `Instalar_Banido_Studio.bat` de uma versão mais nova). O código é atualizado e a pasta `data` (gravações, banco, cortes, vozes) é preservada.

## Problemas comuns

| Sintoma | O que fazer |
|---|---|
| A instalação parou no meio | Execute o instalador de novo: ele continua de onde parou. |
| "CUDA disponível: nao" no final | Atualize o driver da NVIDIA e reinstale com o instalador. |
| Erro de rede/download | Verifique a internet/antivírus e execute de novo. O registro fica em `%LOCALAPPDATA%\BanidoStudio\instalacao.log`. |
| Duas cópias ao mesmo tempo | Use portas diferentes (`BANIDO_PORT` no `.env`). |

## Para quem mantém o projeto

- `installer/Instalar.ps1`: faz toda a instalação (Python embutido 3.11, PyTorch CUDA 12.1, FFmpeg BtbN, MKVToolNix).
- `installer/banido_studio.iss`: script do Inno Setup que empacota o código no `.exe`.
- `.github/workflows/release.yml`: ao criar uma tag `v*` (ex.: `v0.1.0`), o GitHub Actions compila o instalador e o anexa à release.
- Observação: o atualizador por "git clone" (PR #2) não funciona nesta instalação, porque a pasta não é um repositório git; a atualização é feita reinstalando por cima.
