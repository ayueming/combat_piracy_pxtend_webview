"""维权书生成（Word / PDF），不依赖 Qt。"""
import os

from PIL import Image, ImageOps


def fit_image_size(w_px, h_px, max_w, max_h):
    """按宽高比把图片限制在 max_w × max_h 内，返回 (w, h)，单位与 max_* 一致。"""
    if w_px <= 0 or h_px <= 0:
        return max_w, max_h
    ar = w_px / h_px
    if ar > (max_w / max_h):
        return max_w, max_w / ar
    return max_h * ar, max_h


def _iter_cjk_fonts():
    candidates = [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    ]
    for fp in candidates:
        if os.path.exists(fp):
            yield fp


def build_word(items, output_path):
    """items: [(标题, 文件路径), ...]；路径为空或不存在时插入缺失提示。"""
    from docx import Document
    from docx.shared import Inches, Pt
    from docx.enum.text import WD_BREAK
    import io

    doc = Document()
    for idx, (title, file_path) in enumerate(items):
        p_title = doc.add_paragraph()
        p_title.paragraph_format.space_after = Pt(0)
        run = p_title.add_run(title)
        run.bold = True
        run.font.size = Pt(16)
        run.font.name = '微软雅黑'

        p_img = doc.add_paragraph()
        p_img.paragraph_format.space_before = Pt(0)
        p_img.paragraph_format.space_after = Pt(0)

        if file_path and os.path.exists(file_path) and not file_path.lower().endswith('.pdf'):
            try:
                with Image.open(file_path) as img:
                    img = ImageOps.exif_transpose(img)
                    if img.mode in ("RGBA", "P"):
                        img = img.convert("RGB")
                    buf = io.BytesIO()
                    img.save(buf, format='JPEG')
                    buf.seek(0)
                    w_px, h_px = img.size
                fw, fh = fit_image_size(w_px, h_px, 5.7, 8.5)
                p_img.add_run().add_picture(buf, width=Inches(fw), height=Inches(fh))
            except Exception as e:
                print(f"Word插图失败: {e}")
                p_img.add_run("（图片解析失败，请重试）")
        else:
            p_img.add_run("（缺失：未上传截图）")

        if idx < len(items) - 1:
            p_img.add_run().add_break(WD_BREAK.PAGE)

    doc.save(output_path)


def build_pdf(items, output_path):
    from fpdf import FPDF
    import tempfile

    pdf = FPDF()
    font_loaded = False
    for fp in _iter_cjk_fonts():
        try:
            pdf.add_font("chinese", "", fp, uni=True)
            pdf.add_font("chinese", "B", fp, uni=True)
            font_loaded = True
            break
        except Exception:
            continue

    for title, file_path in items:
        pdf.add_page()
        pdf.set_font("chinese" if font_loaded else "helvetica", size=16, style="B")
        pdf.multi_cell(0, 10, txt=title)
        pdf.ln(2)

        if file_path and os.path.exists(file_path) and not file_path.lower().endswith('.pdf'):
            tmp_path = None
            try:
                with Image.open(file_path) as img:
                    img = ImageOps.exif_transpose(img)
                    if img.mode in ("RGBA", "P"):
                        img = img.convert("RGB")
                    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                        img.save(tmp.name, format='JPEG')
                        tmp_path = tmp.name
                    w_px, h_px = img.size
                fw, fh = fit_image_size(w_px, h_px, 146, 230)
                cur_y = pdf.get_y()
                pdf.image(tmp_path, w=fw, h=fh)
                pdf.set_y(cur_y + fh)
            except Exception as e:
                print(f"PDF插图失败: {e}")
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass
        else:
            pdf.set_font("chinese" if font_loaded else "helvetica", size=12)
            pdf.set_text_color(255, 0, 0)
            pdf.cell(0, 10, txt="(缺失：未上传截图)", ln=True)
            pdf.set_text_color(0, 0, 0)

    pdf.output(output_path)