import type { ReactNode } from "react";

export type Row = Record<string, any>;
export type WorkspaceAction = (
  work: () => Promise<any>,
  message?: string,
) => Promise<any>;
export type SourceRenderer = (file: Row, onClose: () => void) => ReactNode;
export const materialRoles: Record<string, string> = {
  rules: "论文规则",
  example: "写作示例",
  research: "研究文献",
  data: "研究数据",
  draft: "已有草稿",
  supervisor: "导师意见",
};
export function sourceLocation(source: Row) {
  const extension =
    source.extension ?? source.name?.match(/\.[^.]+$/)?.[0]?.toLowerCase();
  return extension === ".docx"
    ? "段落与表格"
    : [".txt", ".md", ".csv", ".xlsx"].includes(extension)
      ? `文本片段 / 工作表 ${source.page_number}`
      : `第 ${source.page_number} 页`;
}
