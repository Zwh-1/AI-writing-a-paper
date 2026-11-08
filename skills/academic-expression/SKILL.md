---
name: academic-expression
description: 在保留原义、论证条件和引用的范围内检查学术表达，适用于正文起草、定向修订和润写。
metadata:
  operations: [draft, revise, polish, discourse, review_candidate_discourse]
  tools: [read_section, read_section_range, get_evidence, load_skill]
  standards: [GB/T 7713.1-2025]
  contracts: [section_candidate]
  host_resources:
    references/expression.txt: academic-expression
    references/review.txt: academic-expression-review
  resource_operations:
    references/expression.txt: [draft, revise, polish]
    references/review.txt: [discourse, review_candidate_discourse]
---
起草或修订时读取 references/expression.txt；表达审阅时读取 references/review.txt。它们由宿主映射到项目现有 prompts 并冻结，不维护另一份副本。输入使用当前任务的原文、规则、作者决定及证据范围，输出使用 SectionCandidate；宿主 schemas.SectionCandidate 验证结构，来源与论证核验仍由写作检查流程执行。项目规范冲突保留人工决定，不能由技能自动解决。

围绕本节的具体问题组织段落，使用可回查的对象和关系。保持事实、作者解释和拟议观点的归因。每项改写应改善准确性或可读性，保留必要限定、术语和引用，不能通过换词改变数值或结论强度。局部改写不得扩大到未授权段落。跨章目录和摘要仅作导航，未读取范围不能宣称已通读。证据不足写入当前契约的缺口字段。无需为每段机械补上总结或局限，不承诺任何 AIGC 检测比例。
