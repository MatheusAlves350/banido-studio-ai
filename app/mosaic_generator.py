import subprocess
import json
import os
import shutil
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


def get_video_duration(video_path: Path) -> float:
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        str(video_path)
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(res.stdout)
    return float(data["format"]["duration"])


def format_timestamp(seconds: float) -> str:
    s = max(0, int(seconds))
    hrs = s // 3600
    mins = (s % 3600) // 60
    secs = s % 60
    return f"{hrs:02d}:{mins:02d}:{secs:02d}"


def _load_timestamp_font(size: int = 22):
    candidates = [
        # Windows
        "C:/Windows/Fonts/consolab.ttf",
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/arial.ttf",
        # Linux
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        # macOS
        "/System/Library/Fonts/Menlo.ttc",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/Library/Fonts/Arial.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                pass
    return ImageFont.load_default()


def _extract_single_frame(video_path: Path, ts_str: str, temp_frame: Path, width: int, height: int, use_cuda: bool = True) -> bool:
    base_cmd = ["ffmpeg", "-y"]
    if use_cuda:
        base_cmd.extend(["-hwaccel", "cuda"])
    base_cmd.extend([
        "-ss", ts_str,
        "-i", str(video_path),
        "-frames:v", "1",
        "-vf", f"scale={width}:{height}",
        "-q:v", "2",
        str(temp_frame)
    ])
    res = subprocess.run(base_cmd, capture_output=True, check=False)
    return res.returncode == 0 and temp_frame.exists() and temp_frame.stat().st_size > 1000


def build_batch_command(video_path: Path, timestamps, frame_paths, width: int, height: int, use_cuda: bool = True) -> list:
    """
    Monta UM comando do FFmpeg que abre o arquivo uma vez e gera todos os quadros.

    Cada quadro usa seu próprio "-ss" antes do "-i" (salto rápido pelo índice do MKV),
    então o vídeo NÃO é decodificado inteiro: o resultado é o mesmo da extração
    quadro a quadro, só com um processo em vez de 16.
    """
    cmd = ["ffmpeg", "-y"]
    for ts in timestamps:
        if use_cuda:
            cmd.extend(["-hwaccel", "cuda"])
        cmd.extend(["-ss", f"{ts:.2f}", "-i", str(video_path)])
    for i, frame in enumerate(frame_paths):
        cmd.extend([
            "-map", f"{i}:v:0",
            "-frames:v", "1",
            "-vf", f"scale={width}:{height}",
            "-q:v", "2",
            str(frame),
        ])
    return cmd


def _frame_ok(frame: Path) -> bool:
    return frame.exists() and frame.stat().st_size > 1000


def extract_frames(video_path: Path, timestamps, temp_dir: Path, width: int, height: int):
    """
    Extrai os quadros em uma única chamada do FFmpeg (CUDA, depois CPU).
    Se a chamada única falhar, usa o método antigo, quadro a quadro.
    Retorna (lista de caminhos válidos ou None, usou_cuda).
    """
    frames = [temp_dir / f"frame_{i}.jpg" for i in range(len(timestamps))]

    for use_cuda in (True, False):
        for f in frames:
            f.unlink(missing_ok=True)
        cmd = build_batch_command(video_path, timestamps, frames, width, height, use_cuda=use_cuda)
        res = subprocess.run(cmd, capture_output=True, check=False)
        if res.returncode == 0 and all(_frame_ok(f) for f in frames):
            return list(frames), use_cuda

    # Fallback: método antigo, um quadro por vez
    cuda_ok = True
    result = []
    for ts, frame in zip(timestamps, frames):
        frame.unlink(missing_ok=True)
        ok = False
        if cuda_ok:
            ok = _extract_single_frame(video_path, f"{ts:.2f}", frame, width, height, use_cuda=True)
            if not ok:
                cuda_ok = False
        if not ok:
            ok = _extract_single_frame(video_path, f"{ts:.2f}", frame, width, height, use_cuda=False)
        result.append(frame if ok else None)
    return result, cuda_ok


def generate_video_mosaic(
    video_path: Path,
    output_jpg_path: Path,
    num_cols: int = 4,
    num_rows: int = 4,
    timestamps=None,
) -> bool:
    """
    Gera folha de contato 4x4 (16 cenas em 2240x1260) com aceleração NVDEC CUDA
    (fallback automático para CPU) e carimbo de tempo HH:MM:SS em fonte legível.

    Com `timestamps`, usa esses instantes (em segundos) no lugar da distribuição padrão e ajusta
    o número de linhas ao número de quadros (usado pelos mosaicos adicionais do B8).
    """
    temp_dir = output_jpg_path.parent / f"temp_mosaic_{output_jpg_path.stem}"
    try:
        if timestamps is None:
            duration = get_video_duration(video_path)
            total_frames = num_cols * num_rows

            # Intervalos distribuídos estrategicamente entre 3% e 97% da duração
            timestamps = [
                duration * (0.03 + 0.94 * (i / max(1, total_frames - 1)))
                for i in range(total_frames)
            ]
        else:
            timestamps = list(timestamps)
            num_rows = max(1, -(-len(timestamps) // num_cols))

        temp_dir.mkdir(parents=True, exist_ok=True)
        font = _load_timestamp_font(size=22)

        frame_images = []
        frame_width, frame_height = 560, 315
        extracted_frames, cuda_ok = extract_frames(video_path, timestamps, temp_dir, frame_width, frame_height)

        for i, ts in enumerate(timestamps):
            temp_frame = extracted_frames[i]

            if temp_frame is not None and temp_frame.exists():
                try:
                    with Image.open(temp_frame) as raw_img:
                        img = raw_img.convert("RGB")
                    draw = ImageDraw.Draw(img)

                    label = format_timestamp(ts)
                    bbox = draw.textbbox((0, 0), label, font=font)
                    tw = bbox[2] - bbox[0]
                    th = bbox[3] - bbox[1]
                    pad_x, pad_y = 10, 6
                    box = [10, 10, 10 + tw + pad_x * 2, 10 + th + pad_y * 2]
                    draw.rounded_rectangle(box, radius=6, fill=(10, 14, 20), outline=(0, 229, 255), width=2)
                    draw.text((10 + pad_x, 10 + pad_y - bbox[1]), label, fill=(255, 255, 255), font=font)

                    frame_images.append(img)
                except Exception as e:
                    print(f"Erro ao carregar frame {temp_frame}: {e}")
                    blank = Image.new("RGB", (frame_width, frame_height), color=(30, 30, 30))
                    frame_images.append(blank)
            else:
                blank = Image.new("RGB", (frame_width, frame_height), color=(30, 30, 30))
                frame_images.append(blank)

        # Montar a folha de contato 4x4 (2240x1260)
        mosaic = Image.new("RGB", (frame_width * num_cols, frame_height * num_rows))
        for idx, img in enumerate(frame_images):
            col = idx % num_cols
            row = idx // num_cols
            mosaic.paste(img, (col * frame_width, row * frame_height))

        output_jpg_path.parent.mkdir(parents=True, exist_ok=True)
        mosaic.save(output_jpg_path, quality=88, optimize=True)

        hw_lbl = "NVDEC CUDA" if cuda_ok else "CPU"
        print(f"[MOSAICO] Folha {num_cols}x{num_rows} ({frame_width * num_cols}x{frame_height * num_rows}, {hw_lbl}) salva: {output_jpg_path}", flush=True)
        return True
    except Exception as e:
        print(f"Erro ao gerar mosaico para {video_path}: {e}")
        return False
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3:
        test_video = Path(sys.argv[1])
        test_out = Path(sys.argv[2])
        if test_video.exists():
            generate_video_mosaic(test_video, test_out)
