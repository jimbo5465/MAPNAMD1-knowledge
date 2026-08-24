/* ربات دانش سازمانی — منطق مینی‌اپ (بدون وابستگی خارجی) */

const API = "/api";
let TOKEN = sessionStorage.getItem("kb_token") || null;
let ME = null;

// ── ابزار ─────────────────────────────────────────────────────────────────────

const $ = (id) => document.getElementById(id);

function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
}

async function api(path, opts = {}) {
    const res = await fetch(API + path, {
        ...opts,
        headers: {
            "Content-Type": "application/json",
            ...(TOKEN ? { Authorization: "Bearer " + TOKEN } : {}),
        },
    });
    if (res.status === 401) { TOKEN = null; await auth(); return api(path, opts); }
    const data = await res.json().catch(() => ({ detail: "پاسخ نامعتبر" }));
    if (!res.ok) throw new Error(data.detail || ("خطا " + res.status));
    return data;
}

function faDate(s) {
    if (!s) return "";
    return s; // تاریخ‌ها از سرور میلادی می‌آیند؛ نمایش ساده
}

// ── ورود ──────────────────────────────────────────────────────────────────────

function getInitDataFromUrl() {
    const p = new URLSearchParams(location.search);
    let d = p.get("tgWebAppData");
    if (!d && location.hash) {
        const h = new URLSearchParams(location.hash.slice(1));
        d = h.get("tgWebAppData");
    }
    return d || "";
}

/**
 * initData: تلگرام آن را در URL می‌گذارد؛ بله فقط از طریق SDK
 * (window.Bale.WebApp.initData) تحویل می‌دهد — تا ۶ ثانیه منتظر می‌مانیم.
 */
function getInitData() {
    return new Promise((resolve) => {
        const immediate = getInitDataFromUrl();
        if (immediate) { resolve(immediate); return; }
        const t0 = Date.now();
        const timer = setInterval(() => {
            try {
                const d = window.Bale?.WebApp?.initData;
                if (d) { clearInterval(timer); resolve(d); return; }
            } catch (_) { /* SDK هنوز آماده نیست */ }
            if (Date.now() - t0 > 6000) { clearInterval(timer); resolve(""); }
        }, 150);
    });
}

function detectPlatform() {
    if (window.Bale?.WebApp) return "bale";
    if (window.Telegram?.WebApp) return "telegram";
    const ua = navigator.userAgent || "";
    return /Telegram/i.test(ua) ? "telegram" : "bale";
}

async function auth() {
    const initData = await getInitData();
    if (!initData) throw new Error("این صفحه باید از داخل پیام‌رسان باز شود.");
    const res = await fetch(API + "/auth", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ init_data: initData, platform: detectPlatform() }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "ورود ناموفق بود.");
    TOKEN = data.token;
    ME = data.user;
    sessionStorage.setItem("kb_token", TOKEN);
}

// ── ناوبری ────────────────────────────────────────────────────────────────────

let currentTab = "kn";
const views = ["kn-list", "kn-detail", "obs-list", "obs-detail", "me"];
const titles = { kn: "📚 دانش‌های من", obs: "📓 مشاهدات من", me: "👤 پروفایل من" };

function showView(name, titleOverride) {
    views.forEach((v) => $(v + "-view").classList.add("hidden"));
    $("back-btn").classList.toggle("hidden", !name.includes("detail"));
    $(name + "-view").classList.remove("hidden");
    if (titleOverride !== undefined) $("page-title").textContent = titleOverride;
    else $("page-title").textContent = titles[currentTab] || "";
}

$("back-btn").addEventListener("click", () => {
    showView(currentTab + "-list");
    if (currentTab === "kn") loadKn(state.knPage);
    else loadObs(state.obsPage);
});

document.querySelectorAll(".tabbar button").forEach((btn) => {
    btn.addEventListener("click", () => {
        document.querySelectorAll(".tabbar button").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        currentTab = btn.dataset.tab;
        state.search = {};
        if (currentTab === "kn") { showView("kn-list"); loadKn(0); }
        else if (currentTab === "obs") { showView("obs-list"); loadObs(0); }
        else { showView("me-view", titles.me); renderMe(); }
    });
});

const state = { knPage: 0, knPages: 1, obsPage: 0, obsPages: 1, search: {} };

// ── دانش‌ها ───────────────────────────────────────────────────────────────────

