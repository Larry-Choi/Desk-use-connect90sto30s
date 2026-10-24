const state = {
  categories: [],
  apps: [],
  counts: { registered: 0, favorites: 0, byCategory: {}, favoritesByCategory: {} },
  storagePath: "",
  filter: "all",
  query: "",
  showHidden: false,
  editingId: null,
};

const $ = (id) => document.getElementById(id);

document.querySelector(".search").addEventListener("submit", (event) => event.preventDefault());
$("q").addEventListener("input", () => {
  state.query = $("q").value;
  state.showHidden = false;
  render();
});
$("nav-home").addEventListener("click", () => {
  state.filter = "all";
  state.query = "";
  state.showHidden = false;
  $("q").value = "";
  render();
  document.querySelector(".library").scrollTo(0, 0);
});
$("nav-add").addEventListener("click", () => openPrograms());
$("nav-search").addEventListener("click", () => {
  $("q").focus();
  $("q").select();
});
$("view-all").addEventListener("click", () => {
  state.filter = "all";
  state.query = "";
  state.showHidden = false;
  $("q").value = "";
  render();
  $("grid").scrollIntoView({ block: "nearest" });
});
$("edit-categories").addEventListener("click", openCategories);
$("category-close").addEventListener("click", () => $("categories").close());
$("category-form").addEventListener("submit", saveCategories);
$("rescan").addEventListener("click", rescan);
$("browse").addEventListener("click", browse);
$("cancel-edit").addEventListener("click", () => $("editor").close());
$("delete-app").addEventListener("click", removeCurrent);
$("move-up").addEventListener("click", () => moveCurrent(-1));
$("move-down").addEventListener("click", () => moveCurrent(1));
$("editor-form").addEventListener("submit", saveCurrent);
document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    $("q").focus();
    $("q").select();
  }
  if (event.key === "Escape" && event.target === $("q") && state.query) {
    state.query = "";
    $("q").value = "";
    render();
  }
});

window.addEventListener("pywebviewready", boot);

function boot() {
  buildWave();
  tick();
  setInterval(tick, 1000);
  refresh();
  pollMedia();
  setInterval(pollMedia, 800);
  $("arm").addEventListener("click", toggleNeedle);
  $("play-toggle").addEventListener("click", toggleNeedle);
  $("skip-prev").addEventListener("click", () => skipTrack(-1));
  $("skip-next").addEventListener("click", () => skipTrack(1));
  $("program-form").addEventListener("submit", (event) => event.preventDefault());
  $("program-close").addEventListener("click", () => $("programs").close());
  $("program-file").addEventListener("click", () => {
    $("programs").close();
    openEditor(null);
  });
  $("program-folder").addEventListener("click", pickFolder);
  $("source-menu").addEventListener("click", () => setProgramSource("menu"));
  $("source-running").addEventListener("click", () => setProgramSource("running"));
  $("launch-group").addEventListener("click", launchGroup);
  $("track-form").addEventListener("submit", addTrack);
  $("nav-mail").addEventListener("click", openMail);
  $("mail-close").addEventListener("click", () => $("mail").close());
  $("mail-form").addEventListener("submit", saveMail);
  $("mail-forget").addEventListener("click", forgetMail);
  $("nav-page").addEventListener("click", openPage);
  $("nav-page").addEventListener("contextmenu", (event) => {
    event.preventDefault();
    openPageDialog();
  });
  $("page-close").addEventListener("click", () => $("page").close());
  $("page-form").addEventListener("submit", savePage);
  $("exit-page").addEventListener("click", exitPage);
  $("nav-phone").addEventListener("click", openPhone);
  $("phone-close").addEventListener("click", () => $("phone").close());
  $("phone-form").addEventListener("submit", (event) => event.preventDefault());
  $("usage").addEventListener("click", (event) => {
    if (event.target.closest("[data-usage-settings]")) {
      openUsageSetup(event);
      return;
    }
    openUsage();
  });
  $("usage-close").addEventListener("click", () => $("usage-dialog").close());
  $("usage-form").addEventListener("submit", (event) => event.preventDefault());
  $("usage-setup").addEventListener("click", openUsageSetup);
  $("usage-setup-close").addEventListener("click", () => $("usage-setup-dialog").close());
  $("usage-setup-form").addEventListener("submit", saveUsageSetup);
  $("usage-align-start").addEventListener("click", () => setUsageAlign("start"));
  $("usage-align-end").addEventListener("click", () => setUsageAlign("end"));
  for (const id of ["editor", "programs", "mail", "page", "phone", "usage-dialog", "usage-setup-dialog", "categories"]) {
    $(id).addEventListener("close", () => {
      if ($("library").classList.contains("is-page")) syncPageSoon();
    });
  }
  const openDialog = HTMLDialogElement.prototype.showModal;
  HTMLDialogElement.prototype.showModal = function showDeskDialog() {
    if (window.pywebview) window.pywebview.api.hide_page();
    return openDialog.call(this);
  };
  window.addEventListener("resize", syncPageSoon);
  refreshMail();
  refreshUsage();
  $("program-query").addEventListener("input", renderPrograms);
  $("app-menu").addEventListener("click", onAppMenu);
  document.addEventListener("click", () => {
    $("app-menu").hidden = true;
  });
  window.addEventListener("resize", () => {
    $("app-menu").hidden = true;
  });
}

const clockFormat = new Intl.DateTimeFormat("ko-KR", { hour: "numeric", minute: "2-digit", hour12: true });
const dayFormat = new Intl.DateTimeFormat("ko-KR", { year: "numeric", month: "long", day: "numeric", weekday: "long" });
const dowFormat = new Intl.DateTimeFormat("en-US", { weekday: "long" });
let clockKey = "";

