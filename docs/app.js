const state = {
  tab: "feed",
  channelFilter: null,
  query: "",
  videos: [],
  stocks: [],
  usage: [],
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
  try {
    const usageRes = await fetch(`data/usage.json${bust}`);
    state.usage = usageRes.ok ? (await usageRes.json()).entries || [] : [];
  } catch (e) {
    state.usage = [];
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

function num(n) {
  return (n || 0).toLocaleString("ko-KR");
}

// --- 검색 필터 ---

function matchOpinion(op, q) {
  return op.stock.toLowerCase().includes(q) ||
    (op.ticker || "").toLowerCase().includes(q);
}

function filterVideos(list) {
  const q = state.query.trim().toLowerCase();
  if (!q) return list;
  return list.filter((v) =>
    v.title.toLowerCase().includes(q) || v.opinions.some((op) => matchOpinion(op, q)));
}

function filterStocks(list) {
  const q = state.query.trim().toLowerCase();
  if (!q) return list;
  return list.filter((s) =>
    s.stock.toLowerCase().includes(q) || (s.ticker || "").toLowerCase().includes(q));
}

// --- 카드 렌더링 ---

function usageLine(u) {
  if (!u) return "";
  const input = (u.input_tokens || 0) + (u.cache_creation_input_tokens || 0) + (u.cache_read_input_tokens || 0);
  return `<div class="sub">토큰: 입력 ${num(input)} · 출력 ${num(u.output_tokens)} · API 환산 $${(u.cost_usd || 0).toFixed(3)}</div>`;
}

function videoCard(v) {
  let badges;
  if (v.status === "no_transcript") {
    badges = `<span class="badge none">자막 없음 · 분석 불가</span>`;
  } else if (!v.opinions.length) {
    badges = `<span class="badge none">종목 의견 없음 (시황)</span>`;
  } else {
    badges = v.opinions.map((op) => {
      const cls = STANCE_CLASS[op.stance] || "hold";
      return `<span class="badge ${cls}">${STANCE_ICON[op.stance] || ""} ${esc(op.stance)} ${esc(op.stock)}</span>`;
    }).join("");
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
        ${usageLine(v.usage)}
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

// --- 사용량 탭 ---

function sumUsage(entries) {
  const total = { calls: 0, input: 0, cacheRead: 0, output: 0, cost: 0 };
  for (const e of entries) {
    total.calls += 1;
    total.input += (e.input_tokens || 0) + (e.cache_creation_input_tokens || 0);
    total.cacheRead += e.cache_read_input_tokens || 0;
    total.output += e.output_tokens || 0;
    total.cost += e.cost_usd || 0;
  }
  return total;
}

function usageStatCard(title, entries) {
  const t = sumUsage(entries);
  return `
    <article class="card usage-stat">
      <div class="meta"><span class="channel">${title}</span><span>분석 ${t.calls}회</span></div>
      <div class="big">$${t.cost.toFixed(3)} <small style="font-size:12px;color:var(--muted)">API 환산</small></div>
      <div class="row">입력 ${num(t.input)} 토큰 + 캐시 읽기 ${num(t.cacheRead)}<br>출력 ${num(t.output)} 토큰</div>
    </article>`;
}

function renderUsage() {
  const now = Date.now();
  const within = (hours) => state.usage.filter((e) => now - new Date(e.ts).getTime() <= hours * 3600 * 1000);
  const last5h = within(5);
  const last7d = within(24 * 7);

  const recent = [...last7d].reverse().slice(0, 20).map((e) => `
    <article class="card">
      <div class="meta"><span class="channel">${esc(e.channel)}</span><span>${fmtDate(e.ts)}</span></div>
      ${usageLine(e)}
    </article>`).join("");

  content.innerHTML =
    usageStatCard("최근 5시간", last5h) +
    usageStatCard("최근 7일", last7d) +
    `<p class="usage-note">이 앱의 영상 분석(claude 호출)에 쓰인 토큰만 집계합니다. 구독 요금제 내 사용이라
     실제 청구액이 아닌 API 환산 금액이며, 플랜 한도 대비 %는 공식적으로 조회할 수 없어 제공하지 않습니다.</p>` +
    (recent ? `<h2 style="font-size:14px;margin:4px 6px 10px;color:var(--muted)">최근 분석 내역</h2>${recent}`
            : `<p class="empty">아직 기록된 사용량이 없습니다.<br>다음 분석부터 집계됩니다.</p>`);
}

// --- 화면 렌더링 ---

function render() {
  if (state.tab === "feed") {
    const list = filterVideos(state.videos);
    content.innerHTML = list.length
      ? list.map(videoCard).join("")
      : `<p class="empty">${state.query ? "검색 결과가 없습니다." : "아직 분석된 영상이 없습니다."}</p>`;
  } else if (state.tab === "stocks") {
    const list = filterStocks(state.stocks);
    content.innerHTML = list.length
      ? list.map(stockCard).join("")
      : `<p class="empty">${state.query ? "검색 결과가 없습니다." : "아직 종목 의견이 없습니다."}</p>`;
  } else if (state.tab === "channels") {
    const channels = [...new Set(state.videos.map((v) => v.channel))];
    if (!state.channelFilter || !channels.includes(state.channelFilter)) {
      state.channelFilter = channels[0] || null;
    }
    const chips = channels.map((ch) =>
      `<button class="chip${ch === state.channelFilter ? " active" : ""}" data-channel="${esc(ch)}">${esc(ch)}</button>`).join("");
    const list = filterVideos(state.videos.filter((v) => v.channel === state.channelFilter));
    // 다시 그리면 칩 줄의 가로 스크롤이 0으로 돌아가므로 위치를 보존한다
    const prevScroll = content.querySelector(".chips")?.scrollLeft ?? 0;
    content.innerHTML = `<div class="chips">${chips}</div>` +
      (list.length ? list.map(videoCard).join("") : `<p class="empty">영상이 없습니다.</p>`);
    content.querySelector(".chips").scrollLeft = prevScroll;
  } else if (state.tab === "usage") {
    renderUsage();
  }
  document.getElementById("updated-at").textContent =
    state.generatedAt ? `업데이트 ${fmtDate(state.generatedAt)}` : "";
}

// --- 이벤트 ---

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
  document.getElementById("scroll-area").scrollTo(0, 0);
  render();
});

const searchbar = document.getElementById("searchbar");
const searchInput = document.getElementById("search-input");
const btnSearch = document.getElementById("btn-search");
const suggestionsEl = document.getElementById("suggestions");

// 연관검색어: 종목 데이터에서 이름·티커가 일치하는 종목을 추천한다
function renderSuggestions() {
  const q = searchInput.value.trim().toLowerCase();
  if (!q) {
    suggestionsEl.classList.add("hidden");
    return;
  }
  const matches = state.stocks
    .filter((s) => s.stock.toLowerCase().includes(q) || (s.ticker || "").toLowerCase().includes(q))
    .filter((s) => s.stock.toLowerCase() !== q)
    .slice(0, 8);
  if (!matches.length) {
    suggestionsEl.classList.add("hidden");
    return;
  }
  suggestionsEl.innerHTML = matches.map((s) => `
    <button class="suggestion" data-stock="${esc(s.stock)}">
      <span class="name">${esc(s.stock)}${s.ticker ? `<small>${esc(s.ticker)}</small>` : ""}</span>
      <span class="counts">
        ${s.buy ? `<span class="b">매수 ${s.buy}</span>` : ""}
        ${s.sell ? `<span class="s">매도 ${s.sell}</span>` : ""}
        ${s.hold ? `<span class="h">보유·관망 ${s.hold}</span>` : ""}
      </span>
    </button>`).join("");
  suggestionsEl.classList.remove("hidden");
}

suggestionsEl.addEventListener("click", (e) => {
  const btn = e.target.closest(".suggestion");
  if (!btn) return;
  searchInput.value = btn.dataset.stock;
  state.query = btn.dataset.stock;
  suggestionsEl.classList.add("hidden");
  searchInput.blur(); // 키보드 내리기
  render();
});

btnSearch.addEventListener("click", () => {
  const showing = searchbar.classList.toggle("hidden");
  btnSearch.classList.toggle("active", !showing);
  if (!showing) {
    searchInput.focus();
  } else {
    state.query = "";
    searchInput.value = "";
    suggestionsEl.classList.add("hidden");
    render();
  }
});

searchInput.addEventListener("input", () => {
  state.query = searchInput.value;
  renderSuggestions();
  render();
});

// iOS: 키보드 개폐·회전 후 스크롤이 어긋나면 원위치로 되돌린다
function resetViewport() {
  window.scrollTo(0, 0);
}
window.addEventListener("orientationchange", () => setTimeout(resetViewport, 200));
searchInput.addEventListener("blur", () => setTimeout(resetViewport, 50));
if (window.visualViewport) {
  window.visualViewport.addEventListener("resize", () => {
    if (document.activeElement?.tagName !== "INPUT") resetViewport();
  });
}

document.getElementById("search-clear").addEventListener("click", () => {
  state.query = "";
  searchInput.value = "";
  suggestionsEl.classList.add("hidden");
  searchInput.focus();
  render();
});

document.getElementById("btn-refresh").addEventListener("click", async () => {
  document.getElementById("updated-at").textContent = "갱신 중…";
  if (await loadData()) render();
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