function knCard(e) {
    const statusChip = e.status === "submitted"
        ? '<span class="chip ok">✅ ثبت‌شده</span>'
        : '<span class="chip draft">✏️ پیش‌نویس</span>';
    return `<div class="card" onclick="openKn(${e.id})">
        <h3>${esc(e.title)}</h3>
        <div class="meta">
            <span class="chip">${esc(e.type)}</span>
            ${statusChip}
            ${e.kn_number ? `<span class="chip">${esc(e.kn_number)}</span>` : ""}
            <span class="chip">${esc(e.date)}</span>
        </div>
    </div>`;
}

async function loadKn(page) {
    try {
        state.knPage = page;
        let data;
        const q = (state.search.kn || "").trim();
        if (q) data = await api("/kn/search?q=" + encodeURIComponent(q));
        else data = await api(`/kn?page=${page}`);
        state.knPages = data.pages;
        const box = $("kn-list");
        box.innerHTML = data.items.length
            ? data.items.map(knCard).join("")
            : '<div class="empty">📭 موردی یافت نشد.<br>از ربات می‌توانید دانش ثبت کنید.</div>';
        $("kn-page-info").textContent = `صفحه ${page + 1} از ${data.pages} (${data.total})`;
        $("kn-prev").disabled = page <= 0;
        $("kn-next").disabled = page >= data.pages - 1;
    } catch (e) { alert(e.message); }
}

async function openKn(id) {
    try {
        const e = await api("/kn/" + id);
        const tags = (e.hashtags || []).map((t) => "#" + esc(t)).join(" ");
        $("kn-detail-view").innerHTML = `
            <div class="detail-card">
                <h3 style="margin-bottom:10px">${esc(e.title)}</h3>
                <div class="row"><b>نوع:</b> ${esc(e.type)}</div>
                <div class="row"><b>وضعیت:</b> ${e.status === "submitted" ? "✅ ثبت‌شده" : "✏️ پیش‌نویس"}</div>
                ${e.kn_number ? `<div class="row"><b>شماره:</b> ${esc(e.kn_number)}</div>` : ""}
                <div class="row"><b>تاریخ:</b> ${esc(e.date)}</div>
                ${e.tree_path?.length ? `<div class="row"><b>طبقه‌بندی:</b> ${esc(e.tree_path.join(" ← "))}</div>` : ""}
                ${tags ? `<div class="row"><b>هشتگ:</b> ${tags}</div>` : ""}
                <hr class="divider">
                <p class="desc-title">📝 متن:</p>${esc(e.description)}
            </div>`;
        showView("kn-detail", "📚 جزئیات دانش");
    } catch (err) { alert(err.message); }
}

$("kn-search-input").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") {
        state.search.kn = ev.target.value;
        loadKn(0);
    }
});
$("kn-prev").addEventListener("click", () => loadKn(state.knPage - 1));
$("kn-next").addEventListener("click", () => loadKn(state.knPage + 1));

// ── مشاهدات ───────────────────────────────────────────────────────────────────

function statusFa(s) {
    return { raw: "🟡 خام", maturing: "🟠 در حال بررسی", promoted: "🟢 ارتقایافته", archived: "⚪ بایگانی" }[s] || s;
}

function obsCard(o) {
    const atts = o.attachments || [];
    const attChip = atts.length ? `<span class="chip">📎 ${atts.length}</span>` : "";
    return `<div class="card" onclick="openObs(${o.id})">
        <h3>${esc(o.title)}</h3>
        <div class="meta">
            <span class="chip">${statusFa(o.status)}</span>
            ${attChip}
            <span class="chip">${esc(faDate(o.date))}</span>
        </div>
    </div>`;
}

async function loadObs(page) {
    try {
        state.obsPage = page;
        let data;
        const q = (state.search.obs || "").trim();
        if (q) data = await api("/obs/search?q=" + encodeURIComponent(q));
        else data = await api(`/obs?page=${page}`);
        state.obsPages = data.pages;
        const box = $("obs-list");
        box.innerHTML = data.items.length
            ? data.items.map(obsCard).join("")
            : '<div class="empty">📭 موردی یافت نشد.</div>';
        $("obs-page-info").textContent = `صفحه ${page + 1} از ${data.pages} (${data.total})`;
        $("obs-prev").disabled = page <= 0;
        $("obs-next").disabled = page >= data.pages - 1;
    } catch (e) { alert(e.message); }
}