function tick() {
  const now = new Date();
  const key = `${now.getFullYear()}-${now.getMonth()}-${now.getDate()}-${now.getHours()}-${now.getMinutes()}`;
  if (key !== clockKey) {
    clockKey = key;
    $("clock").textContent = clockFormat.format(now);
    $("dow").textContent = dowFormat.format(now).toUpperCase();
    const today = $("today");
    today.dateTime = now.toISOString();
    today.textContent = dayFormat.format(now);
    $("day-now").textContent = `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
  }
  const minutes = now.getHours() * 60 + now.getMinutes() + now.getSeconds() / 60;
  const fraction = Math.min(1, Math.max(0, minutes / (24 * 60)));
  $("day-fill").style.width = `${fraction * 100}%`;
}

async function refresh() {
  const data = await window.pywebview.api.get_library();
  apply(data);
}

function apply(data) {
  if (!data || !data.ok) {
    toast((data && data.error) || "목록을 불러오지 못했습니다.", true);
    return;
  }
  state.categories = data.categories;
  state.apps = data.apps;
  state.counts = data.counts;
  state.storagePath = data.storagePath || "";
  if (state.filter !== "all" && !state.categories.some((item) => item.id === state.filter)) state.filter = "all";
  if (Array.isArray(data.tracks)) {
    deskTracks = data.tracks;
    if (deskIndex >= deskTracks.length) deskIndex = 0;
    renderTrackList();
  }
  render();
}

function render() {
  renderChips();
  renderCards();
  renderGrid();
  const counts = state.counts;
  $("stats").textContent = `등록 ${counts.registered}개 · 즐겨찾기 ${counts.favorites}개`;
  const group = $("launch-group");
  const category = state.categories.find((item) => item.id === state.filter);
  group.hidden = !category || Boolean(state.query);
  if (category) group.textContent = `${category.title} 열기`;
  $("storage").textContent = state.storagePath ? `이 PC에 저장됨 · ${state.storagePath}` : "";
  const hidden = state.apps.filter((app) => !app.favorite).length;
  const archive = $("archive-row");
  if (hidden > 0) {
    archive.hidden = false;
    archive.innerHTML = "";
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = state.showHidden
      ? "즐겨찾기로 돌아가기"
      : `즐겨찾기에서 뺀 앱 ${hidden}개 보기`;
    button.addEventListener("click", () => {
      state.showHidden = !state.showHidden;
      render();
    });
    archive.appendChild(button);
  } else {
    archive.hidden = true;
  }
}

function renderChips() {
  const chips = $("chips");
  chips.innerHTML = "";
  const items = [{ id: "all", title: "전체" }, ...state.categories];
  for (const item of items) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "chip";
    button.textContent = item.title;
    button.setAttribute("aria-pressed", String(state.filter === item.id && !state.query));
    button.addEventListener("click", () => {
      state.filter = item.id;
      state.query = "";
      state.showHidden = false;
      $("q").value = "";
      render();
    });
    chips.appendChild(button);
  }
}

function renderCards() {
  const cards = $("cards");
  cards.innerHTML = "";
  const art = { ai: "art-ai", video: "art-video", tools: "art-tools" };
  for (const category of state.categories) {
    const registered = state.counts.byCategory[category.id] || 0;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "card";
    if (state.filter === category.id && !state.query) button.classList.add("is-selected");
    button.innerHTML = `
      <span class="card-art ${art[category.id] || "art-tools"}" aria-hidden="true"></span>
      <span class="card-meta">
        <span class="card-title"></span>
        <span class="card-count"></span>
      </span>`;
    button.querySelector(".card-title").textContent = category.title;
    button.querySelector(".card-count").textContent = `등록 ${registered}개`;
    button.addEventListener("click", () => {
      state.filter = category.id;
      state.query = "";
      state.showHidden = false;
      $("q").value = "";
      render();
    });
    button.addEventListener("contextmenu", (event) => {
      event.preventDefault();
      openCategories();
    });
    cards.appendChild(button);
  }
}

function visibleApps() {
  if (state.showHidden) return state.apps.filter((app) => !app.favorite);
  const query = state.query.trim().toLowerCase();
  const favorites = state.apps.filter((app) => app.favorite);
  if (query) return favorites.filter((app) => app.name.toLowerCase().includes(query));
  if (state.filter === "all") return favorites;
  return favorites.filter((app) => app.categoryId === state.filter);
}

function renderGrid() {
  const apps = visibleApps();
  const title = $("launcher-title");
  if (state.showHidden) title.textContent = "즐겨찾기에서 뺀 앱";
  else if (state.query.trim()) title.textContent = `검색 결과 ${apps.length}개`;
  else if (state.filter === "all") title.textContent = `즐겨 쓰는 앱 ${state.counts.favorites}`;
  else {
    const category = state.categories.find((item) => item.id === state.filter);
    const shown = apps.length;
    title.textContent = `${category ? category.title : "분류"} · 즐겨찾기 ${shown}`;
  }

  const grid = $("grid");
  grid.innerHTML = "";
  if (apps.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = state.query.trim()
      ? "찾는 이름의 즐겨찾기가 없습니다."
      : "표시할 앱이 없습니다. 추가하거나 이 PC에서 다시 찾아 보세요.";
    grid.appendChild(empty);
    return;
  }
  for (const app of apps) {
    grid.appendChild(renderTile(app));
  }
}

function renderTile(app) {
  const tile = document.createElement("article");
  tile.className = "app-tile";
  tile.dataset.id = app.id;

  const launch = document.createElement("button");
  launch.type = "button";
  launch.className = "launch";
  launch.addEventListener("click", () => launchApp(app));
  const glyph = document.createElement("span");
  glyph.className = "glyph";
  glyph.setAttribute("aria-hidden", "true");
  fillGlyph(glyph, app.target, app.icon);
  const name = document.createElement("span");
  name.className = "app-name";
  name.textContent = app.name;
  const meta = document.createElement("span");
  meta.className = "app-meta";
  const category = state.categories.find((item) => item.id === app.categoryId);
  meta.textContent = category ? category.title : "";
  launch.append(glyph, name, meta);
  launch.setAttribute("aria-label", `${app.name} 실행`);

  tile.addEventListener("contextmenu", (event) => {
    event.preventDefault();
    openAppMenu(event, app);
  });
  bindGlyphMotion(tile, glyph);
  tile.append(launch);
  return tile;
}

function bindGlyphMotion(tile, glyph) {
  const play = (name) => {
    glyph.classList.remove("is-in", "is-out");
    void glyph.offsetWidth;
    glyph.classList.add(name);
  };
  tile.addEventListener("mouseenter", () => play("is-in"));
  tile.addEventListener("mouseleave", () => {
    if (!glyph.classList.contains("is-in") && !glyph.classList.contains("is-out")) return;
    play("is-out");
  });
  glyph.addEventListener("animationend", (event) => {
    if (event.animationName === "glyph-out") glyph.classList.remove("is-out");
  });
}

const STROKE_ICONS = {
  folder: '<svg viewBox="0 0 24 24"><path d="M3.5 7.5h5.5l1.8 2h9.7v8.5h-17z"/></svg>',
  note: '<svg viewBox="0 0 24 24"><path d="M6 4.5h8.5L18 8v11.5H6z"/><path d="M14.5 4.5V8H18"/><path d="M8.5 12h7M8.5 15.5h5"/></svg>',
  paint: '<svg viewBox="0 0 24 24"><rect x="4" y="5" width="11" height="11" rx="1.5"/><path d="M15 13.5l4.2.6-1.6 3.6-2.8-.8z"/></svg>',
  calc: '<svg viewBox="0 0 24 24"><rect x="5" y="3.5" width="14" height="17" rx="2"/><path d="M8 7.5h8"/><path d="M8.5 12h.1M12 12h.1M15.5 12h.1M8.5 15.5h.1M12 15.5h.1M15.5 15.5h.1"/></svg>',
  terminal: '<svg viewBox="0 0 24 24"><rect x="3.5" y="5" width="17" height="14" rx="2"/><path d="M7 10l3 2-3 2M12 14.5h5"/></svg>',
  app: '<svg viewBox="0 0 24 24"><rect x="5" y="5" width="14" height="14" rx="3"/></svg>',
};

function strokeKind(target) {
  const file = String(target || "").split(/[\\/]/).pop().toLowerCase();
  if (file === "explorer.exe") return "folder";
  if (file === "notepad.exe") return "note";
  if (file === "mspaint.exe") return "paint";
  if (file === "calc.exe") return "calc";
  if (file === "wt.exe" || file === "windowsterminal.exe") return "terminal";
  return "";
}

function fillGlyph(node, target, icon) {
  const kind = strokeKind(target);
  if (kind || !icon) {
    node.innerHTML = STROKE_ICONS[kind || "app"];
    return;
  }
  const img = document.createElement("img");
  img.src = icon;
  img.alt = "";
  node.appendChild(img);
}

async function launchApp(app) {
  const result = await window.pywebview.api.launch(app.id);
  if (!result.ok) toast(result.error || "실행하지 못했습니다.", true);
  else toast(`${result.name} 실행`);
}

async function rescan() {
  const data = await window.pywebview.api.rescan();
  apply(data);
  if (data && data.ok) {
    toast(data.added ? `${data.added}개를 추가했습니다.` : "새로 찾은 앱이 없습니다.");
  }
}

function openEditor(appId, draft) {
  state.editingId = appId;
  const app = state.apps.find((item) => item.id === appId);
  $("editor-title").textContent = app ? "앱 수정" : "앱 추가";
  $("app-name").value = app ? app.name : (draft && draft.name) || "";
  $("app-target").value = app ? app.target : (draft && draft.target) || "";
  $("app-favorite").checked = app ? app.favorite : true;
  $("form-error").textContent = "";
  $("delete-app").hidden = !app;
  $("order-row").hidden = !app;
  const select = $("app-category");
  select.innerHTML = "";
  for (const category of state.categories) {
    const option = document.createElement("option");
    option.value = category.id;
    option.textContent = category.title;
    select.appendChild(option);
  }
  select.value = app ? app.categoryId : (state.filter !== "all" ? state.filter : "tools");
  $("editor").showModal();
  $("app-name").focus();
}

async function browse() {
  const picked = await window.pywebview.api.pick_target();
  if (picked) $("app-target").value = picked;
}

async function saveCurrent(event) {
  event.preventDefault();
  const payload = {
    id: state.editingId || "",
    name: $("app-name").value,
    categoryId: $("app-category").value,
    target: $("app-target").value,
    favorite: $("app-favorite").checked,
  };
  const data = await window.pywebview.api.save_app(payload);
  if (!data.ok) {
    $("form-error").textContent = data.error || "저장하지 못했습니다.";
    return;
  }
  $("editor").close();
  apply(data);
  toast("저장했습니다.");
}

async function removeCurrent() {
  if (!state.editingId) return;
  const app = state.apps.find((item) => item.id === state.editingId);
  const name = app ? app.name : "이 앱";
  if (!window.confirm(`${name} 항목을 목록에서 지울까요? 프로그램 파일은 삭제되지 않습니다.`)) return;
  const data = await window.pywebview.api.remove_app(state.editingId);
  if (!data.ok) {
    $("form-error").textContent = data.error || "삭제하지 못했습니다.";
    return;
  }
  $("editor").close();
  apply(data);
  toast("목록에서 지웠습니다.");
}

async function moveCurrent(delta) {
  if (!state.editingId) return;
  const data = await window.pywebview.api.move(state.editingId, delta);
  apply(data);
}

function openAppMenu(event, app) {
  const menu = $("app-menu");
  menu.dataset.id = app.id;
  const favorite = menu.querySelector("[data-act='favorite']");
  favorite.textContent = app.favorite ? "즐겨찾기에서 빼기" : "즐겨찾기에 넣기";
  menu.hidden = false;
  const width = 168;
  const height = menu.offsetHeight || 180;
  const left = Math.min(event.clientX, window.innerWidth - width - 8);
  const top = Math.min(event.clientY, window.innerHeight - height - 8);
  menu.style.left = `${Math.max(8, left)}px`;
  menu.style.top = `${Math.max(8, top)}px`;
  event.stopPropagation();
}

async function onAppMenu(event) {
  const button = event.target.closest("button");
  if (!button) return;
  event.stopPropagation();
  const menu = $("app-menu");
  const id = menu.dataset.id;
  const app = state.apps.find((item) => item.id === id);
  menu.hidden = true;
  if (!app) return;
  if (button.dataset.act === "edit") {
    openEditor(app.id);
    return;
  }
  if (button.dataset.act === "favorite") {
    const data = await window.pywebview.api.save_app({
      id: app.id,
      name: app.name,
      categoryId: app.categoryId,
      target: app.target,
      favorite: !app.favorite,
    });
    if (!data.ok) toast(data.error || "저장하지 못했습니다.", true);
    else apply(data);
    return;
  }
  if (button.dataset.act === "up" || button.dataset.act === "down") {
    const data = await window.pywebview.api.move(app.id, button.dataset.act === "up" ? -1 : 1);
    if (data && data.ok) apply(data);
    return;
  }
  if (button.dataset.act === "remove") {
    if (!window.confirm(`${app.name} 항목을 목록에서 지울까요? 프로그램 파일은 삭제되지 않습니다.`)) return;
    const data = await window.pywebview.api.remove_app(app.id);
    if (!data.ok) toast(data.error || "삭제하지 못했습니다.", true);
    else {
      apply(data);
      toast("목록에서 지웠습니다.");
    }
  }
}

function toast(message, isError) {
  const el = $("toast");
  el.textContent = message;
  el.hidden = false;
  el.classList.toggle("is-error", Boolean(isError));
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => {
    el.hidden = true;
  }, 3200);
}

let programs = [];
let programSource = "menu";
let needleLock = 0;
let deskTracks = [];
let deskIndex = 0;
let deskPlaying = false;
let browserOwns = false;
let ytPlayer = null;
let ytApi = null;

function buildWave() {
  const pieces = [];
  for (let index = 0; index < 72; index += 1) {
    const height = 6 + Math.round(Math.abs(Math.sin(index * 0.55) * 14 + Math.sin(index * 0.19) * 6));
    pieces.push(`<i style="height:${height}px"></i>`);
  }
  const markup = pieces.join("");
  $("wave-back").innerHTML = markup;
  $("wave-front").innerHTML = markup;
}

const SLEEVES = ["ink", "wine", "sea", "gold", "forest", "violet"];

function sleeveFor(key) {
  const text = String(key || "desk");
  let sum = 0;
  for (let index = 0; index < text.length; index += 1) {
    sum = (sum + text.charCodeAt(index) * (index + 3)) % 997;
  }
  return SLEEVES[sum % SLEEVES.length];
}

function setSleeve(key) {
  paintDisc("", key, "");
}

function paintDisc(videoId, key, image) {
  const label = $("record-label");
  const record = $("record");
  const photo = image || (videoId ? `https://i.ytimg.com/vi/${encodeURIComponent(videoId)}/hqdefault.jpg` : "");
  if (photo) {
    label.style.backgroundImage = `url("${String(photo).replace(/"/g, "")}")`;
    label.classList.add("is-photo");
    return;
  }
  label.style.backgroundImage = "";
  label.classList.remove("is-photo");
  record.dataset.sleeve = sleeveFor(key || "desk");
}

function discFromMedia(data) {
  const title = (data.title || "").trim();
  const known = deskTracks.find((item) => item.videoId && title && (title.includes(item.videoId) || title === item.title));
  paintDisc(known ? known.videoId : "", [data.title, data.artist, data.source].filter(Boolean).join(" "), data.thumbnail || "");
}

let mediaStamp = "";

function mediaStampOf(data) {
  return [data.ready, data.playing, data.title, data.artist, data.source, data.canPrev, data.canNext, data.error, data.thumbnail ? data.thumbnail.length : 0].join("\u0001");
}

async function pollMedia() {
  if (!window.pywebview) return;
  const data = await window.pywebview.api.media_state();
  if (!data || Date.now() < needleLock) return;
  if (mediaStampOf(data) === mediaStamp) return;
  applyMedia(data, false);
}

function applyMedia(data, force) {
  if (!data) return;
  if (!force && Date.now() < needleLock) return;
  mediaStamp = mediaStampOf(data);
  browserOwns = Boolean(data.ready && (data.playing || data.title));
  if (browserOwns && deskPlaying) {
    deskPlaying = false;
    if (ytPlayer && ytPlayer.pauseVideo) ytPlayer.pauseVideo();
  }
  if (!browserOwns && deskTracks.length) {
    const track = deskTracks[deskIndex] || deskTracks[0];
    $("track").textContent = track ? track.title : "담아 둔 곡이 없습니다.";
    if (track) paintDisc(track.videoId, track.title, "");
    $("skip-prev").disabled = deskTracks.length < 2;
    $("skip-next").disabled = deskTracks.length < 2;
    if (!deskPlaying) paintNeedle(false);
    return;
  }
  const playing = Boolean(data.playing);
  paintNeedle(playing);
  discFromMedia(data);
  $("skip-prev").disabled = !data.canPrev;
  $("skip-next").disabled = !data.canNext;
  $("track").textContent = trackLine(data);
  if (force && data.error) toast(data.error, true);
}

function trackLine(data) {
  if (!data.ready) return "재생 상태를 확인하는 중입니다.";
  const name = [data.title, data.artist].filter(Boolean).join(" — ");
  if (data.playing && name) return `${data.source} · ${name}`;
  if (name) return `${data.source} · 멈춤 · ${name}`;
  return "브라우저에서 유튜브를 틀어 두면 바늘로 이어 재생됩니다.";
}

async function toggleNeedle() {
  if (!browserOwns && deskTracks.length) {
    if (deskPlaying) pauseDesk();
    else playDesk(deskIndex);
    return;
  }
  const next = !$("arm").classList.contains("is-playing");
  needleLock = Date.now() + 1200;
  $("arm").classList.toggle("is-playing", next);
  $("record").classList.toggle("is-spinning", next);
  const data = await window.pywebview.api.set_needle(next);
  applyMedia(data, true);
}

async function skipTrack(delta) {
  if (!browserOwns && deskTracks.length) {
    playDesk(deskIndex + delta);
    return;
  }
  const data = await window.pywebview.api.skip_track(delta);
  applyMedia(data, true);
}

function loadYouTubeApi() {
  if (window.YT && window.YT.Player) return Promise.resolve();
  if (ytApi) return ytApi;
  ytApi = new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => reject(new Error("timeout")), 8000);
    const previous = window.onYouTubeIframeAPIReady;
    window.onYouTubeIframeAPIReady = () => {
      window.clearTimeout(timer);
      if (typeof previous === "function") previous();
      resolve();
    };
    const script = document.createElement("script");
    script.src = "https://www.youtube.com/iframe_api";
    script.dataset.ytApi = "1";
    script.onerror = () => {
      window.clearTimeout(timer);
      reject(new Error("load"));
    };
    document.head.appendChild(script);
  });
  return ytApi;
}

