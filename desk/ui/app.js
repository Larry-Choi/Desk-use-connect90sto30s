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
  $("program-query").addEventListener("input", renderPrograms);
}

function tick() {
  const now = new Date();
  $("clock").textContent = new Intl.DateTimeFormat("ko-KR", {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  }).format(now);
  $("dow").textContent = new Intl.DateTimeFormat("en-US", { weekday: "long" }).format(now).toUpperCase();
  const today = $("today");
  today.dateTime = now.toISOString();
  today.textContent = new Intl.DateTimeFormat("ko-KR", {
    year: "numeric",
    month: "long",
    day: "numeric",
    weekday: "long",
  }).format(now);
  const minutes = now.getHours() * 60 + now.getMinutes() + now.getSeconds() / 60;
  const fraction = Math.min(1, Math.max(0, minutes / (24 * 60)));
  $("day-now").textContent = `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
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
  render();
}

function render() {
  renderChips();
  renderCards();
  renderGrid();
  const counts = state.counts;
  $("stats").textContent = `등록 ${counts.registered}개 · 즐겨찾기 ${counts.favorites}개`;
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

  const handle = document.createElement("button");
  handle.type = "button";
  handle.className = "handle";
  handle.draggable = true;
  handle.textContent = "↕";
  handle.setAttribute("aria-label", `${app.name} 순서 바꾸기`);
  handle.addEventListener("dragstart", (event) => {
    event.dataTransfer.setData("text/plain", app.id);
    event.dataTransfer.effectAllowed = "move";
  });

  const launch = document.createElement("button");
  launch.type = "button";
  launch.className = "launch";
  launch.addEventListener("click", () => launchApp(app));
  const glyph = document.createElement("span");
  glyph.className = "glyph";
  glyph.setAttribute("aria-hidden", "true");
  if (app.icon) {
    const img = document.createElement("img");
    img.src = app.icon;
    img.alt = "";
    glyph.appendChild(img);
  } else {
    glyph.classList.toggle("inverse", tone(app.id));
    glyph.textContent = monogram(app.name);
  }
  const name = document.createElement("span");
  name.className = "app-name";
  name.textContent = app.name;
  launch.append(glyph, name);
  launch.setAttribute("aria-label", `${app.name} 실행`);

  const edit = document.createElement("button");
  edit.type = "button";
  edit.className = "icon-btn";
  edit.textContent = "수정";
  edit.setAttribute("aria-label", `${app.name} 수정`);
  edit.addEventListener("click", () => openEditor(app.id));

  tile.addEventListener("dragover", (event) => event.preventDefault());
  tile.addEventListener("drop", (event) => dropOn(event, app.id));
  tile.append(handle, launch, edit);
  return tile;
}

async function dropOn(event, targetId) {
  event.preventDefault();
  const sourceId = event.dataTransfer.getData("text/plain");
  if (!sourceId || sourceId === targetId) return;
  const ids = state.apps.map((app) => app.id);
  const from = ids.indexOf(sourceId);
  const to = ids.indexOf(targetId);
  if (from < 0 || to < 0) return;
  ids.splice(from, 1);
  ids.splice(to, 0, sourceId);
  apply(await window.pywebview.api.reorder_apps(ids));
}

function monogram(name) {
  const ch = Array.from(name.trim())[0] || "?";
  return /[a-z]/i.test(ch) ? ch.toUpperCase() : ch;
}

function tone(id) {
  let sum = 0;
  for (const ch of id) sum += ch.charCodeAt(0);
  return sum % 3 === 0;
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

function openEditor(appId) {
  state.editingId = appId;
  const app = state.apps.find((item) => item.id === appId);
  $("editor-title").textContent = app ? "앱 수정" : "앱 추가";
  $("app-name").value = app ? app.name : "";
  $("app-target").value = app ? app.target : "";
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
let needleLock = 0;

function buildWave() {
  const pieces = [];
  for (let index = 0; index < 56; index += 1) {
    const height = 6 + Math.round(Math.abs(Math.sin(index * 0.62) * 14 + Math.sin(index * 0.21) * 6));
    pieces.push(`<i style="height:${height}px"></i>`);
  }
  const markup = pieces.join("");
  $("wave-back").innerHTML = markup;
  $("wave-front").innerHTML = markup;
}

async function pollMedia() {
  if (!window.pywebview) return;
  applyMedia(await window.pywebview.api.media_state(), false);
}

function applyMedia(data, force) {
  if (!data) return;
  if (!force && Date.now() < needleLock) return;
  const playing = Boolean(data.playing);
  $("arm").classList.toggle("is-playing", playing);
  $("arm").setAttribute("aria-pressed", String(playing));
  $("arm").querySelector(".wand").style.transform = playing ? "rotate(163deg)" : "rotate(-38deg)";
  $("record").classList.toggle("is-spinning", playing);
  $("play-shape").setAttribute("d", playing ? "M7 5.5h3.4v13H7zM13.6 5.5H17v13h-3.4z" : "M9 6.5v11l9-5.5z");
  const label = playing ? "바늘을 받침으로 내리기" : "바늘을 판 위에 올리기";
  $("arm").setAttribute("aria-label", label);
  $("play-toggle").setAttribute("aria-label", label);
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
  const next = !$("arm").classList.contains("is-playing");
  needleLock = Date.now() + 1200;
  $("arm").classList.toggle("is-playing", next);
  $("record").classList.toggle("is-spinning", next);
  const data = await window.pywebview.api.set_needle(next);
  applyMedia(data, true);
}

async function skipTrack(delta) {
  const data = await window.pywebview.api.skip_track(delta);
  applyMedia(data, true);
}

async function openPrograms() {
  if (state.categories.length === 0) await refresh();
  fillProgramCategories();
  $("program-query").value = "";
  $("program-list").textContent = "시작 메뉴를 읽는 중";
  $("programs").showModal();
  const data = await window.pywebview.api.list_programs();
  if (!data.ok) {
    $("program-list").textContent = data.error || "시작 메뉴를 읽지 못했습니다.";
    return;
  }
  programs = data.programs || [];
  renderPrograms();
  $("program-query").focus();
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
  if (item.icon) {
    const img = document.createElement("img");
    img.src = item.icon;
    img.alt = "";
    row.appendChild(img);
  } else {
    const mark = document.createElement("span");
    mark.className = "program-mark";
    mark.textContent = monogram(item.name);
    row.appendChild(mark);
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