async function openObs(id) {
    try {
        const o = await api("/obs/" + id);
        const tags = String(o.tags || "").trim();
        const attsHtml = (o.attachments || []).map((a) =>
            a.is_image
                ? `<span class="att-chip" onclick="showAttImg(event,${a.id},this)">🖼️ ${esc(a.name || "عکس")}</span>`
                : `<a class="att-chip" href="/api/file/obs-att/${a.id}" download onclick="downloadAtt(event,${a.id})">📄 ${esc(a.name || "فایل")}</a>`
        ).join("");
        $("obs-detail-view").innerHTML = `
            <div class="detail-card">
                <h3 style="margin-bottom:10px">${esc(o.title)}</h3>
                <div class="row"><b>وضعیت:</b> ${statusFa(o.status)}</div>
                <div class="row"><b>تاریخ:</b> ${esc(o.date)}</div>
                ${tags ? `<div class="row"><b>هشتگ:</b> ${esc(tags)}</div>` : ""}
                <hr class="divider">
                ${esc(o.content)}
                ${o.attachments?.length ? `<p class="desc-title" style="margin-top:12px">📎 پیوست‌ها:</p><div class="att-list">${attsHtml}</div><div id="img-slot"></div>` : ""}
            </div>`;
        showView("obs-detail", "📓 جزئیات مشاهده");
    } catch (err) { alert(err.message); }
}

async function showAttImg(ev, id, el) {
    ev.stopPropagation();
    try {
        const res = await fetch(`${API}/file/obs-att/${id}`, {
            headers: { Authorization: "Bearer " + TOKEN },
        });
        if (!res.ok) throw new Error("دریافت فایل ناموفق بود.");
        const blob = await res.blob();
        let img = document.getElementById("att-img-" + id);
        if (img) { img.remove(); return; }
        img = document.createElement("img");
        img.className = "att-img";
        img.id = "att-img-" + id;
        img.src = URL.createObjectURL(blob);
        document.getElementById("img-slot").appendChild(img);
        el.scrollIntoView({ behavior: "smooth", block: "center" });
    } catch (e) { alert(e.message); }
}

async function downloadAtt(ev, id) {
    ev.stopPropagation();
    try {
        const res = await fetch(`${API}/file/obs-att/${id}`, {
            headers: { Authorization: "Bearer " + TOKEN },
        });
        if (!res.ok) throw new Error("دریافت فایل ناموفق بود.");
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = ev.currentTarget.textContent.trim() || "attachment";
        a.click();
        URL.revokeObjectURL(url);
    } catch (e) { alert(e.message); }
}

$("obs-search-input").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") {
        state.search.obs = ev.target.value;
        loadObs(0);
    }
});
$("obs-prev").addEventListener("click", () => loadObs(state.obsPage - 1));
$("obs-next").addEventListener("click", () => loadObs(state.obsPage + 1));

// ── پروفایل ───────────────────────────────────────────────────────────────────

async function renderMe() {
    try {
        const m = await api("/me");
        const row = (k, v) =>
            `<div class="prow"><span>${k}</span><span>${esc(v || "—")}</span></div>`;
        $("me-view").innerHTML = `
            <div class="profile-rows">
                ${row("📛 نام", m.full_name)}
                ${row("📞 شماره", m.phone)}
                ${row("🆔 کد پرسنلی", m.personnel_code)}
                ${row("🏗️ پروژه", m.project_name)}
                ${row("💼 سمت", m.position)}
            </div>
            <p class="empty" style="padding-top:20px">ویرایش اطلاعات از طریق ربات انجام می‌شود.</p>`;
    } catch (e) { alert(e.message); }
}

// ── شروع ──────────────────────────────────────────────────────────────────────

(async function boot() {
    try {
        if (!TOKEN) await auth();
        else {
            // اعتبار توکن ذخیره‌شده را با یک فراخوانی سبک بررسی کن
            try { await api("/me"); }
            catch (e) { TOKEN = null; await auth(); }
        }
        $("loading-screen").classList.add("hidden");
        $("app").classList.remove("hidden");
        loadKn(0);
    } catch (e) {
        $("loading-screen").classList.add("hidden");
        $("error-screen").classList.remove("hidden");
        $("error-title").textContent = "عدم دسترسی";
        $("error-detail").textContent = e.message || "خطای ناشناخته.";
    }
})();