function paintNeedle(playing) {
  $("arm").classList.toggle("is-playing", playing);
  $("arm").setAttribute("aria-pressed", String(playing));
  $("arm").querySelector(".wand").style.transform = playing ? "rotate(163deg)" : "rotate(-38deg)";
  $("record").classList.toggle("is-spinning", playing);
  $("chassis").classList.toggle("is-on", playing);
  $("play-shape").setAttribute("d", playing ? "M7 5.5h3.4v13H7zM13.6 5.5H17v13h-3.4z" : "M9 6.5v11l9-5.5z");
  const label = playing ? "바늘을 받침으로 내리기" : "바늘을 판 위에 올리기";
  $("arm").setAttribute("aria-label", label);
  $("play-toggle").setAttribute("aria-label", label);
}

function setDeskVisual(playing) {
  deskPlaying = playing;
  paintNeedle(playing);
  renderTrackList();
}

function pauseDesk() {
  if (ytPlayer && ytPlayer.pauseVideo) ytPlayer.pauseVideo();
  setDeskVisual(false);
}

async function playDesk(index) {
  if (!deskTracks.length) return;
  if (browserOwns) {
    toast("브라우저에서 이미 재생 중입니다. 브라우저를 멈춘 뒤에 책상 곡을 틀 수 있습니다.", true);
    return;
  }
  deskIndex = (index + deskTracks.length) % deskTracks.length;
  const track = deskTracks[deskIndex];
  $("track").textContent = track.title;
  paintDisc(track.videoId, track.title, "");
  try {
    await loadYouTubeApi();
  } catch (error) {
    toast("유튜브 플레이어를 열지 못했습니다.", true);
    return;
  }
  if (!window.YT || !window.YT.Player) {
    toast("유튜브 플레이어를 열지 못했습니다.", true);
    return;
  }
  const host = $("yt-host");
  if (!ytPlayer) {
    const mount = document.createElement("div");
    host.replaceChildren(mount);
    ytPlayer = new window.YT.Player(mount, {
      videoId: track.videoId,
      playerVars: { autoplay: 1, rel: 0, playsinline: 1, origin: window.location.origin },
      events: {
        onReady: (event) => event.target.playVideo(),
        onStateChange: (event) => {
          const stateCode = window.YT && window.YT.PlayerState;
          if (!stateCode) return;
          if (event.data === stateCode.ENDED) playDesk(deskIndex + 1);
          if (event.data === stateCode.PLAYING) setDeskVisual(true);
          if (event.data === stateCode.PAUSED) setDeskVisual(false);
        },
      },
    });
  } else if (ytPlayer.loadVideoById) {
    ytPlayer.loadVideoById(track.videoId);
  }
  setDeskVisual(true);
}

