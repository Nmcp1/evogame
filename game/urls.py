from django.urls import path
from . import views

urlpatterns = [
    path("", views.lobby_list, name="lobby_list"),
    path("lobby/create/", views.lobby_create, name="lobby_create"),
    path("lobby/<int:lobby_id>/", views.lobby_room, name="lobby_room"),

    path("api/lobby/<int:lobby_id>/state/", views.api_state, name="api_state"),
    path("api/lobby/<int:lobby_id>/day/<int:day>/payload/", views.api_day_payload, name="api_day_payload"),

    path("api/lobby/<int:lobby_id>/join/", views.api_join, name="api_join"),
    path("api/lobby/<int:lobby_id>/start/", views.api_start, name="api_start"),
    path("api/lobby/<int:lobby_id>/buy/", views.api_buy, name="api_buy"),
]
