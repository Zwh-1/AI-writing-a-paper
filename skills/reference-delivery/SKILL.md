---
name: reference-delivery
description: 按项目选定标准核对参考文献字段、引用关系和交付范围。
metadata:
  operations: [reference, export, bibliography]
  tools: [list_literature_documents, open_literature_document, load_skill]
  standards: [GB/T 7714-2015, GB/T 7714-2025]
  contracts: [bibliography_record]
---
输入是原始书目字段、人工修订与任务冻结的规范配置。读取 references/delivery.md 查看缺字段处理。输出是字段记录，宿主 bibliography_format.validate 和 render 执行校验与格式化，不能用自由文本宣称整份标准逐条认证。

先检查本项目的标准版本和正式学校要求，保留冲突及其处理理由。期刊、图书、学位论文、会议文献、标准和网页分别核对必需字段。书目样式由程序格式化，不能让模型补造缺失年份、页码、URL或DOI。原始元数据、作者修订、采用时快照和输出文本分开保留。标点格式正确不证明文献真实，书目真实也不证明引用支持当前论断。未完成检查的范围保留待核对状态。
