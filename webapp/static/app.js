/* ربات دانش سازمانی — منطق مینی‌اپ (بدون وابستگی خارجی، سازگار با وب‌ویوهای قدیمی) */

var API = "/api";
var TOKEN = sessionStorage.getItem("kb_token") || null;
var ME = null;

// ── ابزار ─────────────────────────────────────────────────────────────────────

function $(id) { return document.getElementById(id); }

function setStatus(msg) {
    var el = $("loading-msg");
    if (el) el.textContent = msg;
}

function toast(msg) {
    var t = $("toast");
    if (!t) { alert(msg); return; }
    t.textContent = msg;
    t.classList.remove("hidden");
    clearTimeout(t._timer);
    t._timer = setTimeout(function () { t.classList.add("hidden"); }, 5000);
}

// نمایش خطاهای جاوااسکریپت روی صفحه (چون alert در وب‌ویوها حذف می‌شود)
window.addEventListener("error", function (ev) {
    var d = $("dbg");
    if (!d) return;
    d.style.display = "block";
    d.textContent = "JS error: " + (ev.message || "?") + " @line " + (ev.lineno || "?");
});

function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
}

async function api(path, opts) {
    opts = opts || {};
    var headers = { "Content-Type": "application/json" };
    if (TOKEN) headers["Authorization"] = "Bearer " + TOKEN;
    var res = await fetch(API + path, Object.assign({}, opts, { headers: headers }));
    if (res.status === 401) {
        TOKEN = null;
        await auth();
        return api(path, opts);
    }
    var data = {};
    try { data = await res.json(); } catch (e) { data = { detail: "پاسخ نامعتبر" }; }
    if (!res.ok) throw new Error(data.detail || ("خطا " + res.status));
    return data;
}

// ── SDK بله (لود غیرمسدودکننده — اگه در دسترس نبود صفحه نمی‌خوابد) ───────────

var baleSdkPromise = null;

function loadBaleSdk() {
    if (baleSdkPromise) return baleSdkPromise;
    baleSdkPromise = new Promise(function (resolve) {
        try {
            if (window.Bale && window.Bale.WebApp) { resolve(true); return; }
            var done = false;
            var finish = function (ok) { if (!done) { done = true; resolve(ok); } };
            setTimeout(function () { finish(false); }, 6000);
            var s = document.createElement("script");
            s.src = "https://tapi.bale.ai/miniapp.js?3";
            s.onload = function () { finish(true); };
            s.onerror = function () { finish(false); };
            document.head.appendChild(s);
        } catch (e) { resolve(false); }
    });
    return baleSdkPromise;
}

// ── ورود ──────────────────────────────────────────────────────────────────────

function getInitDataFromUrl() {
    try {
        var p = new URLSearchParams(location.search);
        var d = p.get("tgWebAppData");
        if (!d && location.hash && location.hash.length > 1) {
            var h = new URLSearchParams(location.hash.substring(1));
            d = h.get("tgWebAppData");
        }
        return d || "";
    } catch (e) { return ""; }
}

/**
 * initData: تلگرام آن را در URL می‌گذارد؛ بله فقط از طریق SDK تحویل می‌دهد.
 */
async function getInitData() {
    var immediate = getInitDataFromUrl();
    if (immediate) return immediate;

    setStatus("در حال دریافت داده از بله...");
    var ok = await loadBaleSdk();
    if (!ok || !window.Bale || !window.Bale.WebApp) return "";

    // بعد از لود SDK چند بار کوتاه امتحان کن (گاهی با تاخیر مقدار می‌گیرد)
    for (var i = 0; i < 10; i++) {
        var d = "";
        try { d = window.Bale.WebApp.initData || ""; } catch (e) { d = ""; }
        if (d) return d;
        await new Promise(function (r) { setTimeout(r, 200); });
    }
    return "";
}

function detectPlatform() {
    if (window.Bale && window.Bale.WebApp) return "bale";
    if (window.Telegram && window.Telegram.WebApp) return "telegram";
    var ua = navigator.userAgent || "";
    return /Telegram/i.test(ua) ? "telegram" : "bale";
}

