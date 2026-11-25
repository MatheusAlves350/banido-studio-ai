# C5. Exportar capítulos em arquivo `.txt` pronto para colar

> Documento de especificação. Nenhum código foi alterado. Origem: proposta de 25/11/2026, frente C.

## 1. Problema

**Analogia:** é como ter o índice de um livro dentro da capa, mas não na página onde o leitor procura.

O YouTube ignora capítulos embutidos no MKV (registrado na memória do projeto). O texto dos capítulos existe apenas dentro da descrição completa, misturado com a ficha técnica.

## 2. Objetivo

Gerar um `capitulos.txt` ao lado de cada corte ou gravação, com apenas os capítulos no formato `00:00:00 - Título`.

## 3. Regras

- Primeiro capítulo sempre em 00:00:00 (já garantido pelo `normalize_youtube_chapters`).
- Intervalo mínimo de 10 segundos (já garantido).
- Botão "Copiar capítulos" na interface, além do arquivo.

## 4. Riscos

Baixo. A única atenção é manter a mesma regra de normalização já existente, sem duplicar a lógica.

## 5. Estimativa

- **Esforço:** baixo (até 1 dia). **Risco:** baixo.

## 6. Perguntas em aberto

1. O arquivo deve ser criado para cada gravação, para cada corte, ou para ambos?
