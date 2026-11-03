import { useEffect } from "react";
import { useEditor, EditorContent } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { Table } from "@tiptap/extension-table";
import TableRow from "@tiptap/extension-table-row";
import TableHeader from "@tiptap/extension-table-header";
import TableCell from "@tiptap/extension-table-cell";
import type { Row } from "../workspace/types";
import { ResearchAttributes, researchAtoms } from "./ResearchContentExtensions";

export function RichTextEditor({
  value,
  onChange,
  pid,
}: {
  value: Row;
  onChange: (value: Row) => void;
  pid?: string;
}) {
  const initial = value.document || {
    type: "doc",
    content: (value.text || "").split("\n").map((line: string) => ({
      type: "paragraph",
      content: line ? [{ type: "text", text: line }] : [],
    })),
  };
  const editor = useEditor({
    extensions: [
      StarterKit,
      Table.configure({ resizable: true }),
      TableRow,
      TableHeader,
      TableCell,
      ResearchAttributes,
      ...researchAtoms(pid),
    ],
    content: initial,
    onUpdate: ({ editor }) =>
      onChange({
        ...value,
        document: editor.getJSON(),
        text: editor.getText(),
      }),
  });
  useEffect(() => {
    if (
      editor &&
      value.document &&
      JSON.stringify(editor.getJSON()) !== JSON.stringify(value.document)
    )
      editor.commands.setContent(value.document, { emitUpdate: false });
  }, [editor, value.document]);
  return (
    <>
      <div className="studio-toolbar">
        <button
          className="btn"
          onClick={() => editor?.chain().focus().toggleBold().run()}
        >
          加粗
        </button>
        <button
          className="btn"
          onClick={() => editor?.chain().focus().toggleItalic().run()}
        >
          斜体
        </button>
        <button
          className="btn"
          onClick={() => editor?.chain().focus().toggleBulletList().run()}
        >
          列表
        </button>
        <button
          className="btn"
          onClick={() =>
            editor?.chain().focus().toggleHeading({ level: 2 }).run()
          }
        >
          小标题
        </button>
        <button
          className="btn"
          onClick={() =>
            editor
              ?.chain()
              .focus()
              .insertTable({ rows: 3, cols: 3, withHeaderRow: true })
              .run()
          }
        >
          插入表格
        </button>
        {editor?.isActive("table") && (
          <>
            <button
              className="btn"
              onClick={() => editor.chain().focus().addRowAfter().run()}
            >
              增加表格行
            </button>
            <button
              className="btn"
              onClick={() => editor.chain().focus().addColumnAfter().run()}
            >
              增加表格列
            </button>
          </>
        )}
      </div>
      <EditorContent editor={editor} className="studio-note-editor" />
    </>
  );
}
