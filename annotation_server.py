# -*- coding: utf-8 -*-
"""
YOLO 数据集可视化标注工具（零第三方依赖）

用法:
    python annotation_server.py                       # 默认打开 生成数据/yolo_dataset/test
    python annotation_server.py --images D:/xx/images --labels D:/xx/labels
    python annotation_server.py --images ... --labels ... --classes ... --port 8765

功能:
    - 左侧图片列表，右侧图片上叠加显示 YOLO 标注框
    - 拖动框移动、8 个手柄缩放、空白处拖拽绘制新框
    - 撤回(Ctrl+Z) / 重做(Ctrl+Y) / 删除选中框(Delete)
    - 保存(Ctrl+S) 写回标签 txt，坐标保留 6 位小数
"""
import argparse
import json
import mimetypes
import string
import threading
import webbrowser
import os
import re
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

IMAGE_EXTS = ('.png', '.jpg', '.jpeg', '.bmp', '.webp', '.gif')

CONFIG = {
    'images': '',
    'labels': '',
    'classes': '',
}

# 页面动态切换的数据集目录/类别文件(优先于 CONFIG)
ACTIVE = {
    'images': '',
    'labels': '',
    'classes': '',
}


def get_dirs():
    """返回当前生效的 (图片目录, 标签目录)"""
    return (ACTIVE['images'] or CONFIG['images'],
            ACTIVE['labels'] or CONFIG['labels'])


def get_classes_path():
    return ACTIVE['classes'] or CONFIG['classes']


def load_class_names():
    """读取当前生效的类别文件, 每行一个类别名"""
    path = get_classes_path()
    if not os.path.isfile(path):
        return []
    with open(path, 'r', encoding='utf-8') as f:
        return [ln.strip() for ln in f if ln.strip()]


def fmt_num(v: float) -> str:
    """归一化坐标格式化: 保留 6 位小数并去掉尾部 0, 与现有标签风格一致"""
    v = max(0.0, min(1.0, float(v)))
    s = f'{v:.6f}'.rstrip('0')
    if s.endswith('.'):
        s += '0'
    return s


class Server(ThreadingHTTPServer):
    # Windows 下 SO_REUSEADDR 允许重复绑定同一端口,
    # 会造成旧进程仍在服务旧代码的假象, 故关闭以便端口被占时直接报错
    allow_reuse_address = False


