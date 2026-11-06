# YOLO Annotation Web

零依赖的 YOLO 数据集可视化标注工具：一个 Python 标准库后端 + 内嵌网页前端，单文件即可运行。
支持在图片上直接拖拽修改标注框、滚轮缩放标注小目标、撤回/重做、自动保存、批量翻页。

> A zero-dependency web-based annotation tool for YOLO datasets. Single Python file, no pip install required.

![python](https://img.shields.io/badge/python-3.8+-blue) ![deps](https://img.shields.io/badge/dependencies-none-brightgreen) ![license](https://img.shields.io/badge/license-MIT-green)

---

## 为什么需要它

标注 YOLO 数据集时，VLM 自动标注或模型预标注的结果往往需要人工微调。
labelImg / CVAT 这类工具偏重，而本工具的定位是**快速修正已有标注**：

- 打开即用，`python annotation_server.py` 一条命令，**无需安装任何第三方包**
- 直接读写标准的 `images/` + `labels/` 目录结构，改完即生效，无需导入导出
- 针对小目标做了滚轮缩放（最高 20 倍）与坐标换算，放大后画框依然精确

## 快速开始

```bash
# 1. 启动（默认会读取命令行指定的目录，并自动打开浏览器）
python annotation_server.py --images path/to/images --labels path/to/labels

# 2. 浏览器访问
http://127.0.0.1:8765
```

也可以先随便指定一个目录启动，进页面后在顶部目录栏里用"浏览…"图形化选择数据集，
**图片目录、标签目录、类别文件三者可以分别指向不同位置**。

### 命令行参数

| 参数 | 说明 | 默认值 |
|---|---|---|
| `--images` | 图片文件夹 | `生成数据/yolo_dataset/test/images` |
| `--labels` | 标签文件夹 | `生成数据/yolo_dataset/test/labels` |
| `--classes` | 类别文件（每行一个类别名） | `生成数据/classes.txt` |
| `--port` | 监听端口 | `8765` |
| `--no-open` | 启动后不自动打开浏览器 | 关闭 |

## 数据集格式

标准 YOLO 目录结构：

```
dataset/
├── images/
│   ├── 0001.png
│   └── 0002.jpg
├── labels/
│   ├── 0001.txt      # 与图片同名的 txt
│   └── 0002.txt
└── classes.txt       # 可选，每行一个类别名，行号即 class id
```

标签文件每行一个目标，坐标为**归一化中心点格式**：

```
<class_id> <x_center> <y_center> <width> <height>
2 0.513000 0.221500 0.038000 0.113000
0 0.576500 0.590000 0.027000 0.084000
```

**数字格式处理**：保存时统一保留 6 位小数并去掉尾零（`0.513000`），坐标实时钳制在 `[0,1]`，
框最小宽高限制为 `0.002`，不会产生越界或退化的框。解析时对 class id 做 `int(float(...))` 容错，脏行自动跳过。

若一张图的框被全部删除，保存时会**删除对应的空 txt 文件**（符合 YOLO 负样本约定）。

类别名来源优先级：`classes.txt` > `data.yaml` 中的 `names: [...]`。

## 操作方式

### 标注

| 操作 | 方式 |
|---|---|
| 移动框 | 鼠标拖动框体 |
| 缩放框 | 拖动选中框的 8 个手柄 |
| 画新框 | 在图片空白处拖拽，类别取工具栏下拉框当前值 |
| 改类别 | 双击框，改为当前选中类别 |
| 删除框 | 选中后按 `Delete`，或点"✕ 删除框" |
| 撤回 / 重做 | `Ctrl+Z` / `Ctrl+Y`（最多 100 步） |
| 保存 | `Ctrl+S`，或开启"自动保存" |
| 删除整张图片 | "🗑 删除图片"（同时删除图片与标签文件，二次确认，不可恢复） |

### 视图（小目标标注）

| 操作 | 方式 |
|---|---|
| 缩放 | 滚轮，以鼠标位置为中心，1x ~ 20x |
| 平移 | 鼠标中键拖拽 |
| 复位 | 双击图片空白处 |
| 隐藏文字标签 | 取消勾选"显示文字标签"，仅隐藏框上的类别文字，**框体与手柄保留** |

### 导航

| 操作 | 方式 |
|---|---|
| 上/下一张 | `A` / `D`（或 `←` / `→`） |
| 跳转 | 点击左侧图片列表 |
| 折叠列表 | 图片区左上角的 ◀ / ▶ 按钮 |

左侧列表的状态点：🟢 已有标签 · 🟡 有未保存修改 · ⚪ 无标签。
按 `A`/`D` 翻页时，当前图片会自动滚动到列表居中位置（首尾不强制居中）。

## 功能说明

### 自动保存
勾选后，任何修改都会在停止操作约 **250ms** 后自动写回标签文件。
切换图片前会**先等待刷盘完成再切图**，因此不会出现"改完马上按 D 却提示未保存"的情况；
关闭或刷新页面前通过 `sendBeacon` 同步刷盘，不会丢最后一笔修改。

### 目录选择
浏览器出于安全限制拿不到本地真实路径，因此后端提供了 `/api/browse` 目录浏览接口，
页面上弹出文件夹选择器：从盘符列表逐级进入、返回上级、或直接粘贴完整路径跳转。
类别文件选择器会列出目录下的 `.txt` 文件供点选。

### 安全与健壮性
- 所有读写接口都做 `basename` 归一化 + 文件名白名单校验，**拒绝目录穿越**（如 `../evil.png`）
- 服务绑定 `127.0.0.1`，仅本机可访问
- `allow_reuse_address = False`：端口被占用时不会被新旧进程重复绑定（避开 Windows 下 `SO_REUSEADDR`
  导致旧进程仍在服务旧代码的隐蔽问题），而是自动切换到下一个空闲端口
- 快速连按 `A`/`D` 有导航锁，避免异步加载交错造成图片与标注框错位
- 勾选框/下拉框/按钮操作后主动 `blur`，防止焦点残留吞掉快捷键

## HTTP API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/list` | 图片列表、类别名、已标注文件名 |
| GET | `/api/image?name=` | 图片二进制 |
| GET | `/api/label?name=` | 解析后的标注框 JSON |
| GET | `/api/browse?path=&kind=dir\|file` | 目录浏览（`kind=file` 列出 txt） |
| GET | `/api/config` | 当前生效的图片/标签/类别路径、盘符列表 |
| POST | `/api/apply` | 切换图片目录、标签目录、类别文件 |
| POST | `/api/save` | 写回标签文件 |
| POST | `/api/delete` | 删除图片及对应标签文件 |

## 常见问题

**Q: 端口 8765 已被占用会怎样？**
会**自动向后寻找空闲端口**（8766、8767…最多试 50 个），控制台会打印实际使用的端口，
浏览器也会自动打开正确地址，因此不会因为 8765 被占就打不开。
若 50 个端口全被占（极少见）才会报错退出，此时可停掉旧进程或手动 `--port 9000` 指定。

注意：只有在终端按 `Ctrl+C`、或关掉控制台窗口（exe 模式），服务才会真正退出；只关浏览器网页是不会停服务的。多次双击 exe 会各起一个实例、各占一个端口。

**Q: 改了代码但页面没变化？**
先确认旧进程已被杀掉（见上一条），再用 `Ctrl+F5` 强制刷新绕过浏览器缓存。

**Q: 左侧列表里图片和标签数量不一致？**
说明存在孤立的标签文件（图片已删、txt 还在）。这类文件对 YOLO 训练无影响
（训练以图片列表为准），但会让数据集不整洁，可手动清理。

**Q: 支持中文路径吗？**
支持。后端全程使用 UTF-8 编解码，接口传输做 URL 编码，已在含中文的目录结构下验证。

## 打包成 exe（Windows 桌面双击运行）

不想装 Python、希望像普通软件一样双击使用？可以用 PyInstaller 打包成**单文件 exe**：

```bash
pip install pyinstaller pillow     # pillow 仅用于生成图标, 可选
# 方式一: 双击运行仓库里的一键脚本
build_exe.bat
# 方式二: 手动执行
python -m PyInstaller --onefile --console --icon app_icon.ico --name YOLOAnnotator annotation_server.py
```

产物在 `dist\YOLOAnnotator.exe`（约 10 MB），复制到桌面双击即可：会自动启动本地服务并弹出浏览器。

- 打包后 exe 按**自身所在目录**解析默认数据集路径；若旁边没有数据集，不会崩溃，
  而是以空数据集启动，进页面后在顶部目录栏选择图片/标签文件夹即可（见「目录选择」）。
- `--console` 保留一个命令行窗口显示运行状态与端口信息，**关闭该窗口即停止服务**。
- 图标 `app_icon.ico` 可用任意 `.ico` 替换后重新打包。

## 环境要求

- Python 3.8+（仅使用标准库：`http.server`、`json`、`argparse`、`os`、`re`、`urllib`、`webbrowser`、`threading`、`mimetypes`、`string`）
- 现代浏览器（Chrome / Edge / Firefox，需支持 ES6、`sendBeacon`、CSS transform）
- 目录浏览器返回盘符列表的部分为 Windows 实现；类 Unix 系统下会回退为 `/`

## License

[MIT](LICENSE)
