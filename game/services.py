import math
import random
from typing import Dict, List, Tuple

from django.db import transaction
from django.db.models import Avg
from django.utils import timezone

from .models import Lobby, LobbyStatus, LobbyPhase, PlayerSlot, Creature, Food, DayLog

# =========================
# Tiempo real / simulación
# =========================
FRAME_MS = 200  # cada “tick” ~200ms
MAX_FRAMES_GUARD = 600  # guardia por si algo se buguea (máx 2 minutos)

# =========================
# Juego
# =========================
PICKUP_RADIUS = 14.0
BASE_RADIUS = 24.0
EAT_RADIUS = 14.0

BASE_ENERGY = 100
BASE_VISION = 60  # bajado
ENERGY_K = 5.2
MOVE_STEP_BASE = 56.0

UP_ENERGY_ADD = 25
UP_VISION_ADD = 15
UP_ENERGY_COST = 30
UP_VISION_COST = 30
MAX_UPGRADES = 8

# Colores “lógicos” (front los fuerza fijo, pero dejamos igual)
COLORS = ["#3b82f6", "#ef4444", "#22c55e", "#facc15"]


def _clamp(v: float, a: float, b: float) -> float:
    return max(a, min(b, v))


def _dist(ax: float, ay: float, bx: float, by: float) -> float:
    return math.hypot(ax - bx, ay - by)


def _rand_pos(lobby: Lobby) -> Tuple[float, float]:
    return (
        random.uniform(30, lobby.map_w - 30),
        random.uniform(30, lobby.map_h - 30),
    )


def ensure_four_slots(lobby: Lobby) -> List[PlayerSlot]:
    ox = min(300, lobby.map_w * 0.35)
    oy = min(300, lobby.map_h * 0.35)

    bases = [
        (ox, oy),
        (lobby.map_w - ox, oy),
        (ox, lobby.map_h - oy),
        (lobby.map_w - ox, lobby.map_h - oy),
    ]

    slots = []
    for i in range(1, 5):
        slot, _ = PlayerSlot.objects.get_or_create(lobby=lobby, slot_index=i)
        slot.base_x, slot.base_y = bases[i - 1]
        slot.color_hex = COLORS[i - 1]
        slot.save()
        slots.append(slot)
    return slots


def fill_missing_with_bots(lobby: Lobby):
    for slot in lobby.slots.all():
        if not slot.session_key:
            slot.is_bot = True
            slot.display_name = f"Bot {slot.slot_index}"
            slot.session_key = f"BOT-{lobby.id}-{slot.slot_index}"
            slot.save()


def spawn_initial_creatures(lobby: Lobby):
    for slot in lobby.slots.all():
        for _ in range(10):
            x = _clamp(slot.base_x + random.uniform(-70, 70), 20, lobby.map_w - 20)
            y = _clamp(slot.base_y + random.uniform(-70, 70), 20, lobby.map_h - 20)

            energy_max = BASE_ENERGY + slot.energy_bonus
            vision = BASE_VISION + slot.vision_bonus

            Creature.objects.create(
                lobby=lobby,
                owner=slot,
                alive=True,
                size=1.0,
                speed=1.0,
                danger=0.5,
                energy_max=energy_max,
                energy=energy_max,
                vision=vision,
                x=x,
                y=y,
                carried_food=0,
                created_day=lobby.day,
            )


def spawn_food(lobby: Lobby):
    lobby.foods.all().delete()
    Food.objects.bulk_create([
        Food(lobby=lobby, x=_rand_pos(lobby)[0], y=_rand_pos(lobby)[1], active=True)
        for _ in range(lobby.food_per_day)
    ])


def _move_towards(x: float, y: float, tx: float, ty: float, step: float) -> Tuple[float, float]:
    d = _dist(x, y, tx, ty)
    if d <= 1e-6:
        return x, y
    if d <= step:
        return tx, ty
    ux, uy = (tx - x) / d, (ty - y) / d
    return x + ux * step, y + uy * step


def _all_exhausted(creatures: List[Creature]) -> bool:
    for c in creatures:
        if c.alive and c.energy > 0:
            return False
    return True