function renderTrackList() {
  const list = $("track-list");
  if (!list) return;
  list.innerHTML = "";
  deskTracks.forEach((track, index) => {
    const row = document.createElement("li");
    if (index === deskIndex) row.classList.add("is-current");
    const play = document.createElement("button");
    play.type = "button";
    play.className = "track-title";
    play.textContent = track.title;
    play.addEventListener("click", () => playDesk(index));
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "빼기";
    remove.addEventListener("click", () => dropTrack(track.id));
    row.append(play, remove);
    list.appendChild(row);
  });
}

async function addTrack(event) {
  event.preventDefault();
  const raw = $("track-url").value.trim();
  if (!raw) return;
  const data = await window.pywebview.api.add_track(raw);
  if (!data.ok) {
    toast(data.error || "곡을 담지 못했습니다.", true);
    return;
  }
  $("track-url").value = "";
  apply(data);
  toast("곡을 담았습니다.");
}

async function dropTrack(trackId) {
  const data = await window.pywebview.api.remove_track(trackId);
  if (!data.ok) {
    toast(data.error || "곡을 빼지 못했습니다.", true);
    return;
  }
  if (deskPlaying) pauseDesk();
  apply(data);
}

async function launchGroup() {
  const data = await window.pywebview.api.launch_category(state.filter);
  if (!data.ok) toast(data.error || "분류를 열지 못했습니다.", true);
  else toast(`${data.count}개를 열었습니다.`);
}

