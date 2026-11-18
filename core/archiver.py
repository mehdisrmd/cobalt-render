import os
import asyncio
import re
import glob

def sanitize_filename(name):
    return re.sub(r'[^\w\.\-\s]', '_', name)


def _split_file_sync(file_path: str, chunk_mb: int, base_name: str, dir_name: str) -> list:
    chunk_size = chunk_mb * 1024 * 1024
    parts = []
    part_num = 1
    with open(file_path, 'rb') as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            part_path = os.path.join(dir_name, f"{base_name}.zip.{part_num:03d}")
            with open(part_path, 'wb') as pf:
                pf.write(chunk)
            parts.append(part_path)
            part_num += 1
    return parts


async def split_file(file_path: str, chunk_mb: int, base_name: str, dir_name: str) -> list:
    return await asyncio.to_thread(_split_file_sync, file_path, chunk_mb, base_name, dir_name)

async def process_archive(file_path: str, comp_mode: str, password: str, updater):
    updater.action_text = "📦 Processing File"
    file_path = os.path.abspath(file_path)
    dir_name = os.path.dirname(file_path)
    file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
    raw_base = os.path.splitext(os.path.basename(file_path))[0]
    ext = os.path.splitext(file_path)[1].lower()
    new_base = sanitize_filename(raw_base)

    split_size = 95
    has_password = password and password != "None"


    if comp_mode == "rawnormal" and file_size_mb > split_size and not has_password:
        comp_mode = "zip_smart"
        updater.action_text = "🧠 Smart Zipping (Too large for Raw)"


    if comp_mode in ["rawnormal", "rawchunk"] and file_size_mb <= split_size and not has_password:
        final_path = os.path.join(dir_name, f"{new_base}{ext}")
        if file_path != final_path:
            if os.path.exists(final_path):
                os.remove(final_path)
            os.rename(file_path, final_path)
        return [final_path]


    if file_size_mb > split_size and ext in ['.mp4', '.mkv', '.avi', '.webm', '.mov'] and comp_mode == "rawchunk" and not has_password:
        updater.action_text = "🎬 Splitting Video (FFmpeg)"

        out_pattern = os.path.join(dir_name, f"{new_base}_part_%03d{ext}")
        cmd = [
            "ffmpeg", "-i", file_path,
            "-c", "copy",
            "-f", "segment",
            "-segment_time", "300",
            "-reset_timestamps", "1",
            out_pattern, "-y"
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            err_msg = stderr.decode('utf-8', 'ignore')
            raise Exception(f"FFmpeg split failed!\n{err_msg}")

        if os.path.exists(file_path):
            os.remove(file_path)

        parts = glob.glob(os.path.join(dir_name, f"{new_base}_part_*{ext}"))
        parts.sort()

        if not parts:
            raise Exception("Video split failed: output parts not found.")
        return parts


    if ext == ".zip" or os.path.join(dir_name, f"{new_base}.zip") == file_path:
        new_base = f"{new_base}_RGit"

    zip_path = os.path.join(dir_name, f"{new_base}.zip")
    if os.path.exists(zip_path):
        os.remove(zip_path)


    if has_password:
        updater.action_text = "🔐 Zipping File (7z+Pass)"
        cmd = ["7z", "a", "-tzip", f"-p{password}", "-mx=9", "-y", zip_path, file_path]
    else:
        updater.action_text = "📦 Zipping File (zip)"
        cmd = ["zip", "-j"]
        if comp_mode in ["rawnormal", "rawchunk", "zip_smart"]:
            cmd.append("-0")
        else:
            cmd.append("-9")
        cmd.extend([zip_path, file_path])

    process = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        err_msg = stderr.decode('utf-8', 'ignore') or stdout.decode('utf-8', 'ignore')
        raise Exception(f"Archiving failed!\n{err_msg}")

    if os.path.exists(file_path):
        os.remove(file_path)

    if not os.path.exists(zip_path):
        raise Exception("Archiving failed: output zip not found.")


    zip_size_mb = os.path.getsize(zip_path) / (1024 * 1024)
    if zip_size_mb > split_size:
        updater.action_text = "✂️ Splitting Archive (Python)"
        parts = await split_file(zip_path, split_size, new_base, dir_name)
        os.remove(zip_path)
        return parts

    return [zip_path]