async function auth() {
    setStatus("در حال احراز هویت...");
    var initData = await getInitData();
    if (!initData) throw new Error("این صفحه باید از داخل پیام‌رسان باز شود.");
    var res = await fetch(API + "/auth", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ init_data: initData, platform: detectPlatform() }),
    });
    var data = {};
    try { data = await res.json(); } catch (e) {}
    if (!res.ok) throw new Error(data.detail || "ورود ناموفق بود.");
    TOKEN = data.token;
    ME = data.user;
    sessionStorage.setItem("kb_token", TOKEN);
}

// ── ناوبری ────────────────────────────────────────────────────────────────────

var currentTab = "kn";
var views = ["kn-list", "kn-detail", "obs-list", "obs-detail", "me"];
var titles = { kn: "📚 دانش‌های من", obs: "📓 مشاهدات من", me: "👤 پروفایل من" };

function ensureViewEl(name) {
    var el = $(name + "-view");
    if (el) return el;
    el = document.createElement("main");
    el.id = name + "-view";
    el.className = "view hidden";
    var app = $("app");
    var nav = document.querySelector(".tabbar");
    if (nav) app.insertBefore(el, nav);
    else app.appendChild(el);
    return el;
}

function showView(name, titleOverride) {
    views.forEach(function (v) {
        var el = $(v + "-view");
        if (!el) return;
        el.classList.add("hidden");
    });
    var bb = $("back-btn");
    if (!bb) return;
    else bb.classList.toggle("hidden", name.indexOf("detail") === -1);
    var target = ensureViewEl(name);
    target.classList.remove("hidden");
    var pt = $("page-title");
    if (pt) pt.textContent = titleOverride !== undefined ? titleOverride : (titles[currentTab] || "");
}

$("back-btn").addEventListener("click", function () {
    showView(currentTab + "-list");
    if (currentTab === "kn") loadKn(state.knPage);
    else loadObs(state.obsPage);
});

document.querySelectorAll(".tabbar button").forEach(function (btn) {
    btn.addEventListener("click", function () {
        document.querySelectorAll(".tabbar button").forEach(function (b) { b.classList.remove("active"); });
        btn.classList.add("active");
        currentTab = btn.getAttribute("data-tab");
        state.search = {};
        if (currentTab === "kn") { showView("kn-list"); loadKn(0); }
        else if (currentTab === "obs") { showView("obs-list"); loadObs(0); }
        else {
            try {
                showView("me", titles.me);
                setStatusVisible(true);
                renderMe();
            } catch (e) {
                setStatusVisible(false);
                $("me-view").innerHTML = '<div class="empty">⚠️ ' + esc(e.message) + '</div>';
            }
        }
    });
});

var state = { knPage: 0, knPages: 1, obsPage: 0, obsPages: 1, search: {} };

// ── دانش‌ها ───────────────────────────────────────────────────────────────────

function knCard(e) {
    var statusChip = e.status === "submitted"
        ? '<span class="chip ok">✅ ثبت‌شده</span>'
        : '<span class="chip draft">✏️ پیش‌نویس</span>';
    return '<div class="card" onclick="openKn(' + e.id + ')">' +
        '<h3>' + esc(e.title) + '</h3>' +
        '<div class="meta">' +
        '<span class="chip">' + esc(e.type) + '</span>' +
        statusChip +
        (e.kn_number ? '<span class="chip">' + esc(e.kn_number) + '</span>' : "") +
        '<span class="chip">' + esc(e.date) + '</span>' +
        '</div></div>';
}

async function loadKn(page) {
    try {
        state.knPage = page;
        var q = (state.search.kn || "").trim();
        var data = q
            ? await api("/kn/search?q=" + encodeURIComponent(q))
            : await api("/kn?page=" + page);
        state.knPages = data.pages;
        var box = $("kn-list");
        box.innerHTML = data.items.length
            ? data.items.map(knCard).join("")
            : '<div class="empty">📭 موردی یافت نشد.<br>از ربات می‌توانید دانش ثبت کنید.</div>';
        $("kn-page-info").textContent = "صفحه " + (page + 1) + " از " + data.pages + " (" + data.total + ")";
        $("kn-prev").disabled = page <= 0;
        $("kn-next").disabled = page >= data.pages - 1;
    } catch (e) { toast(e.message); }
}

