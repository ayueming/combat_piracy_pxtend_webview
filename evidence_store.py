"""证据文件与数据库的存取逻辑（不依赖 Qt）。"""
import os
import re
import shutil
import uuid

from app_paths import get_user_subdir, DIR_EVIDENCES
from db_config import get_conn, get_current_user

ALLOWED_IMAGE_EXTS = ('.png', '.jpg', '.jpeg', '.bmp')


def get_data_dir():
    """获取当前用户的专属证据文件夹。"""
    user_id = get_current_user()
    if user_id:
        return get_user_subdir(user_id, DIR_EVIDENCES)
    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "evidences_data")
    os.makedirs(data_dir, exist_ok=True)
    return data_dir


def is_path_in_data_dir(path):
    try:
        return os.path.abspath(path).startswith(os.path.abspath(get_data_dir()))
    except Exception:
        return False


def resolve_evidence_path(raw_path):
    """兼容旧数据：原路径失效时按文件名到当前用户目录找回，并回写数据库。

    注意：本函数有副作用（会 UPDATE evidences / books）。
    """
    if not raw_path:
        return ""
    if os.path.exists(raw_path):
        return raw_path

    filename = os.path.basename(raw_path)
    new_path = os.path.join(get_data_dir(), filename)
    if os.path.exists(new_path):
        try:
            conn = get_conn()
            conn.execute("UPDATE evidences SET file_path=? WHERE file_path=?", (new_path, raw_path))
            conn.execute("UPDATE books SET evidence_path=? WHERE evidence_path=?", (new_path, raw_path))
            conn.commit()
            conn.close()
        except Exception:
            pass
        return new_path
    return raw_path


def save_uploaded_image(src_path, type_name, prefix):
    """把用户选择的图片转存进证据目录（含 EXIF 转正），返回最终路径。"""
    data_dir = get_data_dir()
    abs_src = os.path.abspath(src_path)
    abs_data_dir = os.path.abspath(data_dir)

    # 源文件已在数据目录内，直接复用，避免重复拷贝
    if abs_src.startswith(abs_data_dir):
        return abs_src

    safe_prefix = re.sub(r'[\\/:*?"<>|]', '_', str(prefix) if prefix else "未命名")
    safe_type = re.sub(r'[\\/:*?"<>|]', '_', type_name)
    ext = os.path.splitext(src_path)[1].lower()
    new_filename = f"{safe_prefix}_{safe_type}_{uuid.uuid4().hex[:4]}{ext}"
    new_file_path = os.path.join(data_dir, new_filename)

    try:
        from PIL import Image, ImageOps
        with Image.open(src_path) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode in ("RGBA", "P"):
                if new_file_path.lower().endswith('.png'):
                    img = img.convert("RGBA")
                else:
                    img = img.convert("RGB")
            img.save(new_file_path, optimize=True)
    except Exception:
        shutil.copy2(src_path, new_file_path)

    return new_file_path


def upsert_evidence(owner_type, owner_key, ev_type, file_path):
    conn = get_conn()
    try:
        conn.execute(
            "DELETE FROM evidences WHERE owner_type=? AND owner_key=? AND ev_type=?",
            (owner_type, owner_key, ev_type))
        conn.execute(
            "INSERT INTO evidences (owner_type, owner_key, ev_type, file_path) VALUES (?,?,?,?)",
            (owner_type, owner_key, ev_type, file_path))
        conn.commit()
    finally:
        conn.close()


def delete_evidence_record(owner_type, owner_key, ev_type):
    conn = get_conn()
    try:
        conn.execute(
            "DELETE FROM evidences WHERE owner_type=? AND owner_key=? AND ev_type=?",
            (owner_type, owner_key, ev_type))
        conn.commit()
    finally:
        conn.close()


def is_file_shared(file_path):
    """文件是否仍被 evidences 或 books 引用。"""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT 1 FROM evidences WHERE file_path=? "
            "UNION SELECT 1 FROM books WHERE evidence_path=? LIMIT 1",
            (file_path, file_path)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def safe_delete_file(file_path):
    """只删除数据目录内、且无其它记录引用的文件。返回是否真的删除。"""
    if not file_path or not os.path.exists(file_path):
        return False
    if not is_path_in_data_dir(file_path):
        return False
    if is_file_shared(file_path):
        return False
    try:
        os.remove(file_path)
        return True
    except OSError:
        return False