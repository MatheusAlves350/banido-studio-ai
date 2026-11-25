# C3. Exportar vários cortes de uma vez, em fila

> Documento de especificação. Nenhum código foi alterado. Origem: proposta de 25/11/2026, frente C.

## 1. Problema

**Analogia:** é como revelar um rolo de fotos: uma por uma, com o usuário parado na frente da máquina.

Hoje cada corte é exportado manualmente. Com 5 a 10 destaques por sessão, o trabalho se repete várias vezes.

## 2. Objetivo

1. **Selecionar** os destaques aprovados (checkbox).
2. **Exportar em fila**, um após o outro, com barra de progresso e estado por item.
3. **Continuar** mesmo se um item falhar, registrando o erro (E6 traduz).

## 3. Regras

- Um corte por vez, para não disputar disco.
- Nome automático no padrão `DATA - JOGO - TÍTULO`, sem sobrescrever (regra atual).
- Verificação de C4 após cada item.

## 4. Riscos

- **Disco cheio no meio da fila:** mitigação com F8.
- **Ordem errada:** a fila segue a ordem cronológica.

## 5. Dependências

Depende de **C4** para conferência e de **F8** para espaço.

## 6. Estimativa

- **Esforço:** baixo (1 a 2 dias). **Risco:** baixo.

## 7. Perguntas em aberto

1. Os cortes devem ir para a mesma pasta da sessão ou para uma pasta por jogo?
