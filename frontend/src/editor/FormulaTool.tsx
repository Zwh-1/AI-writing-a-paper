import MathInput from "./MathInput";
export default function FormulaTool({
  value,
  onChange,
  inline,
  onInlineChange,
  onInsert,
}: {
  value: string;
  onChange: (value: string) => void;
  inline: boolean;
  onInlineChange: (value: boolean) => void;
  onInsert: () => void;
}) {
  return (
    <div className="studio-card">
      <MathInput value={value} onChange={onChange} />
      <label>
        <input
          type="checkbox"
          checked={inline}
          onChange={(event) => onInlineChange(event.target.checked)}
        />
        在句子中插入行内公式
      </label>
      <button
        className="btn primary"
        disabled={!value.trim()}
        onClick={onInsert}
      >
        插入公式
      </button>
    </div>
  );
}
