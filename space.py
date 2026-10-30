#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
训练卷 PDF 留白工具
====================
把一份训练卷 PDF 变成可以打印作答的「书写版」：每道题后面自动留出空白。

用法一（最简单）：双击本文件
    黑窗口会提示你把 PDF 拖进去，回车即可。文件夹也可以拖，会处理里面所有 PDF。

用法二：命令行
    python space.py 试卷.pdf
    python space.py D:\数学合集             处理该文件夹下所有 PDF
    python space.py A.pdf B.pdf C.pdf       一次多份

常用参数
    --space 1.3              整体留白放大 30%（觉得太窄就加）
    --space 0.8              整体缩小 20%
    --blank "17:260,20:400"  只给这几道题指定留白高度（单位 pt，28pt ≈ 1cm）
    --default 150            所有题统一 150 pt
    --grid "10,19"           给这几道题的留白区画 5mm 浅色方格（作图用）
    -o 输出.pdf              指定输出文件名（只处理一份时可用）

说明
    · 输出文件写在原 PDF 旁边，名字后面加「-书写版」，原卷不会被改动。
    · 公式不是重打的：按原页墨迹整块裁剪、1:1 贴到新页，积分号、上下标原样保留。
    · 依据「靠左、形如 1. 2. 3. 且题号连续」识别题目；若是「一、二、三」或双栏
      排版会识别失败，请告诉我另做适配。