async function pickFolder() {
  const picked = await window.pywebview.api.pick_folder();
  if (!picked) return;
  const name = String(picked).split(/[\\/]/).filter(Boolean).pop() || "폴더";
  $("programs").close();
  openEditor(null, { name, target: picked });
}

function setProgramSource(source) {
  programSource = source;
  $("source-menu").setAttribute("aria-pressed", String(source === "menu"));
  $("source-running").setAttribute("aria-pressed", String(source === "running"));
  loadPrograms();
}

async function openPrograms() {
  if (state.categories.length === 0) await refresh();
  fillProgramCategories();
  $("program-query").value = "";
  programSource = "menu";
  $("source-menu").setAttribute("aria-pressed", "true");
  $("source-running").setAttribute("aria-pressed", "false");
  $("programs").showModal();
  await loadPrograms();
  $("program-query").focus();
}

async function loadPrograms() {
  $("program-list").textContent = programSource === "running" ? "실행 중인 창을 읽는 중" : "시작 메뉴를 읽는 중";
  const data = programSource === "running"
    ? await window.pywebview.api.list_running()
    : await window.pywebview.api.list_programs();
  if (!data.ok) {
    $("program-list").textContent = data.error || "목록을 읽지 못했습니다.";
    return;
  }
  programs = data.programs || [];
  renderPrograms();
  hydrateIcons(programs.filter((item) => !item.icon));
}

function fillProgramCategories() {
  const select = $("program-category");
  const current = select.value;
  select.innerHTML = "";
  for (const category of state.categories) {
    const option = document.createElement("option");
    option.value = category.id;
    option.textContent = category.title;
    select.appendChild(option);
  }
  select.value = state.categories.some((item) => item.id === current)
    ? current
    : (state.filter !== "all" ? state.filter : "tools");
}

function renderPrograms() {
  const list = $("program-list");
  const query = $("program-query").value.trim().toLowerCase();
  const visible = programs.filter((item) => !query || item.name.toLowerCase().includes(query));
  list.innerHTML = "";
  if (visible.length === 0) {
    list.textContent = "해당하는 프로그램이 없습니다.";
    return;
  }
  for (const item of visible.slice(0, 80)) {
    list.appendChild(programRow(item));
  }
}

function programRow(item) {
  const row = document.createElement("div");
  row.className = "program-row";
  row.setAttribute("role", "listitem");
  row.dataset.path = item.path;
  const kind = strokeKind(item.path);
  if (kind || !item.icon) {
    const mark = document.createElement("span");
    mark.className = "program-mark";
    mark.innerHTML = STROKE_ICONS[kind || "app"];
    row.appendChild(mark);
  } else {
    const img = document.createElement("img");
    img.src = item.icon;
    img.alt = "";
    row.appendChild(img);
  }
  const name = document.createElement("p");
  name.textContent = item.name;
  const run = document.createElement("button");
  run.type = "button";
  run.textContent = "실행";
  run.addEventListener("click", async () => {
    const result = await window.pywebview.api.launch_path(item.path);
    if (!result.ok) toast(result.error || "실행하지 못했습니다.", true);
    else toast(`${item.name} 실행`);
  });
  const add = document.createElement("button");
  add.type = "button";
  add.textContent = item.added ? "추가됨" : "추가";
  add.disabled = item.added;
  add.addEventListener("click", () => addProgram(item, add));
  row.append(name, run, add);
  return row;
}

async function hydrateIcons(items) {
  for (const item of items) {
    if (!$("programs").open) return;
    if (strokeKind(item.path)) continue;
    const result = await window.pywebview.api.program_icon(item.path);
    item.icon = (result && result.icon) || "";
    const row = [...document.querySelectorAll(".program-row")].find((node) => node.dataset.path === item.path);
    if (!row || !item.icon) continue;
    const img = document.createElement("img");
    img.src = item.icon;
    img.alt = "";
    const mark = row.querySelector(".program-mark");
    if (mark) mark.replaceWith(img);
  }
}

async function refreshMail() {
  const state = await window.pywebview.api.mail_state();
  if (!state || !state.connected) {
    $("mail-dot").hidden = true;
    $("nav-mail").setAttribute("aria-label", "네이버 메일");
    return;
  }
  const inbox = await window.pywebview.api.mail_inbox();
  paintMail(inbox, false);
}

async function openMail() {
  const state = await window.pywebview.api.mail_state();
  $("mail-address").value = (state && state.address) || "";
  $("mail-password").value = "";
  $("mail-error").textContent = "";
  $("mail-list").replaceChildren();
  $("mail-forget").hidden = !(state && state.connected);
  $("mail").showModal();
  if (state && state.connected) {
    $("mail-fetch").disabled = true;
    const inbox = await window.pywebview.api.mail_open();
    $("mail-fetch").disabled = false;
    paintMail(inbox, true);
  }
}

