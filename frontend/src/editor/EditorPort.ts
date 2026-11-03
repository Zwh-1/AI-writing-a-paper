import type { Editor } from "@tiptap/react";
import { NodeSelection } from "@tiptap/pm/state";
import type { Row } from "../workspace/types";
import { anchorPrefix } from "./EditorExtensions";
export type EditorPort = {
  editor: Editor | null;
  revision: () => string;
  save: () => Promise<boolean>;
  insertAsset: (asset: Row, replace?: boolean) => void;
  adopt: (revision: Row) => void;
  completionContext: () => Row | null;
  insertCompletion: (value: Row) => boolean;
  subscribeUpdates: (listener: () => void) => () => void;
};
export function tiptapPort(
  editor: Editor | null,
  revision: () => string,
  save: () => Promise<boolean>,
  adopt: (value: Row) => void,
  onAsset: (value: Row) => void,
): EditorPort {
  return {
    editor,
    revision,
    save,
    adopt,
    completionContext: () => {
      if (!editor) return null;
      const { from, to, $from } = editor.state.selection;
      let nodeId = $from.parent.attrs.nodeId;
      for (let depth = $from.depth; depth > 0 && !nodeId; depth--)
        nodeId = $from.node(depth).attrs.nodeId;
      if (!nodeId) return null;
      return {
        snapshot: JSON.stringify(editor.getJSON()),
        position: { from, to },
        node_id: nodeId,
        prefix: editor.state.doc.textBetween(0, from, "\n").slice(-12000),
        suffix: editor.state.doc
          .textBetween(to, editor.state.doc.content.size, "\n")
          .slice(0, 6000),
      };
    },
    insertCompletion: (value) => {
      if (
        !editor ||
        value.can_insert === false ||
        revision() !== value.revision_id ||
        JSON.stringify(editor.getJSON()) !== value.snapshot
      )
        return false;
      const parent = editor.state.doc.resolve(value.position.from).parent;
      const parentOffset = editor.state.doc.resolve(value.position.from).parentOffset;
      const prefixPoints = Array.from(parent.textBetween(0, parentOffset, "", "\ufffc")).length;
      const mappings = (value.claim_evidence ?? []).map((item: Row) => ({
        ...item,
        start_cp: item.start_cp + prefixPoints,
        end_cp: item.end_cp + prefixPoints,
        rendered_start_cp: (item.rendered_start_cp ?? item.start_cp) + prefixPoints,
        rendered_end_cp: (item.rendered_end_cp ?? item.end_cp) + prefixPoints,
      }));
      const inline = (value.inline_content ?? [{ type: "text", text: value.text }]).map((node: Row) => {
        if (!node.attrs?.claimMapping) return node;
        return { ...node, attrs: { ...node.attrs, claimMapping: {
          ...node.attrs.claimMapping,
          start_cp: node.attrs.claimMapping.start_cp + prefixPoints,
          end_cp: node.attrs.claimMapping.end_cp + prefixPoints,
        } } };
      });
      editor
        .chain()
        .focus()
        .setTextSelection(value.position)
        .insertContent(inline)
        .updateAttributes(parent.type.name, {
          sourceRefs: [...(parent.attrs.sourceRefs ?? []), ...(value.source_refs ?? [])]
            .filter((ref: Row, index: number, all: Row[]) => all.findIndex((r) => r.type === ref.type && r.id === ref.id && r.version === ref.version) === index),
          claimEvidence: [...(parent.attrs.claimEvidence ?? []), ...mappings],
        })
        .run();
      return true;
    },
    subscribeUpdates: (listener) => {
      editor?.on("update", listener);
      return () => {
        editor?.off("update", listener);
      };
    },
    insertAsset: (asset, replace = false) => {
      if (!editor) return;
      onAsset(asset);
      if(asset.node?.type === 'table'){
        editor.chain().focus().insertContent({...asset.node,attrs:{...asset.node.attrs,artifactId:asset.artifact_id||asset.id,assetVersionId:asset.asset_version_id||''}}).run();
        return;
      }
      const attrs = {
        artifactId: asset.artifact_id || asset.id,
        assetVersionId: asset.asset_version_id || "",
        caption: asset.name || "研究图形",
      };
      if (
        replace &&
        editor.state.selection instanceof NodeSelection &&
        editor.state.selection.node.type.name === "figure"
      )
        editor.chain().focus().updateAttributes("figure", attrs).run();
      else
        editor
          .chain()
          .focus()
          .insertContent({
            type: "figure",
            attrs: { ...attrs, nodeId: crypto.randomUUID() },
          })
          .run();
    },
  };
}