async function openKn(id) {
    try {
        var e = await api("/kn/" + id);
        var tags = (e.hashtags || []).map(function (t) { return "#" + esc(t); }).join(" ");
        var html = '<div class="detail-card">' +
            '<h3 style="margin-bottom:10px">' + esc(e.title) + '</h3>' +
            '<div class="row"><b>نوع:</b> ' + esc(e.type) + '</div>' +
            '<div class="row"><b>وضعیت:</b> ' + (e.status === "submitted" ? "✅ ثبت‌شده" : "✏️ پیش‌نویس") + '</div>' +
            (e.kn_number ? '<div class="row"><b>شماره:</b> ' + esc(e.kn_number) + '</div>' : "") +
            '<div class="row"><b>تاریخ:</b> ' + esc(e.date) + '</div>' +
            (e.tree_path && e.tree_path.length ? '<div class="row"><b>طبقه‌بندی:</b> ' + esc(e.tree_path.join(" ← ")) + '</div>' : "") +
            (tags ? '<div class="row"><b>هشتگ:</b> ' + tags + '</div>' : "") +
            '<hr class="divider">' +
            '<p class="desc-title">📝 متن:</p>' + esc(e.description) +
            '</div>';
        $("kn-detail-view").innerHTML = html;
        showView("kn-detail", "📚 جزئیات دانش");
    } catch (err) { toast(err.message); }
}

$("kn-search-input").addEventListener("keydown", function (ev) {
    if (ev.key === "Enter") { state.search.kn = ev.target.value; loadKn(0); }
});
$("kn-prev").addEventListener("click", function () { loadKn(state.knPage - 1); });
$("kn-next").addEventListener("click", function () { loadKn(state.knPage + 1); });

// ── مشاهدات ───────────────────────────────────────────────────────────────────

function statusFa(s) {
    return { raw: "🟡 خام", maturing: "🟠 در حال بررسی", promoted: "🟢 ارتقایافته", archived: "⚪ بایگانی" }[s] || s;
}

function obsCard(o) {
    var atts = o.attachments || [];
    var attChip = atts.length ? '<span class="chip">📎 ' + atts.length + '</span>' : "";
    return '<div class="card" onclick="openObs(' + o.id + ')">' +
        '<h3>' + esc(o.title) + '</h3>' +
        '<div class="meta">' +
        '<span class="chip">' + statusFa(o.status) + '</span>' +
        attChip +
        '<span class="chip">' + esc(o.date) + '</span>' +
        '</div></div>';
}

async function loadObs(page) {
    try {
        state.obsPage = page;
        var q = (state.search.obs || "").trim();
        var data = q
            ? await api("/obs/search?q=" + encodeURIComponent(q))
            : await api("/obs?page=" + page);
        state.obsPages = data.pages;
        var box = $("obs-list");
        box.innerHTML = data.items.length
            ? data.items.map(obsCard).join("")
            : '<div class="empty">📭 موردی یافت نشد.</div>';
        $("obs-page-info").textContent = "صفحه " + (page + 1) + " از " + data.pages + " (" + data.total + ")";
        $("obs-prev").disabled = page <= 0;
        $("obs-next").disabled = page >= data.pages - 1;
    } catch (e) { toast(e.message); }
}

