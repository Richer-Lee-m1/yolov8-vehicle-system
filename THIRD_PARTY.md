# 第三方来源登记（THIRD_PARTY.md）

产品线借鉴的开源代码、模型与算法均登记于此。只用 MIT / Apache-2.0 /
BSD / CC0 等宽松许可；借鉴代码须在源码文件头注明来源与许可。

| 项目 | 来源 | 许可 | 用途 |
|---|---|---|---|
| ByteTrack 算法 | ifzhang/ByteTrack（GitHub） | MIT | `src/tracker.py` 的算法设计参考（卡尔曼滤波 + 两阶段关联）；本产品为自行重写的 numpy 实现，未直接复制其源码 |
| YOLOv8s VisDrone 权重 | mshamrai/yolov8s-visdrone（Hugging Face） | 模型权重（VisDrone2019 数据集微调） | `models/yolov8s-visdrone.onnx` 的来源权重；R1 由该权重导出 ONNX |
| ONNX Runtime | microsoft/onnxruntime | MIT | 推理运行时（依赖，不随源码分发代码） |
| OpenCV | opencv/opencv | Apache-2.0 | 视频读写与 NMS（依赖）。R16 起改用 opencv-python-headless（同为 Apache-2.0，功能相同、无 Qt 图形依赖），冻结包体积随之缩小；产品代码不调用任何 GUI 函数 |
| openpyxl | python-openxml/openpyxl | MIT | Excel 报告生成 `src/report_export.py`（依赖，P4 打包时随 exe 携带） |
| reportlab | reportlab/reportlab | BSD-3-Clause | PDF 报告生成 `src/report_export.py`（依赖，P4 打包时随 exe 携带）；PDF 中文使用 reportlab 内置 CID 字体 STSong-Light，无需捆绑字体文件 |
| mediamtx | bluenviron/mediamtx（GitHub） | MIT | 仅验收工具：R10 本地 RTSP 测试流服务器（推 test1 循环流供引擎读取验证），不随产品分发、不进 exe |
| PyInstaller | pyinstaller/pyinstaller | GPL-2.0（附 bootloader 例外条款：打包产物可商业分发） | 打包工具：将产品打成 one-folder 可执行包（P4）；仅构建时使用，不随源码分发代码 |
| pywebview | r0x0r/pywebview（GitHub），PyPI 6.2.1（2026-04-15，R18 以 pip 实查确认） | BSD-3-Clause（PyPI 与仓库 README 均注明 BSD） | 桌面壳 `src/desktop_shell.py`（依赖）：Windows 用系统 WebView2 渲染原生窗口；不随包带浏览器内核与 WebView2 Runtime |
| WebView2 Runtime | Microsoft（随 Windows 11 自带；Windows 10 由系统更新推送） | 微软免费分发，开发者可随自有软件分发 | 桌面壳的 Windows 渲染运行时；本产品不随包带 Fixed Version Runtime（>250MB），缺失时启动提示用户安装 Evergreen Runtime |
| bottle（pywebview 依赖） | bottlepy/bottle | MIT | pywebview 内置的 WSGI 服务组件（依赖）；本产品服务端仍为自研 http.server，bottle 仅由 pywebview 内部使用 |
| ffmpeg（含 libx264 等编解码库） | ffmpeg.org（Linux 构建取自系统包，Windows 构建用 gyan.dev 静态 essentials 版） | GPL-3.0（因 libx264 而为 GPL；以独立可执行文件形式随包携带，未链接进产品代码） | 标注视频转码为浏览器可播的 H.264（`src/engine.py` 的 `_transcode_h264`）；若需弱化许可要求，可替换为不含 x264 的 LGPL 构建并改用其他编码器 |
| Pillow | python-pillow/Pillow | HPND（MIT 风格宽松许可） | 仅构建时使用：生成产品图标 `assets/app.ico`（R11）；运行时不依赖 |
| 随包测试视频 test1 | Pexels #9322363《An aerial view of a busy intersection with cars and buses》 | Pexels License（可免费使用，含商用，无需署名） | `test-videos/test1-intersection.mp4`；仅作演示与验收对照，不得当作实测数据 |
| 随包测试视频 test2 | GitHub 开源项目样例视频（具体上游项目未在毕设记录中留存） | 来源未完全核实 | `test-videos/test2-highway.mp4`；仅作演示用途，如需商用分发请自行替换为自有素材 |
| 随包测试视频 test3 | Wikimedia Commons 公开航拍素材 | 公开媒体素材（具体 CC 条款见 Commons 原页面） | `test-videos/test3-motorcycle-swarm.mp4`；仅作极限压力演示，不作计数验收片 |

内部复用（非第三方）：`src/counting.py`、`src/movements.py` 由毕设
源码包 `~/workspace/graduation-design/yolov8-vehicle-system/src/` 原样
复用（用户本人毕设代码），产品线不另写一份；毕设包本身不得改动。
`src/planning.py`（P2 新增）同样复用自该毕设包（Webster 配时、渠化
情景测算、诊断与预测），产品端仅作 UI 包装，未改算法。
`src/movements.py` 的单进口模式（R6 同步毕设包 v13：
SingleApproachClassifier / aggregate_single / EDGE_MOVEMENT）、
`src/merge_surveys.py`（R6 新增，毕设包 v13 同名模块的产品移植，
合并语义一致、另加可 import 的 merge_fragments() 供服务端调用）
与 `data/geometry_template.json`（R6 新增，毕设包同名模板的原样
复制，内含“演示占位、须按实地核对”的标注）同样属内部复用。
