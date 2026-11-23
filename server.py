#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PhotoKit · 本地服务

职责：
1. 托管站点静态页面
2. 项目文件包 API：列表 / 新建 / 打开 / 保存 / 重命名 / 删除 / 导出 / 导入
3. 小红书链接抓图（沿用 agent-browser）

项目文件包结构：
  项目库/<id>/
    project.json   元数据 + 版式设置 + 图片清单
    images/        原始图片
    output/        导出的 PPT
    cover.jpg      列表封面

不调用任何 AI 模型。
启动：python3 server.py  →  http://127.0.0.1:8766
"""

import base64
import io
import json
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LIB = ROOT / "项目库"
EXPORT_DIR = ROOT / "导出"
TEMPLATES_FILE = ROOT / "参数模板.json"
FETCH_RULES_FILE = ROOT / "抓取规则.json"
SESSION = "webfetch"
PORT = 8765

SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
META = "project.json"


# ---------- 通用工具 ----------

def run(cmd, timeout=60):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return p.stdout.strip()


def ensure_dir(path):
    """沙箱下 mkdir(exist_ok=True) 可能误报 EEXIST，先判断再建。"""
    if not path.exists():
        path.mkdir(parents=True)


def now():
    return int(time.time() * 1000)


def new_id():
    return "w_%d_%d" % (now(), __import__("random").randint(1000, 9999))


def safe_id(pid):
    return bool(pid) and bool(SAFE_ID.match(pid))


def proj_dir(pid):
    return LIB / pid if safe_id(pid) else None


def read_meta(pid):
    d = proj_dir(pid)
    if not d or not d.exists():
        return None
    f = d / META
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_meta(pid, meta):
    d = proj_dir(pid)
    (d / META).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def decode_data_url(s):
    """data:image/...;base64,XXXX → bytes；也可接受纯 base64。"""
    if not s:
        return None
    if "," in s and s.strip().lower().startswith("data:"):
        s = s.split(",", 1)[1]
    try:
        return base64.b64decode(s)
    except Exception:
        return None


def read_templates():
    """读取全局参数模板库；缺失或损坏则返回空库。"""
    if not TEMPLATES_FILE.exists():
        return {"version": 1, "templates": []}
    try:
        data = json.loads(TEMPLATES_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("templates"), list):
            return {"version": 1, "templates": []}
        return data
    except Exception:
        return {"version": 1, "templates": []}


def write_templates(data):
    TEMPLATES_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_fetch_rules():
    """读取抓图规则库；缺失或损坏则返回默认容器规则。"""
    default = {"version": 1, "rules": [
        {"id": "r_container", "name": "正文容器（推荐）", "type": "selector",
         "value": ".img-container img", "enabled": True, "note": ""}]}
    if not FETCH_RULES_FILE.exists():
        return default
    try:
        data = json.loads(FETCH_RULES_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("rules"), list):
            return data
    except Exception:
        pass
    return default


def write_fetch_rules(data):
    FETCH_RULES_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def make_cover(pid):
    """取第一张图片生成封面 cover.jpg（3:4 裁切缩略图）。"""
    d = proj_dir(pid)
    meta = read_meta(pid)
    if not d or not meta or not meta.get("images"):
        return
    first = d / "images" / meta["images"][0]["file"]
    if not first.exists():
        return
    try:
        from PIL import Image, ImageOps
        im = Image.open(first).convert("RGB")
        im = ImageOps.exif_transpose(im)
        im = ImageOps.fit(im, (240, 320), method=Image.LANCZOS)
        im.save(d / "cover.jpg", "JPEG", quality=82)
    except Exception:
        # PIL 不可用则直接复制原图当封面
        shutil.copyfile(first, d / "cover.jpg")


# ---------- 项目 API 逻辑 ----------

def api_list():
    items = []
    for d in LIB.iterdir():
        if not d.is_dir():
            continue
        meta = read_meta(d.name)
        if not meta:
            continue
        items.append({
            "id": meta.get("id", d.name),
            "name": meta.get("name", d.name),
            "createdAt": meta.get("createdAt", 0),
            "updatedAt": meta.get("updatedAt", 0),
            "imageCount": len(meta.get("images", [])),
            "hasCover": (d / "cover.jpg").exists(),
            "exportCount": len(meta.get("exports", [])),
            "exportFile": meta.get("exportFile", ""),
        })
    items.sort(key=lambda x: x.get("updatedAt", 0), reverse=True)
    return items


def api_create(name, settings):
    pid = new_id()
    d = proj_dir(pid)
    ensure_dir(d / "images")
    ensure_dir(d / "output")
    meta = {
        "id": pid,
        "name": (name or "未命名项目").strip(),
        "createdAt": now(),
        "updatedAt": now(),
        "settings": settings or {},
        "images": [],
        "exports": [],
    }
    write_meta(pid, meta)
    return meta


def api_open(pid):
    meta = read_meta(pid)
    if not meta:
        return None
    d = proj_dir(pid)
    images = []
    for it in meta.get("images", []):
        f = d / "images" / it.get("file", "")
        if not f.exists():
            continue
        b64 = base64.b64encode(f.read_bytes()).decode("ascii")
        ext = f.suffix.lower().lstrip(".") or "jpeg"
        mime = "image/jpeg" if ext in ("jpg", "jpeg") else ("image/png" if ext == "png" else "image/" + ext)
        im = {"name": it.get("name", f.name), "dataUrl": "data:%s;base64,%s" % (mime, b64)}
        if it.get("note"):
            im["note"] = it["note"]
        images.append(im)
    meta["images"] = images
    ef = meta.get("exportFile")
    if ef:
        meta["exportPath"] = str(EXPORT_DIR / ef)
    return meta


def api_save(pid, payload):
    meta = read_meta(pid)
    if not meta:
        return None
    d = proj_dir(pid)
    img_dir = d / "images"
    ensure_dir(img_dir)

    # 保存版式设置与名称
    if "settings" in payload:
        meta["settings"] = payload["settings"]
    if "name" in payload and str(payload["name"]).strip():
        meta["name"] = str(payload["name"]).strip()

    # 同步图片：frontend 传全量 images [{name, dataUrl}]，按顺序落盘
    incoming = payload.get("images")
    if incoming is not None:
        kept = set()
        new_list = []
        for i, it in enumerate(incoming):
            data = decode_data_url(it.get("dataUrl", ""))
            if not data:
                continue
            name = it.get("name") or ("img_%03d.jpg" % (i + 1))
            ext = Path(name).suffix.lower()
            if ext not in (".jpg", ".jpeg", ".png", ".webp"):
                ext = ".jpg"
            fname = "img_%03d%s" % (i + 1, ext)
            (img_dir / fname).write_bytes(data)
            kept.add(fname)
            item = {"name": name, "file": fname}
            if it.get("note"):
                item["note"] = it["note"]
            new_list.append(item)
        # 删除不再引用的旧图片
        for f in img_dir.iterdir():
            if f.is_file() and f.name not in kept:
                f.unlink()
        meta["images"] = new_list
        make_cover(pid)

    meta["updatedAt"] = now()
    write_meta(pid, meta)
    return {"ok": True, "name": meta["name"], "imageCount": len(meta.get("images", []))}


def api_rename(pid, name):
    meta = read_meta(pid)
    if not meta:
        return None
    meta["name"] = (name or "").strip() or meta["name"]
    meta["updatedAt"] = now()
    write_meta(pid, meta)
    return {"ok": True, "name": meta["name"]}


def api_delete(pid):
    d = proj_dir(pid)
    if not d or not d.exists():
        return False
    shutil.rmtree(d)
    return True


def next_export_no():
    """扫描导出目录，取下一个两位编号（基于已存在的 _NN.pptx）。"""
    ensure_dir(EXPORT_DIR)
    max_no = 0
    for f in EXPORT_DIR.glob("*.pptx"):
        m = re.search(r"_(\d{2})\.pptx$", f.name)
        if m:
            max_no = max(max_no, int(m.group(1)))
    return max_no + 1


def api_add_export(pid, filename, data_bytes):
    """导出到固定目录：同一项目覆盖自己的旧文件，新项目分配 _NN 后缀新建。"""
    meta = read_meta(pid)
    if not meta:
        return None
    ensure_dir(EXPORT_DIR)
    # 已有固定导出文件 → 覆盖；否则分配新编号
    existing = meta.get("exportFile")
    if existing and (EXPORT_DIR / existing).exists():
        fname = existing
        is_new = False
    else:
        no = next_export_no()
        base = re.sub(r'[\\/:*?"<>|]', "_", meta.get("name") or "图片集")
        fname = "%s_%02d.pptx" % (base, no)
        # 极端情况重名则递增
        while (EXPORT_DIR / fname).exists():
            no += 1
            fname = "%s_%02d.pptx" % (base, no)
        is_new = True
    (EXPORT_DIR / fname).write_bytes(data_bytes)
    meta["exportFile"] = fname
    meta.setdefault("exports", []).append({"name": fname, "at": now(), "size": len(data_bytes)})
    if len(meta["exports"]) > 50:
        meta["exports"] = meta["exports"][-50:]
    meta["updatedAt"] = now()
    write_meta(pid, meta)
    return {"ok": True, "name": fname, "isNew": is_new,
            "path": str(EXPORT_DIR / fname)}


def api_export_zip(pid):
    d = proj_dir(pid)
    if not d or not d.exists():
        return None
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in d.rglob("*"):
            if f.is_file():
                z.write(f, f.relative_to(d))
    return buf.getvalue()


def api_import_zip(zip_bytes):
    """导入项目包，返回新项目 meta；校验 zip 内必须含 project.json。"""
    try:
        z = zipfile.ZipFile(io.BytesIO(zip_bytes))
        names = z.namelist()
        if META not in names:
            return None
        raw = z.read(META)
        src = json.loads(raw.decode("utf-8"))
        pid = new_id()
        d = proj_dir(pid)
        ensure_dir(d / "images")
        ensure_dir(d / "output")
        for n in names:
            # 防路径穿越
            clean = Path(n).name if n.startswith("images/") or n.startswith("output/") else n
            if n == META or n == "cover.jpg":
                target = d / Path(n).name
            elif n.startswith("images/") or n.startswith("output/"):
                target = d / n
            else:
                continue
            if ".." in Path(n).parts:
                continue
            ensure_dir(target.parent)
            target.write_bytes(z.read(n))
        meta = {
            "id": pid,
            "name": (src.get("name") or "导入项目") + "（导入）",
            "createdAt": now(),
            "updatedAt": now(),
            "settings": src.get("settings", {}),
            "images": src.get("images", []),
            "exports": src.get("exports", []),
        }
        write_meta(pid, meta)
        return meta
    except Exception:
        return None


# ---------- 抓图（沿用） ----------

def fetch_images(url):
    subprocess.run(["agent-browser", "close", "--session", SESSION], capture_output=True, timeout=20)
    run(["agent-browser", "open", url, "--headed", "--session", SESSION], timeout=90)
    run(["agent-browser", "wait", "3500", "--session", SESSION], timeout=30)
    title = run(["agent-browser", "get", "title", "--session", SESSION], timeout=30)
    if "安全限制" in title or "错误" in title:
        raise RuntimeError("小红书风控拦截，请稍后重试或换网络")
    # 正文图懒加载：滚动到底触发加载，再回顶部，重复几次直到数量稳定
    for _ in range(3):
        run(["agent-browser", "eval", "window.scrollTo(0,document.body.scrollHeight)", "--session", SESSION], timeout=30)
        run(["agent-browser", "wait", "1200", "--session", SESSION], timeout=30)
    run(["agent-browser", "eval", "window.scrollTo(0,0)", "--session", SESSION], timeout=30)
    run(["agent-browser", "wait", "500", "--session", SESSION], timeout=30)
    # 按规则库逐条尝试；每条规则产出候选 URL，取第一条有结果的
    rules = [r for r in read_fetch_rules().get("rules", []) if r.get("enabled", True)]
    uniq, hit_rule = [], ""
    for rule in rules:
        rtype = rule.get("type", "")
        val = rule.get("value", "")
        if rtype == "selector":
            # CSS 选择器：直接取匹配元素的 src
            sel = json.dumps(val)
            js = ("JSON.stringify(Array.from(document.querySelectorAll(" + sel + "))"
                  ".map(function(e){return e.src||e.getAttribute('src')||''}))")
        else:
            # URL 关键词：| 分隔，需全部命中，排除头像和占位图
            kws = [k for k in val.split("|") if k]
            arr = json.dumps(kws)
            js = ("JSON.stringify(Array.from(document.querySelectorAll('img'))"
                  ".map(function(i){return i.src||i.getAttribute('src')||''})"
                  ".filter(function(s){if(s.indexOf('http')!==0||s.indexOf('data:')===0)return false;"
                  "if(s.indexOf('sns-avatar')>=0)return false;"
                  "return " + arr + ".some(function(k){return s.indexOf(k)>=0});}))")
        raw = run(["agent-browser", "eval", js, "--session", SESSION], timeout=30)
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, str):
                parsed = json.loads(parsed)
            cand = parsed if isinstance(parsed, list) else []
        except Exception:
            cand = []
        # 去重保序
        seen, one = set(), []
        for u in cand:
            if u not in seen:
                seen.add(u)
                one.append(u)
        if one:
            uniq, hit_rule = one, rule.get("name", rule.get("id", ""))
            break
    if not uniq:
        raise RuntimeError("所有抓取规则都未命中，可能是笔记为视频、需登录，或规则已失效（可在页面维护规则）")
    out = []
    with tempfile.TemporaryDirectory(prefix="xhs_web_") as td:
        td = Path(td)
        for i, u in enumerate(uniq):
            webp = td / ("%d.webp" % i)
            jpg = td / ("%d.jpg" % i)
            subprocess.run(["curl", "-sL", "-o", str(webp),
                            "-H", "Referer: https://www.xiaohongshu.com/",
                            "-H", "User-Agent: Mozilla/5.0", u], timeout=40)
            subprocess.run(["sips", "-s", "format", "jpeg", str(webp), "--out", str(jpg)],
                           capture_output=True, timeout=40)
            if jpg.exists():
                b64 = base64.b64encode(jpg.read_bytes()).decode("ascii")
                out.append("data:image/jpeg;base64," + b64)
    subprocess.run(["agent-browser", "close", "--session", SESSION], capture_output=True, timeout=20)
    return out, hit_rule, len(uniq)


# ---------- HTTP Handler ----------

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def log_message(self, fmt, *args):
        pass

    def end_headers(self):
        # 静态资源禁用强缓存，避免改了代码后页面仍用旧的缓存版本
        p = self.path.split("?", 1)[0].lower()
        if p.endswith((".html", ".js", ".css")):
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Pragma", "no-cache")
        super().end_headers()

    def _json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _bytes(self, data, mime, filename=None):
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        if filename:
            from urllib.parse import quote
            self.send_header("Content-Disposition",
                             "attachment; filename*=UTF-8''%s" % quote(filename))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw) if raw else {}

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        query = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = {}
        if query:
            for part in query.split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    params[k] = v
        if path == "/api/status":
            self._json({"ok": True, "mode": "local"})
            return
        if path == "/api/templates":
            self._json({"ok": True, "data": read_templates()})
            return
        if path == "/api/fetch-rules":
            self._json({"ok": True, "data": read_fetch_rules()})
            return
        if path == "/api/projects":
            self._json({"ok": True, "projects": api_list()})
            return
        if path == "/api/project/open":
            pid = params.get("id", "")
            meta = api_open(pid)
            self._json({"ok": bool(meta), "project": meta} if meta else
                       {"ok": False, "error": "项目不存在"})
            return
        if path == "/api/export/download":
            pid = params.get("id", "")
            meta = read_meta(pid)
            ef = meta.get("exportFile") if meta else None
            f = (EXPORT_DIR / ef) if ef else None
            if f and f.exists():
                self._bytes(f.read_bytes(), "application/vnd.openxmlformats-officedocument.presentationml.presentation", ef)
            else:
                self.send_response(404); self.end_headers()
            return
        if path == "/api/project/cover":
            pid = params.get("id", "")
            d = proj_dir(pid)
            cover = (d / "cover.jpg") if d else None
            if cover and cover.exists():
                self._bytes(cover.read_bytes(), "image/jpeg")
            else:
                # 兜底：取第一张原图
                meta = read_meta(pid)
                if meta and meta.get("images"):
                    first = d / "images" / meta["images"][0]["file"]
                    if first.exists():
                        mime = "image/png" if first.suffix.lower() == ".png" else "image/jpeg"
                        self._bytes(first.read_bytes(), mime)
                        return
                self.send_response(404); self.end_headers()
            return
        if path == "/api/project/export":
            pid = params.get("id", "")
            data = api_export_zip(pid)
            if data is None:
                self._json({"ok": False, "error": "项目不存在"}, 404)
            else:
                self._bytes(data, "application/zip", "project.zip")
            return
        super().do_GET()

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        try:
            body = self._body()
            if path == "/api/templates/save":
                data = body.get("data")
                if not isinstance(data, dict) or not isinstance(data.get("templates"), list):
                    raise ValueError("模板数据格式不正确")
                write_templates(data)
                self._json({"ok": True})
                return
            if path == "/api/fetch-rules/save":
                data = body.get("data")
                if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
                    raise ValueError("规则数据格式不正确")
                write_fetch_rules(data)
                self._json({"ok": True})
                return
            if path == "/api/fetch":
                url = body.get("url", "").strip()
                if "xiaohongshu.com" not in url:
                    raise ValueError("请提供小红书笔记链接")
                imgs, hit_rule, found = fetch_images(url)
                self._json({"ok": True, "images": imgs,
                            "rule": hit_rule, "found": found})
                return
            if path == "/api/project/create":
                self._json({"ok": True, "project": api_create(
                    body.get("name", ""), body.get("settings", {}))})
                return
            if path == "/api/project/save":
                pid = body.get("id", "")
                r = api_save(pid, body)
                self._json(r if r else {"ok": False, "error": "项目不存在"})
                return
            if path == "/api/project/rename":
                r = api_rename(body.get("id", ""), body.get("name", ""))
                self._json(r if r else {"ok": False, "error": "项目不存在"})
                return
            if path == "/api/project/delete":
                ok = api_delete(body.get("id", ""))
                self._json({"ok": ok} if ok else {"ok": False, "error": "项目不存在"})
                return
            if path == "/api/project/export-ppt":
                pid = body.get("id", "")
                data = decode_data_url(body.get("dataUrl", ""))
                if not data:
                    raise ValueError("PPT 数据为空")
                r = api_add_export(pid, body.get("filename", "export.pptx"), data)
                self._json(r if r else {"ok": False, "error": "项目不存在"})
                return
            if path == "/api/project/import":
                data = decode_data_url(body.get("dataUrl", ""))
                meta = api_import_zip(data) if data else None
                self._json({"ok": bool(meta), "project": meta} if meta else
                           {"ok": False, "error": "项目包无效"})
                return
            if path == "/api/export/reveal":
                # 在 Finder 中打开导出目录，或选中指定文件
                import platform
                pid = body.get("id", "")
                meta = read_meta(pid)
                ef = meta.get("exportFile") if meta else None
                target = (EXPORT_DIR / ef) if (ef and (EXPORT_DIR / ef).exists()) else EXPORT_DIR
                ensure_dir(EXPORT_DIR)
                system = platform.system()
                try:
                    if system == "Darwin":
                        # open -R 选中文件；对目录则用 open 打开
                        cmd = ["open", "-R", str(target)] if target.is_file() else ["open", str(target)]
                    elif system == "Windows":
                        cmd = ["explorer", str(target)]
                    else:
                        cmd = ["xdg-open", str(target)]
                    subprocess.Popen(cmd)
                    self._json({"ok": True, "target": str(target)})
                except Exception as ex:
                    self._json({"ok": False, "error": "无法打开文件夹："+str(ex)}) 
                return
            self._json({"ok": False, "error": "not found"}, 404)
        except Exception as e:
            self._json({"ok": False, "error": str(e)})


if __name__ == "__main__":
    print("=" * 54)
    print("PhotoKit · 本地版已启动")
    print("请在浏览器打开: http://127.0.0.1:%d" % PORT)
    print("按 Ctrl+C 停止")
    print("=" * 54)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
