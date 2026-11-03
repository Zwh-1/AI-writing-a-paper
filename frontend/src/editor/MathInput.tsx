import { useEffect, useRef, useState } from "react";
export default function MathInput({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  const host = useRef<HTMLDivElement>(null),
    field = useRef<any>(null),
    change = useRef(onChange),
    [error, setError] = useState("");
  change.current = onChange;
  useEffect(() => {
    let active = true;
    void import("mathlive")
      .then(({ MathfieldElement }) => {
        if (!active) return;
        MathfieldElement.fontsDirectory = "/mathlive-fonts";
        const element = new MathfieldElement({
          mathVirtualKeyboardPolicy: "manual",
        });
        element.value = value;
        element.setAttribute("aria-label", "可视化公式编辑器");
        element.addEventListener("input", () => change.current(element.value));
        host.current?.appendChild(element);
        field.current = element;
      })
      .catch(() => setError("公式组件加载失败，可继续使用下方 LaTeX 输入。"));
    return () => {
      active = false;
      field.current?.remove();
      field.current = null;
    };
  }, []);
  useEffect(() => {
    if (field.current && field.current.value !== value)
      field.current.value = value;
  }, [value]);
  return (
    <div className="studio-math-input">
      <div ref={host} />
      {error && <p role="alert">{error}</p>}
      <button
        className="btn"
        onClick={() => {
          field.current?.focus();
          (window as any).mathVirtualKeyboard?.show();
        }}
      >
        打开公式符号面板
      </button>
      <label>
        LaTeX
        <textarea
          aria-label="公式 LaTeX"
          value={value}
          onChange={(event) => onChange(event.target.value)}
          rows={3}
        />
      </label>
    </div>
  );
}