def simulate_until_exhausted(lobby: Lobby) -> Dict:
    creatures = list(Creature.objects.filter(lobby=lobby, alive=True).select_related("owner"))
    foods = list(Food.objects.filter(lobby=lobby, active=True))

    returning: Dict[int, bool] = {c.id: False for c in creatures}
    frames = []

    for _ in range(MAX_FRAMES_GUARD):
        # stop cuando todas sin energía (o muertas)
        if _all_exhausted(creatures):
            break

        # 1) movimiento + energía
        for c in creatures:
            if not c.alive:
                continue
            if c.energy <= 0:
                c.energy = 0.0
                continue

            bx, by = c.owner.base_x, c.owner.base_y
            thr = c.danger * c.energy_max

            must_return = returning.get(c.id, False)

            if c.carried_food >= 2:
                must_return = True
                returning[c.id] = True
            elif c.carried_food >= 1 and c.energy <= thr:
                must_return = True
                returning[c.id] = True

            if must_return:
                target = (bx, by)
            else:
                # comida más cercana dentro de visión
                best = None
                best_d = 10**9
                for f in foods:
                    if not f.active:
                        continue
                    d = _dist(c.x, c.y, f.x, f.y)
                    if d <= c.vision and d < best_d:
                        best = f
                        best_d = d
                if best:
                    target = (best.x, best.y)
                else:
                    target = (
                        _clamp(c.x + random.uniform(-260, 260), 10, lobby.map_w - 10),
                        _clamp(c.y + random.uniform(-260, 260), 10, lobby.map_h - 10),
                    )

            step = MOVE_STEP_BASE * c.speed
            c.x, c.y = _move_towards(c.x, c.y, target[0], target[1], step)

            cost = ENERGY_K * ((c.size ** 3) * (c.speed ** 2))
            c.energy = max(0.0, c.energy - cost)

        # 2) pickup comida
        for c in creatures:
            if not c.alive or c.energy <= 0:
                continue
            if c.carried_food >= 2:
                continue
            for f in foods:
                if not f.active:
                    continue
                if _dist(c.x, c.y, f.x, f.y) <= PICKUP_RADIUS:
                    f.active = False
                    c.carried_food += 1
                    thr = c.danger * c.energy_max
                    if c.carried_food >= 2 or (c.carried_food >= 1 and c.energy <= thr):
                        returning[c.id] = True
                    break

        # 3) depredación por tamaño (15% más grande)
        for i in range(len(creatures)):
            a = creatures[i]
            if not a.alive or a.energy <= 0:
                continue
            for j in range(i + 1, len(creatures)):
                b = creatures[j]
                if not b.alive or b.energy <= 0:
                    continue
                if _dist(a.x, a.y, b.x, b.y) > EAT_RADIUS:
                    continue
                if a.size >= 1.15 * b.size:
                    b.alive = False
                    if a.carried_food < 2:
                        a.carried_food += 1
                        thr = a.danger * a.energy_max
                        if a.carried_food >= 2 or (a.carried_food >= 1 and a.energy <= thr):
                            returning[a.id] = True
                elif b.size >= 1.15 * a.size:
                    a.alive = False
                    if b.carried_food < 2:
                        b.carried_food += 1
                        thr = b.danger * b.energy_max
                        if b.carried_food >= 2 or (b.carried_food >= 1 and b.energy <= thr):
                            returning[b.id] = True

        frames.append({
            "creatures": [
                {
                    "id": c.id,
                    "x": round(c.x, 2),
                    "y": round(c.y, 2),
                    "alive": bool(c.alive),
                    "owner": c.owner.slot_index,
                    "carried_food": int(c.carried_food),
                    "energy": round(float(c.energy), 1),
                    "energy_max": round(float(c.energy_max), 1),
                    "size": round(float(c.size), 3),
                }
                for c in creatures
            ],
            "foods": [
                {"id": f.id, "x": round(f.x, 2), "y": round(f.y, 2), "active": bool(f.active)}
                for f in foods
            ],
        })

    # fin del “día”: sobreviven/reproducen SOLO si llegan a base con comida >=1
    births = 0
    deaths = 0
    new_creatures = []

    for c in creatures:
        if not c.alive:
            deaths += 1
            continue

        bx, by = c.owner.base_x, c.owner.base_y
        in_base = _dist(c.x, c.y, bx, by) <= BASE_RADIUS

        if not in_base:
            c.alive = False
            deaths += 1
            continue

        if c.carried_food <= 0:
            c.alive = False
            deaths += 1
            continue

        energy_max = BASE_ENERGY + c.owner.energy_bonus
        vision = BASE_VISION + c.owner.vision_bonus

        if c.carried_food >= 2:
            births += 1

            def mut15(v, lo=0.5, hi=2.3):
                return _clamp(v * (1.0 + random.uniform(-0.15, 0.15)), lo, hi)

            child_size = mut15(c.size)
            child_speed = mut15(c.speed)
            child_danger = _clamp(c.danger * (1.0 + random.uniform(-0.15, 0.15)), 0.1, 0.9)

            x = _clamp(bx + random.uniform(-20, 20), 10, lobby.map_w - 10)
            y = _clamp(by + random.uniform(-20, 20), 10, lobby.map_h - 10)

            new_creatures.append(Creature(
                lobby=lobby,
                owner=c.owner,
                alive=True,
                size=child_size,
                speed=child_speed,
                danger=child_danger,
                energy_max=energy_max,
                energy=energy_max,
                vision=vision,
                x=x,
                y=y,
                carried_food=0,
                created_day=lobby.day + 1,
            ))

        # padre sobrevive
        c.energy_max = energy_max
        c.energy = energy_max
        c.vision = vision
        c.carried_food = 0

    for c in creatures:
        Creature.objects.filter(id=c.id).update(
            alive=c.alive, x=c.x, y=c.y,
            size=c.size, speed=c.speed, danger=c.danger,
            energy_max=c.energy_max, energy=c.energy, vision=c.vision,
            carried_food=c.carried_food,
        )

    if new_creatures:
        Creature.objects.bulk_create(new_creatures)

    # monedas + promedios
    summary_players = []
    for slot in lobby.slots.all():
        alive_qs = Creature.objects.filter(lobby=lobby, owner=slot, alive=True)
        alive_count = alive_qs.count()
        agg = alive_qs.aggregate(avg_size=Avg("size"), avg_speed=Avg("speed"), avg_danger=Avg("danger"))

        slot.coins += alive_count
        slot.save(update_fields=["coins"])

        summary_players.append({
            "slot": slot.slot_index,
            "name": slot.display_name,
            "coins": slot.coins,
            "alive": alive_count,
            "is_bot": slot.is_bot,
            "avg_size": round(float(agg["avg_size"] or 0.0), 2),
            "avg_speed": round(float(agg["avg_speed"] or 0.0), 2),
            "avg_danger": round(float(agg["avg_danger"] or 0.0), 2),
        })

    # winner check
    living_species = sum(
        1 for slot in lobby.slots.all()
        if Creature.objects.filter(lobby=lobby, owner=slot, alive=True).exists()
    )
    finished = False
    winner_slot = None
    if lobby.day + 1 >= lobby.max_days or living_species <= 1:
        finished = True
        best = (-1, None)
        for slot in lobby.slots.all():
            cnt = Creature.objects.filter(lobby=lobby, owner=slot, alive=True).count()
            if cnt > best[0]:
                best = (cnt, slot.slot_index)
        winner_slot = best[1]

    duration_ms = max(1, len(frames) * FRAME_MS)

    return {
        "frame_ms": FRAME_MS,
        "duration_ms": duration_ms,
        "frames": frames,
        "summary": {
            "day_completed": lobby.day + 1,
            "births": births,
            "deaths": deaths,
            "players": summary_players,
            "finished": finished,
            "winner_slot": winner_slot,
        },
    }


