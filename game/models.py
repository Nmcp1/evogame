from django.db import models


class LobbyStatus(models.TextChoices):
    WAITING = "WAITING", "Waiting"
    RUNNING = "RUNNING", "Running"
    FINISHED = "FINISHED", "Finished"


class LobbyPhase(models.TextChoices):
    DAY = "DAY", "Day"
    PAUSE = "PAUSE", "Pause"


class Lobby(models.Model):
    name = models.CharField(max_length=64, default="Lobby")
    status = models.CharField(max_length=16, choices=LobbyStatus.choices, default=LobbyStatus.WAITING)

    day = models.PositiveIntegerField(default=0)
    max_days = models.PositiveIntegerField(default=30)

    map_w = models.PositiveIntegerField(default=900)
    map_h = models.PositiveIntegerField(default=560)

    food_per_day = models.PositiveIntegerField(default=50)

    # ✅ NUEVO: sincronización tiempo real
    phase = models.CharField(max_length=8, choices=LobbyPhase.choices, default=LobbyPhase.DAY)
    phase_started_at = models.DateTimeField(null=True, blank=True)
    phase_end_at = models.DateTimeField(null=True, blank=True)

    # ✅ NUEVO: “día actual” mostrado en tiempo real (día del payload)
    current_payload_day = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"Lobby {self.id} - {self.name}"


class PlayerSlot(models.Model):
    lobby = models.ForeignKey(Lobby, related_name="slots", on_delete=models.CASCADE)
    slot_index = models.PositiveIntegerField()  # 1..4

    session_key = models.CharField(max_length=64, null=True, blank=True)
    display_name = models.CharField(max_length=32, default="Player")
    is_bot = models.BooleanField(default=False)

    color_hex = models.CharField(max_length=16, default="#999999")

    coins = models.IntegerField(default=0)
    energy_bonus = models.IntegerField(default=0)
    vision_bonus = models.IntegerField(default=0)

    base_x = models.FloatField(default=80)
    base_y = models.FloatField(default=80)

    class Meta:
        unique_together = ("lobby", "slot_index")

    def __str__(self):
        return f"Lobby {self.lobby_id} - Slot {self.slot_index}"


class Creature(models.Model):
    lobby = models.ForeignKey(Lobby, related_name="creatures", on_delete=models.CASCADE)
    owner = models.ForeignKey(PlayerSlot, related_name="creatures", on_delete=models.CASCADE)

    alive = models.BooleanField(default=True)

    size = models.FloatField(default=1.0)
    speed = models.FloatField(default=1.0)
    danger = models.FloatField(default=0.5)

    energy_max = models.FloatField(default=100.0)
    energy = models.FloatField(default=100.0)
    vision = models.FloatField(default=90.0)

    x = models.FloatField(default=100.0)
    y = models.FloatField(default=100.0)

    carried_food = models.IntegerField(default=0)
    created_day = models.IntegerField(default=0)

    def __str__(self):
        return f"C{self.id} S{self.owner.slot_index} alive={self.alive}"


class Food(models.Model):
    lobby = models.ForeignKey(Lobby, related_name="foods", on_delete=models.CASCADE)
    x = models.FloatField()
    y = models.FloatField()
    active = models.BooleanField(default=True)


class DayLog(models.Model):
    lobby = models.ForeignKey(Lobby, related_name="day_logs", on_delete=models.CASCADE)
    day = models.PositiveIntegerField()
    payload = models.JSONField(default=dict)

    class Meta:
        unique_together = ("lobby", "day")
