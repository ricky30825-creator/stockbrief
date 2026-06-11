const state = {
  tab: "feed",
  channelFilter: null,
  videos: [],
  stocks: [],
  generatedAt: null,
};

const content = document.getElementById("content");

const STANCE_CLASS = { 매수: "buy", 매도: "sell", 보유: "hold", 관망: "hold" };
const STANCE_ICON = { 매수: "🟢", 매도: "🔴", 보유: "⚪", 관망: "⚪" };

async function loadData() {
  const bust = `?t=${Date.now()}`;
  try {
    const [videosRes, stocksRes] = await Promise.all([
      fetch(`data/videos.json${bust}`),
      fetch(`data/stocks.json${bust}`),
    ]);
    const videosDoc = await videosRes.json();
    const stocksDoc = await stocksRes.json();
    state.videos = videosDoc.videos || [];
    state.stocks = stocksDoc.stocks || [];
    state.generatedAt = videosDoc.generated_at;
  } catch (e) {
    content.innerHTML = `<p class="empty">데이터를 불러오지 못했습니다.<br>아직 첫 분석 전이거나 네트워크 문제입니다.</p>`;
    return false;
  }
  return true;
}

function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleString("ko-KR", {
    month: "numeric", day: "numeric", weekday: "short",
    hour: "numeric", minute: "2-digit",
  });
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function stanceBadge(op) {
  const cls = STANCE_CLASS[op.stance] || "hold";
  return `<span class="badge ${cls}">${STANCE_ICON[op.stance] || ""} ${esc(op.stance)} ${esc(op.stock)}</span>`;
}

function videoCard(v) {
  let badges;
  if (v.status === "no_transcript") {
    badges = `<span class="badge none">자막 없음 · 분석 불가</span>`;
  } else if (!v.opinions.length) {
    badges = `<span class="badge none">종목 의견 없음 (시황)</span>`;
  } else {
    badges = v.opinions.map(stanceBadge).join("");
  }
  const opinionsHtml = v.opinions.map((op) => `
    <div class="opinion">
      <div class="head">${esc(op.stock)}${op.ticker ? ` <small style="color:var(--muted)">${esc(op.ticker)}</small>` : ""}
        <span class="stance ${STANCE_CLASS[op.stance]}">${esc(op.stance)}</span></div>
      <div class="reasoning">${esc(op.reasoning)}</div>
    </div>`).join("");
  return `
    <article class="card" data-video="${esc(v.video_id)}">
      <div class="meta"><span class="channel">${esc(v.channel)}</span><span>${fmtDate(v.published)}</span></div>
      <h2>${esc(v.title)}</h2>
      <div class="badges">${badges}</div>
      <div class="detail">
        ${v.summary ? `<p class="summary">${esc(v.summary)}</p>` : ""}
        ${opinionsHtml}
        <a class="yt-link" href="${esc(v.url)}" target="_blank" rel="noopener">▶ 유튜브에서 보기</a>
      </div>
    </article>`;
}

function stockCard(s) {
  const opinionsHtml = s.opinions.map((op) => `
    <div class="opinion">
      <div class="head">${esc(op.channel)}
        <span class="stance ${STANCE_CLASS[op.stance]}">${esc(op.stance)}</span>
        <small style="color:var(--muted);font-weight:400">${fmtDate(op.published)}</small></div>
      <div class="reasoning">${esc(op.reasoning)}</div>
      <div class="sub"><a class="yt-link" href="${esc(op.url)}" target="_blank" rel="noopener">${esc(op.title)}</a></div>
    </div>`).join("");
  return `
    <article class="card" data-stock="${esc(s.stock)}">
      <div class="meta">
        <span class="channel">${esc(s.stock)}${s.ticker ? ` · ${esc(s.ticker)}` : ""}${s.market ? ` · ${esc(s.market)}` : ""}</span>
        <span class="stock-counts">
          ${s.buy ? `<span class="b">매수 ${s.buy}</span>` : ""}
          ${s.sell ? `<span class="s">매도 ${s.sell}</span>` : ""}
          ${s.hold ? `<span class="h">보유·관망 ${s.hold}</span>` : ""}
        </span>
      </div>
      <div class="badges">${s.opinions.slice(0, 3).map((op) =>
        `<span class="badge ${STANCE_CLASS[op.stance]}">${esc(op.channel)}: ${esc(op.stance)}</span>`).join("")}</div>
      <div class="detail">${opinionsHtml}</div>
    </article>`;
}

function render() {
  if (state.tab === "feed") {
    const list = state.videos;
    content.innerHTML = list.length
      ? list.map(videoCard).join("")
      : `<p class="empty">아직 분석된 영상이 없습니다.</p>`;
  } else if (state.tab === "stocks") {
    content.innerHTML = state.stocks.length
      ? state.stocks.map(stockCard).join("")
      : `<p class="empty">아직 종목 의견이 없습니다.</p>`;
  } else if (state.tab === "channels") {
    const channels = [...new Set(state.videos.map((v) => v.channel))];
    if (!state.channelFilter || !channels.includes(state.channelFilter)) {
      state.channelFilter = channels[0] || null;
    }
    const chips = channels.map((ch) =>
      `<button class="chip${ch === state.channelFilter ? " active" : ""}" data-channel="${esc(ch)}">${esc(ch)}</button>`).join("");
    const list = state.videos.filter((v) => v.channel === state.channelFilter);
    content.innerHTML = `<div class="chips">${chips}</div>` +
      (list.length ? list.map(videoCard).join("") : `<p class="empty">영상이 없습니다.</p>`);
  }
  document.getElementById("updated-at").textContent =
    state.generatedAt ? `업데이트 ${fmtDate(state.generatedAt)}` : "";
}

content.addEventListener("click", (e) => {
  const chip = e.target.closest(".chip");
  if (chip) {
    state.channelFilter = chip.dataset.channel;
    render();
    return;
  }
  if (e.target.closest("a")) return;
  const card = e.target.closest(".card");
  if (card) card.classList.toggle("open");
});

document.getElementById("tabs").addEventListener("click", (e) => {
  const btn = e.target.closest("button");
  if (!btn) return;
  state.tab = btn.dataset.tab;
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b === btn));
  window.scrollTo(0, 0);
  render();
});

(async function init() {
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("sw.js").catch(() => {});
  }
  if (await loadData()) render();
  // 앱이 다시 포그라운드로 오면 데이터 갱신
  document.addEventListener("visibilitychange", async () => {
    if (document.visibilityState === "visible" && (await loadData())) render();
  });
})();
