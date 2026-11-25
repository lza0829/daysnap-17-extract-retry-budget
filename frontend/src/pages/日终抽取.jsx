import React, { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";

function 徽章({ ok, 真文, 假文 }) {
  return (
    <span className={`徽章 ${ok ? "通" : "败"}`}>
      {ok ? 真文 : 假文}
    </span>
  );
}

export default function 日终抽取() {
  const [日, set日] = useState("2026-11-23");
  const [批, set批] = useState([]);
  const [讯, set讯] = useState("");
  const [cert, setCert] = useState(null);
  const [新上限, set新上限] = useState("3");
  const [忙, set忙] = useState(false);

  const 刷新批 = useCallback(
    () => api.抽取批次().then(set批).catch((e) => set讯(String(e.message || e))),
    []
  );
  const 刷新核证 = useCallback(() => {
    if (!日) return;
    return api
      .预算核证(日)
      .then(setCert)
      .catch((e) => set讯(String(e.message || e)));
  }, [日]);

  useEffect(() => {
    刷新批();
  }, [刷新批]);

  useEffect(() => {
    刷新核证();
  }, [刷新核证]);

  const 抽一次 = (额外) => {
    set忙(true);
    set讯("");
    api
      .抽取(日, 额外)
      .then((r) => {
        if (r.诊断) {
          set讯(
            `预算耗尽：已写诊断批 ${r.诊断批号}（诊断行 ${r.诊断行数 ?? 0}，非有效、不可回放当正式）；正式有效镜仍禁写。`
          );
        } else if (r.拒绝) {
          set讯(`已拒绝：${r.错误码 || "预算耗尽"}，未写任何新快照行。`);
        } else {
          set讯(
            `批次 ${r.批次编号} 写入=${r.写入 ?? 0} 盖写=${r.盖写 ?? 0} 旁置=${r.旁置 ?? 0} 断档=${r.断档 ?? 0}`
          );
        }
      })
      .catch((e) => set讯(String(e.message || e)))
      .finally(() => {
        set忙(false);
        刷新批();
        刷新核证();
      });
  };

  const 设上限 = () => {
    const n = parseInt(新上限, 10);
    if (!n || n < 1) {
      set讯("上限需为 ≥1 的整数");
      return;
    }
    api
      .设置预算上限(日, n)
      .then(() => set讯(`已将 ${日} 的重试预算上限设为 ${n}`))
      .catch((e) => set讯(String(e.message || e)))
      .finally(刷新核证);
  };

  const 注入两次故障 = () => {
    api
      .故障注入(日, 2)
      .then(() => set讯(`已在库内为 ${日} 武装接下来 2 次“首行后失败”，可连续执行抽取观察扣预算与回滚。`))
      .catch((e) => set讯(String(e.message || e)));
  };

  const used = cert?.已用 ?? 0;
  const cap = cert?.上限 ?? 2;
  const pct = cap > 0 ? Math.min(100, Math.round((used / cap) * 100)) : 0;

  return (
    <div>
      <h1 className="题">日终抽取</h1>
      <p className="述">按营业日写周期快照：前日余额 + 当日流水净额。失败整票回滚，重试消耗同日共享预算账本。</p>

      <div className="行">
        <input value={日} onChange={(e) => set日(e.target.value)} placeholder="YYYY-MM-DD" />
        <button disabled={忙} onClick={() => 抽一次()}>执行抽取</button>
        <button className="次" disabled={忙} onClick={注入两次故障}>注入接下来 2 次失败</button>
        <button className="次" onClick={() => { 刷新批(); 刷新核证(); }}>刷新</button>
      </div>
      {讯 ? <p className="讯">{讯}</p> : null}

      <div className="段" style={{ marginTop: "0.4rem" }}>
        <h2 className="段题">重试预算核证 · {日}</h2>
        <div
          style={{
            background: "var(--块)",
            border: "1px solid var(--线)",
            borderRadius: 12,
            padding: "0.9rem 1rem",
          }}
        >
          <div className="行" style={{ marginBottom: "0.6rem", gap: "0.8rem" }}>
            <span>
              口径：<b>{cert?.口径 || "诊断批"}</b>
              {cert?.架构 ? `（${cert.架构 === "乙" ? "乙 · 诊断批为准" : "甲 · 耗尽拒绝为准"}）` : ""}
            </span>
            <徽章 ok={cert?.valid} 真文="核证 valid" 假文="核证 invalid" />
            <徽章 ok={!cert?.耗尽} 真文="预算可用" 假文="已耗尽" />
            <徽章 ok={!cert?.诊断有无} 真文="无诊断镜" 假文="有诊断镜" />
            <徽章 ok={!!cert?.正式有效镜存在} 真文={`正式有效镜 ${cert?.有效镜数 ?? 0}`} 假文="正式有效镜空" />
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: "0.7rem", margin: "0.35rem 0 0.6rem" }}>
            <span style={{ minWidth: 132 }}>
              已用 <b>{used}</b> / 上限 <b>{cap}</b>
              {cert ? `（剩余 ${cert.剩余}）` : ""}
            </span>
            <div style={{ flex: 1, height: 10, borderRadius: 999, background: "var(--块深)", overflow: "hidden" }}>
              <div
                style={{
                  width: `${pct}%`,
                  height: "100%",
                  background: pct >= 100 ? "var(--败)" : "var(--焰)",
                  transition: "width .2s",
                }}
              />
            </div>
            <span style={{ width: 44, textAlign: "right", color: "var(--淡)" }}>{pct}%</span>
          </div>

          <div className="行" style={{ marginBottom: 0 }}>
            <input
              value={新上限}
              onChange={(e) => set新上限(e.target.value)}
              style={{ width: 90 }}
              inputMode="numeric"
            />
            <button className="次" onClick={设上限}>设置/提高上限</button>
            <button className="次" onClick={刷新核证}>刷新核证</button>
            {cert ? (
              <span style={{ color: "var(--淡)", fontSize: "0.85rem" }}>
                账实相符 {String(cert.账实相符)} · 无超扣 {String(cert.无超扣)} · 无漏扣 {String(cert.无漏扣)} ·
                无半截有效镜 {String(cert.无半截有效镜)}
              </span>
            ) : null}
          </div>
        </div>
      </div>

      <div className="段">
        <h2 className="段题">抽取批次</h2>
        {!批.length ? (
          <div className="空态">尚无抽取</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>编号</th>
                <th>营业日</th>
                <th>时刻</th>
                <th>报告</th>
              </tr>
            </thead>
            <tbody>
              {批.map((r) => (
                <tr key={r.编号}>
                  <td>{r.编号}</td>
                  <td>{r.营业日}</td>
                  <td>{r.时刻}</td>
                  <td>{typeof r.报告 === "object" ? JSON.stringify(r.报告) : r.报告}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