class Handler(BaseHTTPRequestHandler):

    # ---------- 通用工具 ----------
    def _send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, data, ctype, code=200):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def _safe_join(self, base, name):
        """防目录穿越: 只允许访问 base 目录下的文件名"""
        name = os.path.basename(urllib.parse.unquote(name))
        return os.path.join(base, name)

    def log_message(self, fmt, *args):
        # 精简日志
        if '/api/' in (args[0] if args else ''):
            super().log_message(fmt, *args)

    # ---------- GET ----------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if route == '/':
            self._send_bytes(PAGE_HTML.encode('utf-8'), 'text/html; charset=utf-8')
            return

        if route == '/api/list':
            self._api_list()
            return

        if route == '/api/image':
            name = qs.get('name', [''])[0]
            path = self._safe_join(get_dirs()[0], name)
            if not os.path.isfile(path):
                self._send_json({'error': '图片不存在'}, 404)
                return
            ctype = mimetypes.guess_type(path)[0] or 'application/octet-stream'
            with open(path, 'rb') as f:
                self._send_bytes(f.read(), ctype)
            return

        if route == '/api/label':
            name = qs.get('name', [''])[0]
            path = self._safe_join(get_dirs()[1], name + '.txt')
            boxes = []
            if os.path.isfile(path):
                with open(path, 'r', encoding='utf-8') as f:
                    for line in f:
                        parts = line.split()
                        if len(parts) >= 5:
                            try:
                                cls = int(float(parts[0]))
                                x, y, w, h = (float(v) for v in parts[1:5])
                            except ValueError:
                                continue
                            boxes.append({'cls': cls, 'x': x, 'y': y, 'w': w, 'h': h})
            self._send_json({'boxes': boxes})
            return

        if route == '/api/browse':
            path = qs.get('path', [''])[0] or ''
            kind = qs.get('kind', ['dir'])[0]
            self._api_browse(path, kind)
            return

        if route == '/api/config':
            imgs, lbls = get_dirs()
            self._send_json({'images': imgs, 'labels': lbls,
                             'classes': get_classes_path(),
                             'drives': self._list_drives()})
            return

        self._send_json({'error': 'not found'}, 404)

    @staticmethod
    def _list_drives():
        """Windows 盘符列表, 供目录浏览器返回根目录用"""
        drives = []
        for letter in string.ascii_uppercase:
            root = f'{letter}:\\'
            if os.path.exists(root):
                drives.append(root)
        return drives or ['/']

    def _api_browse(self, path, kind='dir'):
        """目录浏览: 列出子文件夹(kind=dir)或txt文件(kind=file); 路径为空时返回盘符列表"""
        if not path:
            self._send_json({'path': '', 'dirs': self._list_drives(), 'is_root': True})
            return
        path = os.path.abspath(path)
        if not os.path.isdir(path):
            self._send_json({'error': '目录不存在: ' + path}, 400)
            return
        try:
            if kind == 'file':
                entries = sorted(
                    (e.name for e in os.scandir(path)
                     if e.is_file() and e.name.lower().endswith('.txt')),
                    key=str.lower)
            else:
                entries = sorted(
                    (e.name for e in os.scandir(path) if e.is_dir()),
                    key=str.lower)
        except PermissionError:
            self._send_json({'error': '无权限访问: ' + path}, 403)
            return
        parent = os.path.dirname(path)
        if parent == path:      # 已到根(盘符), 再上级返回盘符列表
            parent = ''
        self._send_json({'path': path, 'parent': parent,
                         'dirs': [os.path.join(path, d) for d in entries],
                         'is_root': False})

    def _api_list(self):
        img_dir, lbl_dir = get_dirs()
        files = []
        for fn in sorted(os.listdir(img_dir)):
            if fn.lower().endswith(IMAGE_EXTS) and os.path.isfile(os.path.join(img_dir, fn)):
                files.append(fn)
        classes = load_class_names()
        labeled = set()
        for fn in os.listdir(lbl_dir):
            if fn.endswith('.txt'):
                labeled.add(os.path.splitext(fn)[0])
        self._send_json({
            'images': files,
            'classes': classes,
            'labeled': sorted(labeled),
            'dirs': {'images': img_dir, 'labels': lbl_dir},
        })

    # ---------- POST ----------
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == '/api/apply':
            self._api_apply()
            return
        if parsed.path == '/api/delete':
            self._api_delete()
            return
        if parsed.path != '/api/save':
            self._send_json({'error': 'not found'}, 404)
            return
        try:
            length = int(self.headers.get('Content-Length', 0))
            data = json.loads(self.rfile.read(length).decode('utf-8'))
            name = os.path.basename(data['name'])          # 图片文件名
            stem = os.path.splitext(name)[0]
            if not re.fullmatch(r'[\w\-.]+', stem):
                raise ValueError('非法文件名')
            boxes = data.get('boxes', [])
            lines = []
            for b in boxes:
                cls = int(b['cls'])
                lines.append(' '.join([str(cls), fmt_num(b['x']), fmt_num(b['y']),
                                       fmt_num(b['w']), fmt_num(b['h'])]))
            os.makedirs(get_dirs()[1], exist_ok=True)
            path = os.path.join(get_dirs()[1], stem + '.txt')
            if lines:
                with open(path, 'w', encoding='utf-8', newline='\n') as f:
                    f.write('\n'.join(lines) + '\n')
            elif os.path.isfile(path):
                os.remove(path)   # 框全部删完 -> 删除空标签文件(YOLO 负样本约定)
            self._send_json({'ok': True, 'path': path, 'count': len(lines)})
        except Exception as e:
            self._send_json({'error': str(e)}, 400)

    def _api_apply(self):
        """切换页面上选择的图片/标签/类别文件目录(允许分开在不同位置)"""
        try:
            length = int(self.headers.get('Content-Length', 0))
            data = json.loads(self.rfile.read(length).decode('utf-8'))
            img_dir = os.path.abspath(data.get('images', ''))
            lbl_dir = os.path.abspath(data.get('labels', ''))
            if not os.path.isdir(img_dir):
                raise ValueError('图片目录不存在: ' + img_dir)
            os.makedirs(lbl_dir, exist_ok=True)
            cls_path = (data.get('classes') or '').strip()
            if cls_path:
                cls_path = os.path.abspath(cls_path)
                if not os.path.isfile(cls_path):
                    raise ValueError('类别文件不存在: ' + cls_path)
            ACTIVE['images'] = img_dir
            ACTIVE['labels'] = lbl_dir
            if cls_path:
                ACTIVE['classes'] = cls_path
            print(f'切换数据集 -> 图片: {img_dir} | 标签: {lbl_dir}' +
                  (f' | 类别: {cls_path}' if cls_path else ''))
            self._send_json({'ok': True, 'images': img_dir, 'labels': lbl_dir,
                             'classes': get_classes_path()})
        except Exception as e:
            self._send_json({'error': str(e)}, 400)

    def _api_delete(self):
        """删除当前图片文件及对应标签文件(不可恢复)"""
        try:
            length = int(self.headers.get('Content-Length', 0))
            data = json.loads(self.rfile.read(length).decode('utf-8'))
            name = os.path.basename(data.get('name', ''))
            stem = os.path.splitext(name)[0]
            if not stem or not re.fullmatch(r'[\w\-.]+', stem):
                raise ValueError('非法文件名')
            img_dir, lbl_dir = get_dirs()
            img_path = os.path.join(img_dir, name)
            lbl_path = os.path.join(lbl_dir, stem + '.txt')
            if not os.path.isfile(img_path):
                raise ValueError('图片不存在: ' + name)
            os.remove(img_path)
            lbl_removed = os.path.isfile(lbl_path)
            if lbl_removed:
                os.remove(lbl_path)
            print(f'删除文件: {img_path}' + (f' 及 {lbl_path}' if lbl_removed else ''))
            self._send_json({'ok': True, 'labelRemoved': lbl_removed})
        except Exception as e:
            self._send_json({'error': str(e)}, 400)


