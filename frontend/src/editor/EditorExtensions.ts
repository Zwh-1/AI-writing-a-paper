import { Extension, Node, mergeAttributes } from "@tiptap/core";
import { Plugin, PluginKey } from "@tiptap/pm/state";
import { Decoration, DecorationSet } from "@tiptap/pm/view";
import katex from "katex";

export function anchorText(node: any): string {
  if (node.isText) return node.text || "";
  const a = node.attrs || {},
    kind = node.type.name;
  if (kind === "inlineFormula" || kind === "formula")
    return a.latex || a.expression || "";
  if (kind === "citation") return "[cite:" + a.citationId + "]";
  if (kind === "footnote") return "(脚注：" + a.noteText + ")";
  if (kind === "crossReference")
    return "[figref:" + (a.targetSectionId || "") + ":" + a.targetId + "]";
  if (kind === "hardBreak") return "\n";
  let text = "";
  node.forEach((child: any) => {
    text += anchorText(child);
  });
  return text;
}
export function anchorPrefix(node: any, position: number): string {
  let text = "";
  node.forEach((child: any, offset: number) => {
    if (position <= offset) return;
    if (child.isText)
      text += (child.text || "").slice(
        0,
        Math.min(child.nodeSize, position - offset),
      );
    else if (position >= offset + child.nodeSize) text += anchorText(child);
  });
  return text;
}
function anchorOffset(node: any, index: number): number {
  let remaining = index,
    result = 0,
    done = false;
  node.forEach((child: any, offset: number) => {
    if (done) return;
    const value = anchorText(child),
      length = Array.from(value).length;
    if (remaining <= length) {
      result =
        offset +
        (child.isText
          ? Array.from(value).slice(0, remaining).join("").length
          : remaining === length
            ? child.nodeSize
            : 0);
      done = true;
    } else {
      remaining -= length;
      result = offset + child.nodeSize;
    }
  });
  return result;
}

export const InlineMath = Node.create({
  name: "inlineFormula",
  group: "inline",
  inline: true,
  atom: true,
  addAttributes() {
    return {
      latex: { default: "" },
      expression: { default: "" },
      omml: { default: "" },
      nodeId: { default: null },
    };
  },
  parseHTML() {
    return [{ tag: "span[data-inline-formula]" }];
  },
  renderHTML({ HTMLAttributes }) {
    return [
      "span",
      mergeAttributes(HTMLAttributes, {
        "data-inline-formula": "true",
        class: "studio-inline-math",
      }),
      HTMLAttributes.latex || HTMLAttributes.expression || "",
    ];
  },
  addNodeView() {
    return ({ node }) => {
      const dom = document.createElement("span");
      dom.className = "studio-inline-math";
      dom.setAttribute("data-inline-formula", "true");
      dom.contentEditable = "false";
      const render = (node: any) =>
        katex.render(node.attrs.latex || node.attrs.expression || "", dom, {
          throwOnError: false,
          trust: false,
          displayMode: false,
        });
      render(node);
      return {
        dom,
        update(next: any) {
          if (next.type.name !== "inlineFormula") return false;
          render(next);
          return true;
        },
        ignoreMutation: () => true,
      };
    };
  },
});
export const commentKey = new PluginKey("studioCommentRanges");
export const CommentRanges = Extension.create({
  name: "studioCommentRanges",
  addProseMirrorPlugins() {
    return [
      new Plugin({
        key: commentKey,
        state: {
          init: () => [],
          apply: (transaction, previous) =>
            transaction.getMeta(commentKey) ?? previous,
        },
        props: {
          decorations(state) {
            const rows = commentKey.getState(state) || [],
              marks: Decoration[] = [];
            state.doc.descendants((node, position) => {
              if (!node.isTextblock) return;
              const value = anchorText(node),
                chars = Array.from(value);
              for (const row of rows) {
                if (
                  row.data.parent_id ||
                  row.data.resolved ||
                  node.attrs.nodeId !== row.data.node_id
                )
                  continue;
                const quote = row.data.quote || "",
                  logical =
                    typeof row.data.start === "number"
                      ? row.data.start
                      : Array.from(value.slice(0, value.indexOf(quote))).length;
                if (
                  !quote ||
                  !value.includes(quote) ||
                  chars
                    .slice(logical, logical + Array.from(quote).length)
                    .join("") !== quote ||
                  (row.data.start == null &&
                    value.indexOf(quote) !== value.lastIndexOf(quote))
                )
                  continue;
                const start = anchorOffset(node, logical),
                  end = anchorOffset(node, logical + Array.from(quote).length);
                if (end > start)
                  marks.push(
                    Decoration.inline(
                      position + 1 + start,
                      position + 1 + end,
                      {
                        class: "studio-comment-range",
                        "data-studio-comment": row.id,
                      },
                    ),
                  );
              }
            });
            return DecorationSet.create(state.doc, marks);
          },
          handleClick(_view, _position, event) {
            const identity = (event.target as Element)
              ?.closest("[data-studio-comment]")
              ?.getAttribute("data-studio-comment");
            if (!identity) return false;
            window.dispatchEvent(
              new CustomEvent("studio-comment-focus", {
                detail: { id: identity },
              }),
            );
            return false;
          },
        },
      }),
    ];
  },
});
