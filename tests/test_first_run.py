from app import first_run


def test_clean_name_remove_caracteres_perigosos():
    assert first_run.clean_name('  Meu "Canal"\n#x ', "Padrao") == "Meu Canal x"
    assert first_run.clean_name("   ", "Padrao") == "Padrao"
    assert first_run.clean_name(None, "Padrao") == "Padrao"
    assert len(first_run.clean_name("a" * 200, "x")) == first_run.MAX_NAME_LEN


def test_validate_folder_exige_caminho_completo(tmp_path):
    pasta, erro = first_run.validate_folder("C")
    assert pasta is None and "caminho completo" in erro
    pasta, erro = first_run.validate_folder("")
    assert pasta is None and erro


def test_validate_folder_cria_pasta(tmp_path):
    alvo = tmp_path / "OBS" / "gravacoes"
    pasta, erro = first_run.validate_folder(f'"{alvo}"')
    assert erro == ""
    assert pasta == alvo.resolve()
    assert alvo.is_dir()


def test_upsert_env_preserva_comentarios_e_troca_valor(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        '# comentario\nBANIDO_HOST_NAME="Host"\nBANIDO_PORT=8765\n# BANIDO_OBS_DIR=\n',
        encoding="utf-8",
    )
    first_run.upsert_env({"BANIDO_HOST_NAME": "Matheus", "BANIDO_OBS_DIR": "E:\\Videos\\OBS"}, path=env)
    linhas = env.read_text(encoding="utf-8").splitlines()
    assert "# comentario" in linhas
    assert "BANIDO_PORT=8765" in linhas
    assert 'BANIDO_HOST_NAME="Matheus"' in linhas
    assert 'BANIDO_OBS_DIR="E:/Videos/OBS"' in linhas
    assert linhas.count('BANIDO_HOST_NAME="Matheus"') == 1


def test_upsert_env_cria_arquivo(tmp_path):
    env = tmp_path / "sub" / ".env"
    first_run.upsert_env({"BANIDO_GROUP_NAME": "Meu Canal"}, path=env)
    assert env.read_text(encoding="utf-8") == 'BANIDO_GROUP_NAME="Meu Canal"\n'


def test_upsert_env_nao_duplica_chave_com_export(tmp_path):
    env = tmp_path / ".env"
    env.write_text("export BANIDO_PORT=1\n", encoding="utf-8")
    first_run.upsert_env({"BANIDO_PORT": "2"}, path=env)
    assert env.read_text(encoding="utf-8").count("BANIDO_PORT") == 1