async function openObs(id) {
    try {
        var o = await api("/obs/" + id);
        var tags = String(o.tags || "").trim();
        var attsHtml = "";
        (o.attachments || []).forEach(function (a) {
            attsHtml += a.is_image
                ? '<span class="att-chip" onclick="showAttImg(event,' + a.id + ',this)">🖼️ ' + esc(a.name || "عکس") + '</span>'
                : '<a class="att-chip" onclick="downloadAtt(event,' + a.id + ',this)">📄 ' + esc(a.name || "فایل") + '</a>';
        });
        var html = '<div class="detail-card">' +
            '<h3 style="margin-bottom:10px">' + esc(o.title) + '</h3>' +
            '<div class="row"><b>وضعیت:</b> ' + statusFa(o.status) + '</div>' +
            '<div class="row"><b>تاریخ:</b> ' + esc(o.date) + '</div>' +
            (tags ? '<div class="row"><b>هشتگ:</b> ' + esc(tags) + '</div>' : "") +
            '<hr class="divider">' +
            esc(o.content) +
            ((o.attachments && o.attachments.length)
                ? '<p class="desc-title" style="margin-top:12px">📎 پیوست‌ها:</p><div class="att-list">' + attsHtml + '</div><div id="img-slot"></div>'
                : "") +
            '</div>';
        $("obs-detail-view").innerHTML = html;
        showView("obs-detail", "📓 جزئیات مشاهده");
    } catch (err) { toast(err.message); }
}

async function fetchAttBlob(id) {
    var res = await fetch(API + "/file/obs-att/" + id, {
        headers: { Authorization: "Bearer " + TOKEN },
    });
    if (!res.ok) throw new Error("دریافت فایل ناموفق بود.");
    return await res.blob();
}

async function showAttImg(ev, id, el) {
    ev.stopPropagation();
    try {
        var blob = await fetchAttBlob(id);
        var old = document.getElementById("att-img-" + id);
        if (old) { old.parentNode.removeChild(old); return; }
        var img = document.createElement("img");
        img.className = "att-img";
        img.id = "att-img-" + id;
        img.src = URL.createObjectURL(blob);
        $("img-slot").appendChild(img);
        el.scrollIntoView({ behavior: "smooth", block: "center" });
    } catch (e) { toast(e.message); }
}

async function downloadAtt(ev, id, el) {
    ev.stopPropagation();
    try {
        var blob = await fetchAttBlob(id);
        var url = URL.createObjectURL(blob);
        var a = document.createElement("a");
        a.href = url;
        a.download = (el.textContent || "attachment").replace(/^[^ ]+ /, "").trim() || "attachment";
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
    } catch (e) { toast(e.message); }
}

$("obs-search-input").addEventListener("keydown", function (ev) {
    if (ev.key === "Enter") { state.search.obs = ev.target.value; loadObs(0); }
});
$("obs-prev").addEventListener("click", function () { loadObs(state.obsPage - 1); });
$("obs-next").addEventListener("click", function () { loadObs(state.obsPage + 1); });

// ── پروفایل ───────────────────────────────────────────────────────────────────

async function renderMe() {
    try {
        var m = await api("/me");
        function row(k, v) {
            return '<div class="prow"><span>' + k + '</span><span>' + esc(v || "—") + '</span></div>';
        }
        var box = ensureViewEl("me");
        box.innerHTML =
            '<div class="profile-rows">' +
            row("📛 نام", m.full_name) +
            row("📞 شماره", m.phone) +
            row("🆔 کد پرسنلی", m.personnel_code) +
            row("🏗️ پروژه", m.project_name) +
            row("💼 سمت", m.position) +
            '</div>' +
            '<p class="empty" style="padding-top:20px">ویرایش اطلاعات از طریق ربات انجام می‌شود.</p>';
        setStatusVisible(false);
    } catch (e) {
        setStatusVisible(false);
        $("me-view").innerHTML = '<div class="empty">⚠️ ' + esc(e.message) + '</div>';
        toast(e.message);
    }
}

function setStatusVisible(on) {
    var s = $("loading-screen");
    if (!s) return;
    if (on) { s.classList.remove("hidden"); setStatus("در حال دریافت پروفایل..."); }
    else s.classList.add("hidden");
}

// ── شروع ──────────────────────────────────────────────────────────────────────

(async function boot() {
    try {
        if (!TOKEN) {
            await auth();
        } else {
            setStatus("در حال بازیابی نشست...");
            try { await api("/me"); }
            catch (e2) { TOKEN = null; await auth(); }
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