@transaction.atomic
def start_game(lobby: Lobby):
    if lobby.status != LobbyStatus.WAITING:
        return

    ensure_four_slots(lobby)
    fill_missing_with_bots(lobby)

    lobby.day = 0
    lobby.status = LobbyStatus.RUNNING
    lobby.food_per_day = 50
    lobby.phase = LobbyPhase.DAY
    lobby.phase_started_at = timezone.now()
    lobby.phase_end_at = None
    lobby.current_payload_day = 0
    lobby.save()

    lobby.creatures.all().delete()
    lobby.foods.all().delete()
    lobby.day_logs.all().delete()

    spawn_initial_creatures(lobby)
    spawn_food(lobby)

    # generar primer día inmediatamente para sincronizar
    payload = simulate_until_exhausted(lobby)
    DayLog.objects.update_or_create(lobby=lobby, day=1, defaults={"payload": payload})
    lobby.current_payload_day = 1
    lobby.phase_started_at = timezone.now()
    lobby.phase_end_at = lobby.phase_started_at + timezone.timedelta(milliseconds=payload["duration_ms"])
    lobby.save(update_fields=["current_payload_day", "phase_started_at", "phase_end_at"])


@transaction.atomic
def maybe_advance_phase(lobby: Lobby):
    """
    Se llama desde api_state (polling).
    Transiciones:
      - DAY -> PAUSE cuando se acaba el tiempo del playback (según duration_ms)
      - PAUSE -> siguiente DAY automático tras 10s
    """
    if lobby.status != LobbyStatus.RUNNING:
        return

    now = timezone.now()
    if lobby.phase_end_at is None:
        return

    if now < lobby.phase_end_at:
        return

    if lobby.phase == LobbyPhase.DAY:
        # pasar a pausa 3s
        lobby.phase = LobbyPhase.PAUSE
        lobby.phase_started_at = now
        lobby.phase_end_at = now + timezone.timedelta(seconds=3)
        lobby.save(update_fields=["phase", "phase_started_at", "phase_end_at"])
        return

    if lobby.phase == LobbyPhase.PAUSE:
        # avanzar al próximo día automáticamente
        lobby.day += 1
        lobby.food_per_day = max(1, lobby.food_per_day - 1)

        # comida + sim
        spawn_food(lobby)
        payload = simulate_until_exhausted(lobby)

        next_day = lobby.current_payload_day + 1
        DayLog.objects.update_or_create(lobby=lobby, day=next_day, defaults={"payload": payload})
        lobby.current_payload_day = next_day

        lobby.phase = LobbyPhase.DAY
        lobby.phase_started_at = now
        lobby.phase_end_at = now + timezone.timedelta(milliseconds=payload["duration_ms"])

        if payload["summary"]["finished"]:
            lobby.status = LobbyStatus.FINISHED

        lobby.save(update_fields=[
            "day", "food_per_day", "current_payload_day",
            "phase", "phase_started_at", "phase_end_at", "status"
        ])


