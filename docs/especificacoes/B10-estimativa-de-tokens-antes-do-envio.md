# B10. Estimativa de tokens antes de enviar à IA

> Documento de especificação. Nenhum código foi alterado. Origem: proposta de 25/11/2026, frente B.

## 1. Problema

**Analogia:** é como ver o preço da corrida antes de entrar no táxi. Hoje o usuário descobre o tamanho do envio só depois.

O Matheus informou que o limite real é o **tamanho de tokens da transcrição**. O log mostra o número depois do envio, mas não antes. Uma sessão longa pode estourar o limite do modelo.

## 2. Objetivo

1. Mostrar **antes do envio** o número estimado de tokens da transcrição e dos mosaicos.
2. Comparar com o **limite do modelo escolhido**, com indicação visual (verde, amarelo, vermelho).
3. Se estourar, sugerir a **análise em duas etapas** (B1) ou a redução do mosaico (B8).

## 3. Regras

- Estimativa simples, de cerca de quatro caracteres por token, com aviso de que é aproximada.
- Limite por modelo guardado no catálogo de modelos (`model_catalog.py`).

## 4. Riscos

- **Estimativa imprecisa:** o número real varia por idioma. Mitigação: margem de segurança e aviso.

## 5. Estimativa

- **Esforço:** baixo (1 a 2 dias). **Risco:** baixo.

## 6. Perguntas em aberto

1. Qual margem de segurança o Matheus prefere, em porcentagem do limite?
