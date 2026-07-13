# light-novel-converter

将简体中文、横版排版的 EPUB 小说转换为台湾繁体中文、竖版排版的
EPUB。文本转换固定使用 OpenCC 的 `s2twp` 配置，因此除了简繁字形，还会
应用“程序 → 程式”“网络 → 網路”“软件 → 軟體”等台湾惯用词。

## 环境要求

- macOS（支持 Apple Silicon）或 Windows 10/11
- Python 3.10 或更高版本
- 运行依赖见 `requirements.txt`

依赖中的 `opencc-python-reimplemented` 是纯 Python 实现，不需要安装 C++
OpenCC。Windows 建议安装 [Python 官方发行版](https://www.python.org/downloads/windows/)，
并保留安装程序中的 `tcl/tk and IDLE` 组件；它负责弹出多选文件窗口。

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

也可以使用 Windows 命令提示字元（CMD）激活环境：

```bat
.venv\Scripts\activate.bat
```

## 命令行用法

日常使用只需输入一条命令。

macOS：

```bash
python convert.py
```

Windows（PowerShell 或 CMD）：

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

## 转换范围

- XHTML/HTML 正文、EPUB 3 `nav` 导航：转换可见的文本节点，包括行内标签
  前后的 `text`/`tail`；即使一个词被 `span`、`em` 或 `ruby/rt` 拆开，也会
  保留 OpenCC 语境，并转换 `alt`、`title`、`aria-label` 等人类可读属性。
- EPUB 2 NCX：转换书名、作者、目录标签，不改 `content@src`。
- OPF 元数据：转换书名、作者、出版社、简介、主题等自然语言字段。
  `dc:identifier`、资源 `href`、`id` 和 `idref` 不会被转换。
- XHTML/HTML：幂等注入带固定 ID 的竖排 CSS，使用标准、WebKit 和 EPUB
  前缀，并设置 `text-orientation: mixed`。全角标点由阅读器结合字体的竖排
  字形自动呈现，不替换成兼容区竖排字符，以免损害复制和搜索。
- 纯图片 XHTML/HTML 页（封面、单张插画、多切片拼图或 SVG）不注入
  竖排 CSS，以保留原书的图片次序、尺寸和拼接几何。重新转换旧版已生成
  的 EPUB 时，也会从纯图片页移除转换器曾经错误注入的样式；作者原有
  CSS 不受影响。
- 插画独占一页：插画前的正文会先分页，插画后的正文或下一张
  插画也会分页，避免图文同页。封面或 XHTML 开头的第一张插画不额外
  插入空白页。这只注入分页 CSS，不改写图片尺寸、资源引用或作者 CSS。
- EPUB 3 OPF：根据 `--page-direction` 设定或保留翻页方向，并更新
  `dcterms:modified`。

## 文件完整性策略

转换器不会使用 `extractall()`。它逐条读取并重写 ZIP，只修改文本类条目；
图片、字体、音频、CSS 等未处理资源的字节、路径和目录层级保持不变。输出先
写入同目录临时文件，通过 ZIP 完整性检查后才用原子替换生成目标文件。

重打包时会确保：

- `mimetype` 是 ZIP 第一个条目；
- `mimetype` 内容严格为 `application/epub+zip`；
- `mimetype` 使用 `ZIP_STORED` 且没有 ZIP extra 字段；
- 其他 ZIP 条目保留原名称、顺序、时间戳、权限、注释与压缩方式。

字体混淆资源只有在 `encryption.xml` 的目标确实是 manifest 声明的字体时
才会原样保留。如果检测到正文 DRM、未知加密算法或数字签名，转换器会明确
报错，避免输出未转换的加密正文或携带已经失效的签名。

## 测试

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

测试会动态构造 EPUB 2/3 样本，并验证 OpenCC 语境转换、嵌套文本、OPF、
NCX、导航、翻页方向、竖排 CSS、纯图片页拼接保护、损坏章节跳过、
插画分页、二进制资源哈希和 ZIP 封装。

如需做发布前验证，建议额外使用官方 EPUBCheck，并在 Apple Books 中人工
检查目标字体对全角引号、括号等竖排字形的支持情况。
