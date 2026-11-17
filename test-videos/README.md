# 随包测试视频（test-videos/）

三段视频随 VehicleCount 便携包一起携带，开包即用，无需另外下载。
仅供熟悉功能与验收对照，**不得当作实测调查数据**写入任何报告。

| 文件 | 内容 | 来源 | 许可/备注 |
|---|---|---|---|
| test1-intersection.mp4 | 路口俯拍，1080p / 16 秒 | Pexels 免费素材 #9322363《An aerial view of a busy intersection with cars and buses》 | Pexels License，可免费使用（含商用），无需署名 |
| test2-highway.mp4 | 高速双向，720p / 20 秒 | GitHub 开源项目样例视频（具体上游项目未在毕设记录中留存） | 来源未完全核实，仅作演示用途；如需商用分发请自行替换为自有素材 |
| test3-motorcycle-swarm.mp4 | 摩托洪流，1080p / 8 秒 | Wikimedia Commons 公开航拍素材 | 公开媒体素材，仅作极限压力演示；具体 CC 条款见 Commons 原页面 |

基准参考值（本机 CPU、VisDrone 权重实跑）：test1 横线 19 / 竖线 39；
test2 共 30 辆；test3 不作计数验收片（摩托 0 检出为已知局限）。
口径：进口流量以定向过线计数为准，流向分类只给结构比例。

如不需要，可直接删除本目录内的视频文件，不影响产品运行；
删除后对应预设在界面上显示 MISSING，改用“上传本地视频”即可。
