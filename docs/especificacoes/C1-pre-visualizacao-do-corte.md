# C1. Pré-visualização do corte antes de exportar

> Documento de especificação. Nenhum código foi alterado. Origem: proposta de 25/11/2026, frente C.

## 1. Problema

**Analogia:** é como provar a roupa antes de comprar. Exportar um corte às cegas e descobrir que ele começa tarde demais custa tempo e espaço.

Hoje o usuário define início e fim sem ver o resultado antes da exportação.

## 2. Objetivo

Um player na interface que mostra o trecho com:
1. **Início e fim ajustáveis** com setas de um quadro ou um segundo.
2. **Marcação da fala** (linha de tempo com as falas), para ver onde a frase começa.
3. **Sugestão do B2**, com botão "usar a sugestão".
4. **Prévia rápida** de 5 segundos antes e depois, para testar as bordas.

## 3. Como funciona

- A prévia usa o arquivo original, em modo leitura. Nada é gravado até clicar em exportar.
- Como o corte real é feito em modo cópia, a prévia avisa que o início pode ser o quadro-chave anterior (#13).

## 4. Riscos

- **Desempenho do player com 4K:** usar uma cópia leve (proxy em baixa resolução) gerada sob demanda.
- **Expectativa errada:** o aviso sobre o quadro-chave evita surpresa.

## 5. Dependências

Depende de **B2** e relaciona-se com **C4** e com a issue **#13**.

## 6. Estimativa

- **Esforço:** médio (3 a 4 dias). **Risco:** baixo.

## 7. Perguntas em aberto

1. Aceitável gerar uma cópia leve de cada corte para a prévia, ocupando espaço temporário?
