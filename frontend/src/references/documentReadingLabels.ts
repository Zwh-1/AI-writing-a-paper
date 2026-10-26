import type { Row } from "../workspace/types";
export const kindLabels: Record<string, string> = {
  research_publication: "研究文献",
  course_guidance: "课程指导",
  grading_criteria: "评分说明",
  writing_sample: "写作范文",
  unclassified: "类型待核对",
};
export const usageLabels: Record<string, string> = {
  research: "研究引用",
  example: "写作示例",
  writing_guidance: "写作指导",
  grading: "评分条件",
};
export const semanticLabels: Record<string, string> = {
  unassessed: "语义上下文尚未判定",
  candidate_assessed: "已记录候选判断，待核对",
  human_reviewed_for_question: "已人工核对当前阅读问题",
};
export const extractionLabel = (value: string) =>
  (
    ({
      pptx_slide: "原生幻灯片文字",
      pdfium_text_order: "原页提取文字",
      ooxml_structure: "原生段落与表格",
      native_extraction: "原生文字",
      ocr: "识别转录候选",
      windows_ocr: "识别转录候选",
      manual: "人工转录候选",
    }) as Record<string, string>
  )[value] || "原文转录候选";
export const place = (locator: Row) =>
  locator.slide_number
    ? `幻灯片 ${locator.slide_number}`
    : locator.physical_page
      ? `原件第 ${locator.page} 页`
      : locator.paragraph
        ? `原生文档块 ${locator.paragraph}`
        : `资料块 ${locator.page || ""}`;
