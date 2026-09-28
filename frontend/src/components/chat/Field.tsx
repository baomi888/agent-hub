// 参数面板的数字输入控件
function Field({
  label,
  hint,
  value,
  min,
  max,
  onCommit,
}: {
  label: string;
  hint: string;
  value: number;
  min: number;
  max: number;
  onCommit: (v: number) => void;
}) {
  return (
    <label className="flex flex-col">
      <span className="text-sm font-medium text-ink">{label}</span>
      <span className="text-xs text-muted">{hint}</span>
      <input
        type="number"
        value={value}
        min={min}
        max={max}
        onChange={(e) => {
          const v = Number(e.target.value);
          if (!Number.isNaN(v)) onCommit(v);
        }}
        className="underline-input mt-1 py-1.5 text-sm"
      />
    </label>
  );
}

export default Field;