# ==================== 前端页面 ====================
PAGE_HTML = r'''<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>YOLO 标注工具</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: "Microsoft YaHei", sans-serif; background: #1e1f24; color: #ddd; height: 100vh; display: flex; flex-direction: column; overflow: hidden; }
  #toolbar { display: flex; align-items: center; gap: 8px; padding: 8px 12px; background: #2a2b31; border-bottom: 1px solid #3a3b42; flex-wrap: wrap; }
  #toolbar button, #toolbar select { background: #3a3b42; color: #ddd; border: 1px solid #4a4b52; border-radius: 4px; padding: 5px 12px; cursor: pointer; font-size: 13px; }
  #toolbar button:hover { background: #4a4b55; }
  #toolbar button:disabled { opacity: .4; cursor: default; }
  #folderBar { display: flex; align-items: center; gap: 6px; padding: 6px 12px; background: #26272d; border-bottom: 1px solid #3a3b42; font-size: 12px; flex-wrap: wrap; }
  #folderBar label { color: #999; }
  #folderBar input { background: #1c1d21; color: #ccc; border: 1px solid #4a4b52; border-radius: 4px; padding: 4px 8px; font-size: 12px; width: 300px; }
  #folderBar button { background: #3a3b42; color: #ddd; border: 1px solid #4a4b52; border-radius: 4px; padding: 4px 10px; cursor: pointer; font-size: 12px; }
  #folderBar button:hover { background: #4a4b55; }
  #btnApplyDir { background: #2d6cdf; border-color: #2d6cdf; color: #fff; }
  #btnApplyDir:hover { background: #3d7cef; }
  #modalMask { position: fixed; inset: 0; background: rgba(0,0,0,.55); display: none; align-items: center; justify-content: center; z-index: 100; }
  #modal { background: #2a2b31; border: 1px solid #4a4b52; border-radius: 8px; width: 580px; max-width: 92vw; padding: 14px; }
  #modalTop { display: flex; gap: 8px; align-items: center; margin-bottom: 10px; }
  #modalPath { flex: 1; background: #1c1d21; color: #ccc; border: 1px solid #4a4b52; border-radius: 4px; padding: 5px 8px; font-size: 12px; }
  #modalList { max-height: 340px; overflow-y: auto; border: 1px solid #3a3b42; border-radius: 4px; background: #222328; }
  .dir-entry { padding: 6px 10px; cursor: pointer; font-size: 13px; border-bottom: 1px solid #2c2d33; word-break: break-all; }
  .dir-entry:hover { background: #34353d; }
  .dir-entry.sel { background: #2d4a7a; }
  #modalBtns { display: flex; justify-content: flex-end; gap: 8px; margin-top: 10px; }
  #modalBtns button { background: #3a3b42; color: #ddd; border: 1px solid #4a4b52; border-radius: 4px; padding: 5px 14px; cursor: pointer; font-size: 13px; }
  #modalBtns button.primary { background: #2d6cdf; border-color: #2d6cdf; color: #fff; }
  #btnSave { background: #2d6cdf; border-color: #2d6cdf; color: #fff; }
  #btnSave:hover { background: #3d7cef; }
  #btnDelete { background: #b03a3a; border-color: #b03a3a; color: #fff; }
  #btnDelete:hover { background: #c94a4a; }
  #btnDelImg { background: #7a2d2d; border-color: #7a2d2d; color: #fff; }
  #btnDelImg:hover { background: #993c3c; }
  .chk { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; color: #bbb; cursor: pointer; user-select: none; }
  #zoomLabel { font-size: 12px; color: #9ab; min-width: 46px; text-align: center; }
  #status { margin-left: auto; font-size: 12px; color: #9a9; white-space: nowrap; }
  #status.dirty { color: #f0b429; }
  #main { flex: 1; display: flex; min-height: 0; }
  #sidebar { width: 230px; background: #25262b; border-right: 1px solid #3a3b42; overflow-y: auto; flex-shrink: 0; position: relative; }
  #sidebar.collapsed { width: 0; overflow: hidden; border-right: none; }
  #btnSidebar { position: absolute; left: 6px; top: 6px; z-index: 30; background: rgba(58,59,66,.85); color: #ddd; border: 1px solid #4a4b52; border-radius: 4px; cursor: pointer; font-size: 12px; padding: 2px 7px; line-height: 1.4; }
  #btnSidebar:hover { background: #4a4b55; }
  .img-item { padding: 6px 10px; font-size: 12px; cursor: pointer; display: flex; align-items: center; gap: 6px; border-left: 3px solid transparent; word-break: break-all; }
  .img-item:hover { background: #30313a; }
  .img-item.active { background: #2d4a7a; border-left-color: #4d8adf; }
  .img-item .dot { width: 8px; height: 8px; border-radius: 50%; background: #555; flex-shrink: 0; }
  .img-item .dot.labeled { background: #4caf50; }
  .img-item .dot.dirty { background: #f0b429; }
  #canvasArea { flex: 1; display: flex; align-items: center; justify-content: center; overflow: hidden; position: relative; background: #17181c; }
  #imgWrap { position: relative; line-height: 0; flex-shrink: 0; }
  #imgWrap img { width: 100%; height: 100%; display: block; user-select: none; -webkit-user-drag: none; }
  #imgWrap.boxes-hidden .box .tag { display: none; }
  .box { position: absolute; border: 2px solid #4caf50; cursor: move; }
  .box.selected { border-color: #ffd54f; box-shadow: 0 0 0 1px #ffd54f inset; z-index: 10; }
  .box .tag { position: absolute; top: -20px; left: -2px; font-size: 11px; line-height: 16px; padding: 0 4px; color: #fff; background: #4caf50; white-space: nowrap; border-radius: 2px; }
  .box.selected .tag { background: #b58900; }
  .handle { position: absolute; width: 9px; height: 9px; background: #fff; border: 1px solid #333; border-radius: 1px; z-index: 11; }
  .h-nw { left:-5px; top:-5px; cursor:nw-resize; } .h-n { left:calc(50% - 5px); top:-5px; cursor:n-resize; }
  .h-ne { right:-5px; top:-5px; cursor:ne-resize; } .h-e { right:-5px; top:calc(50% - 5px); cursor:e-resize; }
  .h-se { right:-5px; bottom:-5px; cursor:se-resize; } .h-s { left:calc(50% - 5px); bottom:-5px; cursor:s-resize; }
  .h-sw { left:-5px; bottom:-5px; cursor:sw-resize; } .h-w { left:-5px; top:calc(50% - 5px); cursor:w-resize; }
  #drawBox { position: absolute; border: 2px dashed #4d8adf; background: rgba(77,138,223,.15); pointer-events: none; display: none; z-index: 20; }
  #imgWrap.drawing { cursor: crosshair; }
  #hint { position: absolute; bottom: 8px; left: 50%; transform: translateX(-50%); font-size: 12px; color: #777; background: rgba(0,0,0,.4); padding: 3px 10px; border-radius: 4px; white-space: nowrap; }
</style>
</head>
<body>
<div id="toolbar">
  <button id="btnPrev" title="上一张 (←)">◀ 上一张</button>
  <span id="counter" style="font-size:13px">0 / 0</span>
  <button id="btnNext" title="下一张 (→)">下一张 ▶</button>
  <button id="btnUndo" title="撤回 (Ctrl+Z)">↩ 撤回</button>
  <button id="btnRedo" title="重做 (Ctrl+Y)">↪ 重做</button>
  <button id="btnDelete" title="删除选中框 (Delete)">✕ 删除框</button>
  <button id="btnDelImg" title="删除当前图片文件及对应标签文件(不可恢复)">🗑 删除图片</button>
  <select id="classSel" title="当前绘制/修改的类别"></select>
  <label class="chk" title="隐藏标注框上的类别文字标签, 防止遮挡图片细节(框体仍保留)"><input type="checkbox" id="chkLabels" checked>显示文字标签</label>
  <label class="chk" title="修改后自动写回标签文件"><input type="checkbox" id="chkAutoSave">自动保存</label>
  <button id="btnSave" title="保存 (Ctrl+S)">💾 保存</button>
  <span id="zoomLabel">100%</span>
  <span id="status"></span>
</div>
<div id="folderBar">
  <label>图片目录</label>
  <input id="imgDirInput" placeholder="图片文件夹路径">
  <button id="btnBrowseImg">浏览…</button>
  <label>标签目录</label>
  <input id="lblDirInput" placeholder="标签文件夹路径(可与图片分开)">
  <button id="btnBrowseLbl">浏览…</button>
  <label>类别文件</label>
  <input id="clsFileInput" placeholder="classes.txt 路径" style="width:220px">
  <button id="btnBrowseCls">浏览…</button>
  <button id="btnApplyDir">切换数据集</button>
</div>
<div id="main">
  <div id="sidebar"></div>
  <div id="canvasArea">
    <button id="btnSidebar" title="折叠/展开图片列表">◀</button>
    <div id="imgWrap">
      <img id="img" draggable="false">
      <div id="drawBox"></div>
    </div>
    <div id="hint">滚轮=缩放 | 中键拖拽=平移 | 双击空白=复位视图 | 空白拖拽=画框 | 双击框=改类别 | A/D=上/下一张 | Del=删框 | Ctrl+Z=撤回</div>
  </div>
</div>
<div id="modalMask">
  <div id="modal">
    <div id="modalTop">
      <button id="btnModalUp" class="btn">⬆ 上级</button>
      <input id="modalPath">
      <button id="btnModalGo" class="btn">跳转</button>
    </div>
    <div id="modalList"></div>
    <div id="modalBtns">
      <button id="btnModalCancel">取消</button>
      <button id="btnModalOk" class="primary">选择当前目录</button>
    </div>
  </div>
</div>
<script>
const COLORS = ['#4caf50','#e91e63','#2196f3','#ff9800','#9c27b0','#00bcd4','#f44336','#8bc34a','#ffc107','#607d8b'];
let images = [], classes = [], labeledSet = new Set(), dirtySet = new Set();
let curIdx = -1, boxes = [], selected = -1;
let undoStack = [], redoStack = [];
let zoom = 1, panX = 0, panY = 0, dispW = 0, dispH = 0;   // 视图缩放/平移状态
let autoSaveTimer = null;

const img = document.getElementById('img'), imgWrap = document.getElementById('imgWrap');
const canvasArea = document.getElementById('canvasArea');
const sidebar = document.getElementById('sidebar'), statusEl = document.getElementById('status');
const classSel = document.getElementById('classSel'), counterEl = document.getElementById('counter');
const drawBoxEl = document.getElementById('drawBox');
const zoomLabel = document.getElementById('zoomLabel');
const chkLabels = document.getElementById('chkLabels'), chkAutoSave = document.getElementById('chkAutoSave');

function colorOf(cls) { return COLORS[cls % COLORS.length]; }
function nameOf(cls) { return classes[cls] !== undefined ? classes[cls] : ('class_' + cls); }
function setStatus(msg, dirty) {
  statusEl.textContent = msg;
  statusEl.className = dirty ? 'dirty' : '';
}
function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

// ---------- 列表 ----------
async function loadList() {
  const r = await fetch('/api/list'); const d = await r.json();
  images = d.images; classes = d.classes || []; labeledSet = new Set(d.labeled || []);
  classSel.innerHTML = '';
  classes.forEach((n, i) => {
    const o = document.createElement('option'); o.value = i; o.textContent = i + ': ' + n;
    classSel.appendChild(o);
  });
  renderSidebar();
  if (images.length) selectImage(0);
  else setStatus('目录下没有图片');
}
function dotClass(fn) {
  if (dirtySet.has(fn)) return 'dot dirty';
  return 'dot' + (labeledSet.has(fn.replace(/\.[^.]+$/, '')) ? ' labeled' : '');
}
function renderSidebar() {
  const keepScroll = sidebar.scrollTop;
  sidebar.innerHTML = '';
  const frag = document.createDocumentFragment();
  images.forEach((fn, i) => {
    const div = document.createElement('div');
    div.className = 'img-item' + (i === curIdx ? ' active' : '');
    const dot = document.createElement('span');
    dot.className = dotClass(fn);
    div.appendChild(dot);
    div.appendChild(document.createTextNode(fn));
    div.onclick = () => { if (i !== curIdx) selectImage(i); };
    frag.appendChild(div);
  });
  sidebar.appendChild(frag);
  sidebar.scrollTop = keepScroll;
}
// 仅更新单项状态点/高亮, 避免每次都重建整个列表(列表长时拖框会卡)
function updateSidebarDot(i) {
  const el = sidebar.children[i];
  if (!el) return;
  const dot = el.querySelector('.dot');
  if (dot) dot.className = dotClass(images[i]);
}
function setActiveItem(i) {
  for (let k = 0; k < sidebar.children.length; k++) {
    sidebar.children[k].classList.toggle('active', k === i);
  }
}
function refreshSidebarActive() {
  if (sidebar.children.length === images.length) setActiveItem(curIdx);
  else renderSidebar();
}
// 当前图片滚动到侧边栏居中位置(开头和末尾不强制居中)
function scrollActiveIntoView() {
  if (sidebar.classList.contains('collapsed')) return;
  const el = sidebar.children[curIdx];
  if (!el) return;
  if (curIdx === 0) { sidebar.scrollTop = 0; return; }
  if (curIdx === images.length - 1) { sidebar.scrollTop = sidebar.scrollHeight; return; }
  const elTop = el.getBoundingClientRect().top - sidebar.getBoundingClientRect().top;
  sidebar.scrollTop += elTop - (sidebar.clientHeight - el.offsetHeight) / 2;
}
// 侧边栏折叠/展开
document.getElementById('btnSidebar').onclick = function () {
  sidebar.classList.toggle('collapsed');
  this.textContent = sidebar.classList.contains('collapsed') ? '▶' : '◀';
};
function markDirty() {
  if (curIdx < 0) return;
  dirtySet.add(images[curIdx]);
  updateSidebarDot(curIdx);
  setStatus('● 未保存修改', true);
  if (chkAutoSave.checked) scheduleAutoSave();
}
function scheduleAutoSave() {
  clearTimeout(autoSaveTimer);
  autoSaveTimer = setTimeout(() => { if (dirtySet.has(images[curIdx])) save(true); }, 250);
}
// 开启自动保存时, 切图/关页前先把待保存的修改刷盘, 避免丢数据与误报"未保存"
async function flushSave() {
  clearTimeout(autoSaveTimer);
  if (curIdx >= 0 && dirtySet.has(images[curIdx])) await save(true);
}

// ---------- 图片/标签加载 ----------
let navBusy = false;   // 导航锁: 快速连按 A/D 时避免异步加载交错
async function selectImage(i) {
  if (navBusy || i < 0 || i >= images.length) return;
  navBusy = true;
  try {
    await doSelectImage(i);
  } finally {
    navBusy = false;
  }
}
async function doSelectImage(i) {
  if (curIdx >= 0 && dirtySet.has(images[curIdx])) {
    if (chkAutoSave.checked) {
      await flushSave();          // 自动保存: 立即刷盘后直接切图
    } else if (!confirm('当前图片有未保存修改，切换后将丢失，确定继续？')) return;
  }
  clearTimeout(autoSaveTimer);
  curIdx = i; selected = -1; undoStack = []; redoStack = [];
  const fn = images[i];
  counterEl.textContent = (i + 1) + ' / ' + images.length;
  img.src = '/api/image?name=' + encodeURIComponent(fn) + '&t=' + Date.now();
  await new Promise(res => { img.onload = res; img.onerror = res; });
  initView();
  const stem = fn.replace(/\.[^.]+$/, '');
  const r = await fetch('/api/label?name=' + encodeURIComponent(stem));
  const d = await r.json();
  boxes = (d.boxes || []).map(b => ({...b}));
  refreshSidebarActive(); renderBoxes(); updateButtons();
  scrollActiveIntoView();
  setStatus(dirtySet.has(fn) ? '● 未保存修改' : '已加载 ' + boxes.length + ' 个框', dirtySet.has(fn));
}

// ---------- 渲染 ----------
function renderBoxes() {
  imgWrap.querySelectorAll('.box').forEach(e => e.remove());
  const W = dispW, H = dispH;
  boxes.forEach((b, i) => {
    const el = document.createElement('div');
    el.className = 'box' + (i === selected ? ' selected' : '');
    const c = colorOf(b.cls);
    el.style.borderColor = i === selected ? '#ffd54f' : c;
    el.style.left = ((b.x - b.w / 2) * W) + 'px';
    el.style.top  = ((b.y - b.h / 2) * H) + 'px';
    el.style.width  = (b.w * W) + 'px';
    el.style.height = (b.h * H) + 'px';
    const tag = document.createElement('span');
    tag.className = 'tag'; tag.style.background = i === selected ? '#b58900' : c;
    tag.textContent = b.cls + ':' + nameOf(b.cls) + '  [' + i + ']';
    el.appendChild(tag);
    if (i === selected) {
      ['nw','n','ne','e','se','s','sw','w'].forEach(dir => {
        const h = document.createElement('div');
        h.className = 'handle h-' + dir; h.dataset.dir = dir;
        el.appendChild(h);
      });
    }
    el.dataset.idx = i;
    imgWrap.appendChild(el);
  });
}
function updateButtons() {
  document.getElementById('btnUndo').disabled = !undoStack.length;
  document.getElementById('btnRedo').disabled = !redoStack.length;
  document.getElementById('btnDelete').disabled = selected < 0;
}

// ---------- 撤回/重做 ----------
function snapshot() { return JSON.stringify({boxes, selected}); }
function pushUndo() {
  undoStack.push(snapshot());
  if (undoStack.length > 100) undoStack.shift();
  redoStack = []; updateButtons();
}
function restore(snap) {
  const s = JSON.parse(snap); boxes = s.boxes; selected = s.selected;
  renderBoxes(); updateButtons(); markDirty();
}
function undo() { if (!undoStack.length) return; redoStack.push(snapshot()); restore(undoStack.pop()); }
function redo() { if (!redoStack.length) return; undoStack.push(snapshot()); restore(redoStack.pop()); }

// ---------- 视图缩放/平移 ----------
function initView() {
  const areaW = canvasArea.clientWidth - 20, areaH = canvasArea.clientHeight - 20;
  const fit = Math.min(areaW / img.naturalWidth, areaH / img.naturalHeight, 1);
  dispW = img.naturalWidth * fit; dispH = img.naturalHeight * fit;
  zoom = 1; panX = 0; panY = 0;
  applyView();
}
function applyView() {
  imgWrap.style.width = dispW + 'px';
  imgWrap.style.height = dispH + 'px';
  imgWrap.style.transform = 'translate(' + panX + 'px,' + panY + 'px) scale(' + zoom + ')';
  zoomLabel.textContent = Math.round(zoom * 100) + '%';
}
// 滚轮缩放: 以鼠标位置为中心, 最大 20 倍
canvasArea.addEventListener('wheel', e => {
  e.preventDefault();
  if (!dispW) return;
  const r = canvasArea.getBoundingClientRect();
  const cx = e.clientX - r.left - r.width / 2;
  const cy = e.clientY - r.top - r.height / 2;
  const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
  const nz = clamp(zoom * factor, 1, 20);
  if (nz === zoom) return;
  panX = cx - (cx - panX) * (nz / zoom);
  panY = cy - (cy - panY) * (nz / zoom);
  zoom = nz;
  applyView();
}, {passive: false});
// 双击空白处复位视图
imgWrap.addEventListener('dblclick', e => {
  if (e.target === img) { initView(); renderBoxes(); }
});
// 文字标签显隐开关(仅隐藏框上的类别文字, 框体保留)
chkLabels.addEventListener('change', () => {
  imgWrap.classList.toggle('boxes-hidden', !chkLabels.checked);
});

// ---------- 交互 ----------
let drag = null;  // {type:'move'|'resize'|'draw'|'pan', ...}

imgWrap.addEventListener('mousedown', e => {
  const W = dispW, H = dispH;
  const rect = imgWrap.getBoundingClientRect();
  // 屏幕坐标 -> 未缩放图像坐标(transform scale 后 rect 已含缩放)
  const mx = (e.clientX - rect.left) / zoom, my = (e.clientY - rect.top) / zoom;

  if (e.button === 1) {          // 中键拖拽平移视图
    e.preventDefault();
    drag = {type:'pan', startX:e.clientX, startY:e.clientY, origPanX:panX, origPanY:panY};
    return;
  }
  if (e.button !== 0) return;
  const handle = e.target.closest('.handle');
  const boxEl = e.target.closest('.box');

  if (handle && boxEl) {           // 缩放
    const idx = +boxEl.dataset.idx;
    pushUndo();
    drag = {type:'resize', idx, dir:handle.dataset.dir, startX:mx, startY:my, orig:{...boxes[idx]}, W, H, moved:false};
    e.preventDefault(); e.stopPropagation();
  } else if (boxEl) {              // 移动 / 选中
    const idx = +boxEl.dataset.idx;
    selected = idx;
    pushUndo();
    drag = {type:'move', idx, startX:mx, startY:my, orig:{...boxes[idx]}, W, H, moved:false};
    renderBoxes();
    e.preventDefault();
  } else if (e.target === img || e.target === imgWrap) {  // 绘制新框
    selected = -1; renderBoxes();
    drag = {type:'draw', startX:mx, startY:my, W, H, moved:false};
    imgWrap.classList.add('drawing');
    e.preventDefault();
  }
});

window.addEventListener('mousemove', e => {
  if (!drag) return;
  if (drag.type === 'pan') {
    panX = drag.origPanX + (e.clientX - drag.startX);
    panY = drag.origPanY + (e.clientY - drag.startY);
    applyView();
    return;
  }
  const rect = imgWrap.getBoundingClientRect();
  const mx = clamp((e.clientX - rect.left) / zoom, 0, dispW);
  const my = clamp((e.clientY - rect.top) / zoom, 0, dispH);
  const dx = (mx - drag.startX) / drag.W, dy = (my - drag.startY) / drag.H;

  if (drag.type === 'move') {
    drag.moved = true;
    const o = drag.orig, b = boxes[drag.idx];
    b.x = clamp(o.x + dx, o.w / 2, 1 - o.w / 2);
    b.y = clamp(o.y + dy, o.h / 2, 1 - o.h / 2);
    renderBoxes();
  } else if (drag.type === 'resize') {
    drag.moved = true;
    const o = drag.orig, b = boxes[drag.idx];
    let x1 = o.x - o.w / 2, y1 = o.y - o.h / 2, x2 = o.x + o.w / 2, y2 = o.y + o.h / 2;
    if (drag.dir.includes('w')) x1 = clamp(o.x - o.w / 2 + dx, 0, x2 - 0.002);
    if (drag.dir.includes('e')) x2 = clamp(o.x + o.w / 2 + dx, x1 + 0.002, 1);
    if (drag.dir.includes('n')) y1 = clamp(o.y - o.h / 2 + dy, 0, y2 - 0.002);
    if (drag.dir.includes('s')) y2 = clamp(o.y + o.h / 2 + dy, y1 + 0.002, 1);
    b.x = (x1 + x2) / 2; b.y = (y1 + y2) / 2; b.w = x2 - x1; b.h = y2 - y1;
    renderBoxes();
  } else if (drag.type === 'draw') {
    drag.moved = true;
    const x1 = Math.min(drag.startX, mx), y1 = Math.min(drag.startY, my);
    const x2 = Math.max(drag.startX, mx), y2 = Math.max(drag.startY, my);
    drawBoxEl.style.display = 'block';
    drawBoxEl.style.left = x1 + 'px'; drawBoxEl.style.top = y1 + 'px';
    drawBoxEl.style.width = (x2 - x1) + 'px'; drawBoxEl.style.height = (y2 - y1) + 'px';
  }
});

window.addEventListener('mouseup', e => {
  if (!drag) return;
  const d = drag; drag = null;
  if (d.type === 'pan') return;
  imgWrap.classList.remove('drawing');
  drawBoxEl.style.display = 'none';

  if (d.type === 'draw') {
    const rect = imgWrap.getBoundingClientRect();
    const mx = clamp((e.clientX - rect.left) / zoom, 0, d.W);
    const my = clamp((e.clientY - rect.top) / zoom, 0, d.H);
    const x1 = Math.min(d.startX, mx), y1 = Math.min(d.startY, my);
    const w = Math.abs(mx - d.startX) / d.W, h = Math.abs(my - d.startY) / d.H;
    if (w > 0.005 && h > 0.005) {   // 过滤误点击
      pushUndo();
      boxes.push({cls: +classSel.value, x: (x1 / d.W) + w / 2, y: (y1 / d.H) + h / 2, w, h});
      selected = boxes.length - 1;
      markDirty();
    }
    renderBoxes(); updateButtons();
  } else if (d.moved) {
    markDirty();
  } else if (d.type === 'move') {
    undoStack.pop(); updateButtons();   // 只是点击选中, 撤销多余快照
  }
});

// 双击框: 切换类别为当前选择类别
imgWrap.addEventListener('dblclick', e => {
  const boxEl = e.target.closest('.box');
  if (!boxEl) return;
  pushUndo();
  boxes[+boxEl.dataset.idx].cls = +classSel.value;
  renderBoxes(); markDirty();
});

// ---------- 保存/删除 ----------
async function save(auto) {
  if (curIdx < 0) return;
  const fn = images[curIdx];
  const r = await fetch('/api/save', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name: fn, boxes})
  });
  const d = await r.json();
  if (d.ok) {
    dirtySet.delete(fn);
    labeledSet.add(fn.replace(/\.[^.]+$/, ''));
    updateSidebarDot(curIdx); updateButtons();
    setStatus((auto ? '✔ 自动保存 ' : '✔ 已保存 ') + d.count + ' 个框');
  } else {
    setStatus('✘ 保存失败: ' + d.error, true);
  }
}
function deleteSelected() {
  if (selected < 0) return;
  pushUndo();
  boxes.splice(selected, 1);
  selected = -1;
  renderBoxes(); updateButtons(); markDirty();
}
async function deleteImage() {
  if (curIdx < 0) return;
  const fn = images[curIdx];
  if (!confirm('将永久删除图片文件及对应标签文件（不可恢复）：\n' + fn + '\n\n确定删除？')) return;
  clearTimeout(autoSaveTimer);
  dirtySet.delete(fn);          // 文件都要删了, 不能再触发自动保存把它写回去
  const r = await fetch('/api/delete', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name: fn})
  });
  const d = await r.json();
  if (!d.ok) { setStatus('✘ 删除失败: ' + d.error, true); alert('删除失败: ' + d.error); return; }
  images.splice(curIdx, 1);
  dirtySet.delete(fn);
  labeledSet.delete(fn.replace(/\.[^.]+$/, ''));
  setStatus('🗑 已删除 ' + fn + (d.labelRemoved ? ' 及其标签文件' : ''));
  const n = images.length;
  if (!n) { curIdx = -1; boxes = []; renderSidebar(); renderBoxes(); counterEl.textContent = '0 / 0'; return; }
  selectImage(Math.min(curIdx, n - 1));
}

// ---------- 导航 ----------
function nav(step) {
  const i = curIdx + step;
  if (i >= 0 && i < images.length) selectImage(i);
}

// ---------- 目录选择 ----------
const modalMask = document.getElementById('modalMask');
const modalPath = document.getElementById('modalPath');
const modalList = document.getElementById('modalList');
const imgDirInput = document.getElementById('imgDirInput');
const lblDirInput = document.getElementById('lblDirInput');
const clsFileInput = document.getElementById('clsFileInput');
let modalTarget = null, modalCur = '', modalParent = '', browseFiles = false, modalSel = '';

function openBrowser(targetInput, files) {
  modalTarget = targetInput;
  browseFiles = !!files;
  modalSel = '';
  modalMask.style.display = 'flex';
  let start = targetInput.value.trim();
  if (browseFiles && start) start = start.replace(/[\\/][^\\/]*$/, '');  // 从文件所在目录开始
  browseTo(start || '');
}
async function browseTo(path) {
  const url = '/api/browse?kind=' + (browseFiles ? 'file' : 'dir') + '&path=' + encodeURIComponent(path);
  const r = await fetch(url);
  const d = await r.json();
  if (d.error) { setStatus('✘ ' + d.error, true); return; }
  modalCur = d.path || ''; modalParent = d.parent || ''; modalSel = '';
  modalPath.value = modalCur || '(选择盘符)';
  modalList.innerHTML = '';
  d.dirs.forEach(entry => {
    const div = document.createElement('div');
    div.className = 'dir-entry';
    div.textContent = entry;
    if (browseFiles && !d.is_root) {
      div.onclick = () => {   // 文件模式: 单击选中, 再点确认
        modalList.querySelectorAll('.dir-entry').forEach(x => x.classList.remove('sel'));
        div.classList.add('sel');
        modalSel = entry;
      };
    } else {
      div.onclick = () => browseTo(entry);
    }
    modalList.appendChild(div);
  });
  if (!d.dirs.length) {
    modalList.innerHTML = '<div class="dir-entry" style="color:#777">' + (browseFiles && !d.is_root ? '(无 txt 文件)' : '(无子文件夹)') + '</div>';
  }
}
document.getElementById('btnBrowseImg').onclick = () => openBrowser(imgDirInput);
document.getElementById('btnBrowseLbl').onclick = () => openBrowser(lblDirInput);
document.getElementById('btnBrowseCls').onclick = () => openBrowser(clsFileInput, true);
document.getElementById('btnModalUp').onclick = () => browseTo(modalParent);
document.getElementById('btnModalGo').onclick = () => browseTo(modalPath.value.trim());
modalPath.addEventListener('keydown', e => { if (e.key === 'Enter') browseTo(modalPath.value.trim()); });
document.getElementById('btnModalCancel').onclick = () => { modalMask.style.display = 'none'; };
document.getElementById('btnModalOk').onclick = () => {
  if (browseFiles) {
    if (!modalSel) { alert('请先在列表中点击选择一个 txt 文件'); return; }
    modalTarget.value = modalSel;
  } else {
    if (!modalCur) { alert('请先进入一个具体目录(当前在盘符列表)'); return; }
    modalTarget.value = modalCur;
  }
  modalMask.style.display = 'none';
};
modalMask.addEventListener('mousedown', e => { if (e.target === modalMask) modalMask.style.display = 'none'; });

async function applyDirs() {
  const imgs = imgDirInput.value.trim(), lbls = lblDirInput.value.trim();
  const cls = clsFileInput.value.trim();
  if (!imgs || !lbls) { alert('请先选择图片目录和标签目录'); return; }
  if (dirtySet.size) {
    if (chkAutoSave.checked) await flushSave();
    else if (!confirm('存在未保存修改，切换目录后将丢失，确定继续？')) return;
  }
  const r = await fetch('/api/apply', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({images: imgs, labels: lbls, classes: cls})
  });
  const d = await r.json();
  if (!d.ok) { setStatus('✘ 切换失败: ' + d.error, true); alert('切换失败: ' + d.error); return; }
  imgDirInput.value = d.images; lblDirInput.value = d.labels; clsFileInput.value = d.classes;
  dirtySet.clear(); curIdx = -1; boxes = []; selected = -1;
  undoStack = []; redoStack = [];
  await loadList();
}
document.getElementById('btnApplyDir').onclick = applyDirs;

async function loadConfig() {
  const r = await fetch('/api/config'); const d = await r.json();
  imgDirInput.value = d.images; lblDirInput.value = d.labels; clsFileInput.value = d.classes || '';
}

document.getElementById('btnPrev').onclick = () => nav(-1);
document.getElementById('btnNext').onclick = () => nav(1);
document.getElementById('btnUndo').onclick = undo;
document.getElementById('btnRedo').onclick = redo;
document.getElementById('btnDelete').onclick = deleteSelected;
document.getElementById('btnDelImg').onclick = deleteImage;
document.getElementById('btnSave').onclick = () => save();

// 仅当焦点在"需要输入文字"的控件上时才屏蔽快捷键;
// 勾选框/下拉框点完后焦点仍留在其上, 不能屏蔽, 否则 A/D 会失效
function blocksShortcut(el) {
  if (!el) return false;
  if (el.isContentEditable) return true;
  const tag = (el.tagName || '').toLowerCase();
  if (tag === 'textarea' || tag === 'select') return true;
  if (tag === 'input') {
    const t = (el.type || 'text').toLowerCase();
    return t === 'text' || t === 'search' || t === 'url' || t === 'email'
        || t === 'password' || t === 'number' || t === 'file';
  }
  return false;
}
window.addEventListener('keydown', e => {
  if (blocksShortcut(e.target)) return;
  if (e.ctrlKey && e.key.toLowerCase() === 'z') { e.preventDefault(); undo(); }
  else if (e.ctrlKey && e.key.toLowerCase() === 'y') { e.preventDefault(); redo(); }
  else if (e.ctrlKey && e.key.toLowerCase() === 's') { e.preventDefault(); save(); }
  else if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); deleteSelected(); }
  else if (e.key === 'ArrowLeft' || e.key.toLowerCase() === 'a') { e.preventDefault(); nav(-1); }
  else if (e.key === 'ArrowRight' || e.key.toLowerCase() === 'd') { e.preventDefault(); nav(1); }
});
// 勾选后立即释放焦点, 保证接着能直接按 A/D
[chkLabels, chkAutoSave].forEach(el => {
  el.addEventListener('change', () => el.blur());
  el.addEventListener('click', () => setTimeout(() => el.blur(), 0));
});
// 类别下拉框选完也释放焦点(下拉框展开时需要方向键选项, 不能屏蔽)
classSel.addEventListener('change', () => classSel.blur());
// 工具栏按钮点完释放焦点, 避免空格/回车误重复触发
document.querySelectorAll('#toolbar button, #folderBar button, #btnSidebar').forEach(b => {
  b.addEventListener('click', () => setTimeout(() => b.blur(), 0));
});
window.addEventListener('resize', () => { if (img.src) initView(); });
window.addEventListener('beforeunload', e => {
  clearTimeout(autoSaveTimer);
  // 自动保存模式: 关页/刷新前用 beacon 同步刷盘, 不再弹拦截提示
  if (chkAutoSave.checked && curIdx >= 0 && dirtySet.has(images[curIdx])) {
    try {
      const payload = new Blob([JSON.stringify({name: images[curIdx], boxes})],
                               {type: 'application/json'});
      if (navigator.sendBeacon('/api/save', payload)) return;
    } catch (err) { /* 降级到下方拦截提示 */ }
  }
  if (dirtySet.size) { e.preventDefault(); e.returnValue = ''; }
});

loadConfig();
loadList();
</script>
</body>
</html>
'''


