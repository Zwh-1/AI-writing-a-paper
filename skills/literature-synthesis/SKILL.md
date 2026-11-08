---
name: literature-synthesis
description: 按研究问题综合文献、对比研究方法并定位原始依据，适用于调查、综述和补证。
metadata:
  operations: [investigate, research, literature]
  tools: [search_writing_materials, read_writing_materials, open_literature_document, read_document_range, load_skill]
  standards: [GB/T 7713.1-2025]
  contracts: [section_candidate]
---
输入是研究问题、来源目录及实际阅读范围；需要综合矩阵时读取 references/synthesis.md。最终写作输出遵循宿主 SectionCandidate，由 schemas.SectionCandidate 校验结构；引用支持率必须另用实际原文核对，不能从结构验证推断。

按当前研究问题选择文献，先核对对象、方法、条件、结果和局限，再用共同维度综合。检索片段、元数据和技能说明均不是原始证据。原文没有报告某项能力不等于作者证明该能力不存在。教学示例不能证明本项目结果。需要其他章节时按其冻结版本读取。缺少证据时报告明确缺口，保留已完成与拟议工作的区别；不编造作者、DOI、数值或因果关系。输出服从当前任务的既有 JSON 契约。