function paintMail(inbox, announce) {
  const dot = $("mail-dot");
  const unseen = inbox && inbox.unseen ? inbox.unseen : 0;
  if (inbox && inbox.ok) dot.hidden = !inbox.dot;
  $("nav-mail").setAttribute("aria-label", !dot.hidden && unseen > 0 ? `네이버 메일, 안 읽음 ${unseen}` : "네이버 메일");
  if (!inbox) return;
  $("mail-forget").hidden = !inbox.connected;
  if (!inbox.ok) {
    $("mail-error").textContent = inbox.error || "메일을 가져오지 못했습니다.";
    if (announce) toast($("mail-error").textContent, true);
    return;
  }
  $("mail-error").textContent = "";
  const list = $("mail-list");
  list.replaceChildren();
  for (const item of inbox.messages || []) {
    const row = document.createElement("li");
    if (item.unseen) row.className = "is-unseen";
    const who = document.createElement("span");
    who.className = "who";
    who.textContent = item.from || "";
    const subject = document.createElement("span");
    subject.className = "subject";
    subject.textContent = item.subject || "(제목 없음)";
    const when = document.createElement("span");
    when.className = "when";
    when.textContent = item.date || "";
    row.append(who, subject, when);
    list.append(row);
  }
}

async function saveMail(event) {
  event.preventDefault();
  $("mail-error").textContent = "";
  $("mail-fetch").disabled = true;
  const inbox = await window.pywebview.api.save_mail($("mail-address").value, $("mail-password").value);
  $("mail-fetch").disabled = false;
  $("mail-password").value = "";
  paintMail(inbox, true);
  if (inbox && inbox.ok) toast("받은편지함을 가져왔습니다.");
}

async function forgetMail() {
  const result = await window.pywebview.api.forget_mail();
  $("mail-address").value = "";
  $("mail-password").value = "";
  paintMail(result, false);
  $("mail-list").replaceChildren();
  toast("메일 연결을 끊었습니다.");
}

async function openPage() {
  const library = $("library");
  if (library.classList.contains("is-page")) {
    exitPage();
    return;
  }
  const state = await window.pywebview.api.page_state();
  if (!state || !state.url) {
    openPageDialog();
    return;
  }
  enterPage();
}

function enterPage() {
  $("library").classList.add("is-page");
  $("exit-page").hidden = false;
  if (!window.pageObserver) {
    window.pageObserver = new ResizeObserver(() => syncPageSoon());
    window.pageObserver.observe($("page-host"));
    window.pageObserver.observe($("page-well"));
    window.pageObserver.observe($("library"));
  }
  requestAnimationFrame(syncPageSoon);
}

function exitPage() {
  $("library").classList.remove("is-page");
  $("exit-page").hidden = true;
  if (window.pywebview) window.pywebview.api.hide_page();
}

let pageSync = 0;
let pageError = "";
function syncPageSoon() {
  if (!$("library").classList.contains("is-page") || !window.pywebview) return;
  clearTimeout(pageSync);
  pageSync = setTimeout(async () => {
    if (!$("library").classList.contains("is-page")) return;
    if (document.querySelector("dialog[open]")) {
      await window.pywebview.api.hide_page();
      return;
    }
    const rect = $("page-well").getBoundingClientRect();
    const placed = await window.pywebview.api.sync_page(
      rect.left,
      rect.top,
      rect.width,
      rect.height,
      window.devicePixelRatio || 1,
    );
    if (placed && !placed.ok) {
      if (placed.error !== pageError) toast(placed.error || "페이지를 열지 못했습니다.", true);
      pageError = placed.error || "";
      return;
    }
    pageError = "";
  }, 60);
}

function pushNotice(item) {
  const stack = $("notices");
  if (!stack || !item) return;
  if (item.dismiss && item.id) {
    const gone = stack.querySelector(`[data-id="${CSS.escape(item.id)}"]`);
    if (gone) gone.remove();
    if (item.id === "call") $("phone-dot").hidden = true;
    return;
  }
  if (item.kind === "mail") $("mail-dot").hidden = false;
  if (item.kind === "call" || item.kind === "pair") $("phone-dot").hidden = false;
  let card = item.id ? stack.querySelector(`[data-id="${CSS.escape(item.id)}"]`) : null;
  if (!card) {
    card = document.createElement("article");
    card.className = "notice";
    if (item.id) card.dataset.id = item.id;
    stack.prepend(card);
  }
  card.replaceChildren();
  const title = document.createElement("strong");
  title.textContent = item.title || "Desk";
  const body = document.createElement("p");
  body.textContent = item.body || "";
  const close = document.createElement("button");
  close.type = "button";
  close.textContent = "닫기";
  close.addEventListener("click", () => {
    card.remove();
    if (item.kind === "call") $("phone-dot").hidden = true;
  });
  card.append(title, body, close);
  while (stack.children.length > 4) stack.lastElementChild.remove();
  if (!item.sticky) {
    const ttl = Number(item.ttl) > 0 ? Number(item.ttl) : 12000;
    clearTimeout(card.timer);
    card.timer = setTimeout(() => card.remove(), ttl);
  }
}

let phoneTimer = 0;
let usageItems = [];

function usageAmounts(bar) {
  const used = Math.max(0, Math.min(100, Math.round(Number(bar.used) || 0)));
  const quota = Boolean(bar.label);
  return { used, shown: quota ? 100 - used : used };
}

function usageColor(used) {
  if (used >= 85) return "#F43F5E";
  if (used >= 60) return "#F97316";
  return "#10B981";
}

function usageLine(bar) {
  const amounts = usageAmounts(bar);
  const row = document.createElement("span");
  row.className = "usage-line";
  if (bar.label) {
    const label = document.createElement("span");
    label.textContent = bar.hint ? `${bar.label} ${bar.hint}` : bar.label;
    row.append(label);
  }
  const track = document.createElement("i");
  const fill = document.createElement("span");
  fill.style.width = `${amounts.shown}%`;
  fill.style.background = usageColor(amounts.used);
  track.append(fill);
  const percent = document.createElement("b");
  percent.textContent = `${amounts.shown}%`;
  row.append(track, percent);
  if (bar.left) {
    const left = document.createElement("em");
    left.textContent = bar.left;
    row.append(left);
  }
  return row;
}

let usageShape = "";

function usageShapeOf(state) {
  const align = state && state.align === "end" ? "end" : "start";
  const items = (state && state.items) || [];
  return `${align}|${items.map((item) => {
    const bars = item.bars || [];
    const shape = bars.map((bar) => `${bar.label || ""}:${bar.hint || ""}:${bar.left ? 1 : 0}`).join(",");
    return `${item.id}~${item.status}~${item.name || ""}~${item.detail || ""}~${shape}`;
  }).join(";")}`;
}