"""

import argparse
import os
import re
import sys

# ---------------------------------------------------------------- 依赖检查
def _importable(py):
    """这个解释器能不能 import pymupdf。"""
    import subprocess
    try:
        r = subprocess.run([py, "-c", "import pymupdf, fitz"], capture_output=True,
                           timeout=25, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.returncode == 0
    except Exception:
        return False


def _candidates():
    """本机可能装了 PyMuPDF 的解释器。"""
    import glob
    import shutil
    import subprocess
    out = []
    try:
        r = subprocess.run(["py", "-0p"], capture_output=True, text=True, timeout=25)
        for line in r.stdout.splitlines():
            m = re.search(r"\s([A-Za-z]:\\.*python\.exe)\s*$", line.strip())
            if m:
                out.append(m.group(1))
    except Exception:
        pass
    for p in ("E:\\anaconda\\python.exe", "D:\\anaconda\\python.exe", "C:\\anaconda\\python.exe"):
        out.append(p)
    for p in glob.glob(os.path.expanduser(r"~\AppData\Local\Programs\Python\Python*\python.exe")):
        out.append(p)
    w = shutil.which("python")
    if w:
        out.append(w)
    seen, res = set(), []
    for p in out:
        k = p.lower()
        if k not in seen and os.path.isfile(p):
            seen.add(k)
            res.append(p)
    return res


def _load_fitz():
    try:
        import pymupdf
        return pymupdf
    except ImportError:
        pass
    try:
        import fitz
        return fitz
    except ImportError:
        pass
    # 当前解释器没有，换一个已经装好的解释器重新运行本脚本
    for py in _candidates():
        if os.path.normcase(py) == os.path.normcase(sys.executable):
            continue
        if _importable(py):
            try:
                os.execv(py, [py, os.path.abspath(__file__)] + sys.argv[1:])
            except Exception:
                continue
    print("需要 PyMuPDF（PDF 处理库，约 20MB）才能运行。")
    ans = input("现在自动安装吗？输入 y 回车：").strip().lower()
    if ans == "y":
        os.system(f'"{sys.executable}" -m pip install pymupdf')
        try:
            import pymupdf
            return pymupdf
        except ImportError:
            sys.exit("安装后仍无法导入。手动执行：pip install pymupdf")
    sys.exit("已取消。手动安装命令：pip install pymupdf")


fitz = _load_fitz()

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ---------------------------------------------------------------- 版式常量
A4 = (594.96, 841.92)
TOP = 62.0
BOTTOM = 782.0
MIN_OWN = 58.0
GAP = 16.0
GRID_STEP = 14.17          # 5 mm
CM = 28.35                 # 1 cm 的磅数

Q_RE = re.compile(r"^(\d+)\s*[.、．]")
SUB_RE = re.compile(r"^(\(\d+\)|（\d+）|\d+[.、．])")
PROOF = ("证明", "求证", "试证", "证毕")
GRAPH = ("作图", "图形", "画图", "图像", "草图")
SOLVE = ("求", "计算", "解", "讨论", "展开", "化简", "判断", "确定")


# ---------------------------------------------------------------- PDF 分析
def collect_ink(page):
    """页面上所有可见内容的包围盒：(x0, y0, x1, y1, 文本或 None)。"""
    items = []
    for b in page.get_text("dict")["blocks"]:
        if b["type"] == 0:
            for l in b["lines"]:
                s = "".join(sp["text"] for sp in l["spans"]).strip()
                items.append((*l["bbox"], s))
        else:
            items.append((*b["bbox"], None))
    for d in page.get_drawings():
        r = d["rect"]
        if r.height < 1.5 and r.width > page.rect.width * 0.6:     # 页眉细线
            continue
        items.append((r.x0, r.y0, r.x1, r.y1, None))
    return items


def question_lines(items, pno=0):
    """识别题号行：最靠左、形如 '3.' 且题号连续。返回 [(题号, y)]。"""
    cands = [it for it in items if it[4] and Q_RE.match(it[4])]
    if not cands:
        return []
    left = min(it[0] for it in cands)
    rows = sorted([it for it in cands if it[0] <= left + 8], key=lambda t: (t[1], t[0]))
    found, seen = [], set()
    for it in rows:
        num = int(Q_RE.match(it[4]).group(1))
        if num in seen:
            continue
        if found and num != found[-1][0] + 1:
            continue
        if not found and pno > 0 and num < 2:
            continue
        seen.add(num)
        found.append((num, it[1]))
    return found


def auto_blank(text, height, nsub, scale):
    """按题面高度、题型、小问个数估算留白（pt）。"""
    base = 72.0 + 0.72 * height
    mult = 1.0
    if any(k in text for k in GRAPH):
        mult *= 1.8
    elif any(k in text for k in PROOF):
        mult *= 1.35
    elif any(k in text for k in SOLVE):
        mult *= 1.15
    mult *= min(1.0 + 0.18 * max(0, nsub - 1), 1.6)
    return max(80.0, min(360.0, base * mult * scale))


# ---------------------------------------------------------------- 核心
def convert(src_path, out_path, scale=1.0, blanks=None, default_blank=0.0,
            grids=None, quiet=False):
    blanks = blanks or {}
    grids = grids or set()
    src = fitz.open(src_path)
    if not src.is_pdf:
        raise ValueError("不是 PDF 文件")
    pw, ph = (A4 if abs(src[0].rect.width - 594.96) < 2
              else (src[0].rect.width, src[0].rect.height))

    segments = []
    for pno in range(src.page_count):
        page = src[pno]
        items = [it for it in collect_ink(page) if it[1] < ph - 52]
        qs = question_lines(items, pno)
        if not qs:
            continue
        starts = [y for _, y in qs]

        def owner(y0):
            idx = -1
            for i, sy in enumerate(starts):
                if y0 >= sy - 9:
                    idx = i
            return idx

        groups = {}
        for it in items:
            groups.setdefault(owner(it[1]), []).append(it)

        if pno == 0 and -1 in groups:                     # 卷头
            hdr = groups[-1]
            segments.append(dict(page=0, top=min(i[1] for i in hdr) - 6,
                                 bottom=max(i[3] for i in hdr) + 6, num=None, text=""))

        for i, (num, sy) in enumerate(qs):
            g = groups.get(i, [])
            if not g:
                continue
            top = min([sy - 7] + [it[1] - 4 for it in g])
            own_bottom = max(it[3] for it in g)
            bottom = own_bottom + 6
            if i + 1 in groups:                           # 不切到下一题
                bottom = min(bottom, min(it[1] for it in groups[i + 1]) - 4)
            bottom = max(bottom, own_bottom + 1)
            segments.append(dict(page=pno, top=top, bottom=bottom, num=num,
                                 text=" ".join(it[4] for it in g if it[4])))

    if not any(s["num"] for s in segments):
        raise ValueError("没识别到题号（形如「1.」的行）")

    for s in segments:
        s["h"] = s["bottom"] - s["top"]
        if s["num"] is None:
            s["blank"], s["grid"] = 0.0, False
            continue
        nsub = len(SUB_RE.findall(s["text"])) if len(s["text"]) > 60 else 0
        s["blank"] = (blanks[s["num"]] if s["num"] in blanks else
                      (default_blank or auto_blank(s["text"], s["h"], nsub, scale)))
        s["grid"] = s["num"] in grids

    # ---- 排版：整题不拆，书写区可续到下一页 ----
    pages, cur, y = [], [], TOP
    for s in segments:
        own = min(s["blank"], MIN_OWN)
        if y + s["h"] + own > BOTTOM:
            pages.append(cur)
            cur, y = [], TOP
        cur.append(("c", s, y, s))
        y += s["h"]
        rem = s["blank"]
        while rem > 0:
            room = BOTTOM - y
            if room <= 0.5:
                pages.append(cur)
                cur, y = [], TOP
                room = BOTTOM - TOP
            take = min(rem, room)
            cur.append(("b", take, y, s))
            y += take
            rem -= take
        y += GAP
    if cur:
        pages.append(cur)
    total = len(pages)
    title = (src.metadata.get("title") or "训练卷").strip()

    def decorate(page, idx):
        gray = (0.58, 0.58, 0.58)
        page.draw_line((40, 800), (pw - 40, 800), color=(0.80, 0.80, 0.80), width=0.6)
        page.insert_text((40, 816), "suncoastmath.cn", fontsize=8,
                         fontname="helv", color=gray)
        page.insert_text((pw - 126, 816), f"第 {idx + 1} 页 / 共 {total} 页",
                         fontsize=8.5, fontname="china-s", color=gray)
        if idx > 0:
            txt = f"{title} · 书写版"
            size = 8.5
            w = sum(size if ord(c) > 0x2E80 else size * 0.5 for c in txt)
            if w > pw - 80:
                size = 7.5
                w = sum(size if ord(c) > 0x2E80 else size * 0.5 for c in txt)
            page.insert_text((pw - 40 - w, 30), txt, fontsize=size,
                             fontname="china-s", color=gray)
            page.draw_line((40, 38), (pw - 40, 38), color=(0.80, 0.80, 0.80), width=0.6)

    out = fitz.open()
    for idx, plist in enumerate(pages):
        page = out.new_page(width=pw, height=ph)
        for kind, val, yy, seg in plist:
            if kind == "c":
                page.show_pdf_page(fitz.Rect(0, yy, pw, yy + val["h"]), src,
                                   val["page"],
                                   clip=fitz.Rect(0, val["top"], pw, val["bottom"]))
            elif seg["grid"]:
                yy2 = yy
                while yy2 < yy + val - 2:
                    page.draw_line((40, yy2), (pw - 40, yy2),
                                   color=(0.86, 0.86, 0.86), width=0.4)
                    yy2 += GRID_STEP
                xx = 40.0
                while xx < pw - 40:
                    page.draw_line((xx, yy), (xx, yy + val),
                                   color=(0.86, 0.86, 0.86), width=0.4)
                    xx += GRID_STEP
        decorate(page, idx)

    out.set_metadata({"title": title + "（书写版）", "subject": "逐题留白书写版"})
    out.save(out_path, garbage=4, deflate=True)

    if not quiet:
        n = sum(1 for s in segments if s["num"])
        print(f"\n  题目 {n} 道　输出 {total} 页　→  {os.path.basename(out_path)}")
        print("  " + "-" * 40)
        for plist in pages:
            for kind, val, yy, seg in plist:
                if kind == "c" and val["num"]:
                    print(f"   第 {val['num']:>2} 题　留白 {val['blank']:>5.0f} pt"
                          f"　约 {val['blank'] / CM:>4.1f} cm")
    return total


# ---------------------------------------------------------------- 交互输入
def _console_encoding():
    """控制台正在用的编码（cp65001 或 cp936）。"""
    try:
        import ctypes
        cp = ctypes.windll.kernel32.GetConsoleCP()
        return {65001: "utf-8", 936: "gbk"}.get(cp, "gbk")
    except Exception:
        return "gbk"


def read_line():
    """读取一行输入；兼容 GBK / UTF-8 控制台。"""
    order = [_console_encoding(), "utf-8", "gbk", "mbcs"]
    try:
        raw = sys.stdin.buffer.readline()
        for enc in order:
            try:
                return raw.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue
        return raw.decode("utf-8", "replace")
    except Exception:
        return input()


def parse_paths(line):
    paths = re.findall(r'"([^"]+)"', line)
    if not paths:
        paths = [p for p in line.strip().strip('"').strip("'").split("  ") if p.strip()]
    return [p.strip().strip('"').strip("'") for p in paths if p.strip()]


def expand(paths, recursive=False):
    out = []
    for p in paths:
        if os.path.isdir(p):
            if recursive:
                for root, _, files in os.walk(p):
                    for f in sorted(files):
                        if f.lower().endswith(".pdf") and not f.endswith("-书写版.pdf"):
                            out.append(os.path.join(root, f))
            else:
                for f in sorted(os.listdir(p)):
                    fp = os.path.join(p, f)
                    if (os.path.isfile(fp) and f.lower().endswith(".pdf")
                            and not f.endswith("-书写版.pdf")):
                        out.append(fp)
        elif os.path.isfile(p):
            out.append(p)
        else:
            print(f"  [跳过] 找不到：{p}")
    return out


# ---------------------------------------------------------------- 入口
def main():
    ap = argparse.ArgumentParser(add_help=True, description="给训练卷 PDF 逐题留出书写空间")
    ap.add_argument("pdf", nargs="*", help="PDF 文件或文件夹（不写则交互输入）")
    ap.add_argument("-o", "--out", help="输出文件名（仅处理一份时可用）")
    ap.add_argument("--space", type=float, default=1.0, help="留白整体倍率，默认 1.0")
    ap.add_argument("--default", type=float, default=0.0, help="所有题统一留白高度 pt")
    ap.add_argument("--blank", default="", help='单独指定某几题，如 "17:260,20:400"')
    ap.add_argument("--grid", default="", help='给某几题加 5mm 方格，如 "10,19"')
    ap.add_argument("-r", "--recursive", action="store_true", help="文件夹参数时递归子目录")
    a = ap.parse_args()

    blanks = {}
    for part in filter(None, (x.strip() for x in a.blank.split(","))):
        k, v = part.split(":")
        blanks[int(k)] = float(v)
    grids = {int(x) for x in a.grid.split(",") if x.strip()}

    interactive = not a.pdf
    paths = a.pdf
    if interactive:
        print("=" * 54)
        print("  训练卷留白工具：生成可直接打印作答的「书写版」")
        print("  把 PDF（或文件夹）拖进本窗口，然后回车")
        print("=" * 54)
        paths = parse_paths(read_line())
        if not paths:
            print("没有输入路径，已退出。")
            return

    files = expand(paths, a.recursive)
    if not files:
        print("没有可处理的 PDF。")
        return
    if a.out and len(files) > 1:
        print("处理多份时不能用 -o，已忽略。")
        a.out = None

    ok = 0
    for path in files:
        out = a.out or os.path.join(
            os.path.dirname(os.path.abspath(path)),
            os.path.splitext(os.path.basename(path))[0] + "-书写版.pdf")
        print(f"\n[处理] {os.path.basename(path)}")
        try:
            convert(path, out, a.space, blanks, a.default, grids)
            ok += 1
        except PermissionError:
            print(f"  [失败] 无法写入 {out}\n         文件正被阅读器占用，关掉 PDF 阅读器再试。")
        except Exception as exc:
            msg = str(exc).lower()
            if "denied" in msg or "permission" in msg or "cannot remove" in msg:
                print(f"  [失败] 无法写入 {out}\n         文件正被阅读器占用，关掉 PDF 阅读器再试。")
            else:
                print(f"  [失败] {exc}")

    print(f"\n完成：成功 {ok} / 共 {len(files)} 份。输出文件在原 PDF 旁边，名字带「-书写版」。")
    if interactive:
        input("\n按回车退出……")


if __name__ == "__main__":
    main()
