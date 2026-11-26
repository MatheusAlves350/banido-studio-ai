"""
Erros em linguagem simples (item E6 do roadmap).

Cada erro conhecido vira três partes: o que aconteceu, o que fazer agora e se vale pedir ajuda.
O texto original nunca é escondido: ele volta em "technical" e a tela mostra em "Detalhes técnicos".

Para ensinar um erro novo ao programa, acrescente uma linha em REGRAS (a ordem importa: vale a primeira
que combinar). Erro que nenhuma regra reconhece cai na mensagem genérica, que sempre sugere o relatório.
"""
import re
import shutil
from typing import Any, Dict, List, Optional, Tuple

from app.config import SERVER_PORT, TEMP_DIR

# Prefixos que o programa põe antes da mensagem original; saem na hora de procurar o erro.
_PREFIXOS = (
    "Aviso (transcrição e .SRT preservados!):",
    "Erro:",
    "Aviso:",
)

# (código, padrões em regex, o que aconteceu, o que fazer agora, sugerir relatório?)
# Nos textos, {pasta}, {livre} e {porta} são preenchidos na hora.
REGRAS: List[Tuple[str, List[str], str, str, bool]] = [
    ("sem_memoria_gpu",
     [r"CUDA out of memory", r"CUBLAS_STATUS_ALLOC_FAILED", r"cudaErrorMemoryAllocation",
      r"CUDA failed with error out of memory", r"OutOfMemoryError", r"CUDNN_STATUS_(ALLOC|INTERNAL)"],
     "A placa de vídeo ficou sem memória.",
     "Feche jogos e outros programas pesados e clique em Processar de novo (o trabalho já feito é aproveitado). "
     "Se repetir, escolha um modelo de transcrição mais leve em Configurações avançadas.",
     False),
    ("sem_espaco_disco",
     [r"No space left on device", r"\[Errno 28\]", r"\[WinError 112\]", r"espaço insuficiente",
      r"not enough space on the disk"],
     "Acabou o espaço no disco.",
     "Libere espaço na pasta de trabalho ({pasta}; livre agora: {livre}). "
     "Apague gravações antigas já publicadas ou esvazie a lixeira e tente de novo.",
     False),
    ("ffmpeg_ausente",
     [r"ffmpeg não encontrado", r"ffmpeg not found", r"ffprobe não encontrado",
      r"No such file or directory: 'ffmpeg'", r"\[WinError 2\][^\n]*ffmpeg", r"'ffmpeg' is not recognized"],
     "Falta um componente de vídeo (FFmpeg).",
     "Reinstale o programa pelo instalador oficial (BanidoStudio-Setup.exe), que traz o FFmpeg.",
     True),
    ("mkvtoolnix_ausente",
     [r"mkvpropedit[^\n]*(não encontrado|not found|não foi encontrado)", r"No such file or directory: 'mkvpropedit'"],
     "Falta o MKVToolNix, usado para gravar os capítulos dentro do vídeo.",
     "Instale o MKVToolNix (mkvtoolnix.download) ou reinstale o programa pelo instalador oficial.",
     False),
    ("porta_ocupada",
     [r"address already in use", r"\[WinError 10048\]", r"\[Errno 98\]", r"Only one usage of each socket address"],
     "Outro programa está usando a porta {porta}.",
     "Feche a janela anterior do Banido Studio (se estiver aberta) e abra o programa de novo.",
     False),
    ("arquivo_em_uso",
     [r"PermissionError", r"\[WinError 32\]", r"Sem permissão ou arquivo em uso", r"being used by another process",
      r"Permission denied"],
     "O Windows não deixou o programa mexer em um arquivo, porque ele está em uso.",
     "Feche o OBS, o player de vídeo e qualquer programa que esteja com a gravação aberta e tente de novo.",
     False),
    ("video_ausente",
     [r"Arquivo MKV não encontrado", r"Arquivo não encontrado", r"MKV not found"],
     "A gravação não está mais na pasta.",
     "Confira se o arquivo foi movido, renomeado ou apagado. Se foi movido, coloque-o de volta na pasta de gravações.",
     False),
    ("video_fora_do_banco",
     [r"não encontrado no banco de dados"],
     "O programa não achou esta gravação na lista dele.",
     "Feche e abra o programa de novo para ele reler a pasta de gravações.",
     True),
    ("sem_audio",
     [r"não possui nenhuma faixa de áudio", r"não possui faixa de áudio"],
     "O vídeo não tem nenhuma faixa de áudio.",
     "Confira no OBS se as faixas de áudio estavam ativas na gravação. Sem áudio não há o que transcrever.",
     False),
    ("sem_transcricao",
     [r"ainda não possui transcrição salva"],
     "Este vídeo ainda não foi transcrito.",
     "Clique em Processar para fazer o processamento completo primeiro.",
     False),
    ("chave_ia_invalida",
     [r"API key not valid", r"API_KEY_INVALID", r"Incorrect API key", r"Invalid API key", r"Unauthorized",
      r"PERMISSION_DENIED", r"(HTTP|status|code|código|error)\D{0,5}40[13]\b"],
     "A IA recusou a chave de acesso.",
     "Abra Configurações, cole a chave de novo (sem espaços) e use o botão de testar. Confira se a chave é do serviço escolhido.",
     False),
    ("chave_ia_ausente",
     [r"[Cc]have[^\n]*não configurada", r"Nenhuma chave"],
     "Falta a chave de acesso da IA.",
     "A transcrição ficou pronta. Abra Configurações, informe a chave e use Re-analisar só com IA.",
     False),
    ("limite_ia",
     [r"RESOURCE_EXHAUSTED", r"(HTTP|status|code|código|error)\D{0,5}429\b", r"[Qq]uota", r"[Rr]ate limit", r"Too Many Requests"],
     "A IA recusou o pedido por limite de uso (muitos pedidos ou cota do dia esgotada).",
     "Espere alguns minutos e use Re-analisar só com IA. Se repetir, troque de modelo ou de serviço em Configurações.",
     False),
    ("rede",
     [r"getaddrinfo failed", r"Name or service not known", r"Temporary failure in name resolution",
      r"ConnectionError", r"Connection (reset|refused|aborted)", r"timed out", r"TimeoutError",
      r"(HTTP|status|code|código|error)\D{0,5}50[0234]\b", r"UNAVAILABLE", r"Max retries exceeded"],
     "Não deu para falar com a internet ou com o serviço da IA.",
     "Confira a conexão e tente de novo. Se a internet está normal, o serviço pode estar fora do ar: espere um pouco.",
     False),
    ("modelo_nao_baixou",
     [r"huggingface", r"snapshot_download", r"cached snapshot", r"Unable to open file 'model\.bin'"],
     "Não deu para baixar ou abrir o modelo de transcrição.",
     "Confira a internet e o espaço livre no disco dos modelos. Na dúvida, apague a pasta do modelo e deixe o programa baixar de novo.",
     True),
    ("resposta_ia_invalida",
     [r"Resposta vazia", r"JSONDecodeError", r"Expecting value", r"JSON retornado pela IA", r"resposta JSON da IA",
      r"Unterminated string"],
     "A IA respondeu fora do formato esperado.",
     "Isso costuma passar sozinho: use Re-analisar só com IA. A transcrição não é perdida.",
     False),
    ("falha_ffmpeg",
     [r"FFmpeg falhou", r"ffmpeg[^\n]*(exited|code \d+)", r"Erro ao criar mix para Whisper"],
     "O FFmpeg (componente de vídeo) não conseguiu terminar a tarefa.",
     "Tente de novo. Se repetir com o mesmo vídeo, ele pode estar danificado: abra-o em um player para conferir.",
     True),
    ("worker_caiu",
     [r"Processo worker encerrou abruptamente", r"encerrou abruptamente", r"0xC0000409", r"3221226505",
      r"-1073740791", r"Lote encerrou"],
     "O processamento fechou de repente, sem aviso.",
     "Clique em Processar de novo: ele continua de onde parou. Se cair outra vez, clique em “Relatar um problema” e envie ao Matheus.",
     True),
    ("cancelado",
     [r"Cancelado pelo usuário", r"cancelado pelo usuário"],
     "O processamento foi cancelado por você.",
     "Nada a fazer. Clique em Processar quando quiser recomeçar.",
     False),
]