function paintUsage(state) {
  const host = $("usage");
  const itemsHost = $("usage-items");
  if (!host || !itemsHost) return;
  const items = (state && state.items) || [];
  usageItems = items;
  host.hidden = false;
  host.classList.toggle("is-end", state && state.align === "end");
  const shape = usageShapeOf(state);
  if (shape !== usageShape) {
    usageShape = shape;
    itemsHost.replaceChildren();
    for (const item of items) itemsHost.append(usagePill(item));
  } else {
    const buttons = itemsHost.querySelectorAll(".usage-pill");
    items.forEach((item, index) => updateUsagePill(buttons[index], item));
  }
  if ($("usage-dialog").open) fillUsageDialog();
}

function usagePill(item) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "usage-pill";
  const name = document.createElement("span");
  name.className = "usage-name";
  name.textContent = item.name || "AI";
  button.append(name);
  if (item.status !== "ready" || !(item.bars || []).length) {
    const note = document.createElement("span");
    note.className = "usage-note";
    note.textContent = item.detail || "사용량을 못 읽었습니다";
    button.append(note);
  } else {
    for (const bar of item.bars) button.append(usageLine(bar));
  }
  return button;
}

function updateUsagePill(button, item) {
  if (!button) return;
  const lines = button.querySelectorAll(".usage-line");
  (item.bars || []).forEach((bar, index) => {
    const line = lines[index];
    if (!line) return;
    const amounts = usageAmounts(bar);
    const fill = line.querySelector("i span");
    fill.style.width = `${amounts.shown}%`;
    fill.style.background = usageColor(amounts.used);
    line.querySelector("b").textContent = `${amounts.shown}%`;
    const left = line.querySelector("em");
    if (left) left.textContent = bar.left || "";
  });
}

let usageSetup = { align: "start", accounts: [] };

function setUsageAlign(align) {
  usageSetup.align = align;
  paintUsageSetup();
}

async function openUsageSetup(event) {
  if (event) event.stopPropagation();
  $("usage-setup-error").textContent = "";
  $("usage-custom-name").value = "";
  $("usage-custom-session").value = "";
  $("usage-custom-week").value = "";
  const data = await window.pywebview.api.usage_settings();
  usageSetup = {
    align: data && data.align === "end" ? "end" : "start",
    accounts: (data && data.accounts) || [],
  };
  paintUsageSetup();
  if (!$("usage-setup-dialog").open) $("usage-setup-dialog").showModal();
}

function quotaPreview(account) {
  const bars = account.bars || [];
  if (!bars.length) return account.meter ? "이 PC" : "";
  return bars.map((bar) => {
    const amounts = usageAmounts(bar);
    const name = bar.label || account.name;
    return `${name} ${amounts.shown}%${bar.left && bar.left !== "--" ? ` ${bar.left}` : ""}`;
  }).join(" · ");
}

function paintUsageSetup() {
  $("usage-align-start").classList.toggle("primary", usageSetup.align !== "end");
  $("usage-align-end").classList.toggle("primary", usageSetup.align === "end");
  const list = $("usage-accounts");
  list.replaceChildren();
  const groups = [
    ["이 PC", (account) => account.meter],
    ["사용량", (account) => !account.meter && !account.custom],
    ["직접 입력", (account) => account.custom],
  ];
  for (const [title, match] of groups) {
    const rows = [];
    usageSetup.accounts.forEach((account, index) => {
      if (match(account)) rows.push([account, index]);
    });
    if (!rows.length) continue;
    const group = document.createElement("section");
    group.className = "quota-group";
    const heading = document.createElement("h3");
    heading.textContent = title;
    group.append(heading);
    for (const [account, index] of rows) group.append(usageAccountRow(account, index));
    list.append(group);
  }
}

function usageAccountRow(account, index) {
  const row = document.createElement("div");
  row.className = "quota-row";
  const box = document.createElement("input");
  box.type = "checkbox";
  box.checked = account.enabled !== false;
  box.addEventListener("change", () => {
    account.enabled = box.checked;
  });
  const mark = document.createElement("span");
  mark.className = "quota-mark";
  mark.textContent = (account.name || "?").slice(0, 1);
  const copy = document.createElement("div");
  copy.className = "quota-copy";
  const name = document.createElement("strong");
  name.textContent = account.name;
  const mini = document.createElement("div");
  mini.className = "quota-mini";
  mini.textContent = quotaPreview(account);
  copy.append(name, mini);
  const buttons = document.createElement("div");
  buttons.className = "quota-move";
  for (const [caption, delta] of [["앞", -1], ["뒤", 1]]) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = caption;
    button.addEventListener("click", () => moveUsageAccount(index, delta));
    buttons.append(button);
  }
  if (account.custom) {
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "삭제";
    remove.addEventListener("click", () => {
      usageSetup.accounts.splice(index, 1);
      paintUsageSetup();
    });
    buttons.append(remove);
  }
  row.append(box, mark, copy, buttons);
  return row;
}

function remainingToUsed(text) {
  if (!text) return null;
  const remaining = Number(text);
  if (!Number.isFinite(remaining)) return null;
  return 100 - Math.max(0, Math.min(100, Math.round(remaining)));
}

function moveUsageAccount(index, delta) {
  const next = index + delta;
  if (next < 0 || next >= usageSetup.accounts.length) return;
  const [item] = usageSetup.accounts.splice(index, 1);
  usageSetup.accounts.splice(next, 0, item);
  paintUsageSetup();
}

async function saveUsageSetup(event) {
  event.preventDefault();
  $("usage-setup-error").textContent = "";
  const name = $("usage-custom-name").value.trim();
  const session = $("usage-custom-session").value.trim();
  const week = $("usage-custom-week").value.trim();
  if (name || session || week) {
    const sessionUsed = remainingToUsed(session);
    const weekUsed = remainingToUsed(week);
    if (!name || (sessionUsed === null && weekUsed === null)) {
      $("usage-setup-error").textContent = "이름과 남은 퍼센트 하나를 입력하세요.";
      return;
    }
    usageSetup.accounts.push({
      id: "",
      name,
      enabled: true,
      custom: true,
      session: sessionUsed,
      week: weekUsed,
      sessionLeft: "",
      weekLeft: "",
    });
  }
  const result = await window.pywebview.api.save_usage({
    align: usageSetup.align,
    order: usageSetup.accounts.map((item) => item.id),
    hidden: usageSetup.accounts.filter((item) => !item.meter && item.enabled === false).map((item) => item.id),
    meters: usageSetup.accounts.filter((item) => item.meter && item.enabled !== false).map((item) => item.id),
    custom: usageSetup.accounts.filter((item) => item.custom).map((item) => ({
      id: item.id,
      name: item.name,
      session: item.session,
      week: item.week,
      sessionLeft: item.sessionLeft || "",
      weekLeft: item.weekLeft || "",
    })),
  });
  paintUsage(result);
  $("usage-setup-dialog").close();
}

