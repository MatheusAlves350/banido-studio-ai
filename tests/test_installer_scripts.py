"""Verificações simples dos scripts de instalação (não rodam o instalador, que é só para Windows)."""
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
ARQUIVO_ULTIMA = "banido_ultima_instalacao.txt"


def test_scripts_powershell_sao_ascii():
    # O PowerShell 5.1 lê arquivos sem BOM como ANSI: acentos viram lixo e podem quebrar o script.
    for ps1 in (RAIZ / "installer").glob("*.ps1"):
        ps1.read_bytes().decode("ascii")


def test_instalador_e_bats_usam_o_mesmo_arquivo_do_ultimo_local_instalado():
    ps1 = (RAIZ / "installer" / "Instalar.ps1").read_text(encoding="ascii")
    assert ARQUIVO_ULTIMA in ps1
    for nome in ("Instalar_Banido_Studio.bat", "Iniciar_Banido_Studio.bat"):
        assert ARQUIVO_ULTIMA in (RAIZ / nome).read_text(encoding="utf-8"), nome


def test_instalador_so_pergunta_a_pasta_quando_nao_recebe_install_dir():
    # O instalador .exe (InstalarSemPerguntas.ps1) sempre passa -InstallDir; nesse caso nao pode perguntar nada.
    ps1 = (RAIZ / "installer" / "Instalar.ps1").read_text(encoding="ascii")
    assert "ContainsKey('InstallDir')" in ps1
    assert "if (-not $InstallDirExplicito)" in ps1
    sem_perguntas = (RAIZ / "installer" / "InstalarSemPerguntas.ps1").read_text(encoding="ascii")
    assert "InstallDir = $InstallDir" in sem_perguntas