_COMPILADAS = [
    (cod, [re.compile(p, re.IGNORECASE) for p in pads], o_que, fazer, ajuda)
    for cod, pads, o_que, fazer, ajuda in REGRAS
]

GENERICA_O_QUE = "Algo deu errado e o programa não reconheceu o motivo."
GENERICA_FAZER = "Tente de novo. Se o erro voltar, clique em “Relatar um problema” e envie ao Matheus."


def _limpar(raw: str) -> str:
    texto = str(raw or "").strip()
    mudou = True
    while mudou:
        mudou = False
        for pref in _PREFIXOS:
            if texto.startswith(pref):
                texto = texto[len(pref):].strip()
                mudou = True
    return texto


def _formatar_gb(bytes_livres: int) -> str:
    gb = bytes_livres / (1024 ** 3)
    return f"{gb:.1f} GB".replace(".", ",")


def _dados_disco() -> Dict[str, str]:
    pasta = str(TEMP_DIR)
    try:
        livre = _formatar_gb(shutil.disk_usage(TEMP_DIR).free)
    except Exception:
        livre = "não consegui medir"
    return {"pasta": pasta, "livre": livre, "porta": str(SERVER_PORT)}


def explain(raw: Optional[str]) -> Dict[str, Any]:
    """Traduz o texto de um erro. Sempre devolve o texto original em "technical"."""
    tecnico = _limpar(raw or "")
    dados = _dados_disco()
    for cod, pads, o_que, fazer, ajuda in _COMPILADAS:
        if any(p.search(tecnico) for p in pads):
            return {
                "known": True, "code": cod,
                "what": o_que.format(**dados), "todo": fazer.format(**dados),
                "report": ajuda, "technical": tecnico,
            }
    return {
        "known": False, "code": "desconhecido",
        "what": GENERICA_O_QUE, "todo": GENERICA_FAZER,
        "report": True, "technical": tecnico,
    }


def friendly_text(raw: Optional[str]) -> str:
    """Versão em uma linha (o que aconteceu + o que fazer), para listas e histórico."""
    info = explain(raw)
    return f"{info['what']} {info['todo']}"
