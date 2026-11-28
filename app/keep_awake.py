"""
Mantém o Windows acordado enquanto o processamento roda (item E4 do roadmap).

Usa SetThreadExecutionState com ES_CONTINUOUS | ES_SYSTEM_REQUIRED. É um pedido temporário ligado à
thread que o fez: se o programa fechar ou travar, o Windows volta ao normal sozinho e o plano de energia
nunca é alterado. O monitor pode apagar normalmente; só o sistema fica ativo.

Fora do Windows não faz nada.
"""
import os
from contextlib import contextmanager
from typing import Callable, Optional

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def _windows_set_state(flags: int) -> int:
    import ctypes
    return int(ctypes.windll.kernel32.SetThreadExecutionState(ctypes.c_uint(flags)))


def _default_setter() -> Optional[Callable[[int], int]]:
    return _windows_set_state if os.name == "nt" else None


@contextmanager
def stay_awake(setter: Optional[Callable[[int], int]] = None):
    """Mantém o PC acordado dentro do bloco `with`. Sempre libera ao sair, mesmo com erro.

    `setter` existe para os testes trocarem a chamada do Windows por uma falsa.
    """
    set_state = setter if setter is not None else _default_setter()
    active = False
    if set_state is not None:
        try:
            active = bool(set_state(ES_CONTINUOUS | ES_SYSTEM_REQUIRED))
        except Exception:
            active = False
    try:
        yield active
    finally:
        if set_state is not None:
            try:
                set_state(ES_CONTINUOUS)
            except Exception:
                pass
