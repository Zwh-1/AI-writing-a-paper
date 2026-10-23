import { Extension } from "@tiptap/core";
import { Plugin } from "@tiptap/pm/state";
import { Decoration, DecorationSet } from "@tiptap/pm/view";
export const LockedNodes = Extension.create<{ getLocks: () => string[] }>({
  name: "lockedNodes",
  addOptions() {
    return { getLocks: () => [] };
  },
  addProseMirrorPlugins() {
    const getLocks = this.options.getLocks;
    return [
      new Plugin({
        filterTransaction(transaction, state) {
          if (!transaction.docChanged) return true;
          const protectedIds = new Set(getLocks());
          if (!protectedIds.size) return true;
          const before = new Map<string, string>(),
            after = new Map<string, string>();
          state.doc.descendants((node) => {
            if (protectedIds.has(node.attrs.nodeId))
              before.set(node.attrs.nodeId, JSON.stringify(node.toJSON()));
          });
          transaction.doc.descendants((node) => {
            if (protectedIds.has(node.attrs.nodeId))
              after.set(node.attrs.nodeId, JSON.stringify(node.toJSON()));
          });
          for (const [id, json] of before) {
            if (after.get(id) !== json) return false;
          }
          return true;
        },
        props: {
          decorations(state) {
            const ids = new Set(getLocks());
            const decorations: Decoration[] = [];
            state.doc.descendants((node, pos) => {
              if (ids.has(node.attrs.nodeId))
                decorations.push(
                  Decoration.node(pos, pos + node.nodeSize, {
                    class: "locked-paragraph",
                    contenteditable: "false",
                    "data-locked": "true",
                  }),
                );
            });
            return DecorationSet.create(state.doc, decorations);
          },
        },
      }),
    ];
  },
});
