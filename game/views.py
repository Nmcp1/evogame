from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.views.decorators.http import require_GET, require_POST
from django.db.models import Avg
from django.utils import timezone

from .models import Lobby, Creature, DayLog, LobbyStatus
from .services import start_game, maybe_advance_phase, buy_upgrade, ensure_four_slots

# ✅ Shop constants (igual a services.py)
UP_ENERGY_ADD = 25
UP_VISION_ADD = 30
UP_ENERGY_COST = 30
UP_VISION_COST = 30


@require_GET
def lobby_list(request):
    lobbies = list(Lobby.objects.order_by("-id")[:50])
    for l in lobbies:
        l.joined = l.slots.filter(session_key__isnull=False, is_bot=False).count()
    return render(request, "game/lobby_list.html", {"lobbies": lobbies})


@require_POST
def lobby_create(request):
    name = (request.POST.get("name") or "Lobby").strip()[:64]
    lobby = Lobby.objects.create(name=name)
    return redirect("lobby_room", lobby_id=lobby.id)


@require_GET
def lobby_room(request, lobby_id: int):
    lobby = get_object_or_404(Lobby, id=lobby_id)
    if not request.session.session_key:
        request.session.save()
    return render(request, "game/lobby_room.html", {"lobby": lobby})


def _compute_winner(lobby: Lobby):
    best_alive = -1
    best_slot = None
    best_name = None
    for s in lobby.slots.all():
        alive = Creature.objects.filter(lobby=lobby, owner=s, alive=True).count()
        if alive > best_alive:
            best_alive = alive
            best_slot = s.slot_index
            best_name = s.display_name
    return best_slot, best_name, best_alive


@require_GET
def api_state(request, lobby_id: int):
    lobby = get_object_or_404(Lobby, id=lobby_id)

    maybe_advance_phase(lobby)
    lobby.refresh_from_db()

    if not request.session.session_key:
        request.session.save()
    sess = request.session.session_key

    me_slot = lobby.slots.filter(session_key=sess).first()
    slot_idx = me_slot.slot_index if me_slot else None

    slots_payload = []
    for s in lobby.slots.order_by("slot_index"):
        alive_qs = Creature.objects.filter(lobby=lobby, owner=s, alive=True)
        alive_count = alive_qs.count()

        agg = alive_qs.aggregate(
            avg_size=Avg("size"),
            avg_speed=Avg("speed"),
            avg_danger=Avg("danger"),
            avg_energy=Avg("energy_max"),
            avg_vision=Avg("vision"),
        )

        slots_payload.append({
            "slot_index": s.slot_index,
            "display_name": s.display_name,
            "color_hex": s.color_hex,
            "is_bot": s.is_bot,
            "coins": s.coins,
            "energy_bonus": s.energy_bonus,
            "vision_bonus": s.vision_bonus,

            "alive": alive_count,
            "avg_size": round(float(agg["avg_size"] or 0.0), 2),
            "avg_speed": round(float(agg["avg_speed"] or 0.0), 2),
            "avg_danger": round(float(agg["avg_danger"] or 0.0), 2),

            # ✅ NUEVOS
            "avg_energy": round(float(agg["avg_energy"] or 0.0), 1),
            "avg_vision": round(float(agg["avg_vision"] or 0.0), 1),

            "base_x": float(s.base_x),
            "base_y": float(s.base_y),
        })

    winner = None
    if lobby.status == LobbyStatus.FINISHED:
        ws, wn, wa = _compute_winner(lobby)
        winner = {"slot": ws, "name": wn, "alive": wa}

    return JsonResponse({
        "ok": True,
        "server_time": timezone.now().isoformat(),
        "winner": winner,
        "shop": {  # ✅ para mostrar costos/bonos
            "energy_cost": UP_ENERGY_COST,
            "vision_cost": UP_VISION_COST,
            "energy_add": UP_ENERGY_ADD,
            "vision_add": UP_VISION_ADD,
        },
        "lobby": {
            "id": lobby.id,
            "name": lobby.name,
            "status": lobby.status,
            "day": lobby.day,
            "max_days": lobby.max_days,
            "map_w": lobby.map_w,
            "map_h": lobby.map_h,
            "food_per_day": lobby.food_per_day,

            "phase": lobby.phase,
            "phase_started_at": lobby.phase_started_at.isoformat() if lobby.phase_started_at else None,
            "phase_end_at": lobby.phase_end_at.isoformat() if lobby.phase_end_at else None,

            "current_payload_day": lobby.current_payload_day,
        },
        "me": {"slot": slot_idx},
        "slots": slots_payload,
    })


@require_GET
def api_day_payload(request, lobby_id: int, day: int):
    lobby = get_object_or_404(Lobby, id=lobby_id)
    log = get_object_or_404(DayLog, lobby=lobby, day=day)
    return JsonResponse({"ok": True, "payload": log.payload})


@require_POST
def api_join(request, lobby_id: int):
    lobby = get_object_or_404(Lobby, id=lobby_id)

    # ✅ asegura slots antes de iniciar
    ensure_four_slots(lobby)

    if not request.session.session_key:
        request.session.save()
    sess = request.session.session_key
    name = (request.POST.get("name") or "Player").strip()[:32]

    slot = lobby.slots.filter(session_key=sess).first()
    if slot:
        return JsonResponse({"ok": True, "slot": slot.slot_index})

    slot = lobby.slots.filter(session_key__isnull=True).order_by("slot_index").first()
    if not slot:
        return JsonResponse({"ok": False, "error": "Lobby lleno."}, status=400)

    slot.session_key = sess
    slot.display_name = name
    slot.is_bot = False
    slot.save(update_fields=["session_key", "display_name", "is_bot"])
    return JsonResponse({"ok": True, "slot": slot.slot_index})


@require_POST
def api_start(request, lobby_id: int):
    lobby = get_object_or_404(Lobby, id=lobby_id)
    start_game(lobby)
    return JsonResponse({"ok": True})


@require_POST
def api_buy(request, lobby_id: int):
    lobby = get_object_or_404(Lobby, id=lobby_id)
    if not request.session.session_key:
        request.session.save()
    sess = request.session.session_key

    slot = lobby.slots.filter(session_key=sess).first()
    if not slot:
        return JsonResponse({"ok": False, "error": "Debes unirte primero."}, status=400)

    kind = (request.POST.get("kind") or "").strip()
    ok, msg = buy_upgrade(slot, kind)
    if not ok:
        return JsonResponse({"ok": False, "error": msg}, status=400)

    return JsonResponse({"ok": True, "detail": msg})
