import { CheckCircle2 } from "lucide-react";
import { useAIAutopilot, type AIDomain } from "./useAIAutopilot";

export default function AutopilotCoverage() {
  const { data } = useAIAutopilot();
  if (!data) return null;
  return (
    <div className="autopilot-metrics">
      {(
        [
          ["materials", "资料"],
          ["rules", "规则"],
        ] as [AIDomain, string][]
      ).map(([key, label]) => {
        const rate = data[key];
        return (
          <section key={key} aria-label={`${label}自动处理覆盖`}>
            <div className="autopilot-metric-heading">
              <h3>{label}</h3>
              <span>
                <CheckCircle2 size={13} />
                {rate.automatic_completed}/{rate.total} 项已完成
              </span>
            </div>
            <div className="autopilot-rate-pair">
              <div>
                <strong>
                  {rate.ai_percent.toFixed(2)}
                  <small>%</small>
                </strong>
                <span>AI 参与率</span>
              </div>
              <div>
                <strong>
                  {rate.automatic_percent.toFixed(2)}
                  <small>%</small>
                </strong>
                <span>自动完成率</span>
              </div>
            </div>
            <progress
              aria-label={`${label}自动完成率`}
              max={100}
              value={rate.automatic_percent}
            />
            <p>
              AI 参与 {rate.ai_covered}/{rate.total} · 自动完成{" "}
              {rate.automatic_completed}/{rate.total}
            </p>
            {key === "rules" && data.extraction && (
              <small>
                原文提取覆盖 {data.extraction.automatic_completed}/
                {data.extraction.total}
              </small>
            )}
          </section>
        );
      })}
    </div>
  );
}