@transaction.atomic
def buy_upgrade(slot: PlayerSlot, kind: str):
    if slot.is_bot:
        return False, "Los bots no compran mejoras."
    if slot.lobby.status != LobbyStatus.RUNNING:
        return False, "La partida no está en curso."
    # solo en PAUSE
    if slot.lobby.phase != LobbyPhase.PAUSE:
        return False, "Solo puedes comprar durante la pausa."

    if kind == "energy":
        if slot.energy_bonus >= UP_ENERGY_ADD * MAX_UPGRADES:
            return False, "Máximo de mejora de energía alcanzado."
        if slot.coins < UP_ENERGY_COST:
            return False, "Monedas insuficientes."
        slot.coins -= UP_ENERGY_COST
        slot.energy_bonus += UP_ENERGY_ADD
        slot.save(update_fields=["coins", "energy_bonus"])
        return True, f"+{UP_ENERGY_ADD} energía base"

    if kind == "vision":
        if slot.vision_bonus >= UP_VISION_ADD * MAX_UPGRADES:
            return False, "Máximo de mejora de visión alcanzado."
        if slot.coins < UP_VISION_COST:
            return False, "Monedas insuficientes."
        slot.coins -= UP_VISION_COST
        slot.vision_bonus += UP_VISION_ADD
        slot.save(update_fields=["coins", "vision_bonus"])
        return True, f"+{UP_VISION_ADD} visión"

    return False, "Tipo de mejora inválido."
