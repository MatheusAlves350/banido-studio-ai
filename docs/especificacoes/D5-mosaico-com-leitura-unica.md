# D5. Mosaico com leitura única do vídeo

> Documento de especificação. Nenhum código foi alterado. Origem: proposta de 25/11/2026, frente D.

## 1. Problema

**Analogia:** para montar um álbum de 16 fotos de uma viagem de 4 horas, a pessoa abre a caixa de fotos 16 vezes. Com uma única abertura, separa as 16 de uma vez.

Hoje o `mosaic_generator.py` chama o FFmpeg **uma vez por quadro** (16 chamadas). Em um MKV 4K de várias horas, cada chamada faz um salto até o ponto no arquivo, o que é lento no disco.

## 2. Objetivo

Gerar os 16 quadros em **uma única passada** do FFmpeg, usando um filtro de seleção de quadros.

## 3. Proposta técnica

- Um único comando com filtro que seleciona os quadros nos instantes calculados (`select` com a lista de tempos).
- Manter a aceleração CUDA quando disponível e o fallback para CPU.
- Manter exatamente os mesmos instantes (3% a 97% da duração), para o resultado não mudar.

## 4. Como validar

- **Teste de equivalência:** os 16 quadros gerados pelo novo método devem ser idênticos aos atuais, ou muito próximos (diferença de compressão apenas).
- **Medida de tempo:** medir antes e depois em um MKV real, em HD e em SSD.

## 5. Riscos

- **Quadros diferentes:** o filtro de seleção pode escolher o quadro-chave mais próximo, e não o exato. Mitigação: o teste de equivalência.
- **Comando muito longo:** limite de tamanho da linha de comando no Windows. Mitigação: usar arquivo de script temporário.

## 6. Estimativa

- **Esforço:** médio (2 a 3 dias), com medição.
- **Risco:** médio, pelo risco de quadros diferentes.

## 7. Perguntas em aberto

1. Vale a pena esperar a medição do A1 para decidir?