function fillUsageDialog() {
  const list = $("usage-list");
  list.replaceChildren();
  for (const item of usageItems) {
    const row = document.createElement("div");
    row.className = "phone-row";
    const text = document.createElement("p");
    const title = document.createElement("strong");
    title.textContent = item.name || "AI";
    text.append(title);
    const bars = item.bars || [];
    if (!bars.length) {
      text.append(document.createElement("br"), document.createTextNode(item.detail || "사용량을 못 읽었습니다"));
    }
    for (const bar of bars) {
      const amounts = usageAmounts(bar);
      const caption = bar.label ? `${bar.hint ? `${bar.label} ${bar.hint}` : bar.label} 남음` : (item.name || "");
      const extra = bar.left ? ` · ${bar.left}` : "";
      text.append(document.createElement("br"), document.createTextNode(`${caption} ${amounts.shown}%${extra}`));
    }
    for (const line of item.lines || []) {
      const who = document.createElement("span");
      who.className = "who";
      who.textContent = line;
      text.append(document.createElement("br"), who);
    }
    if (item.detail && bars.length) {
      const who = document.createElement("span");
      who.className = "who";
      who.textContent = item.detail;
      text.append(document.createElement("br"), who);
    }
    row.append(text);
    list.append(row);
  }
}

function openUsage() {
  if (!usageItems.length) return;
  fillUsageDialog();
  $("usage-dialog").showModal();
}

async function refreshUsage() {
  try {
    paintUsage(await window.pywebview.api.usage_state());
  } catch (error) {
    paintUsage({ items: [] });
  }
}

let categoryDraft = [];

function openCategories() {
  categoryDraft = state.categories.map((item) => ({ id: item.id, title: item.title }));
  $("category-name").value = "";
  $("category-error").textContent = "";
  paintCategories();
  if (!$("categories").open) $("categories").showModal();
}

function paintCategories() {
  const list = $("category-list");
  list.replaceChildren();
  categoryDraft.forEach((item, index) => {
    const row = document.createElement("div");
    row.className = "quota-row";
    const input = document.createElement("input");
    input.value = item.title;
    input.maxLength = 24;
    input.addEventListener("input", () => {
      item.title = input.value;
    });
    const buttons = document.createElement("div");
    buttons.className = "quota-move";
    for (const [caption, delta] of [["앞", -1], ["뒤", 1]]) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = caption;
      button.addEventListener("click", () => moveCategory(index, delta));
      buttons.append(button);
    }
    if (categoryDraft.length > 1) {
      const remove = document.createElement("button");
      remove.type = "button";
      remove.textContent = "삭제";
      remove.addEventListener("click", () => {
        categoryDraft.splice(index, 1);
        paintCategories();
      });
      buttons.append(remove);
    }
    row.append(input, buttons);
    list.append(row);
  });
}

function moveCategory(index, delta) {
  const next = index + delta;
  if (next < 0 || next >= categoryDraft.length) return;
  const [item] = categoryDraft.splice(index, 1);
  categoryDraft.splice(next, 0, item);
  paintCategories();
}

async function saveCategories(event) {
  event.preventDefault();
  $("category-error").textContent = "";
  const items = categoryDraft.map((item) => ({ id: item.id, title: item.title.trim() }));
  const name = $("category-name").value.trim();
  if (name) items.push({ id: "", title: name });
  const data = await window.pywebview.api.save_categories({ categories: items });
  if (!data || !data.ok) {
    $("category-error").textContent = (data && data.error) || "저장하지 못했습니다.";
    return;
  }
  $("categories").close();
  apply(data);
}

async function openPhone() {
  $("phone").showModal();
  await paintPhone();
  clearInterval(phoneTimer);
  phoneTimer = setInterval(() => {
    if (!$("phone").open) {
      clearInterval(phoneTimer);
      return;
    }
    paintPhone();
  }, 2000);
}

async function paintPhone() {
  const state = await window.pywebview.api.phone_state();
  const status = $("phone-status");
  const list = $("phone-list");
  list.replaceChildren();
  if (!state) return;
  if (state.error) status.textContent = state.error;
  else if (!state.listening) status.textContent = "폰을 찾는 중입니다.";
  else if ((state.paired || []).some((item) => item.online)) status.textContent = "폰이 연결되어 있습니다.";
  else status.textContent = "같은 네트워크에서 폰을 찾고 있습니다.";
  for (const item of state.pending || []) {
    list.append(phoneRow(item.name, "연결 요청", [
      ["수락", () => actPhone("phone_accept", item.id)],
      ["거절", () => actPhone("phone_reject", item.id)],
    ]));
  }
  for (const item of state.paired || []) {
    list.append(phoneRow(item.name, item.online ? "연결됨" : "같은 네트워크에 없음", [
      ["끊기", () => actPhone("phone_reject", item.id)],
    ]));
  }
  for (const item of state.nearby || []) {
    list.append(phoneRow(item.name, "발견됨", [
      ["연결", () => actPhone("phone_pair", item.id)],
    ]));
  }
}

function phoneRow(name, detail, actions) {
  const row = document.createElement("div");
  row.className = "phone-row";
  const text = document.createElement("p");
  const who = document.createElement("span");
  who.className = "who";
  who.textContent = detail;
  text.append(document.createTextNode(name), document.createElement("br"), who);
  const buttons = document.createElement("div");
  for (const [label, run] of actions) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.addEventListener("click", run);
    buttons.append(button);
  }
  row.append(text, buttons);
  return row;
}

async function actPhone(method, id) {
  try {
    await window.pywebview.api[method](id);
  } catch (error) {
    toast("폰 연결을 시작하지 못했습니다.", true);
    return;
  }
  await paintPhone();
}

async function openPageDialog() {
  const state = await window.pywebview.api.page_state();
  $("page-url").value = (state && state.url) || "";
  $("page-auto").checked = Boolean(state && state.openWithDesk);
  $("page-error").textContent = "";
  if (!$("page").open) $("page").showModal();
}

async function savePage(event) {
  event.preventDefault();
  $("page-error").textContent = "";
  const result = await window.pywebview.api.save_page($("page-url").value, $("page-auto").checked);
  if (!result.ok) {
    $("page-error").textContent = result.error || "페이지를 열지 못했습니다.";
    return;
  }
  $("page").close();
  enterPage();
}

async function addProgram(item, button) {
  const data = await window.pywebview.api.save_app({
    id: "",
    name: item.name.slice(0, 40),
    categoryId: $("program-category").value,
    target: item.path,
    favorite: true,
  });
  if (!data.ok) {
    toast(data.error || "추가하지 못했습니다.", true);
    return;
  }
  item.added = true;
  button.textContent = "추가됨";
  button.disabled = true;
  apply(data);
  toast(`${item.name} 추가`);
}
