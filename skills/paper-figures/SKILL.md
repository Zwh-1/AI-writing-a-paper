---
name: paper-figures
description: 从可核对的结构和真实数据制定论文图表，解释元素、关系、单位与条件。
metadata:
  operations: [visual, figure, chart, paper_visual]
  tools: [get_evidence, get_run_evidence, read_writing_materials, load_skill]
  standards: [GB/T 7713.1-2025, CY/T 170-2019, CY/T 171-2019]
  contracts: [visual_spec]
  host_resources:
    references/visuals.txt: academic-visuals
  resource_operations:
    references/visuals.txt: [visual, figure, chart]
---
拟定图表前读取 references/visuals.txt（复用项目现有图表提示资源）。输入是当前问题、已读取依据及目标交付尺寸；输出使用宿主 paper_visuals.validate_spec 注册的图表规格。宿主编译器执行几何检查并保存可编辑源与渲染记录，技能不能执行任意绘图脚本。学校或投稿目标参数以任务冻结配置为准，PLOS 参数不能升级为通用硬性要求。

先确定图表回答的问题，再选择图种。结构示意图的节点和边来自当前设计或源码；结果图数值来自实际数据，不能用设计图证明实测结果。保留数据来源、转换、缺失值处理、单位和样本量。误差线须说明真实计算方法。图题、表题和图文编号由确定性服务处理。目标排版尺寸、文字和线宽分别检查；文件 DPI 元数据不等于实际版面分辨率。真实截图和实验图不调用生图模型替代。输出服从现有图表规格，不执行任意代码。
