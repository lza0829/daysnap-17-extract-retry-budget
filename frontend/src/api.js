async function 请求(path, opts = {}) {
  const r = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  const text = await r.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!r.ok) {
    const msg = (data && data.detail) || text || r.statusText;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

export const api = {
  健康: () => 请求("/健康"),
  总览: () => 请求("/总览"),
  台参: () => 请求("/台参"),
  账户: () => 请求("/账户"),
  流水: (q = "") => 请求(`/流水${q}`),
  快照: (q = "") => 请求(`/快照${q}`),
  差额: (q = "") => 请求(`/差额${q}`),
  断档: () => 请求("/断档"),
  跳变: () => 请求("/跳变"),
  旁置: () => 请求("/旁置"),
  夹具: () => 请求("/夹具"),
  装载批次: () => 请求("/装载批次"),
  抽取批次: () => 请求("/抽取批次"),
  回放批次: () => 请求("/回放批次"),
  装载夹具: (名) => 请求("/装载夹具", { method: "POST", body: JSON.stringify({ 名 }) }),
  抽取: (营业日, 额外 = {}) =>
    请求("/抽取", { method: "POST", body: JSON.stringify({ 营业日, ...额外 }) }),
  回放: (营业日) => 请求("/回放", { method: "POST", body: JSON.stringify({ 营业日 }) }),
  重置: () => 请求("/重置业务库", { method: "POST", body: "{}" }),
  预算核证: (营业日) =>
    请求(`/预算核证?营业日=${encodeURIComponent(营业日 || "")}`),
  预算账本: (营业日) =>
    请求(`/预算账本${营业日 ? `?营业日=${encodeURIComponent(营业日)}` : ""}`),
  预算账录: (营业日) =>
    请求(`/预算账录?营业日=${encodeURIComponent(营业日)}`),
  设置预算上限: (营业日, 上限, 口径) =>
    请求("/预算上限", {
      method: "POST",
      body: JSON.stringify({ 营业日, 上限, ...(口径 ? { 口径 } : {}) }),
    }),
  故障注入: (营业日, 次数) =>
    请求("/故障注入", { method: "POST", body: JSON.stringify({ 营业日, 次数 }) }),
  诊断快照: (营业日) =>
    请求(`/诊断快照${营业日 ? `?营业日=${encodeURIComponent(营业日)}` : ""}`),
  诊断晋升: (载荷) =>
    请求("/诊断晋升", { method: "POST", body: JSON.stringify(载荷) }),
  设台参: (键, 值) =>
    请求("/台参", { method: "POST", body: JSON.stringify({ 键, 值 }) }),
};
