import { Extension, Node } from "@tiptap/core";
import { apiUrl } from "../api/client";

const arrayFields = [
  "sourceIds",
  "sourceRefs",
  "factIds",
  "evidenceIds",
  "knowledgeUnitIds",
  "documentReadingIds",
  "knowledgeClaimIds",
  "wikiVersionIds",
  "wikiBlockIds",
  "relatedSectionIds",
  "claimEvidence",
];
const textFields = [
  "nodeId",
  "artifactId",
  "assetVersionId",
  "caption",
  "note",
  "tableStyle",
  "describesFigureId",
  "explanationNodeId",
  "diagramType",
  "figurePurpose",
];
export const ResearchAttributes = Extension.create({
  name: "researchAttributes",
  addGlobalAttributes() {
    return [
      {
        types: [
          "paragraph",
          "heading",
          "blockquote",
          "table",
          "tableRow",
          "tableCell",
          "tableHeader",
          "bulletList",
          "orderedList",
          "listItem",
          "citation",
        ],
        attributes: Object.fromEntries([
          ["claimMapping", { default: null, renderHTML: () => ({}) }],
          ...arrayFields.map((name) => [
            name,
            { default: [], renderHTML: () => ({}) },
          ]),
          ...textFields.map((name) => [
            name,
            { default: null, renderHTML: () => ({}) },
          ]),
        ]),
      },
    ];
  },
});

export function researchAtoms(pid?: string) {
  return [
    "citation",
    "footnote",
    "crossReference",
    "formula",
    "inlineFormula",
    "figure",
  ].map((kind) =>
    Node.create({
      name: kind,
      group: ["formula", "figure"].includes(kind) ? "block" : "inline",
      inline: !["formula", "figure"].includes(kind),
      atom: true,
      addAttributes() {
        return Object.fromEntries([
          ["claimMapping", { default: null, renderHTML: () => ({}) }],
          ...arrayFields.map((name) => [name, { default: [] }]),
          ...textFields.map((name) => [name, { default: null }]),
          ...[
            "citationId",
            "noteText",
            "targetId",
            "targetSectionId",
            "latex",
            "expression",
          ].map((name) => [name, { default: null }]),
        ]);
      },
      parseHTML() {
        return [{ tag: '[data-research-node="' + kind + '"]' }];
      },
      renderHTML({ node }) {
        const attrs = node.attrs;
        const htmlAttrs = {
          "data-research-node": kind,
          contenteditable: "false",
        };
        if (kind === "figure")
          return [
            "figure",
            htmlAttrs,
            ...(pid && attrs.artifactId
              ? [
                  [
                    "img",
                    {
                      src: apiUrl(
                        "/projects/" +
                          encodeURIComponent(pid) +
                          "/studio/assets/" +
                          encodeURIComponent(attrs.artifactId),
                      ),
                      alt: attrs.caption || "研究图片",
                      style: "max-width:100%;max-height:300px",
                    },
                  ],
                ]
              : []),
            ["figcaption", {}, attrs.caption || "研究图片"],
          ] as any;
        const label =
          kind === "citation"
            ? "[文献]"
            : kind === "footnote"
              ? "（脚注：" + (attrs.noteText || "") + "）"
              : kind === "crossReference"
                ? "[图表引用]"
                : attrs.latex || attrs.expression || "[公式]";
        return [kind === "formula" ? "div" : "span", htmlAttrs, label];
      },
      renderText({ node }) {
        return (
          node.attrs.latex ||
          node.attrs.expression ||
          node.attrs.noteText ||
          node.attrs.caption ||
          (kind === "citation" ? "[文献]" : "[图表引用]")
        );
      },
    }),
  );
}