def main():
    ap = argparse.ArgumentParser(description='YOLO 数据集可视化标注工具')
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), '生成数据', 'yolo_dataset', 'test')
    ap.add_argument('--images', default=os.path.join(base, 'images'), help='图片文件夹')
    ap.add_argument('--labels', default=os.path.join(base, 'labels'), help='标签文件夹')
    ap.add_argument('--classes', default=os.path.join(os.path.dirname(base), '..', 'classes.txt'),
                    help='classes.txt 路径(可选, 缺省尝试 data.yaml)')
    ap.add_argument('--port', type=int, default=8765, help='端口, 默认 8765')
    ap.add_argument('--no-open', action='store_true', help='启动后不自动打开浏览器')
    args = ap.parse_args()

    CONFIG['images'] = os.path.abspath(args.images)
    CONFIG['labels'] = os.path.abspath(args.labels)
    CONFIG['classes'] = os.path.abspath(args.classes)
    if not os.path.isdir(CONFIG['images']):
        raise SystemExit(f'图片目录不存在: {CONFIG["images"]}')
    os.makedirs(CONFIG['labels'], exist_ok=True)

    mimetypes.add_type('image/webp', '.webp')
    try:
        server = Server(('127.0.0.1', args.port), Handler)
    except OSError as e:
        raise SystemExit(
            f'端口 {args.port} 已被占用(可能已有一个标注服务在运行)。\n'
            f'  → 直接用浏览器访问 http://127.0.0.1:{args.port} 即可；\n'
            f'  → 或先停掉旧进程：netstat -ano | findstr :{args.port}  然后 taskkill /F /PID <pid>\n'
            f'  → 也可换一个端口：--port 8766\n原始错误: {e}')
    print(f'图片目录: {CONFIG["images"]}')
    print(f'标签目录: {CONFIG["labels"]}')
    print(f'类别文件: {CONFIG["classes"] if os.path.isfile(CONFIG["classes"]) else "(未找到, 尝试 data.yaml)"}')
    print(f'打开浏览器访问: http://127.0.0.1:{args.port}')
    if not args.no_open:
        threading.Timer(0.8, lambda: webbrowser.open(f'http://127.0.0.1:{args.port}')).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n已停止')


if __name__ == '__main__':
    main()
