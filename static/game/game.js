(() => {
  const lobbyId = window.GAME.lobbyId;

  function getCookie(name) {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length === 2) return parts.pop().split(";").shift();
    return null;
  }
  const csrfToken = getCookie("csrftoken");

  const canvas = document.getElementById("gameCanvas");
  const ctx = canvas.getContext("2d");

  const slotsEl = document.getElementById("slots");
  const statusEl = document.getElementById("status");
  const daySummaryEl = document.getElementById("daySummary");

  const joinBtn = document.getElementById("joinBtn");
  const startBtn = document.getElementById("startBtn");
  const buyEnergyBtn = document.getElementById("buyEnergyBtn");
  const buyVisionBtn = document.getElementById("buyVisionBtn");
  const nameInput = document.getElementById("name");

  const TEAM_COLORS = {
    1: "#3b82f6",
    2: "#ef4444",
    3: "#22c55e",
    4: "#facc15",
  };

  const foodImg = new Image();
  foodImg.src = `/static/game/food.svg?v=${Date.now()}`;

  const creatureImg = new Image();
  creatureImg.src = `/static/game/creature.svg?v=${Date.now()}`;

  let currentState = null;
  const payloadCache = new Map();

  let playingDay = null;
  let playing = false;
  let rafId = null;

  let winnerLatched = null;

  function isoToMs(iso) {
    if (!iso) return null;
    const t = Date.parse(iso);
    return Number.isFinite(t) ? t : null;
  }

  async function post(url, formObj = {}) {
    const body = new URLSearchParams();
    for (const [k, v] of Object.entries(formObj)) body.append(k, v);

    const res = await fetch(url, {
      method: "POST",
      headers: {
        "X-CSRFToken": csrfToken,
        "Content-Type": "application/x-www-form-urlencoded",
      },
      body: body.toString(),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Error");
    return data;
  }

  async function get(url) {
    const res = await fetch(url);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Error");
    return data;
  }

  function clear() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = "#07070a";
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    ctx.globalAlpha = 0.08;
    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth = 1;
    const step = 60;
    for (let x = 0; x <= canvas.width; x += step) {
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, canvas.height);
      ctx.stroke();
    }
    for (let y = 0; y <= canvas.height; y += step) {
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(canvas.width, y);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;
  }

  function renderSlots(state) {
    slotsEl.innerHTML = "";
    for (const s of state.slots) {
      const color = TEAM_COLORS[s.slot_index] || "#aaa";
      const div = document.createElement("div");
      div.className = "slot";
      div.innerHTML = `
        <div>
          <span class="dot" style="background:${color}"></span>
          <b style="color:${color}">${s.display_name}</b>
          ${s.is_bot ? '<span class="muted">(BOT)</span>' : ""}
        </div>
        <div class="muted">💰 ${s.coins} — vivos: <b>${s.alive}</b></div>
        <div class="muted">
          Promedios:
          size <b>${s.avg_size}</b> |
          speed <b>${s.avg_speed}</b> |
          danger <b>${s.avg_danger}</b>
        </div>
        <div class="muted">
          energy <b>${s.avg_energy}</b> |
          vision <b>${s.avg_vision}</b>
        </div>
      `;
      slotsEl.appendChild(div);
    }
  }

  function drawBases(slots) {
    for (const s of slots) {
      const bx = Number(s.base_x || 0);
      const by = Number(s.base_y || 0);
      const color = TEAM_COLORS[s.slot_index] || "#888";

      ctx.beginPath();
      ctx.arc(bx, by, 34, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(255,255,255,0.03)";
      ctx.fill();

      ctx.beginPath();
      ctx.arc(bx, by, 26, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(0,0,0,0.35)";
      ctx.fill();
      ctx.strokeStyle = color;
      ctx.lineWidth = 4;
      ctx.stroke();

      ctx.fillStyle = color;
      ctx.font = "bold 13px system-ui";
      ctx.fillText(`${s.display_name}`, bx - 28, by - 32);
    }
  }

  function drawFood(x, y) {
    if (!foodImg.complete || foodImg.naturalWidth === 0) {
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(x - 6, y - 6, 12, 12);
      return;
    }

    ctx.drawImage(foodImg, x - 10, y - 10, 20, 20);

    ctx.globalCompositeOperation = "source-atop";
    ctx.globalAlpha = 0.95;
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(x - 10, y - 10, 20, 20);
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = "source-over";
  }

  function drawCreature(x, y, size, energy, energyMax, team, carriedFood) {
    const s = Math.max(0.55, Math.min(2.3, Number(size ?? 1.0)));
    const w = 30 * s;
    const h = 30 * s;

    if (!creatureImg.complete || creatureImg.naturalWidth === 0) {
      ctx.beginPath();
      ctx.arc(x, y, 10 * s, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(255,255,255,0.15)";
      ctx.fill();
    } else {
      ctx.drawImage(creatureImg, x - w / 2, y - h / 2, w, h);
    }

    const tint = TEAM_COLORS[team] || "#aaa";
    ctx.globalCompositeOperation = "source-atop";
    ctx.globalAlpha = 0.5;
    ctx.fillStyle = tint;
    ctx.fillRect(x - w / 2, y - h / 2, w, h);
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = "source-over";

    const e = Math.max(0, Math.round(Number(energy ?? 0)));
    const em = Math.max(1, Math.round(Number(energyMax ?? 100)));
    const ratio = e / em;

    ctx.fillStyle = ratio > 0.45 ? "#4ade80" : "#ef4444";
    ctx.font = "11px system-ui";
    ctx.fillText(`E:${e}`, x + 12, y + 16);

    if (Number(carriedFood || 0) > 0) {
      ctx.fillStyle = "#e5e7eb";
      ctx.font = "11px system-ui";
      ctx.fillText(`🍃${carriedFood}`, x + 12, y - 12);
    }
  }

  function clampIndex(i, n) {
    if (n <= 0) return 0;
    if (i < 0) return 0;
    if (i > n - 1) return n - 1;
    return i;
  }

  function buildIndexed(frames) {
    return frames.map((f) => {
      const cm = new Map();
      const list = Array.isArray(f?.creatures) ? f.creatures : [];
      for (const c of list) if (c && c.id != null) cm.set(c.id, c);
      const foods = Array.isArray(f?.foods) ? f.foods : [];
      return { creatures: cm, foods };
    });
  }

  function stopPlayback() {
    playing = false;
    playingDay = null;
    if (rafId) cancelAnimationFrame(rafId);
    rafId = null;
  }

  function drawWinnerOverlay(winner) {
    if (!winner) return;
    const color = TEAM_COLORS[winner.slot] || "#ffffff";

    ctx.save();
    ctx.fillStyle = "rgba(0,0,0,0.65)";
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    ctx.textAlign = "center";
    ctx.fillStyle = "#ffffff";
    ctx.font = "bold 52px system-ui";
    ctx.fillText("GANADOR", canvas.width / 2, canvas.height / 2 - 40);

    ctx.fillStyle = color;
    ctx.font = "bold 36px system-ui";
    ctx.fillText(`${winner.name} (Equipo ${winner.slot})`, canvas.width / 2, canvas.height / 2 + 10);

    ctx.fillStyle = "#d1d5db";
    ctx.font = "18px system-ui";
    ctx.fillText(`Criaturas vivas: ${winner.alive ?? 0}`, canvas.width / 2, canvas.height / 2 + 45);

    ctx.restore();
  }

  let clientNowAtFetchMs = Date.now();

  function playDaySynced(payload, slots, serverNowMs, phaseStartMs) {
    if (!payload || !Array.isArray(payload.frames) || payload.frames.length === 0) return;

    const durationMs = Number(payload.duration_ms || 1);
    const indexed = buildIndexed(payload.frames);
    const n = indexed.length;

    playing = true;

    function step() {
      if (!playing) return;

      const nowMs = Date.now();
      const approxServerMs = serverNowMs + (nowMs - clientNowAtFetchMs);
      const t = Math.max(0, Math.min(durationMs, approxServerMs - phaseStartMs));
      const p = durationMs <= 1 ? 1 : (t / durationMs);

      const idxFloat = (n === 1) ? 0 : p * (n - 1);
      const i0 = clampIndex(Math.floor(idxFloat), n);
      const i1 = clampIndex(i0 + 1, n);
      const a = (n === 1) ? 0 : (idxFloat - i0);

      const f0 = indexed[i0];
      const f1 = indexed[i1];
      if (!f0 || !f1) {
        stopPlayback();
        return;
      }

      clear();
      drawBases(slots);

      const foods = a < 0.5 ? f0.foods : f1.foods;
      for (const food of foods) if (food && food.active) drawFood(food.x, food.y);

      for (const [id, c0] of f0.creatures.entries()) {
        const c1 = f1.creatures.get(id) || c0;
        if (!c0 && !c1) continue;
        if (!c0.alive && !c1.alive) continue;

        const x0 = Number(c0.x ?? 0), y0 = Number(c0.y ?? 0);
        const x1 = Number(c1.x ?? x0), y1 = Number(c1.y ?? y0);
        const e0 = Number(c0.energy ?? 0), e1 = Number(c1.energy ?? e0);

        const x = x0 + (x1 - x0) * a;
        const y = y0 + (y1 - y0) * a;
        const e = e0 + (e1 - e0) * a;

        drawCreature(x, y, c0.size, e, c0.energy_max, c0.owner, c0.carried_food);
      }

      rafId = requestAnimationFrame(step);
    }

    rafId = requestAnimationFrame(step);
  }

  async function fetchPayloadIfNeeded(day) {
    if (payloadCache.has(day)) return payloadCache.get(day);
    const data = await get(`/api/lobby/${lobbyId}/day/${day}/payload/`);
    const payload = data.payload || null;
    payloadCache.set(day, payload);
    return payload;
  }

  function setButtonsByPhase(phase, status) {
    const inPause = phase === "PAUSE" && status === "RUNNING";
    buyEnergyBtn.disabled = !inPause;
    buyVisionBtn.disabled = !inPause;
  }

  function applyShopLabels(shop) {
    if (!shop) return;
    buyEnergyBtn.textContent = `Comprar energía (+${shop.energy_add}) — 💰 ${shop.energy_cost}`;
    buyVisionBtn.textContent = `Comprar visión (+${shop.vision_add}) — 💰 ${shop.vision_cost}`;
  }

  async function tick() {
    const st = await get(`/api/lobby/${lobbyId}/state/`);
    currentState = st;

    if (canvas.width !== st.lobby.map_w) canvas.width = st.lobby.map_w;
    if (canvas.height !== st.lobby.map_h) canvas.height = st.lobby.map_h;

    applyShopLabels(st.shop);
    renderSlots(st);

    const serverNowMs = isoToMs(st.server_time) || Date.now();
    const phaseStartMs = isoToMs(st.lobby.phase_started_at);
    const phaseEndMs = isoToMs(st.lobby.phase_end_at);

    const phase = st.lobby.phase;
    const status = st.lobby.status;

    setButtonsByPhase(phase, status);

    if (phaseEndMs) {
      const left = Math.max(0, Math.ceil((phaseEndMs - serverNowMs) / 1000));
      daySummaryEl.textContent = phase === "PAUSE"
        ? `PAUSA: compra mejoras — siguiente día en ${left}s`
        : `DÍA en curso — termina en ${left}s`;
    } else {
      daySummaryEl.textContent = `Fase: ${phase}`;
    }

    statusEl.innerHTML =
      `Estado: <b>${status}</b> — Día: <b>${st.lobby.day}/${st.lobby.max_days}</b> — comida/turno: <b>${st.lobby.food_per_day}</b> — fase: <b>${phase}</b>`;

    if (status === "FINISHED") {
      if (!winnerLatched && st.winner) winnerLatched = st.winner;

      if (playing) stopPlayback();
      clear();
      drawBases(st.slots);
      drawWinnerOverlay(winnerLatched);

      buyEnergyBtn.disabled = true;
      buyVisionBtn.disabled = true;
      return;
    }

    const payloadDay = st.lobby.current_payload_day;

    if (status === "RUNNING" && payloadDay > 0 && phase === "DAY") {
      if (playingDay !== payloadDay) {
        stopPlayback();
        playingDay = payloadDay;
        clientNowAtFetchMs = Date.now();

        const payload = await fetchPayloadIfNeeded(payloadDay);
        if (payload && phaseStartMs != null) {
          playDaySynced(payload, st.slots, serverNowMs, phaseStartMs);
        }
      }
    } else {
      if (playing) stopPlayback();
      clear();
      drawBases(st.slots);
      ctx.fillStyle = "#cfcfcf";
      ctx.font = "14px system-ui";
      ctx.fillText(
        phase === "PAUSE" ? "Pausa: compra mejoras." : "Esperando / No iniciado.",
        20, 28
      );
    }
  }

  joinBtn.onclick = async () => {
    const name = nameInput.value.trim() || "Player";
    await post(`/api/lobby/${lobbyId}/join/`, { name });
    await tick();
  };

  startBtn.onclick = async () => {
    await post(`/api/lobby/${lobbyId}/start/`, {});
    payloadCache.clear();
    winnerLatched = null;
    stopPlayback();
    await tick();
  };

  buyEnergyBtn.onclick = async () => {
    await post(`/api/lobby/${lobbyId}/buy/`, { kind: "energy" });
    await tick();
  };

  buyVisionBtn.onclick = async () => {
    await post(`/api/lobby/${lobbyId}/buy/`, { kind: "vision" });
    await tick();
  };

  tick().catch(() => {});
  setInterval(() => tick().catch(() => {}), 1000);
})();
