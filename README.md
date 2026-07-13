# light-novel-converter

将简体中文、横版排版的 EPUB 小说转换为台湾繁体中文、竖版排版的
EPUB。文本转换固定使用 OpenCC 的 `s2twp` 配置，因此除了简繁字形，还会
应用“程序 → 程式”“网络 → 網路”“软件 → 軟體”等台湾惯用词。

## 环境要求

- Python 3.10 或更高版本
- 运行依赖见 `requirements.txt`

依赖中的 `opencc-python-reimplemented` 是纯 Python 实现，不需要安装 C++
OpenCC。
### macOS 安装

在终端进入项目目录后执行：

```bash
cd /你的项目路径/light-novel-converter
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### Windows 安装

在 PowerShell 中进入项目目录后执行：

```powershell
cd "C:\你的项目路径\light-novel-converter"
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
```

如果 PowerShell 提示无法执行 `Activate.ps1`，只需在当前窗口临时允许脚本，再重新激活：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

也可以使用 Windows CMD 激活环境：

```bat
.venv\Scripts\activate.bat
```

## 命令行用法

日常使用只需输入一条命令。

macOS：

```bash
python convert.py
```

Windows：

```powershell
py convert.py
```

程序会先自动打开系统文件选择框：

- macOS 使用 Finder，按住 Command 键可多选；
- Windows 使用资源管理器文件对话框，按住 Ctrl 键可多选。

返回终端后，再在菜单中选择
转换格式、翻页方向、是否覆盖同名输出和失败策略。每个文件都会先进行 EPUB 格式检查，转换
结束后终端会逐本列出「成功」或「失败」与原因。

输出会放在每本原书同一目录下，文件名为
`<原文件名>_conceverted.epub`。

如需安装成环境内可直接使用的命令，可在项目目录运行：

```bash
python -m pip install .
light-novel-converter
```

Windows 可使用：

```powershell
py -m pip install .
light-novel-converter
```

仍需保留脚本化自动化时，可直接指定输入、输出和参数：

```bash
# 单本转换
python convert.py input.epub output.epub

# 向左翻页（下一页在左侧）
python convert.py input.epub output.epub --page-direction left

# 覆盖已存在的输出文件
python convert.py input.epub output.epub --force

# 任一章节或目录解析失败时整体失败，不留下半成品
python convert.py input.epub output.epub --strict
```

Windows 的路径含空格时，请用英文双引号包住；参数用法与 macOS 相同：

```powershell
py convert.py "C:\Books\input.epub" "C:\Books\output.epub" --page-direction left
```

`--page-direction` 控制全书的阅读推进方向：

- `left`：下一页在左侧，写入
  `spine@page-progression-direction="rtl"`；
- `right`：下一页在右侧，写入
  `spine@page-progression-direction="ltr"`；
- `keep`：保留原书设置，这是默认值。

这个属性是 EPUB 3 的标准写法。如果输入是 EPUB 2，只有在显式指定
`left` 或 `right` 时，转换器才会将它作为 Apple Books 等阅读器可能
识别的兼容扩展写入，并打印 `WARNING`。这类 EPUB 2 输出可能无法通过
严格的 OPF 2 校验；不指定时不会改动原书方向。

默认模式下，如果某个普通 XHTML/HTML/NCX 文件无法解析，脚本会打印
`WARNING`，在输出 EPUB 中原样保留该文件，并继续处理其他章节。作为 EPUB
入口的 `container.xml` 和 OPF 属于核心结构，解析失败时会停止转换。

