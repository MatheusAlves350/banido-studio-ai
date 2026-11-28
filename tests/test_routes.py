"""Garante que a lista de rotas da API não muda ao reorganizar o código em app/routers/."""
from app.main import app

ROTAS_ESPERADAS = [
    ("GET,HEAD", "/openapi.json", "openapi"),
    ("GET,HEAD", "/docs", "swagger_ui_html"),
    ("GET,HEAD", "/docs/oauth2-redirect", "swagger_ui_redirect"),
    ("GET,HEAD", "/redoc", "redoc_html"),
    ("GET", "/login", "login_page"),
    ("POST", "/login", "login_submit"),
    ("GET,POST", "/logout", "logout"),
    ("", "/static", "static"),
    ("", "/mosaics", "mosaics"),
    ("", "/vozes_amigos", "vozes_amigos"),
    ("", "/cortes", "cortes"),
    ("GET", "/", "get_index"),
    ("GET", "/api/status", "server_status"),
    ("GET", "/api/videos", "list_videos"),
    ("GET", "/api/games", "list_games"),
    ("POST", "/api/games", "add_game_history"),
    ("DELETE", "/api/games/{game_name:path}", "remove_game_history"),
    ("POST", "/api/video/{video_id}/game", "save_video_game_and_context"),
    ("GET", "/api/video/{video_id}", "get_video_details"),
    ("POST", "/api/process/{video_id}", "process_video"),
    ("POST", "/api/process/{video_id}/resume-override", "resume_paused_process"),
    ("POST", "/api/reanalyze-ai/{video_id}", "reanalyze_video_ai"),
    ("POST", "/api/normalize-audio/{video_id}", "normalize_video_audio"),
    ("POST", "/api/audio/remove-backup/{video_id}", "remove_video_audio_backup"),
    ("POST", "/api/normalize-audio-batch", "normalize_all_videos_audio"),
    ("POST", "/api/normalize-audio-batch/cancel", "cancel_normalize_batch"),
    ("POST", "/api/reset/{video_id}", "reset_video"),
    ("GET", "/api/logs/video/{video_id}", "get_video_logs"),
    ("GET", "/api/logs/job/{job_id}", "get_specific_job_logs"),
    ("GET", "/api/history", "list_job_history"),
    ("POST", "/api/rename/{video_id}", "rename_video"),
    ("GET", "/api/audio/preview/{video_id}", "preview_highlight_audio"),
    ("GET", "/api/video/preview/{video_id}", "preview_clip_video"),
    ("POST", "/api/cut", "cut_highlight"),
    ("POST", "/api/cut/batch", "start_cut_batch"),
    ("GET", "/api/cut/batch/{job_id}", "get_cut_batch"),
    ("POST", "/api/cut/batch/{job_id}/cancel", "cancel_cut_batch"),
    ("POST", "/api/embed-chapters/{video_id}", "embed_chapters"),
    ("GET", "/api/chapters/text/{video_id}", "get_chapters_text"),
    ("POST", "/api/chapters/export/{video_id}", "export_chapters_txt"),
    ("GET", "/api/speakers/preview/{video_id}/{speaker_tag:path}", "preview_speaker_audio"),
    ("POST", "/api/speakers/confirm", "confirm_speaker"),
    ("POST", "/api/speakers/harvest-host/{video_id}", "harvest_host_samples"),
    ("GET", "/api/ai/estimate/{video_id}", "estimate_ai_tokens"),
    ("GET", "/api/validate-metadata/{video_id}", "validate_metadata"),
    ("POST", "/api/titles/regenerate/{video_id}", "regenerate_video_title"),
    ("GET", "/api/mosaics/{video_id}", "list_video_mosaics"),
    ("GET", "/api/thumbnails/{video_id}", "get_thumbnails"),
    ("POST", "/api/thumbnails/{video_id}", "suggest_thumbnails"),
    ("GET", "/api/ollama/status", "get_ollama_status"),
    ("GET", "/api/claude-prompt/{video_id}", "get_claude_prompt"),
    ("POST", "/api/claude-import/{video_id}", "import_claude_response"),
    ("GET", "/api/settings", "get_settings"),
    ("POST", "/api/settings", "update_settings"),
    ("GET", "/api/models/{provider}", "get_models"),
    ("POST", "/api/test-google-key", "test_google_key"),
    ("POST", "/api/test-nvidia-key", "test_nvidia_key"),
    ("GET", "/api/setup/check", "setup_check"),
    ("GET", "/api/setup", "get_setup"),
    ("POST", "/api/setup/pick-folder", "setup_pick_folder"),
    ("POST", "/api/setup", "post_setup"),
    ("GET", "/api/update/status", "update_status"),
    ("POST", "/api/update/check", "update_check_now"),
    ("POST", "/api/update/apply", "update_apply"),
    ("GET", "/api/update/progress", "update_progress"),
    ("POST", "/api/update/restart", "update_restart"),
    ("GET", "/api/diagnostics/report", "get_diagnostic_report"),
    ("GET", "/api/diagnostics/package", "download_problem_package"),
    ("GET", "/api/env-settings", "get_env_settings"),
    ("POST", "/api/env-settings/pick-folder", "env_settings_pick_folder"),
    ("POST", "/api/env-settings", "save_env_settings"),
    ("GET", "/api/glossary", "get_glossary"),
    ("POST", "/api/glossary", "update_glossary"),
    ("POST", "/api/video/{video_id}/transcript/edit", "edit_transcript_segment"),
    ("POST", "/api/video/{video_id}/transcript/undo", "undo_transcript_edit"),
    ("GET", "/api/backup/status", "backup_status"),
    ("POST", "/api/backup/create", "backup_create"),
    ("POST", "/api/backup/download", "backup_download"),
    ("POST", "/api/backup/preview", "backup_preview"),
    ("POST", "/api/backup/import", "backup_import"),
    ("POST", "/api/backup/restore", "backup_restore"),
    ("POST", "/api/backup/folder", "backup_folder"),
]


def _achatar(rotas):
    for r in rotas:
        if hasattr(r, "path"):
            yield r
        else:
            # FastAPI mais novo agrupa as rotas incluídas em um objeto próprio
            sub = getattr(r, "original_router", None) or r.router
            yield from _achatar(sub.routes)


def test_lista_de_rotas_igual_a_antes_da_separacao():
    atuais = [
        (",".join(sorted(getattr(r, "methods", None) or [])), r.path, getattr(r, "name", ""))
        for r in _achatar(app.routes)
    ]
    assert atuais == ROTAS_ESPERADAS
